"""MAGNUS: el robot de ajedrez DIY.

Paquete modular (inspirado en ROS2) organizado en nodos/módulos que se
comunican mediante mensajes tipados:

    magnus.core    -> contratos de datos compartidos (PositionRequest, MoveResponse)
    magnus.engine  -> nodo del engine de ajedrez (Stockfish u otro motor UCI)

Uso rápido::

    from magnus.engine import ChessEngineNode

    with ChessEngineNode(default_difficulty="MEDIUM") as node:
        resp = node.compute_move_from_fen(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )
        print(resp.uci, resp.san)
"""

from .core import MoveResponse, PositionRequest, STARTING_FEN
from .engine import ChessEngineNode, DifficultyLevel

__all__ = [
    "ChessEngineNode",
    "DifficultyLevel",
    "PositionRequest",
    "MoveResponse",
    "STARTING_FEN",
]

__version__ = "0.1.0"
