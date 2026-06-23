"""Nodo del engine de ajedrez de MAGNUS.

Este es el "nodo" (en sentido ROS2) que ofrece un servicio del tipo
*request/response*:

    PositionRequest (FEN + dificultad)  -->  ChessEngineNode  -->  MoveResponse

Responsabilidades:
    * Parsear la posición (FEN) recibida.
    * Pedir la jugada al backend del motor según el nivel de dificultad.
    * Enriquecer la respuesta con metadatos útiles para el brazo robótico
      (captura, enroque, captura al paso, promoción, jaque/mate, ...).

El nodo es agnóstico al motor: recibe un ``EngineBackend`` (por defecto
``UCIEngineBackend`` -> Stockfish), así que cambiar de motor no afecta al resto
del sistema.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Union

import chess

from ..core.messages import MoveResponse, PositionRequest
from .backend import EngineBackend, UCIEngineBackend
from .difficulty import DifficultyLevel, EngineConfig, get_config

logger = logging.getLogger("magnus.engine.node")


class EngineNodeError(Exception):
    """Error base del nodo del engine."""


class InvalidPositionError(EngineNodeError):
    """La FEN recibida no es válida."""


class GameOverError(EngineNodeError):
    """La posición no tiene jugadas legales (jaque mate o tablas)."""


class ChessEngineNode:
    """Nodo-servicio que calcula jugadas con dificultad configurable.

    Ejemplo::

        with ChessEngineNode(default_difficulty="MEDIUM") as node:
            resp = node.compute_move_from_fen(fen)
            print(resp.uci)            # p. ej. "e2e4"
            print(resp.san)            # p. ej. "e4"
    """

    def __init__(
        self,
        engine_path: Optional[str] = None,
        default_difficulty: Union[str, int, DifficultyLevel] = DifficultyLevel.MEDIUM,
        backend: Optional[EngineBackend] = None,
    ):
        self._backend = backend or UCIEngineBackend(engine_path)
        self._difficulty = DifficultyLevel.parse(default_difficulty)
        self._started = False

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    def start(self) -> "ChessEngineNode":
        if not self._started:
            self._backend.start()
            self._started = True
            logger.info("ChessEngineNode listo (dificultad=%s).", self._difficulty.name)
        return self

    def shutdown(self) -> None:
        if self._started:
            self._backend.quit()
            self._started = False
            logger.info("ChessEngineNode detenido.")

    def __enter__(self) -> "ChessEngineNode":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ------------------------------------------------------------------ #
    # Configuración de dificultad
    # ------------------------------------------------------------------ #
    def set_difficulty(self, level: Union[str, int, DifficultyLevel]) -> None:
        self._difficulty = DifficultyLevel.parse(level)
        logger.info("Dificultad cambiada a %s.", self._difficulty.name)

    def get_difficulty(self) -> DifficultyLevel:
        return self._difficulty

    @staticmethod
    def available_difficulties() -> list[str]:
        return [lvl.name for lvl in DifficultyLevel]

    # ------------------------------------------------------------------ #
    # Servicio principal
    # ------------------------------------------------------------------ #
    def compute_move(self, request: PositionRequest) -> MoveResponse:
        """Calcula la mejor jugada para la posición de la petición."""
        if not self._started:
            self.start()

        board = self._parse_board(request.fen)

        if board.is_game_over():
            raise GameOverError(
                f"La posición ya está terminada ({board.result()}); no hay jugadas."
            )

        level = (
            DifficultyLevel.parse(request.difficulty)
            if request.difficulty is not None
            else self._difficulty
        )
        config: EngineConfig = get_config(level).with_movetime(request.movetime)

        t0 = time.perf_counter()
        result = self._backend.select_move(board, config)
        elapsed = time.perf_counter() - t0

        response = self._build_response(
            board=board,
            move=result.move,
            request=request,
            level=level,
            elapsed=elapsed,
        )
        response.evaluation_cp = result.evaluation_cp
        response.mate_in = result.mate_in
        response.depth = result.depth

        logger.info(
            "[%s] %s -> %s (%s, %.3fs)",
            request.request_id or "-", request.fen.split()[0][:12] + "...",
            response.uci, level.name, elapsed,
        )
        return response

    def compute_move_from_fen(
        self,
        fen: str,
        difficulty: Optional[Union[str, int, DifficultyLevel]] = None,
        movetime: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> MoveResponse:
        """Atajo: construye la ``PositionRequest`` por ti."""
        return self.compute_move(
            PositionRequest(
                fen=fen, difficulty=difficulty, movetime=movetime, request_id=request_id
            )
        )

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_board(fen: str) -> chess.Board:
        try:
            return chess.Board(fen)
        except (ValueError, IndexError) as exc:
            raise InvalidPositionError(f"FEN inválida: {fen!r} ({exc})") from exc

    @staticmethod
    def _build_response(
        board: chess.Board,
        move: chess.Move,
        request: PositionRequest,
        level: DifficultyLevel,
        elapsed: float,
    ) -> MoveResponse:
        """Construye la MoveResponse con todos los metadatos de la jugada.

        Importante: se calcula SOBRE ``board`` (antes de aplicar la jugada) para
        poder detectar capturas/al paso/enroque, y luego se aplica para obtener
        la FEN resultante y el estado de jaque/mate.
        """
        moving_piece = board.piece_at(move.from_square)
        is_capture = board.is_capture(move)
        is_en_passant = board.is_en_passant(move)
        is_castling = board.is_castling(move)
        is_kingside = board.is_kingside_castling(move)

        # Casilla donde está físicamente la pieza capturada.
        captured_square: Optional[str] = None
        captured_piece: Optional[str] = None
        if is_capture:
            if is_en_passant:
                # El peón capturado está en la misma columna del destino pero en
                # la fila del origen, no en la casilla de destino.
                cap_sq = chess.square(
                    chess.square_file(move.to_square),
                    chess.square_rank(move.from_square),
                )
            else:
                cap_sq = move.to_square
            captured_square = chess.square_name(cap_sq)
            cap = board.piece_at(cap_sq)
            captured_piece = cap.symbol() if cap else None

        # Movimiento de la torre en un enroque (el brazo debe moverla también).
        rook_from: Optional[str] = None
        rook_to: Optional[str] = None
        if is_castling:
            rank = chess.square_rank(move.from_square)
            if is_kingside:
                rook_from = chess.square_name(chess.square(7, rank))  # columna h
                rook_to = chess.square_name(chess.square(5, rank))    # columna f
            else:
                rook_from = chess.square_name(chess.square(0, rank))  # columna a
                rook_to = chess.square_name(chess.square(3, rank))    # columna d

        san = board.san(move)
        side_to_move = "white" if board.turn == chess.WHITE else "black"

        # Aplicar la jugada para el estado resultante.
        board.push(move)
        is_check = board.is_check()
        is_checkmate = board.is_checkmate()
        is_stalemate = board.is_stalemate()
        is_game_over = board.is_game_over()
        resulting_fen = board.fen()

        return MoveResponse(
            request_id=request.request_id,
            fen=request.fen,
            resulting_fen=resulting_fen,
            uci=move.uci(),
            san=san,
            from_square=chess.square_name(move.from_square),
            to_square=chess.square_name(move.to_square),
            piece=moving_piece.symbol() if moving_piece else "",
            side_to_move=side_to_move,
            is_capture=is_capture,
            captured_square=captured_square,
            captured_piece=captured_piece,
            is_en_passant=is_en_passant,
            is_castling=is_castling,
            is_kingside_castle=is_kingside if is_castling else False,
            rook_from=rook_from,
            rook_to=rook_to,
            promotion=chess.piece_symbol(move.promotion) if move.promotion else None,
            is_check=is_check,
            is_checkmate=is_checkmate,
            is_stalemate=is_stalemate,
            is_game_over=is_game_over,
            difficulty=level.name,
            compute_time=round(elapsed, 4),
        )
