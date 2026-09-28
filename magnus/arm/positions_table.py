"""Tabla de posiciones pregrabadas del brazo (teach & playback).

Esta es la pieza central de la decisión de arquitectura del brazo: los ángulos
de hombro/codo para cada casilla se graban **una sola vez** (calibración
manual) y en tiempo de juego solo se **consultan** — nunca se calculan.

Formato del JSON (``positions.json``)::

    {
      "e4": {"shoulder": 32.5, "elbow": -110.0},
      ...
      "discard":  {"shoulder": ..., "elbow": ...},
      "exchange": {"shoulder": ..., "elbow": ...}
    }

Una posición por casilla: hombro y codo no controlan altura. El servo S1
recoge y suelta con los mismos dos ángulos para todas las piezas.
Las tablas antiguas con ``approach``/``engage`` siguen cargando: se usa
``engage`` como posición de la casilla y no se reproducen cambios de altura.

Las unidades (grados o pasos de encoder) NO están fijadas por este módulo: se
usan tal cual se grabaron.  Lo único que importa es que la tabla y el backend
usen las mismas.

Además de las 64 casillas, la tabla puede incluir zonas especiales
(``config.ZONE_DISCARD``, ``config.ZONE_EXCHANGE``) para capturas y promociones.

El archivo ``positions.json`` real NO existe todavía (se creará al calibrar el
brazo físico; ver ``examples/generate_positions_template.py``).  Para tests y
demos usar :func:`make_fake_table`, cuyos valores 9999.0 son deliberadamente
irreales para que nunca se confundan con datos medidos.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .. import config

logger = logging.getLogger("magnus.arm.positions_table")

_FILES = "abcdefgh"
ALL_SQUARES: tuple[str, ...] = tuple(
    f"{f}{r}" for r in range(1, config.BOARD_SQUARES + 1) for f in _FILES
)


# Claves que la tabla debería tener para jugar una partida completa: las 64
# casillas y las dos zonas (capturas y promociones).
REQUIRED_KEYS: tuple[str, ...] = ALL_SQUARES + (config.ZONE_DISCARD, config.ZONE_EXCHANGE)

# Claves que la tabla PUEDE tener pero no necesita para jugar.  La de reposo
# entra aquí y no en REQUIRED_KEYS a propósito: si fuese obligatoria, las
# tablas ya grabadas pasarían a estar "incompletas" y la interfaz dejaría de
# permitir el brazo real hasta volver a calibrar.  Sin ella el brazo juega
# igual, solo que se queda donde termine la jugada.
OPTIONAL_KEYS: tuple[str, ...] = (config.ZONE_PARK,)

# Todo lo que tiene sentido grabar con examples/record_arm_positions.py.
RECORDABLE_KEYS: tuple[str, ...] = REQUIRED_KEYS + OPTIONAL_KEYS


class PositionsTableError(Exception):
    """La tabla de posiciones es inválida o está incompleta."""


class UnknownPositionError(PositionsTableError):
    """Se pidió una casilla/zona que no está en la tabla."""


@dataclass(frozen=True)
class JointAngles:
    """Valores de las dos articulaciones (en las unidades de la tabla)."""

    shoulder: float
    elbow: float


@dataclass(frozen=True)
class SquarePosition:
    """Posición de casilla; conserva los campos antiguos por compatibilidad."""

    approach: JointAngles   # campo legado, no se reproduce
    engage: JointAngles     # posición única de agarrar/soltar

    @property
    def position(self) -> JointAngles:
        """Ángulos de hombro/codo para situarse en la casilla."""
        return self.engage


class PositionsTable:
    """Carga y consulta de la tabla de posiciones pregrabadas."""

    def __init__(self, positions: dict[str, SquarePosition]):
        self._positions = positions
        self._validate()

    def _validate(self) -> None:
        missing = [sq for sq in ALL_SQUARES if sq not in self._positions]
        if missing:
            raise PositionsTableError(
                f"Faltan {len(missing)} casillas en la tabla de posiciones "
                f"(p. ej. {missing[:5]}). Deben estar las 64."
            )

    # ------------------------------------------------------------------ #
    # Consulta (esto es TODO lo que hace el brazo en tiempo de juego)
    # ------------------------------------------------------------------ #
    def get(self, key: str) -> SquarePosition:
        """Posición pregrabada de una casilla (``"e4"``) o zona (``"discard"``)."""
        try:
            return self._positions[key]
        except KeyError:
            raise UnknownPositionError(
                f"No hay posición grabada para {key!r}. "
                f"¿Falta en positions.json?"
            ) from None

    def has(self, key: str) -> bool:
        return key in self._positions

    # ------------------------------------------------------------------ #
    # Carga
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: Union[str, Path]) -> "PositionsTable":
        """Carga la tabla desde un archivo JSON."""
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PositionsTableError(
                f"No existe {path}. La tabla se genera calibrando el brazo real "
                "(ver examples/generate_positions_template.py)."
            ) from None
        except json.JSONDecodeError as exc:
            raise PositionsTableError(f"JSON inválido en {path}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "PositionsTable":
        if not isinstance(raw, dict):
            raise PositionsTableError("La tabla debe ser un objeto JSON.")
        positions: dict[str, SquarePosition] = {}
        for key, entry in raw.items():
            if _entry_is_calibrated(entry) is not True:
                raise PositionsTableError(f"Entrada inválida para {key!r}: faltan ángulos numéricos finitos.")
            try:
                if "shoulder" in entry or "elbow" in entry:
                    angles = JointAngles(**entry)
                    positions[key] = SquarePosition(approach=angles, engage=angles)
                else:
                    positions[key] = SquarePosition(
                        approach=JointAngles(**entry["approach"]),
                        engage=JointAngles(**entry["engage"]),
                    )
            except (KeyError, TypeError) as exc:
                raise PositionsTableError(
                    f"Entrada inválida para {key!r}: se esperan 'shoulder' y "
                    f"'elbow' (o el formato legado approach/engage) ({exc})."
                ) from exc
        logger.info("Tabla de posiciones cargada: %d entradas.", len(positions))
        return cls(positions)


# Valor centinela de las tablas falsas: irreal a propósito para que nunca se
# confunda con un ángulo/paso medido de verdad.
FAKE_VALUE: float = 9999.0


def make_fake_table(include_zones: bool = True) -> PositionsTable:
    """Tabla falsa para tests y demos (valores 9999.0, claramente irreales).

    Cada casilla recibe valores distintos derivados del centinela para poder
    verificar en los tests QUÉ casilla se consultó:
    ``shoulder = 9999.0 + índice``, ``elbow = -9999.0 - índice``.
    """
    positions: dict[str, SquarePosition] = {}
    keys = list(ALL_SQUARES)
    if include_zones:
        keys += [config.ZONE_DISCARD, config.ZONE_EXCHANGE, config.ZONE_PARK]
    for i, key in enumerate(keys):
        positions[key] = SquarePosition(
            approach=JointAngles(shoulder=FAKE_VALUE + i, elbow=-FAKE_VALUE - i),
            engage=JointAngles(shoulder=FAKE_VALUE + i, elbow=-FAKE_VALUE - i),
        )
    return PositionsTable(positions)


# --------------------------------------------------------------------------- #
# Inspección sin lanzar (para que la interfaz informe del estado de la tabla)
# --------------------------------------------------------------------------- #
@dataclass
class PositionsReport:
    """Estado de un ``positions.json`` sin cargarlo como tabla de juego.

    A diferencia de :meth:`PositionsTable.load`, NUNCA lanza: una tabla a medio
    calibrar es un estado normal mientras se graba el brazo, y la interfaz
    quiere mostrar "calibradas 12 de 66" en vez de un error.
    """

    path: str
    exists: bool = False
    error: Optional[str] = None            # JSON roto, etc.
    calibrated: list[str] = field(default_factory=list)   # entradas válidas
    missing: list[str] = field(default_factory=list)      # ausentes o con null
    invalid: list[str] = field(default_factory=list)      # mal formadas
    unknown: list[str] = field(default_factory=list)      # claves que no son casilla/zona
    has_park: bool = False                                # zona de reposo grabada

    @property
    def total(self) -> int:
        return len(REQUIRED_KEYS)

    @property
    def complete(self) -> bool:
        """``True`` si la tabla sirve para jugar (64 casillas + 2 zonas).

        La zona de reposo NO cuenta: sin ella se juega igual, solo que el brazo
        se queda donde termine la jugada (ver :attr:`has_park`).
        """
        return self.exists and self.error is None and not self.missing and not self.invalid

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "exists": self.exists,
            "error": self.error,
            "complete": self.complete,
            "calibrated": len(self.calibrated),
            "total": self.total,
            "missing": list(self.missing),
            "invalid": list(self.invalid),
            "unknown": list(self.unknown),
            "has_park": self.has_park,
        }


def _entry_is_calibrated(entry: object) -> Optional[bool]:
    """``True`` válida, ``False`` sin rellenar (nulls), ``None`` mal formada."""
    if not isinstance(entry, dict):
        return None
    values = []
    flat = "shoulder" in entry or "elbow" in entry
    for joints in ([entry] if flat else [entry.get("approach"), entry.get("engage")]):
        if not isinstance(joints, dict):
            return None
        for joint in ("shoulder", "elbow"):
            if joint not in joints:
                return None
            values.append(joints[joint])
    if all(v is None for v in values):
        return False
    if all(isinstance(v, (int, float)) and not isinstance(v, bool)
           and math.isfinite(v) for v in values):
        return True
    return None


def inspect_positions_file(path: Union[str, Path]) -> PositionsReport:
    """Informe de cobertura de un ``positions.json`` (real o plantilla con nulls)."""
    path = Path(path)
    report = PositionsReport(path=str(path))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.missing = list(REQUIRED_KEYS)
        return report
    except (json.JSONDecodeError, OSError) as exc:
        report.exists = True
        report.error = f"No se pudo leer {path.name}: {exc}"
        report.missing = list(REQUIRED_KEYS)
        return report
    report.exists = True
    if not isinstance(raw, dict):
        report.error = "El JSON debe ser un objeto con una entrada por casilla."
        report.missing = list(REQUIRED_KEYS)
        return report
    for key in REQUIRED_KEYS:
        if key not in raw:
            report.missing.append(key)
            continue
        state = _entry_is_calibrated(raw[key])
        if state is True:
            report.calibrated.append(key)
        elif state is False:
            report.missing.append(key)
        else:
            report.invalid.append(key)
    report.unknown = [k for k in raw if k not in RECORDABLE_KEYS]
    report.has_park = _entry_is_calibrated(raw.get(config.ZONE_PARK)) is True
    return report
