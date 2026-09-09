"""``MagnusController``: el corazón de la aplicación de juego.

Ejecuta el bucle de visión en un hilo propio y une los nodos:

    cámara → detección ArUco → placement → GameTracker → (engine) → voz / brazo

y expone dos cosas a la interfaz, sea cual sea:

    * :meth:`snapshot` — un ``dict`` serializable a JSON con TODO el estado
      que la pantalla necesita (tablero, fase, evaluación, pilotos, brazo...).
      Se reconstruye al final de cada iteración, así la interfaz nunca ve un
      estado a medias.
    * :meth:`command` — los botones.  Los comandos se encolan y los aplica el
      propio hilo de visión (un solo hilo toca el estado de la partida: no hay
      carreras entre el servidor HTTP y la detección).

El bucle está en :meth:`step` (una iteración) para poder testearlo sin hilos
ni servidor: ``controller.step()`` con una cámara sintética y un engine falso.
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import chess
import cv2
import numpy as np

from .. import config
from ..core.messages import MoveResponse
from ..vision.aruco_detector import ArucoDetector, DetectionLatch, MarkerRole, corners_by_id, split_by_role
from ..vision.board_pose import BoardPose, BoardPoseError
from ..vision.fen_builder import placement_to_fen_field
from ..vision.game_state import GameTracker
from ..vision.vision_node import CameraBackend, CameraError
from .arm_bridge import ArmSupervisor
from .overlays import draw_camera_overlays
from .session import (
    STABLE_FRAMES,
    SYNTH_BLACK_LINE,
    SYNTH_WHITE_LINE,
    EngineWorker,
    GameSession,
    SyntheticCamera,
    apply_analysis,
    difficulty_catalog,
    eval_text,
    find_orientation,
    read_placement,
)
from .settings import ARM_MODE_OFF, ARM_MODE_SIMULATED, AppSettings

logger = logging.getLogger("magnus.app.controller")

# Ancho máximo del frame que se envía al navegador (el original puede ser 1080p).
STREAM_MAX_WIDTH = 960
STREAM_JPEG_QUALITY = 72
# En modo sintético: frames entre medias-jugadas del "humano" simulado.
SYNTH_PERIOD = 40
# Un placement ilegal debe persistir este número de frames antes de avisar.
ILLEGAL_WARN_FRAMES = STABLE_FRAMES * 4
# Ritmo del bucle cuando la cámara es sintética (no hay que esperar a un sensor).
SYNTH_FRAME_INTERVAL_S = 1.0 / 30.0

PIECES_IN_START_POSITION = 32


class MagnusController:
    """Orquesta visión, engine, voz y brazo; ver el módulo para el diseño."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        settings_path: Optional[str] = None,
        synthetic: bool = False,
        camera: Optional[CameraBackend] = None,
        camera_factory: Optional[Callable[[int], CameraBackend]] = None,
        engine_enabled: bool = True,
        engine_factory: Optional[Callable[[str], Any]] = None,
        voice_enabled: bool = True,
        voice_backend: Any = None,
        arm: Optional[ArmSupervisor] = None,
        arm_step_delay_s: Optional[float] = None,
    ):
        """
        Parámetros:
            settings: ajustes iniciales (se actualizan y guardan con los comandos).
            settings_path: dónde guardar los ajustes (``None`` = no guardar).
            synthetic: sin cámara; tablero simulado que juega solo.
            camera: cámara ya construida (tests); si no, se usa ``camera_factory``.
            camera_factory: ``índice -> CameraBackend`` (por defecto OpenCV).
            engine_enabled / engine_factory: Stockfish (o un nodo falso en tests).
            voice_enabled / voice_backend: voz (o backend falso en tests).
            arm: supervisor del brazo ya construido (tests).
        """
        self.settings = settings
        self._settings_path = Path(settings_path) if settings_path else None
        self.synthetic = synthetic
        self._engine_enabled = engine_enabled
        self._engine_factory = engine_factory
        self._voice_enabled = voice_enabled
        self._voice_backend = voice_backend

        # --- componentes ---
        self._camera_factory = camera_factory or self._default_camera_factory
        self.synth: Optional[SyntheticCamera] = None
        if synthetic:
            self.synth = SyntheticCamera()
            self.camera: Optional[CameraBackend] = self.synth
        else:
            self.camera = camera
        self.camera_error: Optional[str] = None
        self.detector = ArucoDetector()
        self.latch = DetectionLatch()
        self.engine: Optional[EngineWorker] = None
        self.voice = None
        self.arm = arm
        self._arm_step_delay_s = arm_step_delay_s

        # --- estado de la partida (solo lo toca el hilo de visión) ---
        self.session = GameSession(self._robot_color())
        self.in_game = False
        self.game_id = 0
        self.pose: Optional[BoardPose] = None
        self.pose_error: Optional[str] = None
        self.board_turns = int(settings.board_turns)
        self.message: Optional[str] = None
        self._placement: dict[str, str] = {}
        self._prev_placement: dict[str, str] = {}
        self._pending_squares: list[str] = []
        self._off_board = 0
        self._corners_found = 0
        self._corners_remembered = 0
        self._pieces_confirmed = 0
        self._pieces_pending = 0
        self._arm_seen = False
        self._stable = 0
        self._fps = 0.0
        self._frame_idx = 0
        self._last_activity = time.monotonic()
        self._arm_done_uci: Optional[str] = None
        self._arm_pending = False
        self._synth_white = iter(SYNTH_WHITE_LINE)
        self._synth_black = iter(SYNTH_BLACK_LINE)
        self._synth_rng = random.Random(7)
        self._synth_deadline = 0

        # --- comunicación con otros hilos ---
        self._commands: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._events: deque = deque(maxlen=12)
        self._event_id = 0
        self._snapshot: dict = {}
        self._snapshot_lock = threading.Lock()
        self._seq = 0
        self._jpeg: Optional[bytes] = None
        self._jpeg_seq = 0
        self._jpeg_cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_camera_factory(index: int) -> CameraBackend:
        from ..vision.vision_node import OpenCVCameraBackend

        return OpenCVCameraBackend(index, warmup_s=5.0)

    def _robot_color(self) -> chess.Color:
        return chess.WHITE if self.settings.robot_side == "white" else chess.BLACK

    def start(self) -> "MagnusController":
        """Abre la cámara, arranca engine/voz/brazo y el hilo de visión."""
        if self._started:
            return self
        self._open_camera()
        if self._engine_enabled:
            self.engine = EngineWorker(self.settings.difficulty,
                                       node_factory=self._engine_factory).start()
        if self._voice_enabled:
            self._start_voice()
        if self.arm is None:
            kwargs = {}
            if self._arm_step_delay_s is not None:
                kwargs["step_delay_s"] = self._arm_step_delay_s
            self.arm = ArmSupervisor(
                mode=self.settings.arm_mode,
                positions_path=self.settings.positions_path,
                port=self.settings.arm_port,
                **kwargs,
            )
        self._started = True
        self._publish_snapshot()
        self._thread = threading.Thread(target=self._run, name="magnus-vision", daemon=True)
        self._thread.start()
        logger.info("MagnusController en marcha.")
        return self

    def start_without_thread(self) -> "MagnusController":
        """Como :meth:`start` pero sin hilo: el llamante invoca :meth:`step` (tests)."""
        if self._started:
            return self
        self._open_camera()
        if self._engine_enabled:
            self.engine = EngineWorker(self.settings.difficulty,
                                       node_factory=self._engine_factory).start()
        if self._voice_enabled:
            self._start_voice()
        if self.arm is None:
            self.arm = ArmSupervisor(
                mode=self.settings.arm_mode,
                positions_path=self.settings.positions_path,
                port=self.settings.arm_port,
                step_delay_s=self._arm_step_delay_s or 0.0,
            )
        self._started = True
        self._publish_snapshot()
        return self

    def _start_voice(self) -> None:
        from ..voice import VoiceNode
        from ..voice.backend import default_backend

        try:
            backend = self._voice_backend or default_backend()
            self.voice = VoiceNode(
                backend=backend,
                robot_side=self.settings.robot_side,
                muted=self.settings.voice_muted,
            ).start()
        except Exception as exc:  # la voz jamás tumba la partida
            logger.warning("Voz no disponible: %s", exc)
            self.voice = None

    def _open_camera(self) -> None:
        if self.camera is None:
            try:
                self.camera = self._camera_factory(self.settings.camera_index)
            except Exception as exc:
                self.camera, self.camera_error = None, str(exc)
                return
        try:
            self.camera.open()
            self.camera_error = None
        except CameraError as exc:
            self.camera_error = str(exc)
            logger.error("Cámara: %s", exc)

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception:  # pragma: no cover - defensivo
                pass
        if self.engine is not None:
            self.engine.stop()
        if self.voice is not None:
            self.voice.shutdown(wait=False)
        if self.arm is not None:
            self.arm.shutdown()
        self._started = False
        logger.info("MagnusController detenido.")

    def __enter__(self) -> "MagnusController":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ------------------------------------------------------------------ #
    # Comandos (desde cualquier hilo)
    # ------------------------------------------------------------------ #
    COMMANDS = (
        "start_game", "stop_game", "set_difficulty", "set_robot_side", "set_voice",
        "reset_detection", "rotate_mapping", "flip_view", "set_camera", "set_arm",
        "arm_execute", "arm_stop", "say", "synthetic_move",
    )

    def command(self, name: str, params: Optional[dict] = None) -> dict:
        """Encola un comando de la interfaz; se aplica en la siguiente iteración."""
        if name not in self.COMMANDS:
            return {"ok": False, "error": f"Comando desconocido: {name!r}"}
        self._commands.put((name, dict(params or {})))
        return {"ok": True}

    def _process_commands(self) -> None:
        while True:
            try:
                name, params = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                getattr(self, f"_cmd_{name}")(**params)
            except Exception as exc:
                logger.exception("Comando %s falló", name)
                self._emit("error", f"{name}: {exc}")

    def _emit(self, kind: str, text: str) -> None:
        """Evento efímero para la interfaz (tostadas)."""
        self._event_id += 1
        self._events.append({"id": self._event_id, "kind": kind, "text": text,
                             "time": time.time()})

    def _save_settings(self, **changes: Any) -> None:
        self.settings = self.settings.update(**changes)
        if self._settings_path is not None:
            try:
                self.settings.save(self._settings_path)
            except OSError as exc:
                logger.warning("No se pudieron guardar los ajustes: %s", exc)

    # -- partida ---------------------------------------------------------- #
    def _cmd_start_game(self) -> None:
        if self.synth is not None:
            self.synth.reset()
            self._synth_white = iter(SYNTH_WHITE_LINE)
            self._synth_black = iter(SYNTH_BLACK_LINE)
            self._synth_deadline = self._frame_idx + SYNTH_PERIOD
            self.latch.reset()
            self.pose = None
            self._begin_game(turns=0)
            return
        initial = GameTracker().placement()
        by_role = split_by_role(self.latch.confirmed)
        turns = (find_orientation(self.pose, by_role[MarkerRole.PIECE], initial)
                 if self.pose is not None else None)
        if turns is None:
            self.message = (f"Coloca la posición inicial para empezar "
                            f"({len(self._placement)}/{PIECES_IN_START_POSITION} piezas detectadas)")
            self._emit("warn", self.message)
            return
        if turns:
            self.board_turns = (self.board_turns + turns) % 4
            self.pose = self.pose.rotated(turns)
            self._save_settings(board_turns=self.board_turns)
        self._begin_game(turns)

    def _begin_game(self, turns: int) -> None:
        self.session = GameSession(self._robot_color())
        self.in_game = True
        self.game_id += 1
        self._arm_done_uci = None
        self._arm_pending = False
        self._last_activity = time.monotonic()
        self.message = (f"Orientación corregida sola ({turns * 90}°)" if turns else None)
        if self.voice is not None:
            self.voice.commentator.robot_side = self.settings.robot_side
            self.voice.new_game()
            self.voice.greet()
        self._emit("info", "Partida iniciada. ¡Suerte!")

    def _cmd_stop_game(self) -> None:
        self.in_game = False
        self.message = None
        self._arm_pending = False
        if self.arm is not None and self.arm.is_busy:
            self.arm.stop()
        self._emit("info", "Partida detenida: modo observación")

    def _cmd_set_difficulty(self, level: str) -> None:
        name = self.engine.set_difficulty(level) if self.engine else str(level).upper()
        self._save_settings(difficulty=name)
        self._emit("info", f"Dificultad: {name}")

    def _cmd_set_robot_side(self, side: str) -> None:
        self._save_settings(robot_side=side)
        if self.in_game:
            self._emit("info", "El color se aplicará en la próxima partida")
        else:
            self.session = GameSession(self._robot_color())

    def _cmd_set_voice(self, muted: Optional[bool] = None,
                       announce_human_moves: Optional[bool] = None,
                       idle_prompt_s: Optional[float] = None) -> None:
        changes: dict[str, Any] = {}
        if muted is not None:
            changes["voice_muted"] = bool(muted)
            if self.voice is not None:
                self.voice.set_muted(bool(muted))
        if announce_human_moves is not None:
            changes["announce_human_moves"] = bool(announce_human_moves)
        if idle_prompt_s is not None:
            changes["idle_prompt_s"] = max(0.0, float(idle_prompt_s))
        if changes:
            self._save_settings(**changes)

    def _cmd_say(self, text: str) -> None:
        if self.voice is not None and text.strip():
            self.voice.say(text.strip())

    # -- visión ----------------------------------------------------------- #
    def _cmd_reset_detection(self) -> None:
        self.latch.reset()
        self.pose, self.pose_error = None, None
        self._emit("info", "Memoria de detección reiniciada")

    def _cmd_rotate_mapping(self) -> None:
        self.board_turns = (self.board_turns + 1) % 4
        if self.pose is not None:
            self.pose = self.pose.rotated(1)
        self._save_settings(board_turns=self.board_turns)
        self.message = f"Mapeo del tablero girado {self.board_turns * 90}°"

    def _cmd_flip_view(self, value: Optional[bool] = None) -> None:
        flip = (not self.settings.flip_view) if value is None else bool(value)
        self._save_settings(flip_view=flip)

    def _cmd_set_camera(self, index: int) -> None:
        if self.synth is not None:
            self._emit("warn", "En modo sintético no hay cámara que cambiar")
            return
        index = int(index)
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception:  # pragma: no cover - defensivo
                pass
        self.camera = None
        self._save_settings(camera_index=index)
        self._open_camera()
        self.latch.reset()
        self.pose, self.pose_error = None, None
        if self.camera_error:
            self._emit("error", self.camera_error)
        else:
            self._emit("info", f"Cámara {index} abierta")

    # -- brazo ------------------------------------------------------------ #
    def _cmd_set_arm(self, mode: Optional[str] = None,
                     auto_execute: Optional[bool] = None,
                     port: Optional[int] = None,
                     positions_path: Optional[str] = None) -> None:
        changes: dict[str, Any] = {}
        if auto_execute is not None:
            changes["arm_auto_execute"] = bool(auto_execute)
        if port is not None:
            changes["arm_port"] = int(port)
        if positions_path:
            changes["positions_path"] = str(positions_path)
        if mode is not None:
            changes["arm_mode"] = mode
        if changes:
            self._save_settings(**changes)
        if self.arm is not None and (mode is not None or port is not None or positions_path):
            self.arm.configure(mode=self.settings.arm_mode,
                               positions_path=self.settings.positions_path,
                               port=self.settings.arm_port)
            self._arm_pending = False
            self._emit("info", f"Brazo: modo {self.settings.arm_mode}")

    def _cmd_arm_execute(self) -> None:
        planned = self.session.planned
        if not (self.in_game and planned and self._arm_pending):
            self._emit("warn", "No hay jugada pendiente para el brazo")
            return
        self._arm_start(planned)

    def _cmd_arm_stop(self) -> None:
        if self.arm is not None:
            self.arm.stop()
            self._emit("warn", "PARADA del brazo")

    def _arm_start(self, planned: MoveResponse) -> None:
        assert self.arm is not None
        uci = planned.uci
        expected_fen = self.session.tracker.fen()

        def on_done(ok: bool, error: Optional[str]) -> None:
            # Se llama desde el hilo del brazo: solo encola, el hilo de visión
            # aplica el efecto (así nadie más toca el estado de la partida).
            self._commands.put(("_arm_finished", {"uci": uci, "ok": ok, "error": error,
                                                  "fen": expected_fen}))

        if self.arm.execute(planned, on_done=on_done):
            self._arm_pending = False
            self._arm_done_uci = uci
            self._emit("info", f"Brazo ejecutando {planned.san}")
        else:
            self._emit("error", self.arm.snapshot().get("error") or "El brazo no está listo")

    def _cmd__arm_finished(self, uci: str, ok: bool, error: Optional[str], fen: str) -> None:
        if ok:
            # En el mundo simulado, la jugada "física" la hace el brazo simulado.
            if self.synth is not None and self.in_game and self.session.tracker.fen() == fen:
                self.synth.push(uci)
        else:
            self._emit("error", f"Brazo: {error}")
            # La jugada sigue pendiente: el humano puede moverla por el robot
            # o reintentar tras arreglar el problema.
            self._arm_done_uci = None
            self._arm_pending = self.in_game

    # -- sintético -------------------------------------------------------- #
    def _cmd_synthetic_move(self, uci: str) -> None:
        if self.synth is None:
            self._emit("warn", "Solo disponible en modo sintético")
        elif not self.synth.push(uci):
            self._emit("warn", f"Jugada ilegal en el tablero simulado: {uci}")

    # ------------------------------------------------------------------ #
    # El bucle
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self.step()
            except Exception:
                logger.exception("Error en el bucle de visión")
                time.sleep(0.2)
            if self.synth is not None:
                remaining = SYNTH_FRAME_INTERVAL_S - (time.perf_counter() - t0)
                if remaining > 0:
                    time.sleep(remaining)
            elif self.camera is None or self.camera_error:
                time.sleep(0.5)

    def step(self) -> None:
        """Una iteración completa: comandos, frame, detección, partida, snapshot."""
        t0 = time.perf_counter()
        self._process_commands()

        frame = self._read_frame()
        if frame is not None:
            self._frame_idx += 1
            confirmed = self.latch.update(self.detector.detect(frame))
            self._update_pose_and_placement(confirmed)
            self._game_logic()
            self._voice_logic()
            self._arm_logic()
            self._synthetic_logic()
            planned_uci = (self.session.planned.uci
                           if (self.in_game and self.session.planned) else None)
            self._publish_frame(frame, confirmed, planned_uci)
            dt = time.perf_counter() - t0
            self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt) if self._fps else 1.0 / dt
        else:
            # Sin cámara: se siguen atendiendo engine/brazo/voz para que la
            # interfaz no se quede muerta.
            self._game_logic(frame_ok=False)
        self._publish_snapshot()

    def _read_frame(self) -> Optional[np.ndarray]:
        if self.camera is None:
            return None
        try:
            frame = self.camera.read()
        except CameraError as exc:
            if not self.camera_error:
                logger.error("Cámara: %s", exc)
            self.camera_error = str(exc)
            return None
        self.camera_error = None
        return frame

    def _update_pose_and_placement(self, confirmed) -> None:
        by_role = split_by_role(confirmed)
        corners = corners_by_id(by_role[MarkerRole.CORNER])
        self._corners_found = len(corners)
        if len(corners) == 4:
            try:
                self.pose = BoardPose.from_corner_centers(
                    {i: d.center_px for i, d in corners.items()},
                    quarter_turns=self.board_turns,
                )
                self.pose_error = None
            except BoardPoseError as exc:
                self.pose, self.pose_error = None, str(exc)

        placement: dict[str, str] = {}
        pending_squares: list[str] = []
        off_board = 0
        pending_by_role = split_by_role(self.latch.pending)
        if self.pose is not None:
            placement, off_board = read_placement(self.pose, by_role[MarkerRole.PIECE])
            for det in pending_by_role[MarkerRole.PIECE]:
                square = self.pose.pixel_to_square(
                    *det.center_px, tolerance_mm=config.BOARD_EDGE_TOLERANCE_MM
                )
                if square and square not in placement:
                    pending_squares.append(square)

        self._stable = self._stable + 1 if placement == self._prev_placement else 0
        self._prev_placement = dict(placement)
        self._placement = placement
        self._pending_squares = pending_squares
        self._off_board = off_board
        occluded = split_by_role(self.latch.occluded)
        self._corners_remembered = len(occluded[MarkerRole.CORNER])
        self._pieces_confirmed = len(by_role[MarkerRole.PIECE])
        self._pieces_pending = len(pending_by_role[MarkerRole.PIECE])
        self._arm_seen = bool(by_role[MarkerRole.ARM])

    def _game_logic(self, frame_ok: bool = True) -> None:
        session = self.session
        placement = self._placement
        if self.in_game and frame_ok and self.pose is not None and self._stable >= STABLE_FRAMES:
            # Un placement con MÁS piezas que la partida es un transitorio de
            # detección (pieza vieja aún no olvidada): no intentarlo.
            if len(placement) <= len(session.tracker.placement()):
                san = session.try_apply_placement(placement)
                if san:
                    self._on_move_seen(san)
            # Solo avisar si el estado ilegal PERSISTE (no mientras la mano
            # está a medio mover una pieza).
            if placement and placement == session.last_rejected \
                    and self._stable >= ILLEGAL_WARN_FRAMES:
                self.message = "La posición no corresponde a ninguna jugada legal"
                if self.voice is not None:
                    self.voice.warn_board_problem(session.tracker.placement(), placement)

        if self.in_game and self.engine is not None:
            fen = session.want_engine_move()
            if fen:
                session.requested_fen = fen
                self.engine.request(fen)
        if self.engine is not None:
            while True:
                result = self.engine.poll()
                if result is None:
                    break
                kind, payload, source_fen = result
                if kind == "move":
                    if self.in_game:
                        session.accept_engine_response(payload)
                else:
                    apply_analysis(self.voice, session, payload, source_fen)

    def _on_move_seen(self, san: str) -> None:
        session = self.session
        self.message = None
        self._last_activity = time.monotonic()
        self._emit("move", f"{'MAGNUS' if session.last_mover == 'robot' else 'Rival'}: {san}")
        if self.voice is not None and session.last_mover == "human":
            self.voice.confirm_board_fixed()      # solo dice algo si avisó antes
            detail = session.last_move_detail
            if self.settings.announce_human_moves and detail:
                self.voice.announce_move(detail, speaker="human")
            elif detail:
                if detail.is_capture:
                    self.voice.react_to_capture()
                if detail.is_check:
                    self.voice.react_to_check()
        # Se analiza la posición resultante para poder comentar y para la barra
        # de evaluación: tras la jugada del robot queda la referencia, y tras
        # la del humano se compara contra ella (Δ de centipeones).
        if self.engine is not None:
            new_fen = session.tracker.fen()
            session.pending_evals[new_fen] = (
                "after_robot" if session.last_mover == "robot" else "after_human"
            )
            self.engine.request_analysis(new_fen)

    def _voice_logic(self) -> None:
        voice, session = self.voice, self.session
        if voice is None or not self.in_game:
            return
        human_to_move = session.board.turn != session.robot_color
        idle = time.monotonic() - self._last_activity
        idle_limit = float(self.settings.idle_prompt_s)
        if (idle_limit and human_to_move and not session.board.is_game_over()
                and idle > idle_limit and not voice.is_speaking):
            voice.say_waiting()
            self._last_activity = time.monotonic()
        if session.planned and session.planned.uci != session.announced_uci:
            session.announced_uci = session.planned.uci
            voice.announce_move(session.planned)
            voice.say_your_turn()
            self._last_activity = time.monotonic()
        if session.board.is_game_over() and not session.end_announced:
            session.end_announced = True
            board = session.board
            voice.announce_game_end(
                is_checkmate=board.is_checkmate(),
                winner_is_robot=(board.is_checkmate() and board.turn != session.robot_color),
            )

    def _arm_logic(self) -> None:
        """Decide si la jugada planificada debe ir al brazo (auto) o esperar el botón."""
        arm, session = self.arm, self.session
        planned = session.planned
        if (arm is None or arm.mode == ARM_MODE_OFF or not self.in_game
                or planned is None or session.board.turn != session.robot_color):
            self._arm_pending = False
            return
        if planned.uci == self._arm_done_uci or arm.is_busy:
            return
        if not arm.is_ready:
            self._arm_pending = False
            return
        if self.settings.arm_auto_execute:
            self._arm_start(planned)
        else:
            self._arm_pending = True

    def _synthetic_logic(self) -> None:
        """El "mundo físico" simulado: el humano juega el guion y el robot su plan."""
        synth, session = self.synth, self.session
        if synth is None or not self.in_game or synth.board.is_game_over():
            return
        if synth.board.turn == session.robot_color:
            # El robot: si hay brazo (listo u ocupado) la jugada física la hace
            # él al terminar la secuencia; si no, la aplica el mundo simulado.
            if (self.arm is not None and self.arm.mode != ARM_MODE_OFF
                    and self.arm.status in ("ready", "busy")):
                return
            if self.engine is not None and self.engine.status == "listo":
                if session.planned is not None and session.board.turn == session.robot_color \
                        and self._frame_idx >= self._synth_deadline:
                    synth.push(session.planned.uci)
                    self._synth_deadline = self._frame_idx + SYNTH_PERIOD
                return
            if self._frame_idx >= self._synth_deadline:
                synth.push(self._random_synthetic_move())
                self._synth_deadline = self._frame_idx + SYNTH_PERIOD
            return
        if self._frame_idx < self._synth_deadline or session.board.turn != synth.board.turn:
            return
        script = self._synth_black if synth.board.turn == chess.BLACK else self._synth_white
        try:
            uci = next(script)
        except StopIteration:
            uci = self._random_synthetic_move()
        if not synth.push(uci):
            synth.push(self._random_synthetic_move())
        self._synth_deadline = self._frame_idx + SYNTH_PERIOD

    def _random_synthetic_move(self) -> str:
        assert self.synth is not None
        board = self.synth.board
        captures = [m for m in board.legal_moves if board.is_capture(m)]
        pool = captures if captures and self._synth_rng.random() < 0.6 else list(board.legal_moves)
        return self._synth_rng.choice(pool).uci()

    # ------------------------------------------------------------------ #
    # Salida: frame JPEG y snapshot
    # ------------------------------------------------------------------ #
    def _publish_frame(self, frame: np.ndarray, confirmed, planned_uci: Optional[str]) -> None:
        view = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        view = view.copy()
        draw_camera_overlays(view, confirmed, self.latch.pending, self.pose, planned_uci)
        if view.shape[1] > STREAM_MAX_WIDTH:
            scale = STREAM_MAX_WIDTH / view.shape[1]
            view = cv2.resize(view, (STREAM_MAX_WIDTH, int(view.shape[0] * scale)),
                              interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
        if not ok:
            return
        with self._jpeg_cond:
            self._jpeg = buf.tobytes()
            self._jpeg_seq += 1
            self._jpeg_cond.notify_all()

    def latest_jpeg(self) -> tuple[Optional[bytes], int]:
        with self._jpeg_cond:
            return self._jpeg, self._jpeg_seq

    def wait_for_jpeg(self, after_seq: int, timeout: float = 1.0) -> tuple[Optional[bytes], int]:
        """Bloquea hasta que haya un frame más nuevo que ``after_seq`` (o timeout)."""
        with self._jpeg_cond:
            if self._jpeg_seq <= after_seq:
                self._jpeg_cond.wait(timeout)
            return self._jpeg, self._jpeg_seq

    def _phase(self) -> tuple[str, str]:
        """``(fase, subfase)`` para el banner de la interfaz."""
        if not self.in_game:
            if self.synth is not None or self._placement == GameTracker().placement():
                return "setup", "ready"
            return "setup", "waiting_board"
        session = self.session
        if session.board.is_game_over():
            return "over", "game_over"
        if session.board.turn != session.robot_color:
            return "playing", "human_turn"
        if self.arm is not None and self.arm.is_busy:
            return "playing", "arm_moving"
        if session.planned is not None:
            return "playing", "robot_ready"
        return "playing", "robot_thinking"

    def _publish_snapshot(self) -> None:
        session = self.session
        in_game = self.in_game
        planned = session.planned if in_game else None
        phase, sub = self._phase()
        captured_w, captured_b = session.captured() if in_game else ([], [])
        engine = self.engine
        voice = self.voice
        arm_snapshot = self.arm.snapshot() if self.arm is not None else None
        if arm_snapshot is not None:
            arm_snapshot["pending"] = self._arm_pending
            arm_snapshot["auto_execute"] = self.settings.arm_auto_execute
            arm_snapshot["preview"] = self.arm.preview(planned) if planned else []
        if self.synth is not None:
            camera_label = "TABLERO SIMULADO"
        else:
            camera_label = f"CÁMARA {self.settings.camera_index}"
        if in_game:
            fen = session.tracker.fen()
        else:
            fen = placement_to_fen_field(self._placement) if self._placement else None
        eval_cp, mate = session.eval_cp_white, session.mate_in_white
        if mate is not None:
            eval_label = f"M{abs(mate)}" if mate > 0 else f"-M{abs(mate)}"
        elif eval_cp is not None:
            eval_label = f"{eval_cp / 100.0:+.2f}"
        else:
            eval_label = None
        message = (session.status_text() if in_game else None) or self.message \
            or (self.pose.layout_warning if self.pose else None) \
            or (None if (self.pose or self.synth) else "Esquinas del tablero no visibles (IDs 40-43)")

        self._seq += 1
        snapshot = {
            "seq": self._seq,
            "time": time.time(),
            "phase": phase,
            "sub": sub,
            "game_id": self.game_id,
            "in_game": in_game,
            "turn": ("white" if session.board.turn == chess.WHITE else "black") if in_game else None,
            "robot_side": self.settings.robot_side,
            "board": {
                "placement": session.tracker.placement() if in_game else dict(self._placement),
                "last_move": session.last_move_uci() if in_game else None,
                "planned": {
                    "uci": planned.uci, "san": planned.san,
                    "from": planned.from_square, "to": planned.to_square,
                    "eval": eval_text(planned),
                    "is_capture": planned.is_capture,
                    "is_castling": planned.is_castling,
                } if planned else None,
                "check": session.check_square() if in_game else None,
                "pending": list(self._pending_squares),
                "fen": fen,
            },
            "setup": {
                "pieces_detected": len(self._placement),
                "pieces_needed": PIECES_IN_START_POSITION,
                "corners": self._corners_found,
                "ready": sub == "ready",
            },
            "history": list(session.history_san) if in_game else [],
            "captured": {"by_white": captured_w, "by_black": captured_b},
            "eval": {"cp": eval_cp, "mate": mate, "label": eval_label},
            "engine": {
                "enabled": engine is not None,
                "status": engine.status if engine else "desactivado",
                "difficulty": self.settings.difficulty,
                "thinking": bool(engine and engine.thinking),
                "error": engine.error if engine else None,
            },
            "difficulties": difficulty_catalog(),
            "vision": {
                "corners_found": self._corners_found,
                "corners_remembered": self._corners_remembered,
                "pieces_confirmed": self._pieces_confirmed,
                "pieces_pending": self._pieces_pending,
                "pieces_off_board": self._off_board,
                "arm_seen": self._arm_seen,
                "fps": round(self._fps, 1),
                "pose_ok": self.pose is not None,
                "pose_error": self.pose_error,
                "layout_warning": self.pose.layout_warning if self.pose else None,
                "board_turns": self.board_turns,
                "frame": self._frame_idx,
            },
            "camera": {
                "label": camera_label,
                "index": self.settings.camera_index,
                "synthetic": self.synth is not None,
                "ok": self.camera is not None and not self.camera_error,
                "error": self.camera_error,
            },
            "voice": {
                "available": voice is not None,
                "backend": type(voice.backend).__name__ if voice else None,
                "muted": voice.is_muted if voice else True,
                "speaking": voice.is_speaking if voice else False,
                "last_phrase": voice.last_phrase if voice else None,
            },
            "arm": arm_snapshot,
            "message": message,
            "error": self.pose_error or self.camera_error,
            "result": session.result() if in_game else None,
            "settings": self.settings.to_dict(),
            "events": list(self._events),
        }
        with self._snapshot_lock:
            self._snapshot = snapshot

    def snapshot(self) -> dict:
        """El último estado publicado (seguro desde cualquier hilo)."""
        with self._snapshot_lock:
            return self._snapshot

    @property
    def seq(self) -> int:
        with self._snapshot_lock:
            return self._snapshot.get("seq", 0)


__all__ = ["MagnusController", "ARM_MODE_SIMULATED"]
