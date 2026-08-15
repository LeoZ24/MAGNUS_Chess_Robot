#!/usr/bin/env python3
"""Demo de visión EN VIVO de MAGNUS — dashboard profesional.

Muestra en una sola ventana:

    * La cámara con los marcadores detectados (coloreados por rol), la
      cuadrícula del tablero proyectada y la jugada planificada.
    * La representación 2D del tablero con iconos de pieza, la última jugada,
      la flecha de lo que el robot va a jugar, jaques y casillas pendientes.
    * Un panel de estado: turno, historial, capturas, evaluación, FEN y
      métricas de detección.

Los marcadores identifican el TIPO de pieza (todos los peones blancos llevan
el ID 0), así que la detección rastrea varias instancias del mismo ID a la vez.

Modos:
    OBSERVACION  solo muestra lo que la cámara ve (placement crudo).
    PARTIDA      sigue la partida con el GameTracker y, si Stockfish está
                 disponible, muestra la jugada que el robot va a jugar.

La orientación de la cámara da igual (horizontal, vertical, desde el lado de
las blancas o de las negras): cada esquina se identifica por su ID, así que la
homografía se adapta sola.  Lo único que importa es que los 4 marcadores de
esquina recorran el borde del tablero (40 a8 → 41 h8 → 42 h1 → 43 a1) y no en
zig-zag; si están cruzados, el demo lo detecta, lo corrige y lo avisa.

Teclas:
    G  iniciar partida (el tablero físico debe estar en la posición inicial;
       si la orientación no coincide, se corrige sola)
    O  volver al modo observación
    F  girar la VISTA del tablero renderizado (blancas/negras abajo)
    T  girar 90° el MAPEO de casillas (si el tablero digital sale rotado)
    M  silenciar/activar la voz
    R  resetear la memoria de detección (si mueves la cámara o el tablero)
    Q  salir

Uso:
    python3 examples/run_vision_demo.py                     # webcam 0
    python3 examples/run_vision_demo.py --camera 1
    python3 examples/run_vision_demo.py --list-cameras      # ¿qué índice da imagen?
    python3 examples/run_vision_demo.py --synthetic         # sin cámara (simulado)
    python3 examples/run_vision_demo.py --no-engine
    python3 examples/run_vision_demo.py --icons assets/pieces
    python3 examples/run_vision_demo.py --say-voice Jorge   # otra voz de macOS
    python3 examples/run_vision_demo.py --synthetic --screenshot demo.png

Iconos personalizados: pon PNGs llamados wP.png, wN.png, wB.png, wR.png,
wQ.png, wK.png, bP.png, ... bK.png (con canal alfa) en una carpeta y pásala
con --icons.  Mientras falten archivos se usan los iconos vectoriales
integrados.
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from collections import Counter
from typing import Optional

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import chess  # noqa: E402
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from magnus import config  # noqa: E402
from magnus.core.messages import MoveResponse  # noqa: E402
from magnus.vision.aruco_detector import (  # noqa: E402
    ArucoDetector,
    Detection,
    DetectionLatch,
    MarkerRole,
    corners_by_id,
    split_by_role,
)
from magnus.vision.board_pose import BoardPose, BoardPoseError  # noqa: E402
from magnus.vision.board_render import (  # noqa: E402
    BoardRenderer,
    Dashboard,
    DashboardState,
)
from magnus.vision.fen_builder import placement_to_fen_field  # noqa: E402
from magnus.vision.game_state import GameTracker, NoMatchingMoveError, board_placement  # noqa: E402
from magnus.vision.piece_map import ARUCO_TO_PIECE, PIECE_COUNTS  # noqa: E402
from magnus.vision.synthetic import render_board_image  # noqa: E402
from magnus.vision.vision_node import CameraBackend, CameraError  # noqa: E402
from magnus.voice.commentary import PositionEval  # noqa: E402

WINDOW_TITLE = "MAGNUS - Vision"
KEYS_HELP = ("G iniciar partida · O observar · F girar vista · T girar mapeo · "
             "M silenciar voz · R resetear deteccion · Q salir")

# Frames consecutivos con el mismo placement antes de intentar inferir la jugada.
STABLE_FRAMES = 8
# En modo sintético: frames entre medias-jugadas del guion.
SYNTH_PERIOD = 45

ROLE_COLORS = {
    MarkerRole.PIECE: (76, 195, 138),     # verde
    MarkerRole.CORNER: (229, 165, 10),    # azul-cian
    MarkerRole.ARM: (82, 82, 224),        # rojo
    MarkerRole.UNKNOWN: (0, 255, 255),    # amarillo
}

# Guion del modo sintético (apertura italiana con captura y enroque).
SYNTH_WHITE_LINE = ["e2e4", "g1f3", "f1c4", "d2d4", "e1g1", "c2c3"]
SYNTH_BLACK_LINE = ["e7e5", "b8c6", "g8f6", "e5d4", "f8e7", "e8g8"]


# --------------------------------------------------------------------------- #
# Cámara sintética (demo sin hardware)
# --------------------------------------------------------------------------- #
class SyntheticCamera(CameraBackend):
    """Cámara falsa que renderiza un tablero simulado con marcadores reales."""

    def __init__(self):
        self.board = chess.Board()
        self._frame: Optional[np.ndarray] = None
        self._opened = False

    def open(self) -> None:
        self._opened = True

    def read(self) -> np.ndarray:
        if not self._opened:
            raise CameraError("La cámara sintética no está abierta.")
        if self._frame is None:
            self._frame = render_board_image(board_placement(self.board))
        return self._frame.copy()

    def close(self) -> None:
        self._opened = False

    def push(self, uci: str) -> bool:
        """Aplica una jugada al tablero simulado; False si no es legal."""
        move = chess.Move.from_uci(uci)
        if move not in self.board.legal_moves:
            return False
        self.board.push(move)
        self._frame = None
        return True


# --------------------------------------------------------------------------- #
# Stockfish en un hilo aparte (no congela la interfaz)
# --------------------------------------------------------------------------- #
class EngineWorker:
    """Calcula jugadas y analiza posiciones en segundo plano.

    Dos tipos de trabajo, porque sirven a cosas distintas:

    * ``("move", fen)``    -> la jugada que el robot va a jugar, a la dificultad
      elegida.
    * ``("eval", fen)``    -> el análisis a **fuerza fija** que alimenta los
      comentarios de voz.  Va aparte a propósito: así el robot puede jugar en
      fácil para que le ganes y aun así juzgar las jugadas como un maestro.
    """

    def __init__(self, difficulty: str):
        self._difficulty = difficulty
        self._requests: "queue.Queue[Optional[tuple[str, str]]]" = queue.Queue(maxsize=4)
        self._responses: "queue.Queue[tuple[str, object, str]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="engine")
        self.status = "iniciando"          # "iniciando" | "listo" | "no disponible"
        self.label: Optional[str] = None   # para la píldora del dashboard

    def start(self) -> "EngineWorker":
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            from magnus.engine import ChessEngineNode

            with ChessEngineNode(default_difficulty=self._difficulty) as node:
                self.status = "listo"
                self.label = f"STOCKFISH · {self._difficulty}"
                while True:
                    job = self._requests.get()
                    if job is None:
                        return
                    kind, fen = job
                    try:
                        if kind == "move":
                            self._responses.put(("move", node.compute_move_from_fen(fen), fen))
                        else:
                            self._responses.put(("eval", node.analyse_fen(fen), fen))
                    except Exception as exc:  # GameOverError, FEN inválida...
                        logging.getLogger("demo").warning("Engine: %s", exc)
        except Exception as exc:
            self.status = "no disponible"
            logging.getLogger("demo").warning(
                "Stockfish no disponible (%s). El demo sigue sin engine.", exc
            )

    def _submit(self, kind: str, fen: str) -> None:
        if self.status != "listo":
            return
        try:
            self._requests.put_nowait((kind, fen))
        except queue.Full:
            pass

    def request(self, fen: str) -> None:
        """Pide la jugada para una FEN."""
        self._submit("move", fen)

    def request_analysis(self, fen: str) -> None:
        """Pide el análisis de una FEN (para comentar, no para jugar)."""
        self._submit("eval", fen)

    def poll(self) -> Optional[tuple[str, object, str]]:
        """``(tipo, resultado, fen)`` o ``None``; tipo es ``"move"`` o ``"eval"``."""
        try:
            return self._responses.get_nowait()
        except queue.Empty:
            return None

    def stop(self) -> None:
        if self._thread.is_alive():
            try:
                self._requests.put_nowait(None)
            except queue.Full:
                pass


# --------------------------------------------------------------------------- #
# Estado de la partida en el demo
# --------------------------------------------------------------------------- #
class GameSession:
    """Envuelve el GameTracker con lo que la interfaz necesita mostrar."""

    def __init__(self, robot_color: chess.Color):
        self.tracker = GameTracker()
        self.robot_color = robot_color
        self.history_san: list[str] = []
        self.planned: Optional[MoveResponse] = None
        self.requested_fen: Optional[str] = None
        self.last_rejected: Optional[dict[str, str]] = None
        self.last_mover: Optional[str] = None          # "robot" | "human"
        self.announced_uci: Optional[str] = None       # jugada ya narrada
        self.pending_evals: dict[str, str] = {}        # fen -> "after_robot"|"after_human"
        self.end_announced = False

    # -- estado derivado ------------------------------------------------ #
    @property
    def board(self) -> chess.Board:
        return self.tracker.board

    def last_move_uci(self) -> Optional[str]:
        return self.board.peek().uci() if self.board.move_stack else None

    def check_square(self) -> Optional[str]:
        if self.board.is_check():
            return chess.square_name(self.board.king(self.board.turn))
        return None

    def captured(self) -> tuple[list[str], list[str]]:
        """(capturadas por blancas, capturadas por negras), símbolos FEN."""
        current = Counter(board_placement(self.board).values())
        by_white: list[str] = []
        by_black: list[str] = []
        for sym, count in PIECE_COUNTS.items():
            by_white += [sym.lower()] * max(0, count - current[sym.lower()])
            by_black += [sym] * max(0, count - current[sym])
        return by_white, by_black

    def status_text(self) -> Optional[str]:
        board = self.board
        if board.is_checkmate():
            winner = "blancas" if board.turn == chess.BLACK else "negras"
            return f"JAQUE MATE — ganan las {winner}"
        if board.is_stalemate():
            return "TABLAS por ahogado"
        if board.is_insufficient_material():
            return "TABLAS por material insuficiente"
        return None

    # -- jugadas -------------------------------------------------------- #
    def try_apply_placement(self, placement: dict[str, str]) -> Optional[str]:
        """Intenta inferir la jugada humana/física; devuelve su SAN si la hubo.

        Deja en :attr:`last_mover` quién movió (``"robot"`` o ``"human"``): la
        cámara ve ambas jugadas igual, pero la voz solo comenta las del rival.
        """
        if placement == self.tracker.placement() or placement == self.last_rejected:
            return None
        before = self.board.copy()
        try:
            move = self.tracker.update_from_placement(placement)
        except NoMatchingMoveError:
            self.last_rejected = dict(placement)
            return None
        if move is None:
            return None
        san = before.san(move)
        self.history_san.append(san)
        self.last_rejected = None
        self.last_mover = "robot" if before.turn == self.robot_color else "human"
        # La jugada planificada dejó de ser vigente si ya se ejecutó (o cambió).
        self.planned = None
        return san

    def want_engine_move(self) -> Optional[str]:
        """FEN a consultar si es el turno del robot y aún no se pidió."""
        if self.board.is_game_over() or self.board.turn != self.robot_color:
            return None
        fen = self.tracker.fen()
        if self.planned is not None or self.requested_fen == fen:
            return None
        return fen

    def accept_engine_response(self, resp: MoveResponse) -> None:
        """Guarda la respuesta si sigue correspondiendo a la posición actual."""
        if resp.fen == self.tracker.fen():
            self.planned = resp


# --------------------------------------------------------------------------- #
# Lectura del tablero a partir de las detecciones
# --------------------------------------------------------------------------- #
def read_placement(
    pose: BoardPose, piece_dets: list[Detection]
) -> tuple[dict[str, str], int]:
    """``(placement, nº de marcadores fuera del tablero)``.

    Los marcadores de pieza que caen fuera del área de juego se cuentan aparte
    y NO entran en el placement: son piezas capturadas en la zona de descarte o
    marcadores sueltos sobre la mesa.
    """
    placement: dict[str, str] = {}
    off_board = 0
    for det in piece_dets:
        square = pose.pixel_to_square(
            *det.center_px, tolerance_mm=config.BOARD_EDGE_TOLERANCE_MM
        )
        if square is None:
            off_board += 1
        elif square not in placement:
            placement[square] = ARUCO_TO_PIECE[det.aruco_id]
    return placement, off_board


def find_orientation(
    pose: BoardPose, piece_dets: list[Detection], target: dict[str, str]
) -> Optional[int]:
    """Giros de 90° que hacen coincidir lo detectado con ``target``, o ``None``.

    Con los 4 marcadores puestos en el borde pero empezando en otra esquina, el
    tablero sale rotado 90/180/270°.  Al arrancar la partida se conoce la
    posición esperada (la inicial), así que la rotación correcta se deduce
    probando las cuatro — sin recolocar ningún marcador.
    """
    for turns in range(4):
        rotated = pose if turns == 0 else pose.rotated(turns)
        placement, _ = read_placement(rotated, piece_dets)
        if placement == target:
            return turns
    return None


def _handle_analysis(voice, session, analysis, fen: str) -> None:
    """Usa un análisis recién llegado para comentar (o para fijar la referencia).

    Tras la jugada del robot el análisis solo *fija la referencia*; tras la del
    humano se compara con ella y sale el comentario ("buena jugada", "eso fue un
    error"...).
    """
    purpose = session.pending_evals.pop(fen, None)
    if purpose is None:
        return
    position = PositionEval(
        evaluation_cp=analysis.evaluation_cp,
        mate_in=analysis.mate_in,
        side_to_move="white" if fen.split()[1] == "w" else "black",
    )
    if purpose == "after_robot":
        voice.commentator.observe(position)
    else:
        voice.comment_human_move(position)
        voice.comment_advantage(position)


def eval_text(resp: MoveResponse) -> Optional[str]:
    if resp.mate_in is not None:
        return f"M{abs(resp.mate_in)}"
    if resp.evaluation_cp is not None:
        return f"{resp.evaluation_cp / 100.0:+.2f}"
    return None


# --------------------------------------------------------------------------- #
# Overlays sobre la imagen de la cámara
# --------------------------------------------------------------------------- #
def draw_camera_overlays(
    frame: np.ndarray,
    confirmed: list[Detection],
    pending: list[Detection],
    pose: Optional[BoardPose],
    planned_uci: Optional[str],
) -> None:
    # Cuadrícula proyectada del tablero.
    if pose is not None:
        overlay = frame.copy()
        from magnus import config

        step = config.SQUARE_SIZE_MM
        size = config.BOARD_SIZE_MM
        for i in range(9):
            a = pose.mm_to_pixel(i * step, 0.0)
            b = pose.mm_to_pixel(i * step, size)
            c = pose.mm_to_pixel(0.0, i * step)
            d = pose.mm_to_pixel(size, i * step)
            for p1, p2 in ((a, b), (c, d)):
                cv2.line(overlay, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])),
                         (120, 190, 120), 1, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, dst=frame)

    # Marcadores pendientes (grises, finos).
    for det in pending:
        pts = det.corners_px.astype(np.int32)
        cv2.polylines(frame, [pts], True, (150, 150, 150), 1, cv2.LINE_AA)

    # Marcadores confirmados, coloreados por rol.
    for det in confirmed:
        color = ROLE_COLORS[det.role]
        pts = det.corners_px.astype(np.int32)
        cv2.polylines(frame, [pts], True, color, 2, cv2.LINE_AA)
        label = str(det.aruco_id)
        if det.role is MarkerRole.PIECE:
            label = ARUCO_TO_PIECE[det.aruco_id]
            if pose is not None:
                square = pose.pixel_to_square(*det.center_px)
                if square:
                    label = f"{label}·{square}"
        cv2.putText(frame, label, (pts[0][0], pts[0][1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    # La jugada planificada, proyectada sobre el tablero físico.
    if pose is not None and planned_uci:
        try:
            a = pose.square_center_px(planned_uci[:2])
            b = pose.square_center_px(planned_uci[2:4])
        except (ValueError, IndexError):
            return
        cv2.arrowedLine(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])),
                        (90, 200, 70), 3, cv2.LINE_AA, tipLength=0.25)


# --------------------------------------------------------------------------- #
# Bucle principal
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Demo de visión en vivo de MAGNUS (dashboard profesional)"
    )
    parser.add_argument("--camera", type=int, default=0, help="Índice de la cámara")
    parser.add_argument("--list-cameras", action="store_true",
                        help="Lista los índices de cámara que dan imagen y sale")
    parser.add_argument("--camera-warmup", type=float, default=5.0,
                        help="Segundos de espera al primer frame (cámaras virtuales)")
    parser.add_argument("--synthetic", action="store_true",
                        help="Sin cámara: tablero simulado que juega un guion")
    parser.add_argument("--no-engine", action="store_true",
                        help="No usar Stockfish (no se muestra jugada planificada)")
    parser.add_argument("--no-voice", action="store_true",
                        help="Sin voz (tampoco subtítulos)")
    parser.add_argument("--muted", action="store_true",
                        help="Arranca en silencio: subtítulos sí, audio no")
    parser.add_argument("--voice-model", default=None,
                        help="Ruta al .onnx de la voz de Piper a usar")
    parser.add_argument("--say-voice", default=None,
                        help="Voz de macOS a usar (p. ej. Juan, Jorge, Diego); "
                             "lístalas con: say -v '?' | grep es_")
    parser.add_argument("--difficulty", default="MEDIUM",
                        help="Dificultad del engine (EASY/MEDIUM/HARD/...)")
    parser.add_argument("--robot-side", choices=["white", "black"], default="black",
                        help="Color que juega el robot (por defecto negras)")
    parser.add_argument("--icons", default=None,
                        help="Carpeta con PNGs de piezas personalizados (wP.png...)")
    parser.add_argument("--square-px", type=int, default=66,
                        help="Tamaño de casilla del tablero renderizado")
    parser.add_argument("--screenshot", default=None,
                        help="Modo sin ventana: guarda el dashboard aquí y sale")
    parser.add_argument("--frames", type=int, default=260,
                        help="Frames a procesar en modo --screenshot")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.list_cameras:
        from magnus.vision.vision_node import probe_cameras

        found = probe_cameras()
        if not found:
            print("No se encontró ninguna cámara con imagen.\n"
                  "Revisa los permisos de cámara del sistema (y reinicia la app "
                  "desde la que ejecutas) y, si usas una cámara virtual como "
                  "Iriun, que el móvil esté conectado y transmitiendo.")
            return 2
        print("Cámaras con imagen:")
        for index, (width, height) in found:
            print(f"  --camera {index}   ({width}×{height})")
        return 0

    headless = args.screenshot is not None
    robot_color = chess.WHITE if args.robot_side == "white" else chess.BLACK

    # --- Fuente de frames --------------------------------------------- #
    synth: Optional[SyntheticCamera] = None
    if args.synthetic:
        synth = SyntheticCamera()
        camera: CameraBackend = synth
        camera_label = "TABLERO SIMULADO"
    else:
        from magnus.vision.vision_node import OpenCVCameraBackend

        camera = OpenCVCameraBackend(args.camera, warmup_s=args.camera_warmup)
        camera_label = f"CAMARA {args.camera}"

    try:
        camera.open()
    except CameraError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # --- Componentes --------------------------------------------------- #
    detector = ArucoDetector()
    latch = DetectionLatch()
    renderer = BoardRenderer(square_px=args.square_px, icon_dir=args.icons)
    dashboard = Dashboard(renderer=renderer)
    engine = None if args.no_engine else EngineWorker(args.difficulty).start()
    session = GameSession(robot_color)

    voice = None
    if not args.no_voice:
        from magnus.voice import VoiceNode
        from magnus.voice.backend import MacSayBackend, PiperBackend, default_backend

        if args.voice_model:
            backend = PiperBackend(model=args.voice_model)
        elif args.say_voice:
            backend = MacSayBackend(voice=args.say_voice)
        else:
            backend = default_backend()
        voice = VoiceNode(
            backend=backend,
            robot_side="white" if robot_color == chess.WHITE else "black",
            muted=args.muted,
        ).start()

    mode = "OBSERVACION"
    message: Optional[str] = None
    pose: Optional[BoardPose] = None
    pose_error: Optional[str] = None
    board_turns = 0                  # giros de 90° aplicados al mapeo de casillas
    prev_placement: dict[str, str] = {}
    stable = 0
    fps = 0.0
    frame_idx = 0
    synth_white = iter(SYNTH_WHITE_LINE)
    synth_black = iter(SYNTH_BLACK_LINE)
    synth_done = False

    if not headless:
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
        print("MAGNUS — demo de visión. " + KEYS_HELP)

    try:
        while True:
            t0 = time.perf_counter()
            try:
                frame = camera.read()
            except CameraError as exc:
                print(f"\nERROR: {exc}", file=sys.stderr)
                return 2
            frame_idx += 1

            # --- Detección ------------------------------------------- #
            # Las esquinas confirmadas no se olvidan aunque una torre las tape:
            # el tablero y la cámara están fijos (ver DetectionLatch).
            confirmed = latch.update(detector.detect(frame))
            by_role = split_by_role(confirmed)
            corners = corners_by_id(by_role[MarkerRole.CORNER])
            if len(corners) == 4:
                try:
                    pose = BoardPose.from_corner_centers(
                        {i: d.center_px for i, d in corners.items()},
                        quarter_turns=board_turns,
                    )
                    pose_error = None
                except BoardPoseError as exc:
                    pose, pose_error = None, str(exc)

            placement: dict[str, str] = {}
            pending_squares: list[str] = []
            off_board = 0
            if pose is not None:
                placement, off_board = read_placement(pose, by_role[MarkerRole.PIECE])
                for det in split_by_role(latch.pending)[MarkerRole.PIECE]:
                    square = pose.pixel_to_square(
                        *det.center_px, tolerance_mm=config.BOARD_EDGE_TOLERANCE_MM
                    )
                    if square and square not in placement:
                        pending_squares.append(square)

            stable = stable + 1 if placement == prev_placement else 0
            prev_placement = dict(placement)

            # --- Lógica de partida ------------------------------------ #
            if mode == "PARTIDA" and pose is not None and stable >= STABLE_FRAMES:
                # Un placement con MÁS piezas que la partida es un transitorio
                # de detección (pieza vieja aún no olvidada): no intentarlo.
                if len(placement) <= len(session.tracker.placement()):
                    san = session.try_apply_placement(placement)
                    if san:
                        message = None
                        # Se analiza la posición resultante para poder comentar:
                        # tras la jugada del robot queda la referencia, y tras la
                        # del humano se compara contra ella (Δ de centipeones).
                        if engine is not None:
                            nueva_fen = session.tracker.fen()
                            session.pending_evals[nueva_fen] = (
                                "after_robot" if session.last_mover == "robot"
                                else "after_human"
                            )
                            engine.request_analysis(nueva_fen)
                # Solo avisar si el estado ilegal PERSISTE (no mientras la mano
                # está a medio mover una pieza).
                if placement and placement == session.last_rejected \
                        and stable >= STABLE_FRAMES * 4:
                    message = "posicion no corresponde a ninguna jugada legal"
            if mode == "PARTIDA" and engine is not None:
                fen = session.want_engine_move()
                if fen:
                    session.requested_fen = fen
                    engine.request(fen)
                result = engine.poll()
                if result is not None:
                    kind, payload, source_fen = result
                    if kind == "move":
                        session.accept_engine_response(payload)
                    elif voice is not None:
                        _handle_analysis(voice, session, payload, source_fen)

            # --- Voz --------------------------------------------------- #
            if voice is not None and mode == "PARTIDA":
                # Narrar la jugada planificada, una sola vez.
                if session.planned and session.planned.uci != session.announced_uci:
                    session.announced_uci = session.planned.uci
                    voice.announce_move(session.planned)
                # Final de partida.
                if session.board.is_game_over() and not session.end_announced:
                    session.end_announced = True
                    board = session.board
                    voice.announce_game_end(
                        is_checkmate=board.is_checkmate(),
                        winner_is_robot=(board.is_checkmate()
                                         and board.turn != session.robot_color),
                    )

            # --- Guion sintético -------------------------------------- #
            if synth is not None and not synth_done:
                if mode == "OBSERVACION" and placement == session.tracker.placement():
                    mode = "PARTIDA"                     # auto-inicio del demo
                if mode == "PARTIDA" and frame_idx % SYNTH_PERIOD == 0 \
                        and not synth.board.is_game_over():
                    synth_done = not _advance_synthetic_game(
                        synth, session, engine, synth_white, synth_black
                    )

            # --- Render ----------------------------------------------- #
            in_game = mode == "PARTIDA"
            shown_placement = session.tracker.placement() if in_game else placement
            planned_uci = session.planned.uci if (in_game and session.planned) else None
            board_img = renderer.render(
                shown_placement,
                last_move=session.last_move_uci() if in_game else None,
                planned_move=planned_uci,
                check_square=session.check_square() if in_game else None,
                pending_squares=pending_squares,
            )

            camera_view = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            draw_camera_overlays(camera_view, confirmed, latch.pending, pose, planned_uci)

            captured_w, captured_b = session.captured() if in_game else ([], [])
            occluded = split_by_role(latch.occluded)
            state = DashboardState(
                mode=mode,
                turn=("w" if session.board.turn == chess.WHITE else "b") if in_game else None,
                corners_found=len(corners),
                corners_remembered=len(occluded[MarkerRole.CORNER]),
                pieces_confirmed=len(by_role[MarkerRole.PIECE]),
                pieces_pending=len(split_by_role(latch.pending)[MarkerRole.PIECE]),
                pieces_off_board=off_board,
                arm_seen=bool(by_role[MarkerRole.ARM]),
                camera_label=camera_label,
                engine_label=engine.label if engine else None,
                fen=session.tracker.fen() if in_game
                    else (placement_to_fen_field(placement) if placement else None),
                last_move_san=session.history_san[-1] if session.history_san else None,
                planned_san=session.planned.san if session.planned else None,
                planned_uci=session.planned.uci if session.planned else None,
                planned_eval=eval_text(session.planned) if session.planned else None,
                history_san=list(session.history_san),
                captured_by_white=captured_w,
                captured_by_black=captured_b,
                message=session.status_text() or message
                        or ("guion del demo terminado" if synth_done else None)
                        or (pose.layout_warning if pose else None)
                        or (None if pose else "esquinas del tablero no visibles (IDs 40-43)"),
                error=pose_error,
                fps=fps if not headless else None,
                voice_phrase=voice.last_phrase if voice else None,
                voice_muted=voice.is_muted if voice else False,
            )
            output = dashboard.compose(board_img, camera_view, state, KEYS_HELP)

            dt = time.perf_counter() - t0
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt

            # --- Salida / teclado ------------------------------------- #
            if headless:
                if frame_idx >= args.frames:
                    cv2.imwrite(args.screenshot, output)
                    print(f"Dashboard guardado en {args.screenshot}")
                    return 0
                continue

            if frame_idx == 1:
                # Encajar la ventana en pantallas normales (redimensionable).
                scale = min(1.0, 1550.0 / output.shape[1])
                cv2.resizeWindow(WINDOW_TITLE, int(output.shape[1] * scale),
                                 int(output.shape[0] * scale))
            cv2.imshow(WINDOW_TITLE, output)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                return 0
            if key == ord("r"):
                latch.reset()
                pose, pose_error = None, None
            if key == ord("f"):
                renderer.flip()
            if key == ord("m") and voice is not None:
                message = ("voz silenciada" if voice.toggle_mute()
                           else "voz activada")
            if key == ord("t") and pose is not None:
                # Gira el mapeo de casillas (no la vista): útil si los 4
                # marcadores están bien en el borde pero empezando en otra
                # esquina, y el tablero digital sale rotado.
                board_turns = (board_turns + 1) % 4
                pose = pose.rotated(1)
                message = f"mapeo del tablero girado {board_turns * 90}°"
            if key == ord("o"):
                mode = "OBSERVACION"
                message = None
            if key == ord("g"):
                initial = GameTracker().placement()
                turns = (find_orientation(pose, by_role[MarkerRole.PIECE], initial)
                         if pose is not None else None)
                if turns is None:
                    message = (f"coloca la posicion inicial para empezar "
                               f"({len(placement)}/32 piezas detectadas)")
                else:
                    if turns:               # el tablero estaba rotado: se corrige
                        board_turns = (board_turns + turns) % 4
                        pose = pose.rotated(turns)
                    session = GameSession(robot_color)
                    mode = "PARTIDA"
                    if voice is not None:
                        voice.new_game()
                        voice.greet()
                    message = (f"orientacion corregida sola ({turns * 90}°)"
                               if turns else None)
    finally:
        camera.close()
        if engine is not None:
            engine.stop()
        if voice is not None:
            voice.shutdown(wait=False)
        if not headless:
            cv2.destroyAllWindows()


def _advance_synthetic_game(
    synth: SyntheticCamera,
    session: GameSession,
    engine: Optional[EngineWorker],
    white_line,
    black_line,
) -> bool:
    """Juega la siguiente media-jugada del guion; False si el guion terminó."""
    robot_turn = synth.board.turn == session.robot_color
    if robot_turn and engine is not None and engine.status == "listo":
        # El "robot" simulado ejecuta la jugada planificada cuando esté lista.
        if session.planned is None:
            return True                      # esperando al engine
        return synth.push(session.planned.uci)
    script = black_line if synth.board.turn == chess.BLACK else white_line
    try:
        return synth.push(next(script))
    except StopIteration:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
