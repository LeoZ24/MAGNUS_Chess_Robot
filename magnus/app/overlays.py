"""Overlays sobre la imagen de la cámara (marcadores, cuadrícula, jugada).

Dibujo puro con OpenCV: no detecta nada, solo pinta lo que le dan.  Lo usan
la interfaz web (stream de la cámara) y el demo de OpenCV.
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from .. import config
from ..vision.aruco_detector import Detection, MarkerRole
from ..vision.board_pose import BoardPose
from ..vision.piece_map import ARUCO_TO_PIECE

# Colores BGR por rol de marcador.
ROLE_COLORS = {
    MarkerRole.PIECE: (76, 195, 138),     # verde
    MarkerRole.CORNER: (229, 165, 10),    # azul-cian
    MarkerRole.ARM: (82, 82, 224),        # rojo
    MarkerRole.UNKNOWN: (0, 255, 255),    # amarillo
}


def draw_camera_overlays(
    frame: np.ndarray,
    confirmed: list[Detection],
    pending: list[Detection],
    pose: Optional[BoardPose],
    planned_uci: Optional[str],
) -> None:
    """Pinta sobre ``frame`` (in situ) la cuadrícula, los marcadores y la jugada."""
    # Cuadrícula proyectada del tablero.
    if pose is not None:
        overlay = frame.copy()
        step = config.SQUARE_SIZE_MM
        size = config.BOARD_SIZE_MM
        for i in range(config.BOARD_SQUARES + 1):
            a = pose.mm_to_pixel(i * step, 0.0)
            b = pose.mm_to_pixel(i * step, size)
            c = pose.mm_to_pixel(0.0, i * step)
            d = pose.mm_to_pixel(size, i * step)
            for p1, p2 in ((a, b), (c, d)):
                cv2.line(overlay, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])),
                         (120, 190, 120), 1, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, dst=frame)

    # Marcadores pendientes (grises, finos).
    for det in pending:
        pts = det.corners_px.astype(np.int32)
        cv2.polylines(frame, [pts], True, (150, 150, 150), 1, cv2.LINE_AA)

    # Marcadores confirmados, coloreados por rol.
    for det in confirmed:
        color = ROLE_COLORS[det.role]
        pts = det.corners_px.astype(np.int32)
        cv2.polylines(frame, [pts], True, color, 2, cv2.LINE_AA)
        label = str(det.aruco_id)
        if det.role is MarkerRole.PIECE:
            label = ARUCO_TO_PIECE[det.aruco_id]
            if pose is not None:
                square = pose.pixel_to_square(*det.center_px)
                if square:
                    label = f"{label}·{square}"
        cv2.putText(frame, label, (pts[0][0], pts[0][1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    # La jugada planificada, proyectada sobre el tablero físico.
    if pose is not None and planned_uci:
        try:
            a = pose.square_center_px(planned_uci[:2])
            b = pose.square_center_px(planned_uci[2:4])
        except (ValueError, IndexError):
            return
        cv2.arrowedLine(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])),
                        (90, 200, 70), 3, cv2.LINE_AA, tipLength=0.25)
