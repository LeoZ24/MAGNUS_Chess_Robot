"""Backends del brazo robótico de MAGNUS.

El nodo del brazo no habla directamente con el hardware: lo hace a través de un
``ArmBackend``.  Igual que en ``magnus/engine/``, esto permite:

    * ``FakeArmBackend`` -> tests y demos sin hardware (registra los comandos)
    * ``CyberPiBackend`` -> el hardware real (CyberPi / kit mBot2) — **stub**,
      pendiente de confirmar el protocolo de comunicación

Recordatorio de arquitectura: el brazo NO calcula cinemática inversa.  Los
ángulos de hombro/codo vienen siempre de la tabla de posiciones pregrabadas
(``positions_table.py``); el backend solo los ejecuta.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("magnus.arm.backend")


class ArmBackendError(Exception):
    """Error de comunicación o ejecución en el backend del brazo."""


class ArmBackend(ABC):
    """Interfaz que debe implementar cualquier controlador físico del brazo."""

    @abstractmethod
    def connect(self) -> None:
        """Establece la conexión con el hardware."""

    @abstractmethod
    def disconnect(self) -> None:
        """Cierra la conexión y libera recursos."""

    @abstractmethod
    def move_to(self, shoulder: float, elbow: float) -> None:
        """Mueve las articulaciones a los valores dados (de la tabla, sin IK).

        Las unidades (grados o pasos de encoder) son las mismas en las que se
        grabó la tabla de posiciones — el backend no las interpreta.
        """

    @abstractmethod
    def set_gripper(self, engaged: bool) -> None:
        """Activa (agarrar) o desactiva (soltar) la garra (servo 3)."""

    # Azúcar para usarlo como context manager.
    def __enter__(self) -> "ArmBackend":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()


class FakeArmBackend(ArmBackend):
    """Backend falso para tests y demos: registra los comandos, no mueve nada.

    Cada comando queda en :attr:`commands` como una tupla:

        ("connect",) / ("disconnect",)
        ("move_to", shoulder, elbow)
        ("gripper", True|False)
    """

    def __init__(self):
        self.commands: list[tuple] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        self.commands.append(("connect",))
        logger.debug("FakeArmBackend conectado.")

    def disconnect(self) -> None:
        self.connected = False
        self.commands.append(("disconnect",))
        logger.debug("FakeArmBackend desconectado.")

    def move_to(self, shoulder: float, elbow: float) -> None:
        if not self.connected:
            raise ArmBackendError("Backend no conectado (llama a connect()).")
        self.commands.append(("move_to", shoulder, elbow))

    def set_gripper(self, engaged: bool) -> None:
        if not self.connected:
            raise ArmBackendError("Backend no conectado (llama a connect()).")
        self.commands.append(("gripper", engaged))

    def clear(self) -> None:
        """Limpia el registro de comandos (útil entre casos de test)."""
        self.commands.clear()


class CyberPiBackend(ArmBackend):
    """Backend del hardware real: CyberPi (kit mBot2). **AÚN NO IMPLEMENTADO.**

    Hardware previsto:
        * Motor 1 (Encoder) -> hombro
        * Motor 2 (Encoder) -> codo
        * Servo 3           -> garra

    Decisiones pendientes que BLOQUEAN esta implementación (no asumir):

    # TODO(confirmar): protocolo de comunicación con CyberPi — ¿serial USB,
    #   librería específica de mBot2 (p. ej. makeblock), u otra cosa?
    # TODO(confirmar): mecanismo exacto del servo de garra — ¿acerca/aleja el
    #   imán N52 de la pieza, es una pinza mecánica, o controla el eje vertical
    #   de bajar/subir? La semántica de set_gripper() depende de esto.
    """

    _MSG = (
        "CyberPiBackend aún no está implementado: falta confirmar el protocolo "
        "de comunicación con la CyberPi y el mecanismo exacto del servo de "
        "garra. Usa FakeArmBackend para tests y demos."
    )

    def __init__(self, port: str | None = None):
        self.port = port

    def connect(self) -> None:
        raise NotImplementedError(self._MSG)

    def disconnect(self) -> None:
        raise NotImplementedError(self._MSG)

    def move_to(self, shoulder: float, elbow: float) -> None:
        raise NotImplementedError(self._MSG)

    def set_gripper(self, engaged: bool) -> None:
        raise NotImplementedError(self._MSG)
