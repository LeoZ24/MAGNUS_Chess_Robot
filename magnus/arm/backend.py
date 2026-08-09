"""Backends del brazo robótico de MAGNUS.

El nodo del brazo no habla directamente con el hardware: lo hace a través de un
``ArmBackend``.  Igual que en ``magnus/engine/``, esto permite:

    * ``FakeArmBackend`` -> tests y demos sin hardware (registra los comandos)
    * ``CyberPiBackend`` -> el hardware real (CyberPi / kit mBot2) por Wi-Fi TCP

Recordatorio de arquitectura: el brazo NO calcula cinemática inversa.  Los
ángulos de hombro/codo vienen siempre de la tabla de posiciones pregrabadas
(``positions_table.py``); el backend solo los ejecuta.
"""

from __future__ import annotations

import logging
import socket
from abc import ABC, abstractmethod

logger = logging.getLogger("magnus.arm.backend")


class ArmBackendError(Exception):
    """Error de comunicación o ejecución en el backend del brazo."""


class ArmBackend(ABC):
    """Interfaz que debe implementar cualquier controlador físico del brazo."""

    @abstractmethod
    def connect(self) -> None:
        """Establece la conexión con el hardware."""

    @abstractmethod
    def disconnect(self) -> None:
        """Cierra la conexión y libera recursos."""

    @abstractmethod
    def move_to(self, shoulder: float, elbow: float) -> None:
        """Mueve las articulaciones a los valores dados (de la tabla, sin IK).

        Las unidades (grados o pasos de encoder) son las mismas en las que se
        grabó la tabla de posiciones — el backend no las interpreta.
        """

    @abstractmethod
    def set_gripper(self, engaged: bool) -> None:
        """Activa (agarrar) o desactiva (soltar) la garra (servo 3)."""

    # Azúcar para usarlo como context manager.
    def __enter__(self) -> "ArmBackend":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()


class FakeArmBackend(ArmBackend):
    """Backend falso para tests y demos: registra los comandos, no mueve nada.

    Cada comando queda en :attr:`commands` como una tupla:

        ("connect",) / ("disconnect",)
        ("move_to", shoulder, elbow)
        ("gripper", True|False)
    """

    def __init__(self):
        self.commands: list[tuple] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        self.commands.append(("connect",))
        logger.debug("FakeArmBackend conectado.")

    def disconnect(self) -> None:
        self.connected = False
        self.commands.append(("disconnect",))
        logger.debug("FakeArmBackend desconectado.")

    def move_to(self, shoulder: float, elbow: float) -> None:
        if not self.connected:
            raise ArmBackendError("Backend no conectado (llama a connect()).")
        self.commands.append(("move_to", shoulder, elbow))

    def set_gripper(self, engaged: bool) -> None:
        if not self.connected:
            raise ArmBackendError("Backend no conectado (llama a connect()).")
        self.commands.append(("gripper", engaged))

    def clear(self) -> None:
        """Limpia el registro de comandos (útil entre casos de test)."""
        self.commands.clear()


class CyberPiBackend(ArmBackend):
    """Backend real del brazo: servidor TCP que la CyberPi (cliente) contacta.

    ARQUITECTURA DE RED (documentar para los jueces):
        El host (Mac/Pi) actúa como SERVIDOR TCP y la CyberPi como CLIENTE que se
        conecta a él. Esto invierte la relación intuitiva (normalmente el que da
        órdenes es el cliente), pero es una decisión deliberada: en la red del
        hotspot no podíamos leer de forma fiable la IP de la CyberPi, así que es
        la CyberPi la que inicia la conexión hacia una IP de host conocida.
        Resultado: cero descubrimiento de IP, reconexión automática si el enlace
        se cae.

    UNIDADES: grados de MOTOR (encoder), absolutos. Las mismas en las que se
    grabó positions.json. El backend NO las interpreta ni convierte.

    Uso típico (idéntico al de FakeArmBackend, vía ArmNode)::

        from magnus.arm import ArmNode, CyberPiBackend, PositionsTable

        table = PositionsTable.load("magnus/arm/positions.json")
        with ArmNode(backend=CyberPiBackend(), table=table) as arm:
            arm.execute(resp)   # resp: MoveResponse del engine
    """

    def __init__(
        self,
        bind_host: str = "0.0.0.0",
        port: int = 5555,
        *,
        accept_timeout: float = 90.0,
        command_timeout: float = 20.0,
    ):
        """
        Parámetros:
            bind_host: interfaz donde escuchar. "0.0.0.0" = todas (recomendado).
            port: puerto TCP. Debe coincidir con PORT en el cliente CyberPi.
            accept_timeout: segundos a esperar a que la CyberPi se conecte.
            command_timeout: segundos máximos a esperar el ACK de un comando.
                Debe ser mayor que el movimiento físico más lento del brazo.
        """
        self.bind_host = bind_host
        self.port = port
        self.accept_timeout = accept_timeout
        self.command_timeout = command_timeout

        self._server: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._rx_buffer: bytes = b""

    # ------------------------------------------------------------------ #
    # Descubrir la IP local (solo para mostrarla en el log)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _local_ip() -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        ip = self._local_ip()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server.bind((self.bind_host, self.port))
        except OSError as e:
            self._server.close()
            self._server = None
            raise ArmBackendError(
                f"No pude abrir el puerto {self.port}: {e}. "
                "¿Hay otro proceso usándolo?"
            ) from e
        self._server.listen(1)
        self._server.settimeout(self.accept_timeout)

        logger.info("=" * 52)
        logger.info("  CyberPiBackend escuchando en %s:%d", ip, self.port)
        logger.info("  -> pon MAC_IP = \"%s\" en el cliente CyberPi", ip)
        logger.info("  Esperando a que la CyberPi se conecte...")
        logger.info("=" * 52)

        try:
            conn, addr = self._server.accept()
        except socket.timeout as e:
            self.disconnect()
            raise ArmBackendError(
                "La CyberPi no se conectó a tiempo. Verifica: cliente subido y "
                "corriendo, hotspot 2.4 GHz, y MAC_IP correcta en el cliente."
            ) from e

        conn.settimeout(self.command_timeout)
        self._conn = conn
        self._rx_buffer = b""
        logger.info("CyberPi conectada desde %s", addr[0])

        # Handshake: confirmar que responde antes de dar por buena la conexión.
        pong = self._command("PING")
        if pong != "ACK PONG":
            self.disconnect()
            raise ArmBackendError(f"Handshake inesperado: {pong!r}")
        logger.info("Handshake OK. Brazo listo.")

    def disconnect(self) -> None:
        for sock in (self._conn, self._server):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self._conn = None
        self._server = None
        self._rx_buffer = b""
        logger.debug("CyberPiBackend desconectado.")

    # ------------------------------------------------------------------ #
    # Comunicación de bajo nivel
    # ------------------------------------------------------------------ #
    def _read_line(self) -> str:
        """Lee una línea (hasta \\n) de la CyberPi. Bloquea con timeout."""
        assert self._conn is not None
        while b"\n" not in self._rx_buffer:
            try:
                chunk = self._conn.recv(128)
            except socket.timeout as e:
                raise ArmBackendError(
                    "Timeout esperando respuesta de la CyberPi "
                    f"(> {self.command_timeout}s). ¿El brazo se atascó?"
                ) from e
            if not chunk:
                raise ArmBackendError("La CyberPi cerró la conexión.")
            self._rx_buffer += chunk
        line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
        return line.decode(errors="replace").strip()

    def _command(self, text: str) -> str:
        """Envía un comando y devuelve la respuesta (una línea)."""
        if self._conn is None:
            raise ArmBackendError("Backend no conectado (llama a connect()).")
        try:
            self._conn.sendall((text + "\n").encode())
        except OSError as e:
            raise ArmBackendError(f"Fallo al enviar '{text}': {e}") from e
        resp = self._read_line()
        if resp.startswith("ERR"):
            raise ArmBackendError(f"La CyberPi rechazó '{text}': {resp}")
        return resp

    def _expect(self, text: str, expected: str) -> None:
        resp = self._command(text)
        if resp != expected:
            raise ArmBackendError(
                f"Respuesta inesperada a '{text}': {resp!r} (esperaba {expected!r})"
            )

    # ------------------------------------------------------------------ #
    # Interfaz ArmBackend
    # ------------------------------------------------------------------ #
    def move_to(self, shoulder: float, elbow: float) -> None:
        # Formato con 2 decimales: suficiente para grados de motor, evita
        # notación científica que confundiría al parser del cliente.
        self._expect(f"MOVE {shoulder:.2f} {elbow:.2f}", "ACK MOVE")

    def set_gripper(self, engaged: bool) -> None:
        self._expect(f"GRIPPER {1 if engaged else 0}", "ACK GRIPPER")

    # ------------------------------------------------------------------ #
    # Extras útiles (no forman parte del contrato, pero ayudan a calibrar)
    # ------------------------------------------------------------------ #
    def zero_here(self) -> None:
        """Define la pose actual del brazo como el origen (0,0)."""
        self._expect("ZERO", "ACK ZERO")

    def get_position(self) -> tuple[float, float]:
        """Lee los ángulos actuales de motor (hombro, codo). Útil al calibrar."""
        resp = self._command("GET")
        parts = resp.split()
        if len(parts) != 4 or parts[0] != "ACK" or parts[1] != "POS":
            raise ArmBackendError(f"Respuesta GET inválida: {resp!r}")
        return float(parts[2]), float(parts[3])

    def stop(self) -> None:
        """Parada de emergencia: detiene los motores."""
        self._expect("STOP", "ACK STOP")