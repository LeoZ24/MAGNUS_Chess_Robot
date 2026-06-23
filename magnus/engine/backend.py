"""Backends de motor de ajedrez para MAGNUS.

El nodo del engine no habla directamente con Stockfish: lo hace a través de un
``EngineBackend``.  Esto mantiene el sistema modular —puedes cambiar Stockfish
por cualquier otro motor UCI (Lc0, Komodo, ...) o incluso por un motor casero/
neuronal— sin tocar el resto del código.

``UCIEngineBackend`` es la implementación por defecto: arranca cualquier binario
UCI con ``python-chess`` y le aplica la ``EngineConfig`` correspondiente al nivel
de dificultad.
"""

from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import chess
import chess.engine

from .difficulty import EngineConfig

logger = logging.getLogger("magnus.engine.backend")

# Rutas habituales donde suele estar el binario de Stockfish.
_COMMON_ENGINE_PATHS = (
    "/usr/games/stockfish",
    "/usr/local/bin/stockfish",
    "/usr/bin/stockfish",
    "/opt/homebrew/bin/stockfish",
)


def find_engine_binary(explicit: Optional[str] = None) -> str:
    """Localiza el binario del motor.

    Orden de búsqueda: argumento explícito -> variable de entorno
    ``MAGNUS_ENGINE_PATH`` -> ``stockfish`` en el PATH -> rutas habituales.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get("MAGNUS_ENGINE_PATH")
    if env_path:
        candidates.append(env_path)
    which = shutil.which("stockfish")
    if which:
        candidates.append(which)
    candidates.extend(_COMMON_ENGINE_PATHS)

    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    raise FileNotFoundError(
        "No se encontró el binario del motor de ajedrez. Instala Stockfish "
        "(p. ej. `apt install stockfish`) o indica la ruta con el argumento "
        "`engine_path` / la variable de entorno MAGNUS_ENGINE_PATH."
    )


@dataclass
class EnginePlayResult:
    """Resultado neutral devuelto por un backend tras elegir una jugada."""

    move: chess.Move
    evaluation_cp: Optional[int] = None   # centipeones desde el lado a mover
    mate_in: Optional[int] = None
    depth: Optional[int] = None


class EngineBackend(ABC):
    """Interfaz que debe implementar cualquier motor para MAGNUS."""

    @abstractmethod
    def start(self) -> None:
        """Arranca el motor (proceso/recurso)."""

    @abstractmethod
    def select_move(self, board: chess.Board, config: EngineConfig) -> EnginePlayResult:
        """Elige la mejor jugada para ``board`` según ``config``."""

    @abstractmethod
    def quit(self) -> None:
        """Detiene el motor y libera recursos."""

    # Azúcar para usarlo como context manager.
    def __enter__(self) -> "EngineBackend":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.quit()


class UCIEngineBackend(EngineBackend):
    """Backend para cualquier motor que hable el protocolo UCI (Stockfish, ...)."""

    def __init__(self, engine_path: Optional[str] = None):
        self.engine_path = find_engine_binary(engine_path)
        self._engine: Optional[chess.engine.SimpleEngine] = None
        # Recuerda las opciones ya aplicadas para no reconfigurar en cada jugada.
        self._applied: dict = {}

    @property
    def is_running(self) -> bool:
        return self._engine is not None

    def start(self) -> None:
        if self._engine is not None:
            return
        logger.info("Arrancando motor UCI: %s", self.engine_path)
        self._engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
        self._applied = {}

    def _ensure_started(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self.start()
        assert self._engine is not None
        return self._engine

    def _apply_options(self, engine: chess.engine.SimpleEngine, config: EngineConfig) -> None:
        """Traduce la EngineConfig a opciones UCI y las aplica (solo si cambiaron)."""
        options: dict = {"Threads": config.threads, "Hash": config.hash_mb}
        if config.skill_level is not None:
            options["Skill Level"] = config.skill_level
        options["UCI_LimitStrength"] = config.limit_strength
        if config.limit_strength and config.elo is not None:
            options["UCI_Elo"] = config.elo

        if options == self._applied:
            return

        for key, value in options.items():
            # Algunos motores no soportan todas las opciones; las ignoramos
            # con un aviso en vez de fallar.
            if key not in engine.options:
                logger.debug("El motor no soporta la opción %r; se omite.", key)
                continue
            try:
                engine.configure({key: value})
            except chess.engine.EngineError as exc:
                logger.warning("No se pudo aplicar %r=%r: %s", key, value, exc)
        self._applied = options

    @staticmethod
    def _build_limit(config: EngineConfig) -> chess.engine.Limit:
        return chess.engine.Limit(
            time=config.movetime,
            depth=config.depth,
            nodes=config.nodes,
        )

    def select_move(self, board: chess.Board, config: EngineConfig) -> EnginePlayResult:
        engine = self._ensure_started()
        self._apply_options(engine, config)
        limit = self._build_limit(config)

        result = engine.play(board, limit, info=chess.engine.INFO_SCORE)
        if result.move is None:
            raise chess.engine.EngineError("El motor no devolvió ninguna jugada.")

        evaluation_cp: Optional[int] = None
        mate_in: Optional[int] = None
        depth: Optional[int] = None
        info = result.info or {}
        score = info.get("score")
        if score is not None:
            pov = score.pov(board.turn)
            evaluation_cp = pov.score()       # None si es mate
            mate_in = pov.mate()
        depth = info.get("depth")

        return EnginePlayResult(
            move=result.move,
            evaluation_cp=evaluation_cp,
            mate_in=mate_in,
            depth=depth,
        )

    def quit(self) -> None:
        if self._engine is not None:
            logger.info("Deteniendo motor UCI.")
            try:
                self._engine.quit()
            finally:
                self._engine = None
                self._applied = {}
