"""Nodo de visión de MAGNUS: tablero físico -> FEN.

Componentes:

    piece_map       -> mapeo oficial ID ArUco -> pieza, POR TIPO (todos los
                       peones blancos comparten ID; ver piece_map.py)
    aruco_detector  -> detección + enclavamiento multi-instancia, por roles
    calibration     -> calibración de cámara (opcional en v1)
    board_pose      -> homografía tablero(mm) <-> imagen(px) con las 4 esquinas
    fen_builder     -> placement -> texto FEN (puro, sin dependencias)
    game_state      -> GameTracker: deduce la jugada del humano por comparación
    board_render    -> tablero 2D con iconos + dashboard (visualización pura)
    vision_node     -> BoardVisionNode: el nodo principal
"""

from .aruco_detector import (
    ArucoDetector,
    Detection,
    DetectionLatch,
    MarkerRole,
    classify_role,
    corners_by_id,
    split_by_role,
)
from .board_pose import BoardPose, BoardPoseError
from .board_render import (
    BoardRenderer,
    BoardTheme,
    Dashboard,
    DashboardState,
    render_board,
)
from .calibration import CameraCalibration, calibrate_from_chessboard_images
from .fen_builder import build_fen, placement_to_fen_field
from .game_state import GameTracker, NoMatchingMoveError
from .piece_map import (
    ARUCO_TO_PIECE,
    PIECE_COUNTS,
    PIECE_TO_ARUCO,
    describe_id,
    id_for_piece,
    piece_for_id,
)
from .vision_node import (
    BoardVisionNode,
    CameraBackend,
    FakeCameraBackend,
    OpenCVCameraBackend,
    VisionNodeError,
)

__all__ = [
    "ArucoDetector",
    "Detection",
    "DetectionLatch",
    "MarkerRole",
    "classify_role",
    "corners_by_id",
    "split_by_role",
    "BoardPose",
    "BoardPoseError",
    "BoardRenderer",
    "BoardTheme",
    "Dashboard",
    "DashboardState",
    "render_board",
    "CameraCalibration",
    "calibrate_from_chessboard_images",
    "build_fen",
    "placement_to_fen_field",
    "GameTracker",
    "NoMatchingMoveError",
    "ARUCO_TO_PIECE",
    "PIECE_COUNTS",
    "PIECE_TO_ARUCO",
    "describe_id",
    "id_for_piece",
    "piece_for_id",
    "BoardVisionNode",
    "CameraBackend",
    "FakeCameraBackend",
    "OpenCVCameraBackend",
    "VisionNodeError",
]
