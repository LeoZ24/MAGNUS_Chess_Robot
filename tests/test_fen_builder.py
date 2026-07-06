"""Tests del constructor de FEN (magnus/vision/fen_builder.py)."""

import chess
import pytest

from magnus.core.messages import STARTING_FEN
from magnus.vision.fen_builder import (
    InvalidPlacementError,
    build_fen,
    placement_to_fen_field,
)
from magnus.vision.game_state import board_placement


def test_starting_position():
    board = chess.Board()
    placement = board_placement(board)
    assert placement_to_fen_field(placement) == STARTING_FEN.split()[0]


def test_empty_board():
    assert placement_to_fen_field({}) == "8/8/8/8/8/8/8/8"


def test_single_piece():
    assert placement_to_fen_field({"e4": "P"}) == "8/8/8/8/4P3/8/8/8"


def test_build_full_fen_matches_starting():
    board = chess.Board()
    fen = build_fen(board_placement(board), turn="w", castling="KQkq")
    assert fen == STARTING_FEN


@pytest.mark.parametrize("n_moves", [1, 2, 5, 10])
def test_roundtrip_against_python_chess(n_moves):
    """Tras varias jugadas, el campo de placement debe coincidir con python-chess."""
    board = chess.Board()
    for _ in range(n_moves):
        move = next(iter(board.legal_moves))
        board.push(move)
    assert placement_to_fen_field(board_placement(board)) == board.fen().split()[0]


def test_invalid_square_rejected():
    with pytest.raises(InvalidPlacementError):
        placement_to_fen_field({"z9": "P"})


def test_invalid_piece_rejected():
    with pytest.raises(InvalidPlacementError):
        placement_to_fen_field({"e4": "X"})


def test_invalid_turn_rejected():
    with pytest.raises(InvalidPlacementError):
        build_fen({}, turn="x")
