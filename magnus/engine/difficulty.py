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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Union


class DifficultyLevel(IntEnum):
    """Escala de dificultad, de más fácil (1) a máxima fuerza (6)."""

    BEGINNER = 1   # principiante: juega casi al azar, muy poco cálculo
    EASY = 2       # fácil
    MEDIUM = 3     # intermedio (por defecto)
    HARD = 4       # difícil
    EXPERT = 5     # experto
    MAXIMUM = 6    # fuerza máxima del motor (sin limitar)

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
    elo: Optional[int] = None              # UCI_Elo objetivo
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
DIFFICULTY_PRESETS: dict[DifficultyLevel, EngineConfig] = {
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
