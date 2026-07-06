"""Calibración de cámara (matriz intrínseca + coeficientes de distorsión).

La calibración es **opcional** en el pipeline v1: la homografía de las 4
esquinas ya absorbe la perspectiva.  Corregir la distorsión de lente mejora la
precisión en los bordes de la imagen, y será necesaria para el rastreo del
brazo (V2).

Flujo típico::

    # Una sola vez: fotografiar un patrón de ajedrez (el de OpenCV, no el
    # tablero de MAGNUS) desde varios ángulos y calibrar
    calib = calibrate_from_chessboard_images(imagenes, pattern_size=(9, 6),
                                             square_size_mm=25.0)
    calib.save("camera_calibration.npz")

    # En tiempo de juego
    calib = CameraCalibration.load("camera_calibration.npz")
    frame_corregido = calib.undistort(frame)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import cv2
import numpy as np

logger = logging.getLogger("magnus.vision.calibration")


class CalibrationError(Exception):
    """La calibración no pudo completarse."""


@dataclass
class CameraCalibration:
    """Parámetros intrínsecos de la cámara."""

    camera_matrix: np.ndarray       # 3×3
    dist_coeffs: np.ndarray         # coeficientes de distorsión
    reprojection_error: float = 0.0  # error RMS de la calibración (px)

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        """Devuelve el frame con la distorsión de lente corregida."""
        return cv2.undistort(frame, self.camera_matrix, self.dist_coeffs)

    # ------------------------------------------------------------------ #
    # Persistencia
    # ------------------------------------------------------------------ #
    def save(self, path: Union[str, Path]) -> None:
        np.savez(
            str(path),
            camera_matrix=self.camera_matrix,
            dist_coeffs=self.dist_coeffs,
            reprojection_error=np.array([self.reprojection_error]),
        )
        logger.info("Calibración guardada en %s", path)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "CameraCalibration":
        data = np.load(str(path))
        return cls(
            camera_matrix=data["camera_matrix"],
            dist_coeffs=data["dist_coeffs"],
            reprojection_error=float(data["reprojection_error"][0]),
        )


def calibrate_from_chessboard_images(
    images: list[np.ndarray],
    pattern_size: tuple[int, int] = (9, 6),
    square_size_mm: float = 25.0,
) -> CameraCalibration:
    """Calibra la cámara con fotos de un patrón de tablero de ajedrez.

    Args:
        images: frames BGR o grises del patrón, tomados desde varios ángulos
            (mínimo ~10 para buenos resultados).
        pattern_size: esquinas interiores del patrón (columnas, filas).
        square_size_mm: tamaño de cada cuadro del patrón impreso.
    """
    # Puntos 3D del patrón (z=0, en mm).
    objp = np.zeros((pattern_size[0] * pattern_size[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:pattern_size[0], 0:pattern_size[1]].T.reshape(-1, 2)
    objp *= square_size_mm

    obj_points: list[np.ndarray] = []
    img_points: list[np.ndarray] = []
    image_size: tuple[int, int] | None = None

    for i, img in enumerate(images):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        image_size = gray.shape[::-1]
        found, corners = cv2.findChessboardCorners(gray, pattern_size)
        if not found:
            logger.warning("Imagen %d: patrón no encontrado; se omite.", i)
            continue
        corners = cv2.cornerSubPix(
            gray, corners, (11, 11), (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
        )
        obj_points.append(objp)
        img_points.append(corners)

    if len(obj_points) < 3:
        raise CalibrationError(
            f"Solo {len(obj_points)} imágenes útiles; se necesitan al menos 3 "
            "(idealmente 10+) con el patrón visible."
        )

    rms, camera_matrix, dist_coeffs, _, _ = cv2.calibrateCamera(
        obj_points, img_points, image_size, None, None
    )
    logger.info("Calibración completada con %d imágenes (RMS=%.3f px).", len(obj_points), rms)
    return CameraCalibration(
        camera_matrix=camera_matrix, dist_coeffs=dist_coeffs, reprojection_error=float(rms)
    )
