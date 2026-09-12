"""Tests del controlador de la aplicación con cámara sintética y dobles."""

import json
import time

import pytest

from magnus.app.controller import MagnusController
from magnus.app.settings import AppSettings
from magnus.vision.vision_node import CameraBackend, CameraError
from magnus.voice.backend import FakeSpeechBackend

from app_fakes import RandomEngineBackend, fake_engine_factory


def _wait_engine(controller, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.step()
        if controller.engine.status == "listo":
            return True
        time.sleep(0.01)
    return False


def _run(controller, frames, until=None, sleep=0.0):
    for _ in range(frames):
        controller.step()
        if until is not None and until(controller.snapshot()):
            return True
        if sleep:
            time.sleep(sleep)
    return False


@pytest.fixture
def controller(tmp_path):
    settings = AppSettings(difficulty="EASY", arm_mode="simulated", arm_auto_execute=True)
    backend = RandomEngineBackend()
    ctrl = MagnusController(
        settings,
        settings_path=str(tmp_path / "settings.json"),
        synthetic=True,
        engine_factory=fake_engine_factory(backend),
        voice_backend=FakeSpeechBackend(),
        arm_step_delay_s=0.0,
    )
    ctrl.engine_backend = backend
    ctrl.start_without_thread()
    yield ctrl
    ctrl.shutdown()


def test_setup_phase_sees_initial_position(controller):
    assert _wait_engine(controller)
    _run(controller, 20)
    snap = controller.snapshot()
    assert snap["phase"] == "setup" and snap["sub"] == "ready"
    assert snap["setup"]["pieces_detected"] == 32 and snap["setup"]["corners"] == 4
    assert snap["camera"]["synthetic"] and snap["camera"]["ok"]
    assert snap["engine"]["status"] == "listo"
    assert snap["arm"]["mode"] == "simulated" and snap["arm"]["status"] == "ready"
    json.dumps(snap)                          # serializable para la API
    jpeg, seq = controller.latest_jpeg()
    assert jpeg and jpeg[:2] == b"\xff\xd8" and seq > 0


def test_full_synthetic_game_with_simulated_arm(controller):
    assert _wait_engine(controller)
    controller.command("start_game")
    assert _run(controller, 4000, until=lambda s: len(s["history"]) >= 6, sleep=0.001)
    snap = controller.snapshot()
    assert snap["phase"] == "playing" and snap["in_game"]
    assert snap["history"][0] == "e4"                  # guion del humano
    assert snap["arm"]["last_outcome"] == "done"       # el brazo simulado movió
    assert snap["eval"]["label"] is not None           # la barra tiene datos
    assert any("Brazo ejecutando" in e["text"] for e in snap["events"])
    spoken = controller.voice.backend.spoken
    assert spoken and any("Muevo" in t or "muevo" in t for t in spoken)


def test_set_difficulty_applies_and_persists(controller, tmp_path):
    assert _wait_engine(controller)
    controller.command("set_difficulty", {"level": "expert"})
    _run(controller, 5, sleep=0.01)
    snap = controller.snapshot()
    assert snap["settings"]["difficulty"] == "EXPERT"
    assert snap["engine"]["difficulty"] == "EXPERT"
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["difficulty"] == "EXPERT"
    controller.command("start_game")
    assert _run(controller, 3000, until=lambda s: len(s["history"]) >= 2, sleep=0.001)
    played = [d for d in controller.engine_backend.difficulties_seen if d != "ANALYSIS"]
    assert played and played[-1] == "EXPERT"


def test_arm_manual_execution_requires_button(tmp_path):
    settings = AppSettings(arm_mode="simulated", arm_auto_execute=False)
    ctrl = MagnusController(settings, synthetic=True,
                            engine_factory=fake_engine_factory(),
                            voice_enabled=False, arm_step_delay_s=0.0)
    ctrl.start_without_thread()
    try:
        assert _wait_engine(ctrl)
        ctrl.command("start_game")
        assert _run(ctrl, 2000, until=lambda s: s["arm"]["pending"], sleep=0.001)
        snap = ctrl.snapshot()
        assert snap["sub"] == "robot_ready" and snap["board"]["planned"]["uci"]
        assert snap["arm"]["preview"]                    # secuencia visible antes
        # Sin pulsar el botón el brazo no se mueve aunque pasen frames.
        _run(ctrl, 60)
        assert ctrl.snapshot()["arm"]["last_outcome"] is None
        ctrl.command("arm_execute")
        assert _run(ctrl, 2000, until=lambda s: s["arm"]["last_outcome"] == "done", sleep=0.001)
        assert _run(ctrl, 2000, until=lambda s: len(s["history"]) >= 2, sleep=0.001)
    finally:
        ctrl.shutdown()


def test_commands_change_settings_and_modes(controller, tmp_path):
    assert _wait_engine(controller)
    controller.command("set_robot_side", {"side": "white"})
    controller.command("set_voice", {"muted": True, "announce_human_moves": True, "idle_prompt_s": 10})
    controller.command("set_arm", {"mode": "off", "auto_execute": False})
    controller.command("flip_view")
    controller.command("rotate_mapping")
    _run(controller, 3)
    s = controller.snapshot()["settings"]
    assert s["robot_side"] == "white" and s["voice_muted"] and s["announce_human_moves"]
    assert s["idle_prompt_s"] == 10 and s["arm_mode"] == "off" and s["flip_view"]
    assert s["board_turns"] == 1
    assert controller.snapshot()["arm"]["status"] == "off"
    assert controller.voice.is_muted
    # CyberPi sin tabla: error explicado, nunca una excepción.
    controller.command("set_arm", {"mode": "cyberpi", "positions_path": str(tmp_path / "no.json")})
    _run(controller, 3)
    arm = controller.snapshot()["arm"]
    assert arm["mode"] == "cyberpi" and arm["status"] == "error" and arm["positions"]["exists"] is False
    # Comando desconocido: rechazado sin tocar nada.
    assert controller.command("hack_the_planet")["ok"] is False


def test_stop_game_returns_to_setup(controller):
    assert _wait_engine(controller)
    controller.command("start_game")
    _run(controller, 5)
    assert controller.snapshot()["in_game"]
    controller.command("stop_game")
    _run(controller, 3)
    snap = controller.snapshot()
    assert not snap["in_game"] and snap["phase"] == "setup"


def test_camera_failure_keeps_controller_alive():
    class DeadCamera(CameraBackend):
        def open(self):
            raise CameraError("no hay cámara")

        def read(self):
            raise CameraError("no hay cámara")

        def close(self):
            pass

    ctrl = MagnusController(AppSettings(), camera=DeadCamera(), engine_enabled=False,
                            voice_enabled=False)
    ctrl.start_without_thread()
    try:
        _run(ctrl, 3)
        snap = ctrl.snapshot()
        assert snap["camera"]["ok"] is False and "cámara" in snap["camera"]["error"]
        assert snap["phase"] == "setup" and snap["engine"]["enabled"] is False
        ctrl.command("start_game")
        _run(ctrl, 2)
        assert not ctrl.snapshot()["in_game"]
        assert any(e["kind"] == "warn" for e in ctrl.snapshot()["events"])
    finally:
        ctrl.shutdown()


def test_threaded_start_and_shutdown(tmp_path):
    ctrl = MagnusController(AppSettings(arm_mode="off"), synthetic=True, engine_enabled=False,
                            voice_enabled=False)
    with ctrl:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and ctrl.seq < 5:
            time.sleep(0.02)
        assert ctrl.seq >= 5
        jpeg, seq = ctrl.wait_for_jpeg(0, timeout=2.0)
        assert jpeg is not None and seq > 0


def test_arm_home_command_references_the_arm(tmp_path):
    """El botón "Referenciar" llega al brazo y avisa por evento al terminar."""
    settings = AppSettings(arm_mode="simulated", arm_auto_execute=False)
    ctrl = MagnusController(settings, synthetic=True,
                            engine_factory=fake_engine_factory(),
                            voice_enabled=False, arm_step_delay_s=0.0)
    ctrl.start_without_thread()
    try:
        backend = ctrl.arm._node._backend
        ctrl.command("arm_home")
        assert _run(ctrl, 2000, until=lambda s: ("home",) in backend.commands,
                    sleep=0.001)
        texts = [e["text"] for e in ctrl.snapshot()["events"]]
        assert any("eferenciad" in t for t in texts)
    finally:
        ctrl.shutdown()


def test_arm_home_is_refused_while_the_arm_is_off(tmp_path):
    settings = AppSettings(arm_mode="off")
    ctrl = MagnusController(settings, synthetic=True,
                            engine_factory=fake_engine_factory(),
                            voice_enabled=False, arm_step_delay_s=0.0)
    ctrl.start_without_thread()
    try:
        ctrl.command("arm_home")
        _run(ctrl, 5)
        texts = [e["text"] for e in ctrl.snapshot()["events"]]
        assert any("apagado" in t for t in texts)
    finally:
        ctrl.shutdown()


def test_set_arm_persists_auto_home(tmp_path):
    path = tmp_path / "s.json"
    settings = AppSettings(arm_mode="simulated")
    ctrl = MagnusController(settings, synthetic=True, settings_path=str(path),
                            engine_factory=fake_engine_factory(),
                            voice_enabled=False, arm_step_delay_s=0.0)
    ctrl.start_without_thread()
    try:
        ctrl.command("set_arm", {"auto_home": False})
        _run(ctrl, 5)
        assert ctrl.settings.arm_auto_home is False
        assert ctrl.arm.auto_home is False
        assert json.loads(path.read_text(encoding="utf-8"))["arm_auto_home"] is False
    finally:
        ctrl.shutdown()
