"""Nodo del brazo robótico de MAGNUS: MoveResponse -> movimiento físico.

Arquitectura "teach & playback": los ángulos de cada casilla se graban una vez
en la tabla de posiciones y en tiempo de juego solo se consultan — **sin
cinemática inversa**.

Componentes:

    backend         -> ArmBackend (ABC) + FakeArmBackend + CyberPiBackend (stub)
    positions_table -> carga/consulta de positions.json (64 casillas + zonas)
    arm_node        -> ArmNode: planifica y reproduce las secuencias
"""

from .arm_node import ArmNode, ArmNodeError, ArmStep, IncompleteMoveError
from .backend import ArmBackend, ArmBackendError, CyberPiBackend, FakeArmBackend
from .positions_table import (
    ALL_SQUARES,
    JointAngles,
    PositionsTable,
    PositionsTableError,
    SquarePosition,
    UnknownPositionError,
    make_fake_table,
)

__all__ = [
    "ArmNode",
    "ArmNodeError",
    "ArmStep",
    "IncompleteMoveError",
    "ArmBackend",
    "ArmBackendError",
    "CyberPiBackend",
    "FakeArmBackend",
    "ALL_SQUARES",
    "JointAngles",
    "PositionsTable",
    "PositionsTableError",
    "SquarePosition",
    "UnknownPositionError",
    "make_fake_table",
]
