"""Generación de imágenes sintéticas del tablero para tests y demos.

Permite probar TODO el pipeline de visión sin cámara ni tablero físico: se
"renderiza" una vista cenital del tablero con los marcadores ArUco de las 4
esquinas y de las piezas en las casillas indicadas, y esa imagen se inyecta en
el nodo de visión mediante ``FakeCameraBackend``.
"""

from __future__ import annotations

import logging
from typing import Iterable, Mapping, Optional

import cv2
import cv2.aruco as aruco
import numpy as np

from .. import config
from .board_pose import CORNER_MM_BY_NAME, EXPECTED_LAYOUT, BoardPose
from .piece_map import PIECE_TO_ARUCO

logger = logging.getLogger("magnus.vision.synthetic")


class SyntheticBoardError(Exception):
    """No se pudo generar la imagen sintética."""


def _ids_for_placement(placement: dict[str, str]) -> dict[str, int]:
    """Asigna a cada casilla el ID ArUco de su TIPO de pieza.

    El marcador identifica el tipo (todos los peones blancos son el ID 0), así
    que varias casillas pueden compartir ID — exactamente como en el tablero
    físico.  Falla solo si el símbolo no es una pieza FEN válida.
    """
    assignment: dict[str, int] = {}
    for square, sym in sorted(placement.items()):
        if sym not in PIECE_TO_ARUCO:
            raise SyntheticBoardError(
                f"Símbolo de pieza inválido en {square}: {sym!r}."
            )
        assignment[square] = PIECE_TO_ARUCO[sym]
    return assignment


def _paste_marker(
    canvas: np.ndarray,
    dictionary,
    aruco_id: int,
    center_px: tuple[int, int],
    side_px: int,
) -> None:
    marker = aruco.generateImageMarker(dictionary, aruco_id, side_px)
    marker_bgr = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    x, y = center_px
    half = side_px // 2
    canvas[y - half : y - half + side_px, x - half : x - half + side_px] = marker_bgr


def render_board_image(
    placement: dict[str, str],
    px_per_mm: float = 3.0,
    margin_mm: float = 30.0,
    piece_marker_mm: float = 14.0,
    corner_marker_mm: float = 16.0,
    corner_layout: Optional[Mapping[int, str]] = None,
    hidden_corners: Iterable[int] = (),
    extra_markers: Iterable[tuple[int, tuple[float, float]]] = (),
) -> np.ndarray:
    """Renderiza una vista cenital sintética del tablero (imagen BGR).

    Args:
        placement: ``{"e4": "P", ...}`` — casilla -> símbolo FEN.
        px_per_mm: resolución de la imagen simulada.
        margin_mm: margen blanco alrededor del tablero (zona de silencio ArUco).
        piece_marker_mm: lado del marcador de cada pieza.
        corner_marker_mm: lado de los marcadores de esquina.
        corner_layout: dónde se pega *físicamente* cada marcador de esquina
            (``{40: "a8", ...}``).  Por defecto, la colocación correcta; sirve
            para simular marcadores mal colocados o intercambiados.
        hidden_corners: IDs de esquina que NO se dibujan — simula un marcador
            tapado por una pieza (p. ej. una torre sobre la esquina).
        extra_markers: marcadores sueltos ``(id, (x_mm, y_mm))`` en coordenadas
            del tablero; con coordenadas negativas o mayores que el tablero
            quedan FUERA del área de juego (marcador olvidado en la mesa, pieza
            capturada en la zona de descarte).
    """
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, config.ARUCO_DICT_NAME))
    size_px = int(round((config.BOARD_SIZE_MM + 2 * margin_mm) * px_per_mm))
    canvas = np.full((size_px, size_px, 3), 255, dtype=np.uint8)

    def to_px(x_mm: float, y_mm: float) -> tuple[int, int]:
        return (
            int(round((x_mm + margin_mm) * px_per_mm)),
            int(round((y_mm + margin_mm) * px_per_mm)),
        )

    # Líneas suaves de las casillas (solo decorativas, no afectan la detección).
    for i in range(config.BOARD_SQUARES + 1):
        c = i * config.SQUARE_SIZE_MM
        cv2.line(canvas, to_px(c, 0), to_px(c, config.BOARD_SIZE_MM), (220, 220, 220), 1)
        cv2.line(canvas, to_px(0, c), to_px(config.BOARD_SIZE_MM, c), (220, 220, 220), 1)

    # Esquinas del tablero.
    corner_side = int(round(corner_marker_mm * px_per_mm))
    layout = corner_layout if corner_layout is not None else EXPECTED_LAYOUT
    hidden = set(hidden_corners)
    for aruco_id, corner_name in layout.items():
        if aruco_id in hidden:
            continue
        x_mm, y_mm = CORNER_MM_BY_NAME[corner_name]
        _paste_marker(canvas, dictionary, aruco_id, to_px(x_mm, y_mm), corner_side)

    # Piezas.
    piece_side = int(round(piece_marker_mm * px_per_mm))
    for square, aruco_id in _ids_for_placement(placement).items():
        x_mm, y_mm = BoardPose.square_center_mm(square)
        _paste_marker(canvas, dictionary, aruco_id, to_px(x_mm, y_mm), piece_side)

    # Marcadores sueltos (pueden caer fuera del área de juego).
    for aruco_id, (x_mm, y_mm) in extra_markers:
        _paste_marker(canvas, dictionary, aruco_id, to_px(x_mm, y_mm), piece_side)

    logger.debug(
        "Imagen sintética generada: %d px, %d piezas.", size_px, len(placement)
    )
    return canvas
