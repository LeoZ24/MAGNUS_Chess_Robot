"""Servidor HTTP de la aplicación (solo librería estándar).

Rutas:

    GET  /                      la interfaz (``static/index.html``)
    GET  /static/<archivo>      CSS / JS de la interfaz
    GET  /api/state             el snapshot del controlador (JSON)
    GET  /api/events            el mismo snapshot en streaming (Server-Sent
                                Events): se envía cada vez que cambia
    GET  /stream/camera.mjpg    la cámara con overlays (multipart MJPEG)
    POST /api/command           ``{"name": "...", "params": {...}}``

Se usa ``ThreadingHTTPServer`` porque los streams (SSE y MJPEG) mantienen la
conexión abierta: cada cliente ocupa un hilo mientras mira.  El estado lo
sirve el controlador ya serializado, así que aquí no se toca la partida.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from .controller import MagnusController

logger = logging.getLogger("magnus.app.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Cadencia máxima del stream de estado (el snapshot cambia en cada frame, pero
# la interfaz no necesita más de ~12 actualizaciones por segundo).
SSE_MIN_INTERVAL_S = 1.0 / 12.0
SSE_KEEPALIVE_S = 1.0
MJPEG_BOUNDARY = "magnusframe"
MAX_BODY_BYTES = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    """Un handler por petición; el controlador llega vía el servidor."""

    server: "AppServer"
    protocol_version = "HTTP/1.1"

    # -- utilidades ----------------------------------------------------- #
    def log_message(self, fmt: str, *args) -> None:   # silencia el log por defecto
        logger.debug("%s " + fmt, self.address_string(), *args)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str,
                    status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self._send_json({"ok": False, "error": "no encontrado"}, HTTPStatus.NOT_FOUND)

    # -- GET ------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802 (nombre fijado por BaseHTTPRequestHandler)
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            elif path == "/api/state":
                self._send_json(self.server.controller.snapshot())
            elif path == "/api/events":
                self._serve_events()
            elif path == "/stream/camera.mjpg":
                self._serve_mjpeg()
            elif path == "/stream/camera.jpg":
                jpeg, _ = self.server.controller.latest_jpeg()
                if jpeg is None:
                    self._not_found()
                else:
                    self._send_bytes(jpeg, "image/jpeg")
            else:
                self._not_found()
        except (BrokenPipeError, ConnectionResetError):
            pass    # el navegador cerró la pestaña: normal en los streams

    def _serve_static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            self._not_found()
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",
                                                                 "application/json"):
            content_type += "; charset=utf-8"
        self._send_bytes(target.read_bytes(), content_type)

    def _serve_events(self) -> None:
        controller = self.server.controller
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # El stream ocupa la conexión entera: al terminar se cierra (no se
        # reutiliza para otra petición).  Se fija DESPUÉS de las cabeceras
        # porque send_header("Connection", ...) lo sobreescribiría.
        self.close_connection = True
        last_seq = -1
        last_sent = 0.0
        while not self.server.stopping:
            snapshot = controller.snapshot()
            seq = snapshot.get("seq", 0)
            now = time.monotonic()
            if seq != last_seq and now - last_sent >= SSE_MIN_INTERVAL_S:
                data = json.dumps(snapshot, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_seq, last_sent = seq, now
            elif now - last_sent >= SSE_KEEPALIVE_S:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                last_sent = now
            time.sleep(SSE_MIN_INTERVAL_S / 2)

    def _serve_mjpeg(self) -> None:
        controller = self.server.controller
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        seq = -1
        while not self.server.stopping:
            jpeg, new_seq = controller.wait_for_jpeg(seq, timeout=1.0)
            if jpeg is None or new_seq == seq:
                continue
            seq = new_seq
            self.wfile.write(
                f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
            )
            self.wfile.write(jpeg)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
            if self.server.single_frame:       # tests: un frame y fuera
                return

    # -- POST ----------------------------------------------------------- #
    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path != "/api/command":
            self._not_found()
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._send_json({"ok": False, "error": "cuerpo demasiado grande"},
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            name = payload["name"]
            params = payload.get("params") or {}
            if not isinstance(name, str) or not isinstance(params, dict):
                raise ValueError("formato: {name: str, params: object}")
        except (ValueError, KeyError, TypeError) as exc:
            self._send_json({"ok": False, "error": f"petición inválida: {exc}"},
                            HTTPStatus.BAD_REQUEST)
            return
        result = self.server.controller.command(name, params)
        self._send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)


class AppServer(ThreadingHTTPServer):
    """Servidor de la interfaz; ``start()``/``stop()`` lo corren en un hilo."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, controller: MagnusController, host: str = "127.0.0.1", port: int = 8080):
        super().__init__((host, port), _Handler)
        self.controller = controller
        self.stopping = False
        self.single_frame = False
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        shown = "localhost" if host in ("0.0.0.0", "127.0.0.1", "") else host
        return f"http://{shown}:{port}/"

    def start(self) -> "AppServer":
        self._thread = threading.Thread(target=self.serve_forever, name="magnus-http",
                                        daemon=True)
        self._thread.start()
        logger.info("Interfaz en %s", self.url)
        return self

    def stop(self) -> None:
        self.stopping = True
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def __enter__(self) -> "AppServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
