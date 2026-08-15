"""Nodo de visión de MAGNUS: tablero físico -> placement -> FEN.

Equivalente en visión al ``ChessEngineNode``: un nodo con backend
intercambiable (cámara real de OpenCV o una cámara falsa para tests) que
produce el estado del tablero.

Flujo interno de :meth:`BoardVisionNode.get_board_placement`::

    frames -> ArucoDetector -> DetectionLatch (enclavamiento)
           -> separar por rol -> homografía (esquinas) -> casilla de cada pieza

Y :meth:`BoardVisionNode.get_board_fen` añade el ``GameTracker`` para deducir
turno/enroque/al paso comparando contra las jugadas legales de la partida.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from .. import config
from .aruco_detector import (
    ArucoDetector,
    Detection,
    DetectionLatch,
    MarkerRole,
    corners_by_id,
    split_by_role,
)
from .board_pose import BoardPose
from .calibration import CameraCalibration
from .game_state import GameTracker
from .piece_map import ARUCO_TO_PIECE

logger = logging.getLogger("magnus.vision.node")


class VisionNodeError(Exception):
    """Error base del nodo de visión."""


class CameraError(VisionNodeError):
    """No se pudo abrir o leer la cámara."""


class BoardNotFoundError(VisionNodeError):
    """No se detectaron las 4 esquinas del tablero."""


class AmbiguousBoardError(VisionNodeError):
    """Dos piezas detectadas en la misma casilla."""


# --------------------------------------------------------------------------- #
# Backends de cámara
# --------------------------------------------------------------------------- #
class CameraBackend(ABC):
    """Fuente de frames intercambiable (cámara real o falsa)."""

    @abstractmethod
    def open(self) -> None:
        """Abre el recurso de captura."""

    @abstractmethod
    def read(self) -> np.ndarray:
        """Devuelve el siguiente frame (BGR)."""

    @abstractmethod
    def close(self) -> None:
        """Libera el recurso de captura."""

    def __enter__(self) -> "CameraBackend":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class OpenCVCameraBackend(CameraBackend):
    """Cámara real via ``cv2.VideoCapture``."""

    def __init__(self, index: int = 0):
        self.index = index
        self._cap = None

    def open(self) -> None:
        import cv2

        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            raise CameraError(f"No se pudo abrir la cámara {self.index}.")
        logger.info("Cámara %d abierta.", self.index)

    def read(self) -> np.ndarray:
        if self._cap is None:
            raise CameraError("La cámara no está abierta (llama a open()).")
        ok, frame = self._cap.read()
        if not ok:
            raise CameraError("No se pudo leer un frame de la cámara.")
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Cámara %d cerrada.", self.index)


class FakeCameraBackend(CameraBackend):
    """Cámara falsa para tests y demos: reproduce frames pre-cargados en bucle."""

    def __init__(self, frames: list[np.ndarray]):
        if not frames:
            raise ValueError("FakeCameraBackend necesita al menos un frame.")
        self._frames = frames
        self._i = 0
        self._opened = False

    def open(self) -> None:
        self._opened = True

    def read(self) -> np.ndarray:
        if not self._opened:
            raise CameraError("La cámara falsa no está abierta (llama a open()).")
        frame = self._frames[self._i % len(self._frames)]
        self._i += 1
        return frame

    def close(self) -> None:
        self._opened = False


# --------------------------------------------------------------------------- #
# Nodo principal
# --------------------------------------------------------------------------- #
class BoardVisionNode:
    """Nodo de visión: captura frames y produce el placement / la FEN del tablero.

    Ejemplo::

        with BoardVisionNode(camera=OpenCVCameraBackend(0)) as node:
            placement = node.get_board_placement()   # {"e4": "P", ...}
            fen = node.get_board_fen()               # tras la jugada del humano
    """

    def __init__(
        self,
        camera: CameraBackend,
        detector: Optional[ArucoDetector] = None,
        calibration: Optional[CameraCalibration] = None,
        tracker: Optional[GameTracker] = None,
        confirm_n: int = config.DETECTION_CONFIRM_N,
        edge_tolerance_mm: float = config.BOARD_EDGE_TOLERANCE_MM,
    ):
        self._camera = camera
        self._detector = detector or ArucoDetector()
        self._calibration = calibration
        self._tracker = tracker or GameTracker()
        self._confirm_n = confirm_n
        self._edge_tolerance_mm = edge_tolerance_mm
        self._pose: Optional[BoardPose] = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    def start(self) -> "BoardVisionNode":
        if not self._started:
            self._camera.open()
            self._started = True
            logger.info("BoardVisionNode listo.")
        return self

    def shutdown(self) -> None:
        if self._started:
            self._camera.close()
            self._started = False
            logger.info("BoardVisionNode detenido.")

    def __enter__(self) -> "BoardVisionNode":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    @property
    def tracker(self) -> GameTracker:
        return self._tracker

    @property
    def board_pose(self) -> Optional[BoardPose]:
        """Última pose válida del tablero (``None`` si nunca se calculó)."""
        return self._pose

    def reset_board_pose(self) -> None:
        """Olvida la pose memorizada: obliga a re-detectar las 4 esquinas.

        Llamar solo si de verdad se movió la cámara o el tablero.
        """
        self._pose = None
        logger.info("Pose del tablero olvidada; se recalculará con las 4 esquinas.")

    # ------------------------------------------------------------------ #
    # Escaneo
    # ------------------------------------------------------------------ #
    def scan(self, max_frames: Optional[int] = None) -> list[Detection]:
        """Captura frames y devuelve las detecciones confirmadas (enclavadas).

        La lista puede contener varias instancias con el mismo ID ArUco (los
        marcadores identifican el *tipo* de pieza: los 8 peones blancos
        comparten ID).  Cada escaneo usa un enclavamiento *fresco*: así una
        pieza retirada del tablero (capturada) no queda "fantasma" en memoria.
        """
        if not self._started:
            self.start()
        max_frames = max_frames or (self._confirm_n * 3)
        # forget_after=0: en un escaneo corto de un solo uso no hay que olvidar.
        latch = DetectionLatch(confirm_n=self._confirm_n, forget_after=0)
        confirmed: list[Detection] = []
        for _ in range(max_frames):
            frame = self._camera.read()
            if self._calibration is not None:
                frame = self._calibration.undistort(frame)
            confirmed = latch.update(self._detector.detect(frame))
        logger.debug("Escaneo: %d marcadores confirmados.", len(confirmed))
        return confirmed

    def update_board_pose(self, detections: list[Detection]) -> BoardPose:
        """Recalcula la pose con las esquinas detectadas, o reutiliza la anterior.

        Como el tablero y la cámara están fijos, la última pose válida se
        memoriza: si una pieza tapa un marcador de esquina (típico: una torre
        sobre la esquina) se sigue usando la homografía anterior en vez de
        perder el tablero.  Usar :meth:`reset_board_pose` si algo se movió.

        Raises:
            BoardNotFoundError: si faltan esquinas y no hay ninguna pose previa.
        """
        corners = corners_by_id(split_by_role(detections)[MarkerRole.CORNER])
        if len(corners) == 4:
            self._pose = BoardPose.from_corner_centers(
                {i: det.center_px for i, det in corners.items()}
            )
            return self._pose
        if self._pose is not None:
            logger.debug(
                "Solo %d/4 esquinas visibles; se usa la pose memorizada.", len(corners)
            )
            return self._pose
        raise BoardNotFoundError(
            f"Solo {len(corners)}/4 esquinas del tablero detectadas "
            f"(IDs esperados: {config.ARUCO_IDS_BOARD_CORNERS})."
        )

    def get_board_placement(self, max_frames: Optional[int] = None) -> dict[str, str]:
        """Escanea y devuelve ``{"e4": "P", ...}`` (casilla -> símbolo FEN).

        Los marcadores de pieza que caen **fuera** del área de juego se ignoran
        (con un aviso por log): es lo normal para las piezas ya capturadas en la
        zona de descarte o para marcadores sueltos sobre la mesa.
        """
        confirmed = self.scan(max_frames)
        pose = self.update_board_pose(confirmed)

        placement: dict[str, str] = {}
        outside = 0
        for det in split_by_role(confirmed)[MarkerRole.PIECE]:
            symbol = ARUCO_TO_PIECE[det.aruco_id]
            square = pose.pixel_to_square(
                *det.center_px, tolerance_mm=self._edge_tolerance_mm
            )
            if square is None:
                outside += 1
                logger.debug(
                    "Pieza %r (ID %d) fuera del tablero en (%.0f, %.0f): ignorada.",
                    symbol, det.aruco_id, det.center_px[0], det.center_px[1],
                )
                continue
            if square in placement:
                raise AmbiguousBoardError(
                    f"Dos piezas en {square}: {placement[square]!r} y {symbol!r}."
                )
            placement[square] = symbol

        if outside:
            logger.info("%d marcadores de pieza fuera del tablero (ignorados).", outside)
        logger.info("Placement detectado: %d piezas.", len(placement))
        return placement

    def get_board_fen(self, max_frames: Optional[int] = None) -> str:
        """Escanea, deduce la jugada del humano (si la hubo) y devuelve la FEN exacta.

        Usa el ``GameTracker``: el placement detectado se compara contra las
        jugadas legales de la partida en curso, lo que resuelve turno, derechos
        de enroque y casilla al paso sin hardware adicional.
        """
        placement = self.get_board_placement(max_frames)
        self._tracker.update_from_placement(placement)
        return self._tracker.fen()

    def notify_robot_move(self, uci: str) -> None:
        """Registra en el tracker la jugada que el robot acaba de ejecutar."""
        self._tracker.apply_move(uci)
