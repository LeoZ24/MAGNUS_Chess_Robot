"""Tests del pipeline de visión completo, con imágenes sintéticas.

Se renderiza una vista cenital del tablero con marcadores ArUco reales
(generados con cv2.aruco) y se inyecta en el nodo mediante
``FakeCameraBackend`` — sin cámara ni tablero físico.
"""

import chess
import pytest

from magnus.core.messages import STARTING_FEN
from magnus.vision.game_state import GameTracker, board_placement
from magnus.vision.synthetic import SyntheticBoardError, render_board_image
from magnus.vision.vision_node import (
    BoardNotFoundError,
    BoardVisionNode,
    FakeCameraBackend,
)


def _node_for(placement: dict[str, str], **node_kwargs) -> BoardVisionNode:
    frame = render_board_image(placement)
    return BoardVisionNode(camera=FakeCameraBackend([frame]), **node_kwargs)


def test_detects_sparse_position():
    placement = {"e1": "K", "e8": "k", "d4": "Q", "a7": "p"}
    with _node_for(placement) as node:
        assert node.get_board_placement() == placement


def test_detects_full_starting_position():
    """Las 32 piezas de la posición inicial deben detectarse en su casilla."""
    placement = board_placement(chess.Board())
    with _node_for(placement) as node:
        assert node.get_board_placement() == placement


def test_get_board_fen_starting_position():
    placement = board_placement(chess.Board())
    with _node_for(placement) as node:
        assert node.get_board_fen() == STARTING_FEN


def test_full_move_cycle_with_tracker():
    """Humano juega e2e4 (visto por cámara) y el robot responde e7e5 (via engine)."""
    board = chess.Board()
    board.push_uci("e2e4")
    tracker = GameTracker()

    with _node_for(board_placement(board), tracker=tracker) as node:
        fen = node.get_board_fen()
        assert fen == board.fen()          # turno y al paso exactos
        node.notify_robot_move("e7e5")     # jugada del robot (del engine)
        board.push_uci("e7e5")
        assert tracker.fen() == board.fen()


def test_missing_corners_raises():
    # Imagen totalmente blanca: sin esquinas ni piezas.
    import numpy as np

    blank = np.full((400, 400, 3), 255, dtype=np.uint8)
    with BoardVisionNode(camera=FakeCameraBackend([blank])) as node:
        with pytest.raises(BoardNotFoundError):
            node.get_board_placement()


def test_too_many_pieces_of_a_kind_rejected():
    """No se puede renderizar un placement con 2 reyes blancos (solo hay 1 ID)."""
    with pytest.raises(SyntheticBoardError):
        render_board_image({"e1": "K", "d1": "K"})
