"""Núcleo de MAGNUS: contratos de datos compartidos entre módulos/nodos."""

from .messages import MoveResponse, PositionRequest, STARTING_FEN

__all__ = ["PositionRequest", "MoveResponse", "STARTING_FEN"]
