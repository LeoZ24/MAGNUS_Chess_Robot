#!/usr/bin/env python3
"""Demo de visión EN VIVO con la webcam — sucesor de ArUco_Test.py.

Muestra en pantalla:
    * Los marcadores detectados, coloreados por rol (piezas / esquinas / brazo)
    * La casilla calculada para cada pieza (cuando las 4 esquinas son visibles)
    * El placement y la FEN construida en tiempo real

Teclas:
    R -> resetear la memoria de detección (si mueves la cámara o el tablero)
    Q -> salir

Requiere los marcadores físicos impresos (ver generate_aruco_markers.py):
4 esquinas (IDs 40-43) pegadas en las esquinas del área de juego y las piezas
con sus IDs 0-31.
"""

from __future__ import annotations

import argparse
import logging
import sys

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from magnus.vision.aruco_detector import (  # noqa: E402
    ArucoDetector,
    DetectionLatch,
    MarkerRole,
    split_by_role,
)
from magnus.vision.board_pose import BoardPose, BoardPoseError  # noqa: E402
from magnus.vision.fen_builder import placement_to_fen_field  # noqa: E402
from magnus.vision.piece_map import ARUCO_TO_PIECE  # noqa: E402

ROLE_COLORS = {
    MarkerRole.PIECE: (0, 255, 0),      # verde
    MarkerRole.CORNER: (255, 0, 0),     # azul
    MarkerRole.ARM: (0, 0, 255),        # rojo
    MarkerRole.UNKNOWN: (0, 255, 255),  # amarillo
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo de visión en vivo de MAGNUS")
    parser.add_argument("--camera", type=int, default=0, help="Índice de la cámara")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"ERROR: no se pudo abrir la cámara {args.camera}", file=sys.stderr)
        return 2

    detector = ArucoDetector()
    latch = DetectionLatch()
    print("Detectando... R = resetear memoria, Q = salir.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        confirmed = latch.update(detector.detect(frame))
        by_role = split_by_role(confirmed)

        # Pose del tablero (si las 4 esquinas están confirmadas).
        pose = None
        corners = by_role[MarkerRole.CORNER]
        if len(corners) == 4:
            try:
                pose = BoardPose.from_corner_centers(
                    {i: d.center_px for i, d in corners.items()}
                )
            except BoardPoseError:
                pose = None

        # Dibujar detecciones.
        placement: dict[str, str] = {}
        for aruco_id, det in confirmed.items():
            color = ROLE_COLORS[det.role]
            pts = det.corners_px.astype(np.int32)
            cv2.polylines(frame, [pts], True, color, 2)
            cx, cy = int(det.center_px[0]), int(det.center_px[1])

            label = f"{det.role.value} {aruco_id}"
            if det.role is MarkerRole.PIECE and pose is not None:
                square = pose.pixel_to_square(*det.center_px)
                if square:
                    piece = ARUCO_TO_PIECE[aruco_id]
                    placement[square] = piece
                    label = f"{piece}@{square}"
            cv2.putText(frame, label, (pts[0][0], pts[0][1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            cv2.circle(frame, (cx, cy), 3, (255, 255, 0), -1)

        # Panel de estado.
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, h - 90), (w, h), (30, 30, 30), -1)
        corner_txt = f"Esquinas: {len(corners)}/4"
        piece_txt = f"Piezas: {len(by_role[MarkerRole.PIECE])}"
        cv2.putText(frame, f"{corner_txt} | {piece_txt}", (15, h - 62),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        if placement:
            fen_field = placement_to_fen_field(placement)
            cv2.putText(frame, f"FEN: {fen_field}", (15, h - 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        else:
            msg = ("Coloca los 4 marcadores de esquina (IDs 40-43)"
                   if pose is None else "Sin piezas sobre el tablero")
            cv2.putText(frame, msg, (15, h - 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 255), 1)
        cv2.putText(frame, "R = resetear | Q = salir", (15, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)

        cv2.imshow("MAGNUS - Vision", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            latch.reset()

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
