"""MAGNUS: el robot de ajedrez DIY.

Paquete modular (inspirado en ROS2) organizado en nodos/módulos que se
comunican mediante mensajes tipados:

    magnus.core    -> contratos de datos compartidos (PositionRequest, MoveResponse)
    magnus.engine  -> nodo del engine de ajedrez (Stockfish u otro motor UCI)
    magnus.vision  -> nodo de visión (ArUco/OpenCV): tablero físico -> FEN
    magnus.arm     -> nodo del brazo: MoveResponse -> movimiento pregrabado
    magnus.config  -> constantes físicas y de detección centralizadas

Uso rápido::

    from magnus.engine import ChessEngineNode

    with ChessEngineNode(default_difficulty="MEDIUM") as node:
        resp = node.compute_move_from_fen(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )
        print(resp.uci, resp.san)

Los submódulos ``vision`` y ``arm`` no se importan aquí para que usar solo el
engine no arrastre OpenCV: impórtalos directamente
(``from magnus.vision import BoardVisionNode``).
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
