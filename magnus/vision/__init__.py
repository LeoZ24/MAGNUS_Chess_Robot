"""Nodo de visión de MAGNUS: tablero físico -> FEN.

Componentes:

    piece_map       -> mapeo oficial ID ArUco -> pieza (0-15 blancas, 16-31 negras)
    aruco_detector  -> detección + enclavamiento, separada por roles
    calibration     -> calibración de cámara (opcional en v1)
    board_pose      -> homografía tablero(mm) <-> imagen(px) con las 4 esquinas
    fen_builder     -> placement -> texto FEN (puro, sin dependencias)
    game_state      -> GameTracker: deduce la jugada del humano por comparación
    vision_node     -> BoardVisionNode: el nodo principal
"""

from .aruco_detector import ArucoDetector, Detection, DetectionLatch, MarkerRole, classify_role
from .board_pose import BoardPose, BoardPoseError
from .calibration import CameraCalibration, calibrate_from_chessboard_images
from .fen_builder import build_fen, placement_to_fen_field
from .game_state import GameTracker, NoMatchingMoveError
from .piece_map import ARUCO_TO_PIECE, describe_id, piece_for_id
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
    "BoardPose",
    "BoardPoseError",
    "CameraCalibration",
    "calibrate_from_chessboard_images",
    "build_fen",
    "placement_to_fen_field",
    "GameTracker",
    "NoMatchingMoveError",
    "ARUCO_TO_PIECE",
    "describe_id",
    "piece_for_id",
    "BoardVisionNode",
    "CameraBackend",
    "FakeCameraBackend",
    "OpenCVCameraBackend",
    "VisionNodeError",
]
