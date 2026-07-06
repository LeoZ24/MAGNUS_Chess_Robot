"""Detección de marcadores ArUco con enclavamiento, separada por roles.

Hay **tres roles** de marcadores en MAGNUS, cada uno con su rango de ID (ver
``magnus/config.py``) y su propósito:

    * PIECE  (0-31): piezas de ajedrez -> construir la FEN
    * CORNER (40-43): esquinas del tablero -> homografía tablero↔cámara
    * ARM    (44): extremo del brazo -> corrección de posición (V2, futuro)

Este módulo NO mezcla la lógica de los tres: solo detecta, clasifica y aplica
el "enclavamiento" (un marcador debe verse N frames consecutivos antes de
considerarse confirmado, para evitar falsos positivos — heredado del prototipo
``ArUco_Test.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import cv2
import cv2.aruco as aruco
import numpy as np

from .. import config

logger = logging.getLogger("magnus.vision.aruco_detector")


class MarkerRole(Enum):
    """Rol de un marcador según su rango de ID."""

    PIECE = "piece"
    CORNER = "corner"
    ARM = "arm"
    UNKNOWN = "unknown"


def classify_role(aruco_id: int) -> MarkerRole:
    """Clasifica un ID ArUco en su rol (pieza / esquina / brazo)."""
    if aruco_id in config.ARUCO_IDS_PIECES:
        return MarkerRole.PIECE
    if aruco_id in config.ARUCO_IDS_BOARD_CORNERS:
        return MarkerRole.CORNER
    if aruco_id == config.ARUCO_ID_ARM:
        return MarkerRole.ARM
    return MarkerRole.UNKNOWN


@dataclass
class Detection:
    """Un marcador detectado en un frame."""

    aruco_id: int
    center_px: tuple[float, float]
    corners_px: np.ndarray          # 4×2, esquinas del marcador en px
    role: MarkerRole = MarkerRole.UNKNOWN

    def __post_init__(self) -> None:
        if self.role is MarkerRole.UNKNOWN:
            self.role = classify_role(self.aruco_id)


class ArucoDetector:
    """Envoltorio de ``cv2.aruco`` con el preprocesado del prototipo.

    Aplica ecualización de histograma y parámetros de umbral adaptativo
    agresivos (útiles con cámaras/iluminación difíciles).
    """

    def __init__(self, dict_name: str = config.ARUCO_DICT_NAME):
        dictionary = aruco.getPredefinedDictionary(getattr(aruco, dict_name))
        params = aruco.DetectorParameters()
        # Configuración agresiva para cámaras difíciles (del prototipo).
        params.adaptiveThreshWinSizeMin = 3
        params.adaptiveThreshWinSizeMax = 25
        params.errorCorrectionRate = 1.0
        self._detector = aruco.ArucoDetector(dictionary, params)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detecta todos los marcadores en un frame BGR o en escala de grises."""
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        gray = cv2.equalizeHist(gray)

        corners_list, ids, _ = self._detector.detectMarkers(gray)
        detections: list[Detection] = []
        if ids is None:
            return detections
        # ids puede venir como Nx1 (OpenCV 4) o N (OpenCV 5); aplanar unifica.
        for marker_corners, marker_id in zip(corners_list, np.asarray(ids).flatten()):
            pts = marker_corners.reshape(4, 2)
            center = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))
            detections.append(
                Detection(aruco_id=int(marker_id), center_px=center, corners_px=pts)
            )
        return detections


@dataclass
class _LatchEntry:
    count: int = 0
    detection: Optional[Detection] = None


@dataclass
class DetectionLatch:
    """Enclavamiento: confirma un marcador tras N frames consecutivos.

    Una vez confirmado, el marcador queda "fijo" en memoria (se sigue
    actualizando su posición si se re-detecta, pero no se pierde si la cámara
    lo deja de ver un instante).  Usar :meth:`reset` si se mueve la cámara.
    """

    confirm_n: int = config.DETECTION_CONFIRM_N
    _pending: dict[int, _LatchEntry] = field(default_factory=dict)
    _confirmed: dict[int, Detection] = field(default_factory=dict)

    def update(self, detections: list[Detection]) -> dict[int, Detection]:
        """Procesa las detecciones de un frame y devuelve las confirmadas."""
        seen_ids = set()
        for det in detections:
            seen_ids.add(det.aruco_id)
            if det.aruco_id in self._confirmed:
                # Ya fijo: micro-ajuste de posición.
                self._confirmed[det.aruco_id] = det
                continue
            entry = self._pending.setdefault(det.aruco_id, _LatchEntry())
            entry.count += 1
            entry.detection = det
            if entry.count >= self.confirm_n:
                self._confirmed[det.aruco_id] = det
                del self._pending[det.aruco_id]
                logger.debug("Marcador %d confirmado (%s).", det.aruco_id, det.role.value)

        # Un marcador pendiente que deja de verse pierde su racha.
        for aruco_id in list(self._pending):
            if aruco_id not in seen_ids:
                del self._pending[aruco_id]

        return dict(self._confirmed)

    @property
    def confirmed(self) -> dict[int, Detection]:
        return dict(self._confirmed)

    def forget(self, aruco_id: int) -> None:
        """Olvida un marcador confirmado (p. ej. una pieza capturada)."""
        self._confirmed.pop(aruco_id, None)

    def reset(self) -> None:
        """Borra toda la memoria (usar si la cámara o el tablero se movieron)."""
        self._pending.clear()
        self._confirmed.clear()
        logger.info("DetectionLatch reseteado.")


def split_by_role(detections: dict[int, Detection]) -> dict[MarkerRole, dict[int, Detection]]:
    """Separa un dict de detecciones confirmadas por rol."""
    out: dict[MarkerRole, dict[int, Detection]] = {role: {} for role in MarkerRole}
    for aruco_id, det in detections.items():
        out[det.role][aruco_id] = det
    return out
