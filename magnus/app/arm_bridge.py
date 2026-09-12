"""Supervisor del brazo para la aplicación: apagado, simulado o CyberPi.

Envuelve al :class:`~magnus.arm.ArmNode` con lo que una interfaz necesita y el
nodo no debe saber:

    * tres **modos** intercambiables en caliente (``off`` / ``simulated`` /
      ``cyberpi``), ver :data:`magnus.app.settings.ARM_MODES`;
    * la conexión con la CyberPi, el **referenciado** (``home``) y la ejecución
      de cada jugada en **hilos propios** (``connect()`` espera hasta 90 s a que
      la placa llame, referenciar tarda decenas de segundos y una jugada física
      varios más: nada de eso puede bloquear la visión);
    * **progreso paso a paso** y **parada** para mostrarlos en pantalla;
    * un informe de cobertura de ``positions.json`` para que el modo real solo
      se habilite cuando la tabla esté completa.

El supervisor NO calcula geometría ni decide jugadas: recibe la
``MoveResponse`` del engine y se la pasa al nodo, que busca en la tabla.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..arm.arm_node import ArmNode, ArmNodeError, ArmStep
from ..arm.backend import ArmBackend, ArmBackendError, CyberPiBackend, FakeArmBackend
from ..arm.positions_table import (
    PositionsReport,
    PositionsTable,
    PositionsTableError,
    inspect_positions_file,
    make_fake_table,
)
from ..core.messages import MoveResponse
from .settings import ARM_MODE_CYBERPI, ARM_MODE_OFF, ARM_MODE_SIMULATED, ARM_MODES

logger = logging.getLogger("magnus.app.arm_bridge")

# Pausa entre pasos en modo simulado, para que la secuencia se vea avanzar.
SIMULATED_STEP_DELAY_S = 0.45

# Textos legibles de cada primitiva (para la lista de pasos de la interfaz).
STEP_LABELS = {
    "approach": "Aproximar a {target}",
    "engage": "Bajar a {target}",
    "grip_on": "Activar garra",
    "grip_off": "Soltar pieza",
}

ZONE_LABELS = {"discard": "zona de descarte", "exchange": "zona de intercambio"}


class ArmStopped(ArmNodeError):
    """La ejecución se abortó desde la interfaz (botón de parada)."""


def describe_step(step: ArmStep) -> str:
    target = ZONE_LABELS.get(step.target or "", step.target or "")
    return STEP_LABELS.get(step.action, step.action).format(target=target)


class ArmSupervisor:
    """Gestiona el ``ArmNode`` en nombre de la interfaz (ver módulo)."""

    def __init__(
        self,
        mode: str = ARM_MODE_OFF,
        positions_path: str = "magnus/arm/positions.json",
        port: int = 5555,
        *,
        step_delay_s: float = SIMULATED_STEP_DELAY_S,
        auto_home: bool = True,
        backend_factory: Optional[Callable[[int], ArmBackend]] = None,
    ):
        """
        ``auto_home`` referencia el brazo nada más conectar (recomendado): los
        motores encoder no tienen cero absoluto, así que sin referencia la
        tabla de posiciones apunta a un sitio distinto en cada arranque.

        ``backend_factory(port)`` permite sustituir el ``CyberPiBackend`` real
        por uno falso en los tests del modo ``cyberpi``.
        """
        self._lock = threading.RLock()
        self._mode = ARM_MODE_OFF
        self._positions_path = positions_path
        self._port = port
        self._step_delay_s = step_delay_s
        self._auto_home = bool(auto_home)
        self._backend_factory = backend_factory or (lambda p: CyberPiBackend(port=p))

        self._node: Optional[ArmNode] = None
        self._planner = ArmNode(backend=FakeArmBackend(), table=make_fake_table())
        self._report: PositionsReport = inspect_positions_file(positions_path)
        self._status = "off"          # off | connecting | homing | ready | busy | error
        self._error: Optional[str] = None
        self._steps: list[str] = []
        self._step_index = -1
        self._executing_uci: Optional[str] = None
        self._last_uci: Optional[str] = None
        self._last_outcome: Optional[str] = None   # done | error | stopped
        self._stop_flag = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._generation = 0          # invalida hilos de conexión antiguos

        self.configure(mode=mode)

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    @property
    def is_busy(self) -> bool:
        return self.status == "busy"

    @property
    def auto_home(self) -> bool:
        return self._auto_home

    @property
    def report(self) -> PositionsReport:
        return self._report

    def refresh_report(self) -> PositionsReport:
        """Vuelve a leer ``positions.json`` (por si se acaba de calibrar)."""
        self._report = inspect_positions_file(self._positions_path)
        return self._report

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "modes": list(ARM_MODES),
                "status": self._status,
                "error": self._error,
                "port": self._port,
                "auto_home": self._auto_home,
                "can_home": self._can_home_locked(),
                "steps": list(self._steps),
                "step_index": self._step_index,
                "executing_uci": self._executing_uci,
                "last_uci": self._last_uci,
                "last_outcome": self._last_outcome,
                "positions": self._report.to_dict(),
            }

    # ------------------------------------------------------------------ #
    # Configuración de modo
    # ------------------------------------------------------------------ #
    def configure(
        self,
        mode: Optional[str] = None,
        positions_path: Optional[str] = None,
        port: Optional[int] = None,
        auto_home: Optional[bool] = None,
    ) -> None:
        """Cambia de modo (y/o de tabla/puerto).  Desconecta el modo anterior."""
        with self._lock:
            if mode is not None and mode not in ARM_MODES:
                raise ValueError(f"Modo de brazo desconocido: {mode!r}")
            if positions_path is not None:
                self._positions_path = positions_path
            if port is not None:
                self._port = int(port)
            if auto_home is not None:
                self._auto_home = bool(auto_home)
            new_mode = mode or self._mode
            self._teardown_locked()
            self._mode = new_mode
            self._error = None
            self._steps, self._step_index = [], -1
            self.refresh_report()

            if new_mode == ARM_MODE_OFF:
                self._status = "off"
            elif new_mode == ARM_MODE_SIMULATED:
                self._node = ArmNode(backend=FakeArmBackend(), table=make_fake_table())
                self._node.start()
                self._status = "ready"
            else:
                self._start_cyberpi_locked()

    def _teardown_locked(self) -> None:
        self._generation += 1
        self._stop_flag.set()
        node, self._node = self._node, None
        if node is not None:
            try:
                node.shutdown()
            except Exception as exc:  # pragma: no cover - defensivo
                logger.warning("Al desconectar el brazo: %s", exc)
        self._status = "off"

    def _start_cyberpi_locked(self) -> None:
        if not self._report.complete:
            self._status = "error"
            if not self._report.exists:
                self._error = (f"No existe {self._report.path}: hay que calibrar el "
                               f"brazo antes de usar la CyberPi.")
            elif self._report.error:
                self._error = self._report.error
            else:
                pending = len(self._report.missing) + len(self._report.invalid)
                self._error = (f"Tabla incompleta: faltan {pending} de "
                               f"{self._report.total} posiciones.")
            return
        try:
            table = PositionsTable.load(self._positions_path)
        except PositionsTableError as exc:
            self._status, self._error = "error", str(exc)
            return
        backend = self._backend_factory(self._port)
        node = ArmNode(backend=backend, table=table)
        self._node = node
        self._status = "connecting"
        self._stop_flag.clear()
        generation = self._generation
        thread = threading.Thread(target=self._connect, args=(node, generation),
                                  name="magnus-arm-connect", daemon=True)
        thread.start()

    def _connect(self, node: ArmNode, generation: int) -> None:
        try:
            node.start()
        except (ArmBackendError, OSError) as exc:
            with self._lock:
                if generation == self._generation:
                    self._status, self._error = "error", str(exc)
                    self._node = None
            return

        # Referenciado: sin él los ángulos de la tabla no significan nada
        # (el encoder arranca en 0 dondequiera que esté el brazo).  Va aquí,
        # en el hilo de conexión, porque tarda decenas de segundos.
        if self._auto_home:
            with self._lock:
                if generation != self._generation:
                    node.shutdown()
                    return
                self._status = "homing"
            try:
                node.home()
            except (ArmBackendError, OSError) as exc:
                with self._lock:
                    if generation == self._generation:
                        self._status = "error"
                        self._error = (f"No pude referenciar el brazo: {exc} "
                                       "Revisa los topes o desactiva el "
                                       "referenciado automático.")
                        self._node = None
                node.shutdown()
                return

        with self._lock:
            if generation != self._generation:      # ya se cambió de modo
                node.shutdown()
                return
            self._status = "ready"
            logger.info("Brazo CyberPi listo.")

    # ------------------------------------------------------------------ #
    # Referenciado (home)
    # ------------------------------------------------------------------ #
    def _can_home_locked(self) -> bool:
        return self._node is not None and self._status == "ready"

    def home(self, on_done: Optional[Callable[[bool, Optional[str]], None]] = None) -> bool:
        """Vuelve a referenciar el brazo a mano.  ``False`` si no se puede ahora.

        Sirve para recuperar el cero sin reiniciar la partida: si alguien
        empuja el brazo o un motor pierde pasos, los ángulos de la tabla dejan
        de apuntar a donde deben y esto los vuelve a alinear.

        Corre en su propio hilo (referenciar tarda decenas de segundos).
        ``on_done(ok, error)`` se llama desde ese hilo al terminar.
        """
        with self._lock:
            if not self._can_home_locked():
                return False
            node = self._node
            assert node is not None
            self._status = "homing"
            self._error = None
            generation = self._generation
        thread = threading.Thread(
            target=self._home, args=(node, generation, on_done),
            name="magnus-arm-home", daemon=True,
        )
        thread.start()
        return True

    def _home(self, node: ArmNode, generation: int, on_done) -> None:
        ok, error = True, None
        try:
            node.home()
        except (ArmNodeError, ArmBackendError, OSError) as exc:
            ok, error = False, str(exc)
            logger.error("Fallo al referenciar el brazo: %s", exc)
        with self._lock:
            if generation == self._generation and self._node is node:
                self._error = error
                # Un referenciado fallido deja el cero en un estado
                # desconocido: mejor marcarlo en rojo que dejar jugar.
                self._status = "ready" if ok else "error"
        if on_done:
            on_done(ok, error)

    # ------------------------------------------------------------------ #
    # Planificación y ejecución
    # ------------------------------------------------------------------ #
    def preview(self, resp: MoveResponse) -> list[str]:
        """La secuencia que ejecutaría (o ejecutará) el brazo, como texto.

        En modo apagado se planifica igualmente con la tabla falsa: sirve
        para enseñar la secuencia aunque no haya brazo.
        """
        with self._lock:
            node = self._node if (self._node is not None and self._status
                                  in ("ready", "busy")) else self._planner
        try:
            return [describe_step(s) for s in node.plan(resp)]
        except ArmNodeError as exc:
            logger.warning("No se pudo planificar %s: %s", resp.uci, exc)
            return []

    def execute(
        self,
        resp: MoveResponse,
        on_done: Optional[Callable[[bool, Optional[str]], None]] = None,
    ) -> bool:
        """Ejecuta la jugada en un hilo.  ``False`` si el brazo no está listo.

        ``on_done(ok, error)`` se llama desde el hilo del brazo al terminar.
        """
        with self._lock:
            if self._status != "ready" or self._node is None:
                return False
            node = self._node
            try:
                plan = node.plan(resp)
            except ArmNodeError as exc:
                self._error = str(exc)
                self._last_outcome = "error"
                logger.error("Plan imposible para %s: %s", resp.uci, exc)
                if on_done:
                    on_done(False, str(exc))
                return False
            self._status = "busy"
            self._error = None
            self._steps = [describe_step(s) for s in plan]
            self._step_index = -1
            self._executing_uci = resp.uci
            self._stop_flag.clear()
            self._worker = threading.Thread(
                target=self._execute, args=(node, resp, on_done),
                name="magnus-arm-exec", daemon=True,
            )
            self._worker.start()
            return True

    def _execute(self, node: ArmNode, resp: MoveResponse, on_done) -> None:
        def on_step(index: int, step: ArmStep) -> None:
            if self._stop_flag.is_set():
                raise ArmStopped("Ejecución detenida desde la interfaz.")
            with self._lock:
                self._step_index = index
            if self._mode == ARM_MODE_SIMULATED and self._step_delay_s > 0:
                time.sleep(self._step_delay_s)

        ok, error = True, None
        try:
            node.execute(resp, on_step=on_step)
            outcome = "done"
        except ArmStopped as exc:
            ok, error, outcome = False, str(exc), "stopped"
        except (ArmNodeError, ArmBackendError, OSError) as exc:
            ok, error, outcome = False, str(exc), "error"
            logger.error("Fallo del brazo en %s: %s", resp.uci, exc)
        with self._lock:
            self._last_uci = resp.uci
            self._last_outcome = outcome
            self._executing_uci = None
            self._error = error
            if outcome == "done":
                self._step_index = len(self._steps)
            # Tras un error de comunicación el brazo real queda en estado
            # desconocido: se exige reconfigurar (volver a elegir el modo).
            if outcome == "error" and self._mode == ARM_MODE_CYBERPI:
                self._status = "error"
            elif self._node is node:
                self._status = "ready"
        if on_done:
            on_done(ok, error)

    def stop(self) -> None:
        """Parada: aborta la secuencia en curso y frena los motores si es real."""
        self._stop_flag.set()
        with self._lock:
            node = self._node
        backend = getattr(node, "_backend", None) if node is not None else None
        if isinstance(backend, CyberPiBackend):
            try:
                backend.stop()
            except (ArmBackendError, OSError) as exc:
                logger.error("La parada de emergencia falló: %s", exc)

    def shutdown(self) -> None:
        with self._lock:
            self._teardown_locked()
