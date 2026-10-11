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
    assert describe_step(ArmStep("move", "discard")) == "Mover a zona de descarte"
    assert describe_step(ArmStep("grip_on")) == "Recoger pieza"


def test_off_mode_previews_but_never_executes():
    sup = ArmSupervisor(mode="off")
    assert sup.status == "off" and not sup.is_ready
    preview = sup.preview(_resp())
    assert preview == ["Soltar pieza", "Mover a e2", "Recoger pieza",
                       "Mover a e4", "Soltar pieza", "Retirarse del tablero"]
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
    assert snap["last_uci"] == "e2e4" and snap["step_index"] == len(snap["steps"]) == 6
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
        def move_to(self, shoulder, elbow, tolerance=None):
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
    try:
        sup.execute(_resp())
        assert _wait(lambda: sup.snapshot()["last_outcome"] == "done")
        # Esperar a que el lector vea el estado final antes de detenerlo.
        assert _wait(lambda: 6 in seen)
        assert max(seen) == 6
    finally:
        stop.set()
        t.join()
        sup.shutdown()


# ---------------------------------------------------------------------- #
# Referenciado (home): los motores encoder no tienen cero absoluto, así que
# sin esto la tabla de posiciones apunta a un sitio distinto cada arranque.
# ---------------------------------------------------------------------- #

def test_cyberpi_homes_automatically_before_becoming_ready(tmp_path):
    path = tmp_path / "positions.json"
    _complete_table(path)
    fake = FakeArmBackend()
    sup = ArmSupervisor(mode="cyberpi", positions_path=str(path),
                        backend_factory=lambda port: fake)
    assert _wait(lambda: sup.is_ready)
    # El referenciado ocurre DESPUÉS de conectar y ANTES de cualquier jugada.
    assert fake.commands[:2] == [("connect",), ("home",)]
    sup.shutdown()


def test_auto_home_can_be_turned_off(tmp_path):
    path = tmp_path / "positions.json"
    _complete_table(path)
    fake = FakeArmBackend()
    sup = ArmSupervisor(mode="cyberpi", positions_path=str(path), auto_home=False,
                        backend_factory=lambda port: fake)
    assert _wait(lambda: sup.is_ready)
    assert ("home",) not in fake.commands
    assert sup.auto_home is False
    sup.shutdown()


def test_failed_homing_marks_error_and_never_becomes_ready(tmp_path):
    path = tmp_path / "positions.json"
    _complete_table(path)

    class NoStop(FakeArmBackend):
        def home(self):
            raise ArmBackendError("no encontre el tope del codo")

    sup = ArmSupervisor(mode="cyberpi", positions_path=str(path),
                        backend_factory=lambda port: NoStop())
    assert _wait(lambda: sup.status == "error")
    assert not sup.is_ready                  # jugar sin cero fiable: nunca
    assert "no encontre el tope del codo" in sup.snapshot()["error"]
    sup.shutdown()


def test_manual_home_reports_progress_and_returns_to_ready():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.0)
    assert sup.snapshot()["can_home"] is True
    done = []
    assert sup.home(on_done=lambda ok, err: done.append((ok, err)))
    assert _wait(lambda: done) and done == [(True, None)]
    assert _wait(lambda: sup.is_ready)
    sup.shutdown()


def test_manual_home_failure_marks_error():
    class NoStop(FakeArmBackend):
        def home(self):
            raise ArmBackendError("tope no encontrado")

    sup = ArmSupervisor(mode="simulated", step_delay_s=0.0)
    sup._node._backend = NoStop()            # el simulado no falla nunca solo
    sup._node._backend.connect()
    done = []
    assert sup.home(on_done=lambda ok, err: done.append(ok))
    assert _wait(lambda: done) and done == [False]
    assert _wait(lambda: sup.status == "error")
    sup.shutdown()


def test_cannot_home_while_off_or_busy():
    off = ArmSupervisor(mode="off")
    assert off.snapshot()["can_home"] is False
    assert off.home() is False
    off.shutdown()

    sup = ArmSupervisor(mode="simulated", step_delay_s=0.05)
    assert sup.execute(_resp())
    assert _wait(lambda: sup.is_busy)
    assert sup.home() is False               # nunca a mitad de una jugada
    sup.shutdown()


def test_configure_can_change_auto_home_without_changing_mode():
    sup = ArmSupervisor(mode="simulated", step_delay_s=0.0, auto_home=True)
    sup.configure(auto_home=False)
    assert sup.auto_home is False
    assert sup.snapshot()["auto_home"] is False
    sup.shutdown()


# --------------------------------------------------------------------------- #
# La pantalla sigue al brazo pieza a pieza (sin esperar a la cámara)
# --------------------------------------------------------------------------- #

from magnus.app.arm_bridge import placement_during, release_index  # noqa: E402
from magnus.arm.arm_node import ArmNode  # noqa: E402
from magnus.arm.positions_table import make_fake_table  # noqa: E402
from magnus.vision.game_state import board_placement  # noqa: E402


def _plan(resp):
    return ArmNode(backend=FakeArmBackend(), table=make_fake_table()).plan(resp)


def _after(fen, uci):
    board = chess.Board(fen) if fen else chess.Board()
    board.push(chess.Move.from_uci(uci))
    return board_placement(board)


def _frames(fen, resp):
    """Placement que se ve tras cada paso del plan (de 0 a todos)."""
    start = board_placement(chess.Board(fen) if fen else chess.Board())
    plan = _plan(resp)
    return [placement_during(start, plan, done, resp) for done in range(len(plan) + 1)]


def test_simple_move_appears_when_the_piece_is_released():
    resp = _resp("e2e4")
    plan = _plan(resp)
    frames = _frames(None, resp)
    released = release_index(plan)
    assert plan[released].action == "grip_off"
    start = board_placement(chess.Board())
    # Levantada, la pieza sigue en su casilla; al soltarla ya está en la nueva.
    assert all(frame == start for frame in frames[:released + 1])
    assert frames[released + 1] == _after(None, "e2e4")
    assert frames[-1] == _after(None, "e2e4")      # la retirada no cambia nada


def test_capture_first_clears_the_captured_piece():
    fen = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    resp = _resp("e4d5", fen)
    frames = _frames(fen, resp)
    seen = [f for i, f in enumerate(frames) if i == 0 or f != frames[i - 1]]
    assert len(seen) == 3                    # inicio -> sin peón negro -> jugada
    assert "d5" not in seen[1] and seen[1]["e4"] == "P"
    assert seen[2] == _after(fen, "e4d5")


def test_en_passant_removes_the_pawn_beside_the_destination():
    fen = "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"
    resp = _resp("e5f6", fen)
    resp.captured_square = "f5"              # al paso: no es to_square
    assert _frames(fen, resp)[-1] == _after(fen, "e5f6")


def test_castling_moves_king_then_rook():
    fen = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    resp = _resp("e1g1", fen)
    resp.is_castling, resp.rook_from, resp.rook_to = True, "h1", "f1"   # como el engine
    frames = _frames(fen, resp)
    seen = [f for i, f in enumerate(frames) if i == 0 or f != frames[i - 1]]
    assert len(seen) == 3
    assert seen[1]["g1"] == "K" and seen[1]["h1"] == "R"
    assert seen[2] == _after(fen, "e1g1")


def test_promotion_brings_the_new_piece_from_the_exchange_zone():
    fen = "8/4P3/8/8/8/8/k7/4K3 w - - 0 1"
    resp = _resp("e7e8q", fen)
    assert _frames(fen, resp)[-1] == _after(fen, "e7e8q")
    black = "4k3/8/8/8/8/8/4p3/K7 b - - 0 1"
    resp = _resp("e2e1n", black)
    assert _frames(black, resp)[-1] == _after(black, "e2e1n")
