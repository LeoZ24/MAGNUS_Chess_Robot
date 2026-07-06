"""Nodo del brazo de MAGNUS: MoveResponse -> secuencia de movimientos pregrabados.

Recibe la ``MoveResponse`` del engine y la traduce a una secuencia de
primitivas (approach / engage / garra) usando **solo** la tabla de posiciones
pregrabadas — sin ningún cálculo geométrico (ver ``positions_table.py``).

La planificación (:meth:`ArmNode.plan`) está separada de la ejecución
(:meth:`ArmNode.execute`) para poder testear las secuencias sin hardware.

Secuencia básica de "levantar y colocar" (pick & place) de ``X`` a ``Y``::

    approach(X) -> engage(X) -> garra ON  -> approach(X)
 -> approach(Y) -> engage(Y) -> garra OFF -> approach(Y)

Los casos especiales encadenan varios pick & place, usando los metadatos de la
``MoveResponse`` (el brazo NO re-calcula nada de ajedrez):

    * Captura (incl. al paso): la pieza de ``captured_square`` va a la zona de
      descarte ANTES de mover la pieza que captura.
    * Enroque: primero el rey (``from``->``to``), luego la torre
      (``rook_from``->``rook_to``).
    * Promoción (provisional): el peón va de ``from_square`` a la zona de
      descarte y la pieza promovida va de la zona de intercambio a ``to_square``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .. import config
from ..core.messages import MoveResponse
from .backend import ArmBackend
from .positions_table import PositionsTable

logger = logging.getLogger("magnus.arm.node")


class ArmNodeError(Exception):
    """Error base del nodo del brazo."""


class IncompleteMoveError(ArmNodeError):
    """La MoveResponse no tiene los campos necesarios para planificar."""


@dataclass(frozen=True)
class ArmStep:
    """Una primitiva de la secuencia del brazo.

    ``action`` es una de:
        * ``"approach"`` -> mover a la sub-posición segura de ``target``
        * ``"engage"``   -> mover a la sub-posición de contacto de ``target``
        * ``"grip_on"``  -> activar la garra (agarrar)
        * ``"grip_off"`` -> desactivar la garra (soltar)
    """

    action: str
    target: Optional[str] = None    # casilla ("e4") o zona ("discard"/"exchange")

    def __str__(self) -> str:
        return f"{self.action}({self.target})" if self.target else self.action


def _pick_and_place(source: str, dest: str) -> list[ArmStep]:
    """Secuencia para llevar la pieza de ``source`` a ``dest``."""
    return [
        ArmStep("approach", source),
        ArmStep("engage", source),
        ArmStep("grip_on"),
        ArmStep("approach", source),   # subir antes de desplazarse (imán N52)
        ArmStep("approach", dest),
        ArmStep("engage", dest),
        ArmStep("grip_off"),
        ArmStep("approach", dest),     # retirarse a altura segura
    ]


class ArmNode:
    """Nodo que ejecuta jugadas físicas a partir de una ``MoveResponse``.

    Ejemplo (sin hardware)::

        from magnus.arm import ArmNode, FakeArmBackend, make_fake_table

        with ArmNode(backend=FakeArmBackend(), table=make_fake_table()) as arm:
            arm.execute(resp)          # resp: MoveResponse del engine
    """

    def __init__(self, backend: ArmBackend, table: PositionsTable):
        self._backend = backend
        self._table = table
        self._started = False

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    def start(self) -> "ArmNode":
        if not self._started:
            self._backend.connect()
            self._started = True
            logger.info("ArmNode listo.")
        return self

    def shutdown(self) -> None:
        if self._started:
            self._backend.disconnect()
            self._started = False
            logger.info("ArmNode detenido.")

    def __enter__(self) -> "ArmNode":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ------------------------------------------------------------------ #
    # Planificación (pura, testeable sin hardware)
    # ------------------------------------------------------------------ #
    def plan(self, resp: MoveResponse) -> list[ArmStep]:
        """Traduce la ``MoveResponse`` a la secuencia de primitivas del brazo."""
        if not resp.from_square or not resp.to_square:
            raise IncompleteMoveError(
                f"MoveResponse sin from/to: {resp.uci!r}. ¿Vino del engine?"
            )

        steps: list[ArmStep] = []

        # 1. Captura (incluida al paso): retirar la pieza capturada primero.
        #    En al paso, captured_square != to_square — por eso se usa siempre
        #    el campo captured_square y nunca to_square.
        if resp.is_capture:
            if not resp.captured_square:
                raise IncompleteMoveError(
                    f"Captura sin captured_square en {resp.uci!r}."
                )
            steps += _pick_and_place(resp.captured_square, config.ZONE_DISCARD)

        # 2. La jugada principal.
        if resp.promotion:
            # Provisional: el peón sale del tablero y la pieza promovida entra
            # desde la zona de intercambio (evita que el peón "viaje" de más).
            steps += _pick_and_place(resp.from_square, config.ZONE_DISCARD)
            steps += _pick_and_place(config.ZONE_EXCHANGE, resp.to_square)
        else:
            steps += _pick_and_place(resp.from_square, resp.to_square)

        # 3. Enroque: mover también la torre (después del rey).
        if resp.is_castling:
            if not resp.rook_from or not resp.rook_to:
                raise IncompleteMoveError(
                    f"Enroque sin rook_from/rook_to en {resp.uci!r}."
                )
            steps += _pick_and_place(resp.rook_from, resp.rook_to)

        # Verificar que todas las posiciones existen en la tabla ANTES de
        # ejecutar nada (mejor fallar en seco que a mitad de jugada).
        for step in steps:
            if step.target is not None:
                self._table.get(step.target)

        logger.info("Plan para %s: %d pasos.", resp.uci, len(steps))
        return steps

    # ------------------------------------------------------------------ #
    # Ejecución
    # ------------------------------------------------------------------ #
    def execute(self, resp: MoveResponse) -> list[ArmStep]:
        """Planifica y reproduce la jugada en el backend. Devuelve el plan."""
        if not self._started:
            self.start()
        steps = self.plan(resp)
        for step in steps:
            self._run_step(step)
        logger.info("Jugada %s ejecutada (%d pasos).", resp.uci, len(steps))
        return steps

    def _run_step(self, step: ArmStep) -> None:
        if step.action == "approach":
            angles = self._table.get(step.target).approach
            self._backend.move_to(angles.shoulder, angles.elbow)
        elif step.action == "engage":
            angles = self._table.get(step.target).engage
            self._backend.move_to(angles.shoulder, angles.elbow)
        elif step.action == "grip_on":
            self._backend.set_gripper(True)
        elif step.action == "grip_off":
            self._backend.set_gripper(False)
        else:
            raise ArmNodeError(f"Primitiva desconocida: {step.action!r}")
