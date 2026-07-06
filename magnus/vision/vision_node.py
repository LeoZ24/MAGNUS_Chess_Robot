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
from .aruco_detector import ArucoDetector, Detection, DetectionLatch, MarkerRole, split_by_role
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
    """Dos piezas detectadas en la misma casilla, o pieza fuera del tablero."""


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
    ):
        self._camera = camera
        self._detector = detector or ArucoDetector()
        self._calibration = calibration
        self._tracker = tracker or GameTracker()
        self._confirm_n = confirm_n
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

    # ------------------------------------------------------------------ #
    # Escaneo
    # ------------------------------------------------------------------ #
    def scan(self, max_frames: Optional[int] = None) -> dict[int, Detection]:
        """Captura frames y devuelve las detecciones confirmadas (enclavadas).

        Cada escaneo usa un enclavamiento *fresco*: así una pieza retirada del
        tablero (capturada) no queda "fantasma" en la memoria.
        """
        if not self._started:
            self.start()
        max_frames = max_frames or (self._confirm_n * 3)
        latch = DetectionLatch(confirm_n=self._confirm_n)
        confirmed: dict[int, Detection] = {}
        for _ in range(max_frames):
            frame = self._camera.read()
            if self._calibration is not None:
                frame = self._calibration.undistort(frame)
            confirmed = latch.update(self._detector.detect(frame))
        logger.debug("Escaneo: %d marcadores confirmados.", len(confirmed))
        return confirmed

    def get_board_placement(self, max_frames: Optional[int] = None) -> dict[str, str]:
        """Escanea y devuelve ``{"e4": "P", ...}`` (casilla -> símbolo FEN)."""
        confirmed = self.scan(max_frames)
        by_role = split_by_role(confirmed)

        corners = by_role[MarkerRole.CORNER]
        if len(corners) < 4:
            raise BoardNotFoundError(
                f"Solo {len(corners)}/4 esquinas del tablero detectadas "
                f"(IDs esperados: {config.ARUCO_IDS_BOARD_CORNERS})."
            )
        pose = BoardPose.from_corner_centers(
            {i: det.center_px for i, det in corners.items()}
        )

        placement: dict[str, str] = {}
        occupied_by: dict[str, int] = {}
        for aruco_id, det in by_role[MarkerRole.PIECE].items():
            square = pose.pixel_to_square(*det.center_px)
            if square is None:
                raise AmbiguousBoardError(
                    f"La pieza con ID {aruco_id} se detectó fuera del tablero."
                )
            if square in placement:
                raise AmbiguousBoardError(
                    f"Dos piezas en {square}: IDs {occupied_by[square]} y {aruco_id}."
                )
            placement[square] = ARUCO_TO_PIECE[aruco_id]
            occupied_by[square] = aruco_id

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
