"""Tests de la homografía tablero <-> imagen (magnus/vision/board_pose.py).

Se usa una homografía sintética de identidad (los centros de los marcadores de
esquina en px coinciden con sus coordenadas mm), de modo que las conversiones
son directamente verificables.
"""

import pytest

from magnus import config
from magnus.vision.board_pose import BoardPose, BoardPoseError

# Esquinas "perfectas": px == mm (homografía identidad).
IDENTITY_CORNERS = {
    config.ARUCO_IDS_BOARD_CORNERS[0]: (0.0, 0.0),                                    # a8
    config.ARUCO_IDS_BOARD_CORNERS[1]: (config.BOARD_SIZE_MM, 0.0),                   # h8
    config.ARUCO_IDS_BOARD_CORNERS[2]: (config.BOARD_SIZE_MM, config.BOARD_SIZE_MM),  # h1
    config.ARUCO_IDS_BOARD_CORNERS[3]: (0.0, config.BOARD_SIZE_MM),                   # a1
}


@pytest.fixture
def pose() -> BoardPose:
    return BoardPose.from_corner_centers(IDENTITY_CORNERS)


def test_missing_corner_raises():
    incomplete = dict(IDENTITY_CORNERS)
    incomplete.pop(config.ARUCO_IDS_BOARD_CORNERS[0])
    with pytest.raises(BoardPoseError):
        BoardPose.from_corner_centers(incomplete)


def test_square_centers_mm():
    # a8 es la primera casilla del sistema de coordenadas (esquina en 0,0).
    assert BoardPose.square_center_mm("a8") == (16.0, 16.0)
    assert BoardPose.square_center_mm("h1") == (240.0, 240.0)
    assert BoardPose.square_center_mm("e4") == (144.0, 144.0)


@pytest.mark.parametrize("square", ["a1", "a8", "h1", "h8", "e4", "d5", "c3"])
def test_pixel_to_square_roundtrip(pose, square):
    """El centro de cada casilla debe mapearse de vuelta a esa casilla."""
    x_px, y_px = pose.square_center_px(square)
    assert pose.pixel_to_square(x_px, y_px) == square


def test_pixel_outside_board_returns_none(pose):
    assert pose.pixel_to_square(-10.0, 100.0) is None
    assert pose.pixel_to_square(100.0, -10.0) is None
    assert pose.pixel_to_square(config.BOARD_SIZE_MM + 5, 100.0) is None
    assert pose.pixel_to_square(100.0, config.BOARD_SIZE_MM + 5) is None


def test_mm_pixel_roundtrip(pose):
    x_px, y_px = pose.mm_to_pixel(100.0, 200.0)
    x_mm, y_mm = pose.pixel_to_mm(x_px, y_px)
    assert abs(x_mm - 100.0) < 1e-6
    assert abs(y_mm - 200.0) < 1e-6


def test_scaled_homography():
    """Con la imagen escalada ×2 y desplazada, el mapeo sigue siendo correcto."""
    scaled = {
        i: (x * 2 + 50, y * 2 + 30) for i, (x, y) in IDENTITY_CORNERS.items()
    }
    pose = BoardPose.from_corner_centers(scaled)
    # El centro de e4 (144,144 mm) debe caer en (144*2+50, 144*2+30) px.
    x_px, y_px = pose.square_center_px("e4")
    assert abs(x_px - 338.0) < 1e-6
    assert abs(y_px - 318.0) < 1e-6
    assert pose.pixel_to_square(x_px, y_px) == "e4"
