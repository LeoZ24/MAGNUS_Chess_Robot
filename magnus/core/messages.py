"""Mensajes tipados de MAGNUS.

Esta es la capa de *contrato* entre módulos/nodos, análoga a los archivos
``.msg`` de ROS2.  Son estructuras de datos puras: **no** dependen de ningún
engine de ajedrez ni de ``python-chess``, de modo que cualquier nodo (visión,
control del brazo, GUI, etc.) pueda construirlas/leerlas sin arrastrar
dependencias pesadas.

Flujo previsto:

    [nodo de visión]  --PositionRequest-->  [nodo del engine]  --MoveResponse-->  [nodo del brazo]

El "estado X del tablero" se representa con una FEN (Forsyth-Edwards Notation),
que es exactamente lo que el módulo de visión producirá a partir del tablero
físico.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# FEN de la posicion inicial estandar de ajedrez.
STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@dataclass
class PositionRequest:
    """Petición enviada al nodo del engine: "dame la mejor jugada para esta posición".

    Attributes:
        fen: Posición del tablero en notación FEN (el "estado X").
        difficulty: Nivel de dificultad a usar para *esta* petición. Si es
            ``None`` se usa la dificultad por defecto configurada en el nodo.
            Acepta el nombre del nivel (``"MEDIUM"``) o su valor entero.
        movetime: Tiempo máximo de cálculo en segundos para esta petición.
            Si es ``None`` se usa el de la configuración de dificultad.
        request_id: Identificador opcional para correlacionar petición/respuesta
            (útil cuando varios nodos comparten el mismo engine).
    """

    fen: str = STARTING_FEN
    difficulty: Optional[str | int] = None
    movetime: Optional[float] = None
    request_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PositionRequest":
        # Toma solo las claves conocidas; ignora extras para ser tolerante.
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class MoveResponse:
    """Respuesta del nodo del engine: la jugada elegida y todos sus metadatos.

    Los campos extra (captura, enroque, al paso, casilla del rey/torre...) están
    pensados para el **nodo del brazo robótico**: por ejemplo, un enroque obliga
    a mover también la torre, y una captura al paso retira un peón que no está en
    la casilla de destino.
    """

    # --- Identidad / eco de la petición ---
    request_id: Optional[str] = None
    fen: str = STARTING_FEN          # posición de entrada (antes de mover)
    resulting_fen: str = ""          # posición tras aplicar la jugada

    # --- La jugada ---
    uci: str = ""                    # "e2e4", "e7e8q" (promoción)
    san: str = ""                    # "e4", "Nf3", "O-O", "exd6 e.p."
    from_square: str = ""            # "e2"
    to_square: str = ""              # "e4"
    piece: str = ""                  # "P", "N", "b", ... (mayúscula = blancas)
    side_to_move: str = ""           # "white" / "black"

    # --- Naturaleza de la jugada (para el brazo) ---
    is_capture: bool = False
    captured_square: Optional[str] = None   # dónde está la pieza capturada
    captured_piece: Optional[str] = None
    is_en_passant: bool = False
    is_castling: bool = False
    is_kingside_castle: bool = False
    rook_from: Optional[str] = None         # casilla origen de la torre (enroque)
    rook_to: Optional[str] = None           # casilla destino de la torre
    promotion: Optional[str] = None         # "q", "r", "b", "n"
    is_check: bool = False
    is_checkmate: bool = False
    is_stalemate: bool = False
    is_game_over: bool = False

    # --- Información del engine ---
    difficulty: str = ""             # nivel usado
    evaluation_cp: Optional[int] = None      # centipeones, signo = lado a mover
    mate_in: Optional[int] = None            # mate en N (None si no hay mate)
    depth: Optional[int] = None              # profundidad alcanzada
    compute_time: float = 0.0                # segundos que tardó el engine

    def to_dict(self) -> dict:
        return asdict(self)
