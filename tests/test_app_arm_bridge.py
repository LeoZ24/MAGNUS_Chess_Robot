"""Tests del supervisor del brazo (modos apagado / simulado / CyberPi falso)."""

import json
import threading
import time

import chess

from magnus.app.arm_bridge import ArmSupervisor, describe_step
from magnus.app.session import move_to_response
from magnus.arm.arm_node import ArmStep
from magnus.arm.backend import ArmBackendError, FakeArmBackend
from magnus.arm.positions_table import REQUIRED_KEYS


def _resp(uci="e2e4", fen=None):
    board = chess.Board(fen) if fen else chess.Board()
    move = chess.Move.from_uci(uci)
    after = board.copy()
    after.push(move)
    resp = move_to_response(board, move, board.san(move), after)
    if resp.is_capture:
        resp.captured_square = resp.to_square
    return resp


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _complete_table(path):
    entry = {"approach": {"shoulder": 10.0, "elbow": 20.0},
             "engage": {"shoulder": 11.0, "elbow": 21.0}}
    path.write_text(json.dumps({k: entry for k in REQUIRED_KEYS}), encoding="utf-8")


def test_describe_step_uses_zone_labels():
    assert describe_step(ArmStep("approach", "discard")) == "Aproximar a zona de descarte"
    assert describe_step(ArmStep("grip_on")) == "Activar garra"


def test_off_mode_previews_but_never_executes():
    sup = ArmSupervisor(mode="off")
    assert sup.status == "off" and not sup.is_ready
    preview = sup.preview(_resp())
    assert preview[0] == "Aproximar a e2" and len(preview) == 8
    assert sup.execute(_resp()) is False
    sup.shutdown()


def test_simulated_mode_executes_with_progress_and_callback():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.0)
    assert sup.is_ready
    done = []
    assert sup.execute(_resp(), on_done=lambda ok, err: done.append((ok, err)))
    assert _wait(lambda: done)
    assert done == [(True, None)]
    snap = sup.snapshot()
    assert snap["status"] == "ready" and snap["last_outcome"] == "done"
    assert snap["last_uci"] == "e2e4" and snap["step_index"] == len(snap["steps"]) == 8
    sup.shutdown()


def test_simulated_execution_can_be_stopped():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.2)
    done = []
    assert sup.execute(_resp(), on_done=lambda ok, err: done.append((ok, err)))
    assert _wait(lambda: sup.is_busy)
    sup.stop()
    assert _wait(lambda: done)
    assert done[0][0] is False and "detenida" in done[0][1]
    assert sup.snapshot()["last_outcome"] == "stopped"
    assert sup.is_ready                        # simulado: vuelve a estar listo
    sup.shutdown()


def test_busy_supervisor_rejects_second_execution():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.2)
    assert sup.execute(_resp())
    assert _wait(lambda: sup.is_busy)
    assert sup.execute(_resp()) is False
    sup.stop()
    sup.shutdown()


def test_cyberpi_without_table_is_an_error(tmp_path):
    sup = ArmSupervisor(mode="cyberpi", positions_path=str(tmp_path / "positions.json"))
    snap = sup.snapshot()
    assert snap["status"] == "error" and "calibrar" in snap["error"]
    assert snap["positions"]["complete"] is False
    sup.shutdown()


def test_cyberpi_with_complete_table_uses_backend_factory(tmp_path):
    path = tmp_path / "positions.json"
    _complete_table(path)
    fake = FakeArmBackend()
    sup = ArmSupervisor(mode="cyberpi", positions_path=str(path), port=6000,
                        backend_factory=lambda port: fake)
    assert _wait(lambda: sup.is_ready)
    assert fake.connected
    done = []
    assert sup.execute(_resp(), on_done=lambda ok, err: done.append(ok))
    assert _wait(lambda: done) and done == [True]
    # El backend recibió posiciones REALES de la tabla (no las falsas 9999.x).
    moves = [cmd for cmd in fake.commands if cmd[0] == "move_to"]
    assert moves and all(cmd[1] in (10.0, 11.0) for cmd in moves)
    sup.shutdown()
    assert not fake.connected


def test_cyberpi_backend_failure_marks_error(tmp_path):
    path = tmp_path / "positions.json"
    _complete_table(path)

    class Exploding(FakeArmBackend):
        def move_to(self, shoulder, elbow):
            raise ArmBackendError("motor atascado")

    sup = ArmSupervisor(mode="cyberpi", positions_path=str(path),
                        backend_factory=lambda port: Exploding())
    assert _wait(lambda: sup.is_ready)
    done = []
    assert sup.execute(_resp(), on_done=lambda ok, err: done.append((ok, err)))
    assert _wait(lambda: done)
    assert done[0][0] is False and "atascado" in done[0][1]
    assert sup.status == "error"               # el brazo real exige reconfigurar
    sup.shutdown()


def test_reconfigure_switches_mode_cleanly():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.0)
    sup.configure(mode="off")
    assert sup.status == "off" and sup.mode == "off"
    sup.configure(mode="simulated")
    assert sup.is_ready
    sup.shutdown()
    assert sup.status == "off"


def test_snapshot_is_thread_safe_under_execution():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.02)
    stop = threading.Event()
    seen = []

    def reader():
        while not stop.is_set():
            seen.append(sup.snapshot()["step_index"])

    t = threading.Thread(target=reader)
    t.start()
    sup.execute(_resp())
    _wait(lambda: sup.snapshot()["last_outcome"] == "done")
    stop.set()
    t.join()
    assert max(seen) == 8
    sup.shutdown()
