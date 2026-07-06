"""Construcción de cadenas FEN a partir de un "placement" detectado.

Módulo **puro**: no depende de OpenCV ni de ``python-chess``, solo transforma
un diccionario ``{"e4": "P", "e8": "k", ...}`` (casilla -> símbolo FEN de la
pieza) en el texto FEN correspondiente.

La FEN completa tiene 6 campos::

    rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1
    ^placement                                  ^turno ^enroque ^al paso ^semi ^full

Este módulo solo *formatea*; la lógica para deducir turno/enroque/al paso a
partir de la partida en curso vive en ``magnus/vision/game_state.py``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("magnus.vision.fen_builder")

_FILES = "abcdefgh"
_VALID_PIECES = set("KQRBNPkqrbnp")


class InvalidPlacementError(Exception):
    """El placement contiene casillas o piezas inválidas."""


def _validate_placement(placement: dict[str, str]) -> None:
    for square, piece in placement.items():
        if (
            len(square) != 2
            or square[0] not in _FILES
            or not square[1].isdigit()
            or not (1 <= int(square[1]) <= 8)
        ):
            raise InvalidPlacementError(f"Casilla inválida: {square!r}")
        if piece not in _VALID_PIECES:
            raise InvalidPlacementError(f"Pieza inválida en {square}: {piece!r}")


def placement_to_fen_field(placement: dict[str, str]) -> str:
    """Convierte ``{"e4": "P", ...}`` en el primer campo de la FEN.

    Recorre las filas de la 8 a la 1 y, dentro de cada fila, las columnas de la
    'a' a la 'h', agrupando casillas vacías consecutivas como dígitos.
    """
    _validate_placement(placement)
    rows: list[str] = []
    for rank in range(8, 0, -1):
        row = ""
        empty = 0
        for file_char in _FILES:
            piece = placement.get(f"{file_char}{rank}")
            if piece is None:
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows)


def build_fen(
    placement: dict[str, str],
    turn: str = "w",
    castling: str = "-",
    en_passant: str = "-",
    halfmove: int = 0,
    fullmove: int = 1,
) -> str:
    """Construye la FEN completa a partir del placement y los demás campos.

    Args:
        placement: casilla -> símbolo FEN (``{"e1": "K", "e8": "k", ...}``).
        turn: ``"w"`` o ``"b"``.
        castling: derechos de enroque (``"KQkq"``, subconjunto, o ``"-"``).
        en_passant: casilla al paso (``"e3"``) o ``"-"``.
        halfmove: contador de semi-jugadas (regla de los 50 movimientos).
        fullmove: número de jugada completa.
    """
    if turn not in ("w", "b"):
        raise InvalidPlacementError(f"Turno inválido: {turn!r} (usa 'w' o 'b')")
    field = placement_to_fen_field(placement)
    return f"{field} {turn} {castling} {en_passant} {halfmove} {fullmove}"
