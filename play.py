#!/usr/bin/env python3
"""MAGNUS — jugar una partida con la interfaz web.

Arranca el robot completo (visión + engine + voz + brazo) y abre el panel de
control en el navegador.  Desde ahí se inicia la partida, se cambia la
dificultad en caliente, se elige el color del robot, se configura la cámara,
la voz y el brazo, y se ve la partida en vivo con el tablero animado.

Uso:
    python3 play.py                       # webcam 0, abre el navegador
    python3 play.py --camera 1
    python3 play.py --synthetic           # sin cámara: tablero simulado que juega solo
    python3 play.py --list-cameras        # ¿qué índice da imagen?
    python3 play.py --host 0.0.0.0        # controlar desde una tablet/móvil del hotspot
    python3 play.py --kiosk               # pantalla completa para la feria
    python3 play.py --no-engine --no-voice

Los ajustes que se cambian desde la interfaz se guardan en
``magnus_settings.json`` (cámbialo con --settings) y se recuperan al arrancar.
Los argumentos de línea de comandos tienen prioridad sobre el archivo.

Brazo: mientras no exista ``magnus/arm/positions.json`` (la tabla calibrada),
la interfaz solo ofrece los modos "apagado" y "simulado".  Cuando la tabla
esté completa, basta con elegir "CyberPi" en Ajustes > Brazo.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import webbrowser
from pathlib import Path

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from magnus.app.controller import MagnusController  # noqa: E402
from magnus.app.server import AppServer  # noqa: E402
from magnus.app.settings import ARM_MODES, DEFAULT_SETTINGS_FILE, AppSettings  # noqa: E402
from magnus.engine.difficulty import DifficultyLevel  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MAGNUS — jugar con la interfaz web")
    parser.add_argument("--camera", type=int, default=None, help="Índice de la cámara")
    parser.add_argument("--list-cameras", action="store_true",
                        help="Lista los índices de cámara que dan imagen y sale")
    parser.add_argument("--synthetic", action="store_true",
                        help="Sin cámara: tablero simulado que juega solo")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interfaz de red del servidor (0.0.0.0 = toda la red local)")
    parser.add_argument("--port", type=int, default=8080, help="Puerto HTTP")
    parser.add_argument("--no-browser", action="store_true",
                        help="No abrir el navegador automáticamente")
    parser.add_argument("--kiosk", action="store_true",
                        help="Abre la interfaz en modo kiosco (pantalla completa, sin ajustes)")
    parser.add_argument("--no-engine", action="store_true", help="No usar Stockfish")
    parser.add_argument("--no-voice", action="store_true", help="Sin voz")
    parser.add_argument("--voice-model", default=None,
                        help="Ruta al .onnx de la voz de Piper a usar")
    parser.add_argument("--say-voice", default=None,
                        help="Voz de macOS a usar (p. ej. Juan, Jorge, Diego)")
    parser.add_argument("--difficulty", default=None,
                        choices=[lvl.name for lvl in DifficultyLevel],
                        help="Dificultad inicial (se puede cambiar desde la interfaz)")
    parser.add_argument("--robot-side", choices=["white", "black"], default=None,
                        help="Color que juega el robot")
    parser.add_argument("--arm", choices=list(ARM_MODES), default=None,
                        help="Modo inicial del brazo")
    parser.add_argument("--settings", default=DEFAULT_SETTINGS_FILE,
                        help="Archivo de ajustes persistentes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.list_cameras:
        from magnus.vision.vision_node import probe_cameras

        found = probe_cameras()
        if not found:
            print("No se encontró ninguna cámara con imagen.\n"
                  "Revisa los permisos de cámara del sistema (y reinicia la app "
                  "desde la que ejecutas) y, si usas una cámara virtual como "
                  "Iriun, que el móvil esté conectado y transmitiendo.")
            return 2
        print("Cámaras con imagen:")
        for index, (width, height) in found:
            print(f"  --camera {index}   ({width}×{height})")
        return 0

    # --- Ajustes: archivo < línea de comandos --------------------------- #
    settings = AppSettings.load(args.settings)
    overrides = {}
    if args.camera is not None:
        overrides["camera_index"] = args.camera
    if args.difficulty:
        overrides["difficulty"] = args.difficulty
    if args.robot_side:
        overrides["robot_side"] = args.robot_side
    if args.arm:
        overrides["arm_mode"] = args.arm
    if overrides:
        settings = settings.update(**overrides)

    voice_backend = None
    if not args.no_voice and (args.voice_model or args.say_voice):
        from magnus.voice.backend import MacSayBackend, PiperBackend

        voice_backend = (PiperBackend(model=args.voice_model) if args.voice_model
                         else MacSayBackend(voice=args.say_voice))

    controller = MagnusController(
        settings,
        settings_path=args.settings,
        synthetic=args.synthetic,
        engine_enabled=not args.no_engine,
        voice_enabled=not args.no_voice,
        voice_backend=voice_backend,
    )
    server = AppServer(controller, host=args.host, port=args.port)

    stop = threading.Event()

    def handle_signal(signum, frame):  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    url = server.url + ("?kiosk=1" if args.kiosk else "")
    print("=" * 64)
    print("  MAGNUS — panel de control")
    print(f"  {url}")
    if args.host == "0.0.0.0":
        print("  (accesible desde cualquier dispositivo de la red local)")
    print("  Ctrl+C para salir")
    print("=" * 64)

    with controller, server:
        if controller.camera_error and not args.synthetic:
            print(f"AVISO: {controller.camera_error}", file=sys.stderr)
            print("La interfaz arranca igual: cambia la cámara desde Ajustes.",
                  file=sys.stderr)
        if not args.no_browser:
            webbrowser.open(url)
        while not stop.is_set():
            stop.wait(0.5)
    print("Hasta la próxima.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
