"""Mapeo oficial ID ArUco -> pieza de ajedrez.

Este es el mapeo definitivo del proyecto (reemplaza a los 12 IDs sueltos que
usaba el prototipo ``ArUco_Test.py``).  Convención:

    * IDs 0-15  -> piezas BLANCAS (símbolo FEN en mayúscula)
    * IDs 16-31 -> piezas NEGRAS  (símbolo FEN en minúscula)
    * Dentro de cada color el orden es: K, Q, R, R, B, B, N, N, P×8

El símbolo sigue la convención FEN de ``python-chess``: ``"K"`` = rey blanco,
``"k"`` = rey negro, etc.  Al imprimir los marcadores físicos, usar
``examples/generate_aruco_markers.py`` para obtener un PNG por pieza con el ID
correcto en el nombre del archivo.
"""

from __future__ import annotations

# Orden de piezas dentro de cada color (16 piezas por bando).
_PIECE_ORDER: tuple[str, ...] = (
    "K", "Q", "R", "R", "B", "B", "N", "N",
    "P", "P", "P", "P", "P", "P", "P", "P",
)

# ID ArUco -> símbolo FEN de la pieza (mayúscula = blanca, minúscula = negra).
ARUCO_TO_PIECE: dict[int, str] = {
    **{i: sym for i, sym in enumerate(_PIECE_ORDER)},                    # 0-15 blancas
    **{i + 16: sym.lower() for i, sym in enumerate(_PIECE_ORDER)},      # 16-31 negras
}

# Nombres legibles por símbolo (para logs, demos y nombres de archivo).
PIECE_NAMES: dict[str, str] = {
    "K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight", "P": "pawn",
}


def piece_for_id(aruco_id: int) -> str:
    """Devuelve el símbolo FEN de la pieza para un ID ArUco.

    Raises:
        KeyError: si el ID no corresponde a ninguna pieza.
    """
    return ARUCO_TO_PIECE[aruco_id]


def describe_id(aruco_id: int) -> str:
    """Descripción legible de un ID, p. ej. ``"white queen (Q)"``."""
    symbol = ARUCO_TO_PIECE[aruco_id]
    color = "white" if symbol.isupper() else "black"
    return f"{color} {PIECE_NAMES[symbol.upper()]} ({symbol})"
