"""Módulo del engine de ajedrez de MAGNUS.

Expone el nodo del engine, los niveles de dificultad y el backend de motor.
"""

from .backend import EngineBackend, EnginePlayResult, UCIEngineBackend, find_engine_binary
from .chess_engine_node import (
    ChessEngineNode,
    EngineNodeError,
    GameOverError,
    InvalidPositionError,
)
from .difficulty import DIFFICULTY_PRESETS, DifficultyLevel, EngineConfig, get_config

__all__ = [
    "ChessEngineNode",
    "EngineNodeError",
    "InvalidPositionError",
    "GameOverError",
    "DifficultyLevel",
    "EngineConfig",
    "DIFFICULTY_PRESETS",
    "get_config",
    "EngineBackend",
    "UCIEngineBackend",
    "EnginePlayResult",
    "find_engine_binary",
]
