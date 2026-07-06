"""Tests del GameTracker: inferencia de jugadas a partir del placement."""

import chess
import pytest

from magnus.vision.game_state import (
    GameTracker,
    IllegalPositionError,
    NoMatchingMoveError,
    board_placement,
)


def _placement_after(*ucis: str) -> dict[str, str]:
    """Placement resultante de aplicar jugadas UCI desde la posición inicial."""
    board = chess.Board()
    for uci in ucis:
        board.push_uci(uci)
    return board_placement(board)


def test_no_change_returns_none():
    tracker = GameTracker()
    assert tracker.update_from_placement(_placement_after()) is None


def test_detects_simple_pawn_move():
    tracker = GameTracker()
    move = tracker.update_from_placement(_placement_after("e2e4"))
    assert move is not None and move.uci() == "e2e4"
    # La FEN resultante debe ser exacta (incluida la casilla al paso).
    board = chess.Board()
    board.push_uci("e2e4")
    assert tracker.fen() == board.fen()


def test_detects_capture():
    setup = ("e2e4", "d7d5")
    tracker = GameTracker()
    for i in range(1, len(setup) + 1):
        tracker.update_from_placement(_placement_after(*setup[:i]))
    move = tracker.update_from_placement(_placement_after(*setup, "e4d5"))
    assert move.uci() == "e4d5"


def test_detects_castling():
    """El enroque cambia dos piezas de sitio; debe detectarse como una sola jugada."""
    setup = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6")
    tracker = GameTracker()
    for i in range(1, len(setup) + 1):
        tracker.update_from_placement(_placement_after(*setup[:i]))
    move = tracker.update_from_placement(_placement_after(*setup, "e1g1"))
    assert move.uci() == "e1g1"
    # Los derechos de enroque de las blancas deben haber desaparecido.
    assert "K" not in tracker.fen().split()[2]


def test_detects_en_passant():
    setup = ("e2e4", "a7a6", "e4e5", "d7d5")
    tracker = GameTracker()
    for i in range(1, len(setup) + 1):
        tracker.update_from_placement(_placement_after(*setup[:i]))
    move = tracker.update_from_placement(_placement_after(*setup, "e5d6"))
    assert move.uci() == "e5d6"


def test_impossible_placement_raises():
    tracker = GameTracker()
    bad = _placement_after()
    bad["e5"] = "Q"  # aparece una dama de la nada
    with pytest.raises(NoMatchingMoveError):
        tracker.update_from_placement(bad)


def test_apply_robot_move():
    tracker = GameTracker()
    tracker.apply_move("e2e4")
    board = chess.Board()
    board.push_uci("e2e4")
    assert tracker.fen() == board.fen()


def test_apply_illegal_move_raises():
    tracker = GameTracker()
    with pytest.raises(IllegalPositionError):
        tracker.apply_move("e2e5")
