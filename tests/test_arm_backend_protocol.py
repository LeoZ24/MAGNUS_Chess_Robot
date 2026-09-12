"""Tests del protocolo de texto entre el host y la CyberPi.

No hay hardware: se levanta un **cliente falso** que habla el mismo protocolo
de líneas que ``examples/cyberpi_arm_client.py`` y se comprueba que el
``CyberPiBackend`` manda lo que debe y entiende lo que recibe.
"""

import socket
import threading

import pytest

from magnus.arm.backend import ArmBackendError, CyberPiBackend, FakeArmBackend


class FakeCyberPi:
    """Cliente falso: se conecta al backend y responde según ``replies``.

    ``replies`` mapea el primer token del comando a la respuesta.  Los comandos
    recibidos quedan en ``received`` para poder afirmar sobre ellos.
    """

    def __init__(self, port, replies=None):
        self.port = port
        self.received: list[str] = []
        self.replies = {
            "PING": "ACK PONG",
            "HOME": "ACK HOME 0.0 0.0",
            "ZERO": "ACK ZERO",
            "GRIPPER": "ACK GRIPPER",
            "STOP": "ACK STOP",
            "GET": "ACK POS 1.5 -2.5",
            **(replies or {}),
        }
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._sock: socket.socket | None = None

    def start(self):
        self._thread.start()
        return self

    def _reply_for(self, line: str) -> str:
        cmd = line.split()[0]
        reply = self.replies.get(cmd)
        if callable(reply):
            return reply(line)
        return reply if reply is not None else f"ERR desconocido: {cmd}"

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", self.port))
        self._sock = sock
        buf = b""
        try:
            while True:
                data = sock.recv(128)
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode().strip()
                    if not line:
                        continue
                    self.received.append(line)
                    sock.sendall((self._reply_for(line) + "\n").encode())
        except OSError:
            return

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def link():
    """Un backend conectado a un cliente falso; se cierran solos al terminar."""
    created = []

    def make(replies=None):
        port = _free_port()
        backend = CyberPiBackend(bind_host="127.0.0.1", port=port,
                                 accept_timeout=5.0, command_timeout=5.0,
                                 home_timeout=5.0)
        cyber = FakeCyberPi(port, replies)
        # El cliente se conecta mientras el backend está en accept().
        threading.Timer(0.05, cyber.start).start()
        backend.connect()
        created.append((backend, cyber))
        return backend, cyber

    yield make
    for backend, cyber in created:
        cyber.close()
        backend.disconnect()


def test_handshake_sends_ping(link):
    _, cyber = link()
    assert cyber.received == ["PING"]


def test_home_asks_the_board_to_reference_itself(link):
    backend, cyber = link()
    backend.home()
    assert cyber.received[-1] == "HOME"


def test_home_rejects_an_unexpected_answer(link):
    backend, _ = link({"HOME": "ACK ZERO"})
    with pytest.raises(ArmBackendError, match="HOME"):
        backend.home()


def test_home_surfaces_the_board_error(link):
    backend, _ = link({"HOME": "ERR referenciado: no encontre el tope"})
    with pytest.raises(ArmBackendError, match="no encontre el tope"):
        backend.home()


def test_home_restores_the_normal_timeout(link):
    backend, _ = link()
    before = backend.command_timeout
    backend.home()
    assert backend.command_timeout == before


def test_move_sends_two_decimals(link):
    backend, cyber = link({"MOVE": "ACK MOVE"})
    backend.move_to(12.345, -7.0)
    assert cyber.received[-1] == "MOVE 12.35 -7.00"


def test_move_accepts_the_old_ack_without_angles(link):
    """El cliente viejo contesta "ACK MOVE" a secas: debe seguir valiendo."""
    backend, _ = link({"MOVE": "ACK MOVE"})
    backend.move_to(10.0, 20.0)
    assert backend.last_reached is None


def test_move_records_the_angles_actually_reached(link):
    backend, _ = link({"MOVE": "ACK MOVE 9.80 19.50"})
    backend.move_to(10.0, 20.0)
    assert backend.last_reached == (9.8, 19.5)


def test_move_warns_when_the_arm_falls_short(link, caplog):
    backend, _ = link({"MOVE": "ACK MOVE 2.00 20.00"})
    with caplog.at_level("WARNING", logger="magnus.arm.backend"):
        backend.move_to(30.0, 20.0)
    assert "corto" in caplog.text


def test_move_ignores_unparseable_angles(link):
    """Los ángulos son diagnóstico: si vienen rotos no tumban la jugada."""
    backend, _ = link({"MOVE": "ACK MOVE bastante poco"})
    backend.move_to(1.0, 2.0)
    assert backend.last_reached is None


def test_move_rejects_a_wrong_answer(link):
    backend, _ = link({"MOVE": "ACK GRIPPER"})
    with pytest.raises(ArmBackendError, match="ACK MOVE"):
        backend.move_to(1.0, 2.0)


def test_get_position_parses_the_pair(link):
    backend, _ = link()
    assert backend.get_position() == (1.5, -2.5)


def test_home_needs_a_connection():
    backend = CyberPiBackend(bind_host="127.0.0.1", port=_free_port())
    with pytest.raises(ArmBackendError, match="no conectado"):
        backend.home()


def test_fake_backend_records_home():
    fake = FakeArmBackend()
    fake.connect()
    fake.home()
    assert ("home",) in fake.commands


def test_fake_backend_home_needs_connection():
    with pytest.raises(ArmBackendError, match="no conectado"):
        FakeArmBackend().home()
