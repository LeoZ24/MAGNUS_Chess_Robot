"""Niveles de dificultad del engine de MAGNUS.

Define una escala de dificultad de alto nivel (``DifficultyLevel``) y la traduce
a una configuración concreta del engine (``EngineConfig``).  Esta capa es **pura
datos**: no importa ``python-chess`` ni habla con ningún motor; el ``backend`` es
quien interpreta estos valores.  Así puedes cambiar los presets sin tocar la
lógica del engine, y reutilizar los mismos niveles con cualquier motor UCI.

Mapeo a opciones de Stockfish (y la mayoría de motores UCI):
    * ``skill_level``     -> opción ``Skill Level`` (0-20)
    * ``limit_strength``  -> opción ``UCI_LimitStrength`` (bool)
    * ``elo``             -> opción ``UCI_Elo`` (Elo objetivo aproximado)
    * ``depth`` / ``movetime`` / ``nodes`` -> límite de búsqueda

Stockfish no baja de ~1320 de Elo con ``UCI_Elo``.  Los niveles por debajo de
eso (``LEARNER`` .. ``AMATEUR``) se debilitan **por software**: el backend pide
varias jugadas candidatas (``multipv``) y, con probabilidad ``mistake_chance``,
elige una peor que la mejor (sin perder más de ``max_loss_cp``).  En esos
niveles ``elo`` es solo una etiqueta aproximada para la interfaz.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Union


class DifficultyLevel(IntEnum):
    """Escala de dificultad, de más fácil (1) a máxima fuerza (10)."""

    LEARNER = 1    # acaba de aprender a mover las piezas (errores a propósito)
    NOVICE = 2     # novato (errores a propósito)
    CASUAL = 3     # jugador ocasional (errores a propósito)
    AMATEUR = 4    # aficionado (errores a propósito)
    BEGINNER = 5   # el mínimo de Stockfish (~1350)
    EASY = 6       # fácil
    MEDIUM = 7     # intermedio (por defecto)
    HARD = 8       # difícil
    EXPERT = 9     # experto
    MAXIMUM = 10   # fuerza máxima del motor (sin limitar)

    @classmethod
    def parse(cls, value: Union[str, int, "DifficultyLevel"]) -> "DifficultyLevel":
        """Acepta ``"MEDIUM"``, ``"medium"``, ``3`` o un ``DifficultyLevel``."""
        if isinstance(value, DifficultyLevel):
            return value
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, str):
            key = value.strip().upper()
            if key in cls.__members__:
                return cls[key]
            # permitir también nombres numéricos en texto: "3"
            if key.isdigit():
                return cls(int(key))
        raise ValueError(
            f"Dificultad inválida: {value!r}. "
            f"Usa uno de {[m.name for m in cls]} o 1-{len(cls)}."
        )


@dataclass(frozen=True)
class EngineConfig:
    """Configuración concreta que el backend aplica al motor.

    Cualquier campo en ``None`` significa "no lo fijes / sin límite".
    """

    name: str = "MEDIUM"
    # Fuerza de juego
    skill_level: Optional[int] = None      # 0-20 (Skill Level de Stockfish)
    limit_strength: bool = False           # activa UCI_LimitStrength
    elo: Optional[int] = None              # UCI_Elo objetivo (o etiqueta)
    # Errores a propósito (niveles por debajo del mínimo de Stockfish)
    multipv: int = 1                       # jugadas candidatas a pedir
    mistake_chance: float = 0.0            # prob. de no elegir la mejor
    max_loss_cp: Optional[int] = None      # lo peor que puede ser el error
    # Límite de búsqueda (cuánto "piensa")
    movetime: Optional[float] = None       # segundos por jugada
    depth: Optional[int] = None            # profundidad máxima
    nodes: Optional[int] = None            # nodos máximos
    # Recursos del motor
    threads: int = 1
    hash_mb: int = 64

    def with_movetime(self, movetime: Optional[float]) -> "EngineConfig":
        """Devuelve una copia con el ``movetime`` sobreescrito (si no es None)."""
        if movetime is None:
            return self
        return EngineConfig(**{**self.__dict__, "movetime": movetime})


# Presets: del más débil al más fuerte.
# Para los niveles bajos limitamos Skill Level / Elo y damos poco tiempo; en el
# nivel máximo no limitamos nada y dejamos más tiempo de cálculo.
#
# Los cuatro primeros no limitan a Stockfish (Skill Level 20, sin UCI_Elo): lo
# que los hace débiles son los errores a propósito, y un Stockfish limitado
# además elegiría mal las candidatas que luego comparamos.
DIFFICULTY_PRESETS: dict[DifficultyLevel, EngineConfig] = {
    DifficultyLevel.LEARNER: EngineConfig(
        name="LEARNER", skill_level=20, elo=400,
        movetime=0.05, depth=1, multipv=12, mistake_chance=0.85, max_loss_cp=None,
    ),
    DifficultyLevel.NOVICE: EngineConfig(
        name="NOVICE", skill_level=20, elo=700,
        movetime=0.05, depth=1, multipv=10, mistake_chance=0.7, max_loss_cp=900,
    ),
    DifficultyLevel.CASUAL: EngineConfig(
        name="CASUAL", skill_level=20, elo=950,
        movetime=0.05, depth=1, multipv=8, mistake_chance=0.55, max_loss_cp=600,
    ),
    DifficultyLevel.AMATEUR: EngineConfig(
        name="AMATEUR", skill_level=20, elo=1150,
        movetime=0.05, depth=1, multipv=6, mistake_chance=0.5, max_loss_cp=450,
    ),
    DifficultyLevel.BEGINNER: EngineConfig(
        name="BEGINNER", skill_level=0, limit_strength=True, elo=1350,
        movetime=0.05, depth=1,
    ),
    DifficultyLevel.EASY: EngineConfig(
        name="EASY", skill_level=3, limit_strength=True, elo=1500,
        movetime=0.1, depth=4,
    ),
    DifficultyLevel.MEDIUM: EngineConfig(
        name="MEDIUM", skill_level=8, limit_strength=True, elo=1800,
        movetime=0.2, depth=8,
    ),
    DifficultyLevel.HARD: EngineConfig(
        name="HARD", skill_level=14, limit_strength=True, elo=2200,
        movetime=0.5, depth=14,
    ),
    DifficultyLevel.EXPERT: EngineConfig(
        name="EXPERT", skill_level=18, limit_strength=True, elo=2600,
        movetime=1.0, depth=18,
    ),
    DifficultyLevel.MAXIMUM: EngineConfig(
        name="MAXIMUM", skill_level=20, limit_strength=False, elo=None,
        movetime=2.0, depth=None,
    ),
}


def get_config(level: Union[str, int, DifficultyLevel]) -> EngineConfig:
    """Devuelve el ``EngineConfig`` para un nivel de dificultad dado."""
    return DIFFICULTY_PRESETS[DifficultyLevel.parse(level)]


# Configuración de ANÁLISIS: no se usa para jugar, sino para **juzgar** jugadas
# (los comentarios de voz).  Va siempre a fuerza máxima y a tiempo corto, para
# que la calidad del comentario NO dependa de la dificultad a la que juega el
# robot: puede jugar en BEGINNER para que le ganes y aun así comentar bien.
ANALYSIS_CONFIG = EngineConfig(
    name="ANALYSIS", skill_level=20, limit_strength=False, elo=None,
    movetime=0.25, depth=12,
)
