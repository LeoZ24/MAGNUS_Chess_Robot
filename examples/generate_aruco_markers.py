#!/usr/bin/env python3
"""Genera los PNG de todos los marcadores ArUco del proyecto, listos para imprimir.

El marcador identifica el TIPO de pieza (tipo + color), no la pieza individual:
todos los peones blancos llevan el mismo marcador.  Por eso se generan 17
imágenes (12 tipos de pieza + 4 esquinas + 1 brazo) y cada etiqueta indica
CUÁNTAS COPIAS imprimir de ese marcador:

    piece_00_white_pawn_x8.png ... piece_23_black_king_x1.png   (12 tipos)
    corner_40_a8.png / corner_41_h8.png / corner_42_h1.png / corner_43_a1.png
    arm_44.png

Consejo: imprime una copia extra de cada dama (x2 en vez de x1) para poder
representar una promoción — las copias comparten ID, así que funcionan igual.

Cada marcador lleva un borde blanco (zona de silencio, necesaria para la
detección) y una etiqueta de texto debajo.

Uso:

    python examples/generate_aruco_markers.py
    python examples/generate_aruco_markers.py --output markers/ --size 600
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import cv2  # noqa: E402
import cv2.aruco as aruco  # noqa: E402
import numpy as np  # noqa: E402

from magnus import config  # noqa: E402
from magnus.vision.piece_map import (  # noqa: E402
    ARUCO_TO_PIECE,
    PIECE_COUNTS,
    PIECE_NAMES,
)

CORNER_NAMES = dict(zip(config.ARUCO_IDS_BOARD_CORNERS, ("a8", "h8", "h1", "a1")))


def make_marker_png(dictionary, aruco_id: int, label: str, size: int) -> np.ndarray:
    """Marcador con zona de silencio blanca y etiqueta de texto debajo."""
    marker = aruco.generateImageMarker(dictionary, aruco_id, size)
    border = size // 6
    canvas = np.full((size + 2 * border + 40, size + 2 * border), 255, dtype=np.uint8)
    canvas[border : border + size, border : border + size] = marker
    cv2.putText(canvas, label, (border, size + 2 * border + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, 0, 2)
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera los marcadores ArUco de MAGNUS")
    parser.add_argument("--output", default="aruco_markers", help="Carpeta de salida")
    parser.add_argument("--size", type=int, default=400, help="Lado del marcador en px")
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, config.ARUCO_DICT_NAME))

    count = 0
    # 12 tipos de pieza (las copias físicas comparten marcador).
    for aruco_id, sym in sorted(ARUCO_TO_PIECE.items()):
        color = "white" if sym.isupper() else "black"
        name = PIECE_NAMES[sym.upper()]
        copies = PIECE_COUNTS[sym.upper()]
        filename = f"piece_{aruco_id:02d}_{color}_{name}_x{copies}.png"
        label = f"{aruco_id}: {color} {name}  (imprimir x{copies})"
        img = make_marker_png(dictionary, aruco_id, label, args.size)
        cv2.imwrite(str(out / filename), img)
        count += 1

    # 4 esquinas del tablero.  El orden de los IDs RECORRE EL BORDE
    # (40 a8 -> 41 h8 -> 42 h1 -> 43 a1), no es orden de lectura: ponerlos en
    # zig-zag cruza dos esquinas del tablero.
    for aruco_id, corner in CORNER_NAMES.items():
        filename = f"corner_{aruco_id}_{corner}.png"
        following = CORNER_NAMES[
            config.ARUCO_IDS_BOARD_CORNERS[
                (config.ARUCO_IDS_BOARD_CORNERS.index(aruco_id) + 1) % 4
            ]
        ]
        label = f"{aruco_id}: esquina {corner}  (siguiente por el borde: {following})"
        img = make_marker_png(dictionary, aruco_id, label, args.size)
        cv2.imwrite(str(out / filename), img)
        count += 1

    # Marcador del brazo.
    img = make_marker_png(dictionary, config.ARUCO_ID_ARM,
                          f"{config.ARUCO_ID_ARM}: brazo", args.size)
    cv2.imwrite(str(out / f"arm_{config.ARUCO_ID_ARM}.png"), img)
    count += 1

    total_pieces = 2 * sum(PIECE_COUNTS.values())
    print(f"{count} marcadores escritos en {out}/ "
          f"(las etiquetas indican las copias a imprimir: {total_pieces} piezas en total).")
    print("Imprimir a tamaño real: el marcador de cada pieza debe caber en la "
          f"tapa de {config.PIECE_DIAMETER_MM} mm con su anillo blanco.")
    print("Esquinas: pegarlas RECORRIENDO EL BORDE "
          f"({' -> '.join(f'{i} {CORNER_NAMES[i]}' for i in config.ARUCO_IDS_BOARD_CORNERS)}"
          " -> vuelta al primero), con el centro del marcador en la esquina "
          "exterior del área de juego.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
