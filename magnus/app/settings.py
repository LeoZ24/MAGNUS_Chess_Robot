"""Ajustes persistentes de la aplicación de juego.

Todo lo que se puede cambiar desde la interfaz (dificultad, color del robot,
cámara, voz, modo del brazo...) vive aquí y se guarda en un JSON para que la
próxima vez el robot arranque igual que se dejó.

Es una capa de **datos puros**: no importa ningún nodo.  El controlador es
quien aplica cada ajuste al componente que corresponda.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Union

from .. import config

logger = logging.getLogger("magnus.app.settings")

# Modos del brazo, del más seguro al real.
ARM_MODE_OFF = "off"                # el robot canta la jugada y el humano la mueve
ARM_MODE_SIMULATED = "simulated"    # backend falso: muestra la secuencia paso a paso
ARM_MODE_CYBERPI = "cyberpi"        # brazo real por TCP (requiere positions.json)
ARM_MODES: tuple[str, ...] = (ARM_MODE_OFF, ARM_MODE_SIMULATED, ARM_MODE_CYBERPI)

DEFAULT_SETTINGS_FILE = "magnus_settings.json"
DEFAULT_POSITIONS_FILE = "magnus/arm/positions.json"


@dataclass
class AppSettings:
    """Ajustes editables desde la interfaz.

    Cualquier campo nuevo debe llevar valor por defecto: los archivos guardados
    con versiones anteriores tienen que seguir cargando.
    """

    # Partida
    difficulty: str = "MEDIUM"
    robot_side: str = "black"            # "white" | "black"

    # Cámara / visión
    camera_index: int = 0
    board_turns: int = 0                 # giros de 90° del mapeo de casillas
    flip_view: bool = False              # tablero digital con negras abajo

    # Voz
    voice_muted: bool = False
    announce_human_moves: bool = config.VOICE_ANNOUNCE_HUMAN_MOVES
    idle_prompt_s: float = config.VOICE_IDLE_PROMPT_S

    # Brazo
    arm_mode: str = ARM_MODE_OFF
    arm_auto_execute: bool = False       # sin esto hay que pulsar "Ejecutar"
    arm_port: int = 5555                 # puerto TCP que espera a la CyberPi
    positions_path: str = DEFAULT_POSITIONS_FILE

    # ------------------------------------------------------------------ #
    # Validación
    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        """Lanza ``ValueError`` si algún ajuste no tiene sentido."""
        if self.robot_side not in ("white", "black"):
            raise ValueError(f"robot_side inválido: {self.robot_side!r}")
        if self.arm_mode not in ARM_MODES:
            raise ValueError(f"arm_mode inválido: {self.arm_mode!r} (usa {ARM_MODES})")
        if not (0 <= int(self.board_turns) <= 3):
            raise ValueError("board_turns debe estar entre 0 y 3")
        if not (0 < int(self.arm_port) < 65536):
            raise ValueError("arm_port fuera de rango")

    # ------------------------------------------------------------------ #
    # Serialización
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        """Construye los ajustes ignorando claves desconocidas (tolerante)."""
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        settings.validate()
        return settings

    def update(self, **changes: Any) -> "AppSettings":
        """Devuelve una copia con los cambios aplicados y validados."""
        merged = {**self.to_dict(), **changes}
        return AppSettings.from_dict(merged)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "AppSettings":
        """Carga el JSON; si no existe o está roto devuelve los valores por defecto."""
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            settings = cls.from_dict(raw)
            logger.info("Ajustes cargados de %s.", path)
            return settings
        except FileNotFoundError:
            logger.info("No hay %s: se usan los ajustes por defecto.", path)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Ajustes ilegibles en %s (%s): se usan los por defecto.",
                           path, exc)
        return cls()

    def save(self, path: Union[str, Path]) -> None:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        logger.debug("Ajustes guardados en %s.", path)
