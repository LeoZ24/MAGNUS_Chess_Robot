"""Tabla de posiciones pregrabadas del brazo (teach & playback).

Esta es la pieza central de la decisión de arquitectura del brazo: los ángulos
de hombro/codo para cada casilla se graban **una sola vez** (calibración
manual) y en tiempo de juego solo se **consultan** — nunca se calculan.

Formato del JSON (``positions.json``)::

    {
      "e4": {
        "approach": {"shoulder": 32.5, "elbow": 110.0},
        "engage":   {"shoulder": 35.0, "elbow": 118.0}
      },
      ...
      "discard":  { "approach": {...}, "engage": {...} },
      "exchange": { "approach": {...}, "engage": {...} }
    }

    * ``approach``: el brazo está sobre la casilla, a altura segura (el imán
      N52 no influye en piezas vecinas)
    * ``engage``: el brazo está bajado, en posición de agarrar/soltar

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
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from .. import config

logger = logging.getLogger("magnus.arm.positions_table")

_FILES = "abcdefgh"
ALL_SQUARES: tuple[str, ...] = tuple(
    f"{f}{r}" for r in range(1, config.BOARD_SQUARES + 1) for f in _FILES
)


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
    """Las dos sub-posiciones grabadas para una casilla o zona."""

    approach: JointAngles   # sobre la casilla, a altura segura
    engage: JointAngles     # bajado, en posición de agarrar/soltar


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
        positions: dict[str, SquarePosition] = {}
        for key, entry in raw.items():
            try:
                positions[key] = SquarePosition(
                    approach=JointAngles(**entry["approach"]),
                    engage=JointAngles(**entry["engage"]),
                )
            except (KeyError, TypeError) as exc:
                raise PositionsTableError(
                    f"Entrada inválida para {key!r}: se esperan sub-posiciones "
                    f"'approach' y 'engage' con 'shoulder' y 'elbow' ({exc})."
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
        keys += [config.ZONE_DISCARD, config.ZONE_EXCHANGE]
    for i, key in enumerate(keys):
        positions[key] = SquarePosition(
            approach=JointAngles(shoulder=FAKE_VALUE + i, elbow=-FAKE_VALUE - i),
            engage=JointAngles(shoulder=FAKE_VALUE + i + 0.5, elbow=-FAKE_VALUE - i - 0.5),
        )
    return PositionsTable(positions)
