"""Mapeo oficial ID ArUco -> pieza de ajedrez.

Este es el mapeo definitivo del proyecto.  **El marcador identifica el TIPO de
pieza (tipo + color), no la pieza individual**: todos los peones blancos llevan
el mismo marcador (ID 0), las dos torres negras llevan el ID 18, etc.  Por eso
un mismo ID puede aparecer varias veces en el tablero a la vez — la detección
distingue las instancias por su posición (ver ``aruco_detector.DetectionLatch``).

IDs físicamente impresos (los huecos entre IDs son intencionales — son los
marcadores que existen en el mundo real):

    blancas: 0=peón  1=caballo  2=alfil  6=torre  8=dama  9=rey
    negras: 12=peón 15=caballo 16=alfil 18=torre 21=dama 23=rey

El símbolo sigue la convención FEN de ``python-chess``: mayúsculas = blancas,
minúsculas = negras.  Al imprimir los marcadores físicos, usar
``examples/generate_aruco_markers.py`` — genera un PNG por tipo con el número
de copias a imprimir en la etiqueta.
"""

from __future__ import annotations

# ID ArUco -> símbolo FEN de la pieza (mayúscula = blanca, minúscula = negra).
# Un ID por TIPO de pieza; varias piezas físicas comparten el mismo ID.
ARUCO_TO_PIECE: dict[int, str] = {
    # Blancas
    0: "P",     # peón blanco
    1: "N",     # caballo blanco
    2: "B",     # alfil blanco
    6: "R",     # torre blanca
    8: "Q",     # dama blanca
    9: "K",     # rey blanco
    # Negras
    12: "p",    # peón negro
    15: "n",    # caballo negro
    16: "b",    # alfil negro
    18: "r",    # torre negra
    21: "q",    # dama negra
    23: "k",    # rey negro
}

# Símbolo FEN -> ID ArUco (inverso; válido porque el mapeo es 1 ID por tipo).
PIECE_TO_ARUCO: dict[str, int] = {sym: i for i, sym in ARUCO_TO_PIECE.items()}

# Copias físicas de cada tipo en un juego estándar (por color).  Útil para el
# generador de marcadores y para validaciones; una promoción puede poner en
# juego una copia extra (p. ej. una segunda dama), y eso es representable
# porque las copias comparten ID.
PIECE_COUNTS: dict[str, int] = {"K": 1, "Q": 1, "R": 2, "B": 2, "N": 2, "P": 8}

# Nombres legibles por símbolo (para logs, demos y nombres de archivo).
PIECE_NAMES: dict[str, str] = {
    "K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight", "P": "pawn",
}

# Nombres en español (para la interfaz visual y las etiquetas impresas).
PIECE_NAMES_ES: dict[str, str] = {
    "K": "rey", "Q": "dama", "R": "torre", "B": "alfil", "N": "caballo", "P": "peon",
}


def piece_for_id(aruco_id: int) -> str:
    """Devuelve el símbolo FEN de la pieza para un ID ArUco.

    Raises:
        KeyError: si el ID no corresponde a ninguna pieza.
    """
    return ARUCO_TO_PIECE[aruco_id]


def id_for_piece(symbol: str) -> int:
    """Devuelve el ID ArUco del tipo de pieza (``"P"`` -> 0, ``"k"`` -> 23).

    Raises:
        KeyError: si el símbolo no es una pieza FEN válida.
    """
    return PIECE_TO_ARUCO[symbol]


def describe_id(aruco_id: int) -> str:
    """Descripción legible de un ID, p. ej. ``"white queen (Q)"``."""
    symbol = ARUCO_TO_PIECE[aruco_id]
    color = "white" if symbol.isupper() else "black"
    return f"{color} {PIECE_NAMES[symbol.upper()]} ({symbol})"
