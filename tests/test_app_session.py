"""Tests de magnus.app.session: sesión de partida, hilo del engine y utilidades."""

import time

import chess

from magnus.app.session import (
    EngineWorker,
    GameSession,
    SyntheticCamera,
    difficulty_catalog,
    move_to_response,
)
from magnus.engine.difficulty import DifficultyLevel

from app_fakes import RandomEngineBackend, fake_engine_factory


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------- #
# EngineWorker
# --------------------------------------------------------------------------- #
def test_engine_worker_moves_and_changes_difficulty():
    backend = RandomEngineBackend()
    worker = EngineWorker("EASY", node_factory=fake_engine_factory(backend)).start()
    try:
        assert _wait(lambda: worker.status == "listo")
        assert worker.label == "STOCKFISH · EASY"

        fen = chess.Board().fen()
        worker.request(fen)
        assert _wait(lambda: not worker._responses.empty())
        kind, resp, source = worker.poll()
        assert kind == "move" and source == fen and resp.uci
        assert backend.difficulties_seen == ["EASY"]

        assert worker.set_difficulty("hard") == "HARD"
        worker.request(fen)
        assert _wait(lambda: not worker._responses.empty())
        worker.poll()
        assert backend.difficulties_seen[-1] == "HARD"
        assert worker.difficulty == "HARD"
    finally:
        worker.stop()


def test_engine_worker_analysis_and_unavailable_engine():
    worker = EngineWorker("MEDIUM", node_factory=fake_engine_factory()).start()
    try:
        assert _wait(lambda: worker.status == "listo")
        worker.request_analysis(chess.Board().fen())
        assert _wait(lambda: not worker._responses.empty())
        kind, analysis, _ = worker.poll()
        assert kind == "eval" and analysis.evaluation_cp is not None
    finally:
        worker.stop()

    def broken_factory(_):
        raise RuntimeError("sin stockfish")

    worker = EngineWorker("MEDIUM", node_factory=broken_factory).start()
    assert _wait(lambda: worker.status == "no disponible")
    assert "stockfish" in (worker.error or "")
    worker.request(chess.Board().fen())          # no explota, se ignora
    assert worker.poll() is None


def test_difficulty_catalog_matches_levels():
    catalog = difficulty_catalog()
    assert [c["name"] for c in catalog] == [lvl.name for lvl in DifficultyLevel]
    assert all(c["description"] for c in catalog)
    assert catalog[-1]["elo"] is None            # MAXIMUM sin límite


# --------------------------------------------------------------------------- #
# GameSession
# --------------------------------------------------------------------------- #
def test_session_applies_human_move_and_wants_engine_move():
    session = GameSession(chess.BLACK)
    board = chess.Board()
    board.push_uci("e2e4")
    from magnus.vision.game_state import board_placement

    assert session.want_engine_move() is None      # blancas mueven, no el robot
    assert session.try_apply_placement(board_placement(board)) == "e4"
    assert session.last_mover == "human"
    assert session.history_san == ["e4"]
    fen = session.want_engine_move()
    assert fen == board.fen()
    session.requested_fen = fen
    assert session.want_engine_move() is None      # ya pedida


def test_session_eval_is_from_white_perspective():
    session = GameSession(chess.BLACK)
    session.note_eval(50, None, side_to_move="black")
    assert session.eval_cp_white == -50
    session.note_eval(None, 3, side_to_move="white")
    assert session.mate_in_white == 3 and session.eval_cp_white is None
    session.note_eval(None, 0, side_to_move="white")   # blancas ya recibieron mate
    assert session.mate_in_white == -1


def test_session_result_for_checkmate():
    session = GameSession(chess.WHITE)
    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):    # mate del loco: pierden blancas
        session.tracker.board.push_uci(uci)
    result = session.result()
    assert result["kind"] == "checkmate" and result["winner"] == "black"
    assert result["robot_won"] is False
    assert "ganas tú" in result["text"]


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def test_move_to_response_en_passant_and_castling():
    board = chess.Board("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3")
    move = chess.Move.from_uci("e5f6")
    after = board.copy()
    after.push(move)
    resp = move_to_response(board, move, board.san(move), after)
    assert resp.is_en_passant and resp.is_capture and resp.captured_piece == "p"
    assert resp.fen == board.fen() and resp.resulting_fen == after.fen()

    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    move = chess.Move.from_uci("e1g1")
    after = board.copy()
    after.push(move)
    resp = move_to_response(board, move, board.san(move), after)
    assert resp.is_castling and resp.is_kingside_castle and resp.san == "O-O"


def test_synthetic_camera_push_and_reset():
    cam = SyntheticCamera()
    cam.open()
    first = cam.read()
    assert cam.push("e2e4") and not cam.push("e2e4")   # la segunda ya no es legal
    assert cam.read().shape == first.shape
    cam.reset()
    assert cam.board.fullmove_number == 1
    cam.close()
