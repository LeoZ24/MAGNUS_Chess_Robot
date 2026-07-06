"""Seguimiento del estado de la partida a partir de la visión.

El módulo de visión solo puede *ver* dónde hay piezas (el "placement"), pero
una FEN completa necesita además el turno, los derechos de enroque y la casilla
al paso.  ``GameTracker`` resuelve esto por **inferencia por software**:

    1. Mantiene un ``chess.Board`` interno con el estado real de la partida
       (empezando desde una posición conocida, normalmente la inicial).
    2. Cuando la visión entrega un placement nuevo, lo compara contra el
       placement resultante de **cada jugada legal**: la que coincida es la
       jugada que hizo el humano.
    3. Con la jugada identificada, ``python-chess`` actualiza turno, enroque y
       al paso de forma exacta — sin sensores ni botones extra.

Las jugadas del robot se aplican directamente con :meth:`apply_move` (vienen
del engine, no hace falta inferirlas).
"""

from __future__ import annotations

import logging
from typing import Optional

import chess

logger = logging.getLogger("magnus.vision.game_state")


class GameStateError(Exception):
    """Error base del seguimiento de partida."""


class NoMatchingMoveError(GameStateError):
    """El placement detectado no corresponde a ninguna jugada legal."""


class IllegalPositionError(GameStateError):
    """El placement detectado no es alcanzable desde la posición actual."""


def board_placement(board: chess.Board) -> dict[str, str]:
    """Extrae el placement ``{"e4": "P", ...}`` de un ``chess.Board``."""
    return {
        chess.square_name(sq): piece.symbol()
        for sq, piece in board.piece_map().items()
    }


class GameTracker:
    """Rastrea la partida y deduce las jugadas del humano desde el placement.

    Ejemplo::

        tracker = GameTracker()                  # posición inicial
        move = tracker.update_from_placement(placement_detectado)
        print(move.uci(), tracker.fen())         # jugada del humano + FEN exacta
        tracker.apply_move(resp.uci)             # jugada del robot (del engine)
    """

    def __init__(self, fen: Optional[str] = None):
        self._board = chess.Board(fen) if fen else chess.Board()

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    def fen(self) -> str:
        """FEN completa y exacta de la posición actual."""
        return self._board.fen()

    def placement(self) -> dict[str, str]:
        """Placement esperado según el estado interno."""
        return board_placement(self._board)

    @property
    def board(self) -> chess.Board:
        """Acceso de solo-lectura al tablero interno (no hacer push directo)."""
        return self._board

    # ------------------------------------------------------------------ #
    # Actualización
    # ------------------------------------------------------------------ #
    def update_from_placement(self, placement: dict[str, str]) -> Optional[chess.Move]:
        """Deduce y aplica la jugada del humano a partir del placement detectado.

        Returns:
            La jugada detectada, o ``None`` si el placement coincide con la
            posición actual (nadie ha movido todavía).

        Raises:
            NoMatchingMoveError: si ninguna jugada legal produce ese placement.
        """
        if placement == self.placement():
            logger.debug("Placement sin cambios; nadie ha movido.")
            return None

        for move in self._board.legal_moves:
            self._board.push(move)
            candidate = board_placement(self._board)
            self._board.pop()
            if candidate == placement:
                self._board.push(move)
                logger.info("Jugada del humano detectada: %s", move.uci())
                return move

        raise NoMatchingMoveError(
            "El placement detectado no corresponde a ninguna jugada legal desde "
            f"{self._board.fen()!r}. ¿Pieza mal detectada o tablero desordenado?"
        )

    def apply_move(self, uci: str) -> chess.Move:
        """Aplica una jugada conocida (la del robot, devuelta por el engine)."""
        move = chess.Move.from_uci(uci)
        if move not in self._board.legal_moves:
            raise IllegalPositionError(
                f"La jugada {uci!r} no es legal en {self._board.fen()!r}."
            )
        self._board.push(move)
        return move

    def reset(self, fen: Optional[str] = None) -> None:
        """Reinicia la partida (posición inicial o una FEN dada)."""
        self._board = chess.Board(fen) if fen else chess.Board()
        logger.info("GameTracker reiniciado: %s", self._board.fen())
