"""Tests de la homografía tablero <-> imagen (magnus/vision/board_pose.py).

Se usa una homografía sintética de identidad (los centros de los marcadores de
esquina en px coinciden con sus coordenadas mm), de modo que las conversiones
son directamente verificables.
"""

import itertools
import math

import pytest

from magnus import config
from magnus.vision.board_pose import (
    CORNER_MM_BY_NAME,
    CORNER_NAMES,
    EXPECTED_LAYOUT,
    BoardPose,
    BoardPoseError,
    deduce_corner_layout,
)

# Esquinas "perfectas": px == mm (homografía identidad).
IDENTITY_CORNERS = {
    config.ARUCO_IDS_BOARD_CORNERS[0]: (0.0, 0.0),                                    # a8
    config.ARUCO_IDS_BOARD_CORNERS[1]: (config.BOARD_SIZE_MM, 0.0),                   # h8
    config.ARUCO_IDS_BOARD_CORNERS[2]: (config.BOARD_SIZE_MM, config.BOARD_SIZE_MM),  # h1
    config.ARUCO_IDS_BOARD_CORNERS[3]: (0.0, config.BOARD_SIZE_MM),                   # a1
}

ALL_SQUARES = [f"{f}{r}" for f in "abcdefgh" for r in range(1, 9)]


def corners_for_layout(layout: dict[int, str]) -> dict[int, tuple[float, float]]:
    """Centros px de los marcadores si cada ID se pega en la esquina indicada."""
    return {i: CORNER_MM_BY_NAME[name] for i, name in layout.items()}


def rotate_view(
    corners: dict[int, tuple[float, float]], degrees: float
) -> dict[int, tuple[float, float]]:
    """Gira los centros alrededor del origen: simula la cámara en otro ángulo."""
    rad = math.radians(degrees)
    cos, sin = math.cos(rad), math.sin(rad)
    return {i: (x * cos - y * sin, x * sin + y * cos) for i, (x, y) in corners.items()}


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


# --------------------------------------------------------------------------- #
# Colocación de los marcadores de esquina
# --------------------------------------------------------------------------- #
def test_layout_of_well_placed_corners_matches_labels():
    pose = BoardPose.from_corner_centers(IDENTITY_CORNERS)
    assert pose.layout.corner_by_id == EXPECTED_LAYOUT
    assert pose.layout.is_expected
    assert pose.layout_warning is None


@pytest.mark.parametrize("degrees", [0, 37, 90, 180, 270])
def test_camera_angle_does_not_matter(degrees):
    """Cada esquina se identifica por su ID: da igual cómo esté girada la cámara."""
    pose = BoardPose.from_corner_centers(rotate_view(IDENTITY_CORNERS, degrees))
    assert pose.layout.corner_by_id == EXPECTED_LAYOUT
    for square in ALL_SQUARES:
        assert pose.pixel_to_square(*pose.square_center_px(square)) == square


def test_corners_in_reading_order_are_repaired():
    """El error típico: 40 y 41 arriba, 42 abajo-izq y 43 abajo-der (zig-zag).

    Antes cruzaba dos esquinas y la homografía salía degenerada: TODAS las
    piezas caían "fuera del tablero" y no se generaba ninguna FEN.  Ahora la
    mayoría (40 y 41, bien colocados) fija la orientación y el par cruzado se
    corrige solo.
    """
    reading_order = {40: "a8", 41: "h8", 42: "a1", 43: "h1"}
    pose = BoardPose.from_corner_centers(corners_for_layout(reading_order))
    assert pose.layout.corner_by_id == reading_order      # colocación real deducida
    assert not pose.layout.is_expected
    assert "42" in pose.layout_warning and "43" in pose.layout_warning
    for square in ALL_SQUARES:
        assert pose.pixel_to_square(*pose.square_center_px(square)) == square


def swapped_layout(a: int, b: int) -> dict[int, str]:
    """Colocación con los marcadores de las posiciones ``a`` y ``b`` cambiados."""
    names = list(CORNER_NAMES)
    names[a], names[b] = names[b], names[a]
    return dict(zip(config.ARUCO_IDS_BOARD_CORNERS, names))


@pytest.mark.parametrize("swap", [(0, 1), (1, 2), (2, 3), (0, 3)])
def test_swap_of_two_adjacent_markers_is_repaired(swap):
    """Con dos marcadores contiguos cambiados, los otros dos fijan la orientación."""
    layout = swapped_layout(*swap)
    pose = BoardPose.from_corner_centers(corners_for_layout(layout))
    assert pose.layout.corner_by_id == layout             # se deduce la real
    assert pose.layout_warning is not None
    for square in ALL_SQUARES:
        assert pose.pixel_to_square(*pose.square_center_px(square)) == square


@pytest.mark.parametrize("swap", [(0, 2), (1, 3)])
def test_swap_of_two_opposite_markers_is_ambiguous_but_usable(swap):
    """Cambiar dos esquinas opuestas es ambiguo: no hay pista para desempatar.

    Las dos hipótesis (girada 180° una de otra) explican igual de bien las
    etiquetas, así que se elige la que respeta el marcador 40 y se avisa.  El
    tablero resultante es válido — la orientación se termina de fijar al
    empezar la partida (tecla G del demo) o girando el mapeo (tecla T).
    """
    layout = swapped_layout(*swap)
    pose = BoardPose.from_corner_centers(corners_for_layout(layout))
    assert pose.layout_warning is not None
    squares = {pose.pixel_to_square(*pose.square_center_px(s)) for s in ALL_SQUARES}
    assert len(squares) == 64 and None not in squares


@pytest.mark.parametrize("perm", list(itertools.permutations(CORNER_NAMES)))
def test_no_layout_produces_a_degenerate_homography(perm):
    """Ninguna de las 24 colocaciones posibles puede cruzar el cuadrilátero."""
    layout = dict(zip(config.ARUCO_IDS_BOARD_CORNERS, perm))
    pose = BoardPose.from_corner_centers(corners_for_layout(layout))
    # Todas las casillas del tablero caen dentro del tablero (aunque la
    # orientación pueda estar girada o espejada si hay 3+ marcadores mal).
    squares = {pose.pixel_to_square(*pose.square_center_px(s)) for s in ALL_SQUARES}
    assert len(squares) == 64 and None not in squares


def test_non_convex_corners_are_rejected():
    """Un "marcador de esquina" que no está en una esquina no es utilizable."""
    bad = dict(IDENTITY_CORNERS)
    bad[config.ARUCO_IDS_BOARD_CORNERS[2]] = (100.0, 100.0)   # dentro del tablero
    with pytest.raises(BoardPoseError):
        BoardPose.from_corner_centers(bad)


def test_deduce_layout_rejects_collinear_corners():
    flat = {i: (float(k * 50), 0.0)
            for k, i in enumerate(config.ARUCO_IDS_BOARD_CORNERS)}
    with pytest.raises(BoardPoseError):
        deduce_corner_layout(flat)


def test_auto_layout_can_be_disabled():
    reading_order = {40: "a8", 41: "h8", 42: "a1", 43: "h1"}
    pose = BoardPose.from_corner_centers(
        corners_for_layout(reading_order), auto_layout=False
    )
    assert pose.layout.corner_by_id == EXPECTED_LAYOUT     # se confía en la etiqueta


# --------------------------------------------------------------------------- #
# Giro del mapeo de casillas
# --------------------------------------------------------------------------- #
def test_quarter_turns_rotate_the_square_mapping():
    """Girar 90° el mapeo: el punto que era a8 pasa a leerse como la esquina siguiente."""
    pose = BoardPose.from_corner_centers(IDENTITY_CORNERS)
    turned = pose.rotated(1)
    corner_px = pose.square_center_px("a8")
    assert turned.pixel_to_square(*corner_px) == "h8"
    assert turned.rotated(3).pixel_to_square(*corner_px) == "a8"   # vuelta completa


def test_quarter_turns_argument_matches_rotated():
    pose = BoardPose.from_corner_centers(IDENTITY_CORNERS, quarter_turns=2)
    same = BoardPose.from_corner_centers(IDENTITY_CORNERS).rotated(2)
    assert pose.layout.corner_by_id == same.layout.corner_by_id
    for square in ALL_SQUARES:
        assert (pose.pixel_to_square(*pose.square_center_px(square))
                == same.pixel_to_square(*same.square_center_px(square)))


def test_rotating_a_pose_without_corner_centers_raises():
    import numpy as np

    with pytest.raises(BoardPoseError):
        BoardPose(np.eye(3)).rotated(1)


# --------------------------------------------------------------------------- #
# Tolerancia al borde
# --------------------------------------------------------------------------- #
def test_tolerance_accepts_marker_just_outside_the_edge(pose):
    """Un marcador 3 mm fuera del borde es una pieza del borde mal centrada."""
    assert pose.pixel_to_square(-3.0, 100.0, tolerance_mm=6.0) == "a5"
    assert pose.pixel_to_square(100.0, -3.0, tolerance_mm=6.0) == "d8"


def test_tolerance_still_rejects_markers_off_the_board(pose):
    """Más allá de la tolerancia (pieza capturada en la mesa) se descarta."""
    assert pose.pixel_to_square(-40.0, 100.0, tolerance_mm=6.0) is None
    assert pose.pixel_to_square(config.BOARD_SIZE_MM + 40.0, 100.0,
                                tolerance_mm=6.0) is None


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
