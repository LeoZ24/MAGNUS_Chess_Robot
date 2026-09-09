"""Dobles de prueba compartidos por los tests de ``magnus.app`` (sin hardware)."""

from __future__ import annotations

import random

import chess

from magnus.engine import ChessEngineNode
from magnus.engine.backend import EngineBackend, EnginePlayResult


class RandomEngineBackend(EngineBackend):
    """Motor falso y determinista: prefiere capturas, si no una jugada al azar."""

    def __init__(self, seed: int = 1):
        self.rng = random.Random(seed)
        self.difficulties_seen: list[str] = []

    def start(self) -> None:
        pass

    def quit(self) -> None:
        pass

    def select_move(self, board: chess.Board, config) -> EnginePlayResult:
        self.difficulties_seen.append(config.name)
        captures = [m for m in board.legal_moves if board.is_capture(m)]
        pool = captures or list(board.legal_moves)
        return EnginePlayResult(move=self.rng.choice(pool),
                                evaluation_cp=self.rng.randint(-200, 200), depth=2)


def fake_engine_factory(backend: RandomEngineBackend | None = None):
    """``node_factory`` para ``EngineWorker``/``MagnusController`` sin Stockfish."""
    backend = backend or RandomEngineBackend()

    def factory(difficulty: str) -> ChessEngineNode:
        return ChessEngineNode(backend=backend, default_difficulty=difficulty)

    return factory
