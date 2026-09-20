"""Regresiones del cliente MicroPython con un shield falso, sin red ni motores."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


CLIENT_PATH = Path(__file__).resolve().parents[1] / "examples/cyberpi_arm_client.py"


class StartupReached(BaseException):
    """Detiene el arranque simulado antes de abrir red o mover motores."""


@pytest.mark.parametrize("module_name", ["__main__", "user_program", None])
def test_complete_uploaded_script_starts_without_requiring_main(monkeypatch, module_name):
    messages = []
    colors = []

    def stop_before_network():
        assert any("MAGNUS" in message for message in messages)
        assert colors == ["blue"]
        raise StartupReached

    cyberpi = SimpleNamespace(
        console=SimpleNamespace(clear=lambda: None, println=messages.append),
        led=SimpleNamespace(on=colors.append),
        wifi=SimpleNamespace(is_connect=stop_before_network),
    )
    monkeypatch.setitem(sys.modules, "cyberpi", cyberpi)
    monkeypatch.setitem(sys.modules, "usocket", SimpleNamespace())
    namespace = {} if module_name is None else {"__name__": module_name}
    with pytest.raises(StartupReached):
        exec(compile(CLIENT_PATH.read_text(), str(CLIENT_PATH), "exec"), namespace)
    assert any("MAGNUS" in message for message in messages)


class FakeMbot2:
    """Simula encoders y retencion; permite introducir carga y atasco."""

    def __init__(self):
        self.angles = {"EM1": 0.0, "EM2": 0.0}
        self.locked = {"EM1": False, "EM2": False}
        self.turns = []
        self.events = []
        self.fraction = 1.0
        self.after_turn = lambda port: None

    def EM_get_angle(self, port):
        return self.angles[port]

    def EM_lock(self, enabled, port):
        self.events.append(("lock", enabled, port))
        for axis in self.angles if port == "all" else (port,):
            self.locked[axis] = enabled

    def EM_turn(self, delta, speed, port):
        self.turns.append((delta, speed, port))
        self.angles[port] += delta * self.fraction
        self.after_turn(port)

    def EM_stop(self, port):
        self.events.append(("stop", port))

    def EM_reset_angle(self, port):
        assert not self.locked[port], "No resetear un encoder con un objetivo viejo"
        self.angles[port] = 0.0

    def EM_set_power(self, power, port):
        assert not self.locked[port], "No buscar el tope contra la retencion"
        self.events.append(("power", power, port))


@pytest.fixture
def client(monkeypatch):
    hardware = FakeMbot2()

    def stop_before_network():
        raise StartupReached

    cyberpi = SimpleNamespace(
        mbot2=hardware,
        console=SimpleNamespace(clear=lambda: None, println=lambda text: None),
        led=SimpleNamespace(on=lambda color: None),
        wifi=SimpleNamespace(is_connect=stop_before_network),
    )
    monkeypatch.setitem(sys.modules, "cyberpi", cyberpi)
    monkeypatch.setitem(sys.modules, "usocket", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("arm_client_test", CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Ejecutar tambien el arranque real, pero interrumpir ANTES de la red.
    # No alterar el archivo de produccion para facilitar su importacion.
    with pytest.raises(StartupReached):
        spec.loader.exec_module(module)
    module.time = SimpleNamespace(sleep=lambda seconds: None)
    module.VERBOSE = False
    return module, hardware


def test_move_holds_both_axes_and_returns_final_shoulder_reading(client):
    module, hardware = client

    def change_load(port):
        assert all(hardware.locked.values())
        if port == "EM2":
            hardware.angles["EM1"] -= 0.5

    hardware.after_turn = change_load
    assert module.handle("MOVE 30 -20") == "ACK MOVE 29.5 -20.0"
    assert all(hardware.locked.values())


def test_move_rejects_shoulder_drift_during_elbow_motion(client):
    module, hardware = client

    def change_load(port):
        if port == "EM2":
            hardware.angles["EM1"] -= 10.0

    hardware.after_turn = change_load
    with pytest.raises(ValueError, match="hombro no mantuvo"):
        module.handle("MOVE 30 -20")


def test_move_at_current_pose_also_enables_hold(client):
    module, hardware = client
    module.handle("MOVE 0 0")
    assert all(hardware.locked.values())
    assert hardware.turns == []


def test_stop_and_manual_zero_release_and_next_move_reenables_hold(client):
    module, hardware = client
    module.handle("MOVE 30 -20")
    assert module.handle("STOP") == "ACK STOP"
    assert not any(hardware.locked.values())
    module.handle("MOVE 30 -20")
    assert all(hardware.locked.values())
    assert module.handle("ZERO") == "ACK ZERO"
    assert hardware.angles == {"EM1": 0.0, "EM2": 0.0}
    assert not any(hardware.locked.values())


@pytest.mark.parametrize("command", ["MOVE 30 20", "MOVE 30 nan", "MOVE inf -20"])
def test_invalid_either_target_never_moves_the_other_axis(client, command):
    module, hardware = client
    with pytest.raises(ValueError, match="fuera de limites"):
        module.handle(command)
    assert hardware.turns == []


def test_stalled_motor_has_a_bounded_number_of_attempts(client):
    module, hardware = client
    hardware.fraction = 0.0
    with pytest.raises(ValueError, match="hombro no alcanzo"):
        module.handle("MOVE 30 -20")
    assert len(hardware.turns) == module.MOVE_MAX_PASSES
    assert all(turn[2] == "EM1" for turn in hardware.turns)


def test_small_shoulder_corrections_keep_speed_under_load(client):
    module, hardware = client
    module.handle("MOVE 10 -10")
    assert hardware.turns == [(10.0, 60, "EM1"), (-10.0, 40, "EM2")]


def test_backlash_stays_in_limits_and_rereads_before_relative_correction(client):
    module, hardware = client
    hardware.angles["EM1"] = 10.0
    module.BACKLASH_DEG = 5.0
    module.handle("MOVE 2 0")
    assert [turn[0] for turn in hardware.turns] == [-10.0, 2.0]
    assert hardware.angles["EM1"] == 2.0


def test_home_keeps_the_other_axis_held_and_relocks_at_new_zero(client):
    module, hardware = client
    axes = []

    def seek(port, sign, power, name):
        axes.append(port)
        other = "EM1" if port == "EM2" else "EM2"
        assert hardware.locked[other]
        module._hw_hold(False, port)
        module._hw_stop_axis(port)

    module._seek_stop = seek
    module.handle("HOME")
    assert axes == ["EM2", "EM2", "EM1", "EM1"]
    assert all(hardware.locked.values())
    assert hardware.angles == {"EM1": 0.0, "EM2": 0.0}


def test_seek_stop_disables_hold_and_stops_on_timeout(client):
    module, hardware = client
    module._hw_hold(True, "all")
    elapsed = iter([0.0, module.HOME_TIMEOUT_S + 1.0])
    module.time.time = lambda: next(elapsed)
    with pytest.raises(ValueError, match="no encontre el tope"):
        module._seek_stop("EM2", 1, 30, "codo")
    assert hardware.locked == {"EM1": True, "EM2": False}
    assert ("stop", "EM2") in hardware.events


def test_missing_lock_api_reports_error_before_motion_and_still_allows_stop(client):
    module, hardware = client
    hardware.EM_lock = None
    with pytest.raises(ValueError, match="EM_lock no disponible"):
        module.handle("MOVE 30 -20")
    assert hardware.turns == []
    assert module.handle("STOP") == "ACK STOP"


@pytest.mark.parametrize("stall", [False, True])
def test_session_releases_on_disconnect_and_on_motion_error(client, stall):
    module, hardware = client
    hardware.fraction = 0.0 if stall else 1.0
    replies = []
    closed = []
    packets = iter([b"MOVE 30 -20\n", b""])

    def receive(size):
        if replies:
            assert all(hardware.locked.values()) is (not stall)
        return next(packets)

    sock = SimpleNamespace(
        connect=lambda address: None, recv=receive,
        send=lambda data: replies.append(data), close=lambda: closed.append(True),
    )
    module.usocket = SimpleNamespace(AF_INET=2, SOCK_STREAM=1, socket=lambda *args: sock)
    module.sesion()
    assert replies[0].startswith(b"ERR " if stall else b"ACK MOVE ")
    assert not any(hardware.locked.values())
    assert closed == [True]
