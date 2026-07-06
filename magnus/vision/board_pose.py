"""Pose del tablero: homografía tablero(mm) <-> imagen(px).

A partir de los 4 marcadores ArUco de las esquinas del tablero se calcula una
homografía que permite convertir cualquier punto entre el sistema de
coordenadas físico del tablero (milímetros) y el de la imagen (píxeles).  Con
ella, el centro de cada marcador de pieza se traduce a la casilla donde está.

Sistema de coordenadas del tablero (en mm)::

    origen (0,0) = esquina exterior de a8      x+ -> hacia la columna h
                                               y+ -> hacia la fila 1

    ID 40 (a8) ─────────── ID 41 (h8)          (vista cenital con las blancas
        │                      │                abajo, como un diagrama de
    ID 43 (a1) ─────────── ID 42 (h1)           ajedrez estándar)

Los marcadores de esquina deben colocarse de modo que su **centro** coincida
con la esquina exterior del área de juego correspondiente.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from .. import config

logger = logging.getLogger("magnus.vision.board_pose")

# Coordenada mm (esquina exterior del área de juego) de cada ID de esquina.
_CORNER_MM: dict[int, tuple[float, float]] = {
    config.ARUCO_IDS_BOARD_CORNERS[0]: (0.0, 0.0),                                   # a8
    config.ARUCO_IDS_BOARD_CORNERS[1]: (config.BOARD_SIZE_MM, 0.0),                  # h8
    config.ARUCO_IDS_BOARD_CORNERS[2]: (config.BOARD_SIZE_MM, config.BOARD_SIZE_MM), # h1
    config.ARUCO_IDS_BOARD_CORNERS[3]: (0.0, config.BOARD_SIZE_MM),                  # a1
}

_FILES = "abcdefgh"


class BoardPoseError(Exception):
    """No se pudo calcular la pose del tablero."""


class BoardPose:
    """Homografía tablero(mm) <-> imagen(px) y mapeo píxel -> casilla."""

    def __init__(self, homography_mm_to_px: np.ndarray):
        self._h = np.asarray(homography_mm_to_px, dtype=np.float64)
        self._h_inv = np.linalg.inv(self._h)

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #
    @classmethod
    def from_corner_centers(cls, corner_centers_px: dict[int, tuple[float, float]]) -> "BoardPose":
        """Crea la pose a partir de los centros (px) de los 4 marcadores de esquina.

        Args:
            corner_centers_px: ``{id_aruco: (x_px, y_px)}`` — deben estar los 4
                IDs de ``config.ARUCO_IDS_BOARD_CORNERS``.
        """
        missing = set(config.ARUCO_IDS_BOARD_CORNERS) - set(corner_centers_px)
        if missing:
            raise BoardPoseError(
                f"Faltan marcadores de esquina: {sorted(missing)}. "
                f"Se necesitan los 4 IDs {config.ARUCO_IDS_BOARD_CORNERS}."
            )
        src_mm = np.array([_CORNER_MM[i] for i in config.ARUCO_IDS_BOARD_CORNERS], dtype=np.float64)
        dst_px = np.array(
            [corner_centers_px[i] for i in config.ARUCO_IDS_BOARD_CORNERS], dtype=np.float64
        )
        h = cv2.getPerspectiveTransform(src_mm.astype(np.float32), dst_px.astype(np.float32))
        logger.debug("Homografía calculada a partir de 4 esquinas.")
        return cls(h)

    # ------------------------------------------------------------------ #
    # Transformaciones
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
        vec = h @ np.array([x, y, 1.0])
        return float(vec[0] / vec[2]), float(vec[1] / vec[2])

    def mm_to_pixel(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """Punto del tablero (mm) -> coordenadas de imagen (px)."""
        return self._apply(self._h, x_mm, y_mm)

    def pixel_to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Coordenadas de imagen (px) -> punto del tablero (mm)."""
        return self._apply(self._h_inv, x_px, y_px)

    # ------------------------------------------------------------------ #
    # Casillas
    # ------------------------------------------------------------------ #
    @staticmethod
    def square_center_mm(square: str) -> tuple[float, float]:
        """Centro (mm) de una casilla, p. ej. ``"e4"`` -> ``(144.0, 144.0)``."""
        file_idx = _FILES.index(square[0])
        rank = int(square[1])
        x = (file_idx + 0.5) * config.SQUARE_SIZE_MM
        y = ((config.BOARD_SQUARES - rank) + 0.5) * config.SQUARE_SIZE_MM
        return x, y

    def square_center_px(self, square: str) -> tuple[float, float]:
        """Centro de una casilla en coordenadas de imagen."""
        return self.mm_to_pixel(*self.square_center_mm(square))

    def pixel_to_square(self, x_px: float, y_px: float) -> Optional[str]:
        """Casilla en la que cae un píxel, o ``None`` si está fuera del tablero."""
        x_mm, y_mm = self.pixel_to_mm(x_px, y_px)
        file_idx = int(x_mm // config.SQUARE_SIZE_MM)
        row = int(y_mm // config.SQUARE_SIZE_MM)
        if x_mm < 0 or y_mm < 0 or file_idx >= config.BOARD_SQUARES or row >= config.BOARD_SQUARES:
            return None
        rank = config.BOARD_SQUARES - row
        return f"{_FILES[file_idx]}{rank}"
