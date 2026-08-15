"""Detección de marcadores ArUco con enclavamiento, separada por roles.

Hay **tres roles** de marcadores en MAGNUS, cada uno con su rango de ID (ver
``magnus/config.py``) y su propósito:

    * PIECE  (0-23, 12 IDs): tipos de pieza de ajedrez -> construir la FEN
    * CORNER (40-43): esquinas del tablero -> homografía tablero↔cámara
    * ARM    (44): extremo del brazo -> corrección de posición (V2, futuro)

Este módulo NO mezcla la lógica de los tres: solo detecta, clasifica y aplica
el "enclavamiento" (un marcador debe verse N frames consecutivos antes de
considerarse confirmado, para evitar falsos positivos — heredado del prototipo
``ArUco_Test.py``).

**Importante — varios marcadores comparten ID:** el marcador identifica el
*tipo* de pieza (todos los peones blancos llevan el ID 0), así que un mismo ID
puede aparecer hasta 8 veces en un frame.  Por eso el enclavamiento no indexa
por ID: mantiene una *pista* (track) por instancia física y asocia cada
detección nueva a la pista más cercana del mismo ID (asociación espacial).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional

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
    """Un marcador detectado en un frame.

    Como los IDs de pieza identifican el *tipo* y no la pieza individual, dos
    ``Detection`` distintas pueden compartir ``aruco_id`` — se diferencian por
    ``center_px``.
    """

    aruco_id: int
    center_px: tuple[float, float]
    corners_px: np.ndarray          # 4×2, esquinas del marcador en px
    role: MarkerRole = MarkerRole.UNKNOWN

    def __post_init__(self) -> None:
        if self.role is MarkerRole.UNKNOWN:
            self.role = classify_role(self.aruco_id)

    @property
    def marker_side_px(self) -> float:
        """Lado medio del marcador en píxeles (escala local de la imagen)."""
        pts = self.corners_px
        sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
        return sum(sides) / 4.0


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
        """Detecta todos los marcadores en un frame BGR o en escala de grises.

        Devuelve TODAS las instancias, incluidas varias con el mismo ID (p. ej.
        los 8 peones blancos comparten el ID 0).
        """
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
            pts = np.asarray(marker_corners, dtype=np.float64).reshape(4, 2)
            center = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))
            detections.append(
                Detection(aruco_id=int(marker_id), center_px=center, corners_px=pts)
            )
        return detections


@dataclass
class _Track:
    """Una instancia física de marcador rastreada a lo largo de los frames."""

    detection: Detection
    hits: int = 1           # frames consecutivos vista
    missed: int = 0         # frames consecutivos sin verse (solo confirmadas)
    confirmed: bool = False


class DetectionLatch:
    """Enclavamiento multi-instancia: confirma marcadores tras N frames seguidos.

    A diferencia de un dict indexado por ID, este enclavamiento mantiene una
    *pista* por instancia física, de modo que los 8 peones blancos (todos con
    el ID 0) se rastrean por separado.  La asociación entre frames es espacial:
    una detección continúa la pista del mismo ID más cercana, siempre que esté
    a menos de ``lado_del_marcador × match_radius_factor`` píxeles (umbral
    auto-escalado: no depende de la resolución ni de la distancia de la cámara).

    Comportamiento:

    * Una pista *pendiente* que deja de verse un frame pierde su racha
      (detección estricta de N frames consecutivos, como el prototipo).
    * Una pista *confirmada* sobrevive ausencias breves (oclusión de la mano),
      pero se olvida tras ``forget_after`` frames sin verse — así una pieza
      movida o capturada no deja un "fantasma".  ``forget_after=0`` desactiva
      el olvido (útil en escaneos cortos de un solo uso).
    * **Las esquinas son la excepción**: no se olvidan nunca (ver
      ``config.DETECTION_FORGET_FRAMES_CORNERS``).  El tablero y la cámara no
      se mueven durante la partida, así que una esquina tapada por una torre se
      queda memorizada en su última posición y la homografía sigue viva.
    * Los IDs de esquina son únicos (a diferencia de los de pieza): mientras
      haya una pista de esquina confirmada, otra detección del mismo ID lejos
      de ella se ignora — un marcador de esquina suelto en la mesa o un reflejo
      no puede robarle el sitio al bueno.
    * :meth:`reset` borra toda la memoria (cámara o tablero movidos).
    """

    #: Roles cuyos IDs identifican una instancia única (no hay dos esquinas 40).
    UNIQUE_ID_ROLES: frozenset[MarkerRole] = frozenset({MarkerRole.CORNER})

    def __init__(
        self,
        confirm_n: int = config.DETECTION_CONFIRM_N,
        forget_after: int = config.DETECTION_FORGET_FRAMES,
        match_radius_factor: float = config.DETECTION_MATCH_RADIUS_FACTOR,
        forget_after_by_role: Optional[Mapping[MarkerRole, int]] = None,
    ):
        self.confirm_n = confirm_n
        self.forget_after = forget_after
        self.match_radius_factor = match_radius_factor
        # Política de olvido por rol; lo no listado usa `forget_after`.
        self.forget_after_by_role: dict[MarkerRole, int] = (
            dict(forget_after_by_role)
            if forget_after_by_role is not None
            else {MarkerRole.CORNER: config.DETECTION_FORGET_FRAMES_CORNERS}
        )
        self._tracks: list[_Track] = []

    # ------------------------------------------------------------------ #
    # Actualización por frame
    # ------------------------------------------------------------------ #
    def update(self, detections: list[Detection]) -> list[Detection]:
        """Procesa las detecciones de un frame y devuelve las confirmadas."""
        matched: set[int] = set()       # índices de pistas continuadas este frame
        new_tracks: list[_Track] = []

        for det in detections:
            idx = self._match(det, exclude=matched)
            if idx is None:
                if self._is_duplicate_unique(det):
                    logger.debug(
                        "Detección extra del marcador único %d en (%.0f, %.0f) "
                        "ignorada: ya hay una pista confirmada en otra posición.",
                        det.aruco_id, det.center_px[0], det.center_px[1],
                    )
                    continue
                track = _Track(detection=det)
                track.confirmed = track.hits >= self.confirm_n
                new_tracks.append(track)
                continue
            track = self._tracks[idx]
            track.detection = det       # micro-ajuste de posición
            track.hits += 1
            track.missed = 0
            matched.add(idx)
            if not track.confirmed and track.hits >= self.confirm_n:
                track.confirmed = True
                logger.debug(
                    "Marcador %d confirmado en (%.0f, %.0f) [%s].",
                    det.aruco_id, det.center_px[0], det.center_px[1], det.role.value,
                )

        # Pistas no vistas este frame: las pendientes pierden la racha; las
        # confirmadas aguantan hasta forget_after frames (las esquinas, siempre).
        survivors: list[_Track] = []
        for idx, track in enumerate(self._tracks):
            if idx in matched:
                survivors.append(track)
                continue
            if not track.confirmed:
                continue                # racha rota: se descarta
            track.missed += 1
            forget_after = self.forget_for(track.detection.role)
            if forget_after and track.missed >= forget_after:
                det = track.detection
                logger.debug(
                    "Marcador %d olvidado tras %d frames sin verse (%.0f, %.0f).",
                    det.aruco_id, track.missed, det.center_px[0], det.center_px[1],
                )
                continue
            survivors.append(track)

        self._tracks = survivors + new_tracks
        return self.confirmed

    def forget_for(self, role: MarkerRole) -> int:
        """Frames sin ver tras los que se olvida un marcador de ese rol (0 = nunca)."""
        return self.forget_after_by_role.get(role, self.forget_after)

    def _is_duplicate_unique(self, det: Detection) -> bool:
        """``True`` si es un ID único (esquina) que ya tiene pista confirmada."""
        if det.role not in self.UNIQUE_ID_ROLES:
            return False
        return any(
            t.confirmed and t.detection.aruco_id == det.aruco_id for t in self._tracks
        )

    def _match(self, det: Detection, exclude: set[int]) -> Optional[int]:
        """Índice de la pista más cercana compatible con la detección, o None."""
        radius = det.marker_side_px * self.match_radius_factor
        best_idx: Optional[int] = None
        best_dist = math.inf
        for idx, track in enumerate(self._tracks):
            if idx in exclude or track.detection.aruco_id != det.aruco_id:
                continue
            prev = track.detection.center_px
            dist = math.hypot(det.center_px[0] - prev[0], det.center_px[1] - prev[1])
            if dist <= radius and dist < best_dist:
                best_idx, best_dist = idx, dist
        return best_idx

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    @property
    def confirmed(self) -> list[Detection]:
        """Detecciones confirmadas (todas las instancias, IDs repetidos incluidos)."""
        return [t.detection for t in self._tracks if t.confirmed]

    @property
    def pending(self) -> list[Detection]:
        """Detecciones aún en racha de confirmación (no confirmadas)."""
        return [t.detection for t in self._tracks if not t.confirmed]

    @property
    def occluded(self) -> list[Detection]:
        """Confirmadas que NO se ven ahora mismo: se recuerdan de frames previos.

        Sirve para avisar en la interfaz de que, p. ej., una esquina está tapada
        y se está usando su última posición conocida.
        """
        return [t.detection for t in self._tracks if t.confirmed and t.missed > 0]

    def stats(self) -> dict[MarkerRole, int]:
        """Número de instancias confirmadas por rol (para interfaces/demos)."""
        counts: dict[MarkerRole, int] = {role: 0 for role in MarkerRole}
        for det in self.confirmed:
            counts[det.role] += 1
        return counts

    def reset(self) -> None:
        """Borra toda la memoria (usar si la cámara o el tablero se movieron)."""
        self._tracks.clear()
        logger.info("DetectionLatch reseteado.")


def split_by_role(detections: list[Detection]) -> dict[MarkerRole, list[Detection]]:
    """Separa una lista de detecciones por rol (sin mezclar sus lógicas)."""
    out: dict[MarkerRole, list[Detection]] = {role: [] for role in MarkerRole}
    for det in detections:
        out[det.role].append(det)
    return out


def corners_by_id(detections: list[Detection]) -> dict[int, Detection]:
    """Indexa detecciones de ESQUINA por ID (las esquinas sí tienen ID único).

    Si un ID de esquina aparece repetido (reflejo, marcador duplicado), se
    conserva la primera instancia y se avisa por log.
    """
    out: dict[int, Detection] = {}
    for det in detections:
        if det.role is not MarkerRole.CORNER:
            continue
        if det.aruco_id in out:
            logger.warning(
                "Esquina %d detectada más de una vez; se ignora la instancia extra "
                "en (%.0f, %.0f).",
                det.aruco_id, det.center_px[0], det.center_px[1],
            )
            continue
        out[det.aruco_id] = det
    return out
