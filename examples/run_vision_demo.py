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
import sys
import time
from typing import Optional

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import chess  # noqa: E402
import cv2  # noqa: E402

from magnus import config  # noqa: E402
from magnus.app.overlays import draw_camera_overlays  # noqa: E402
from magnus.app.session import (  # noqa: E402
    STABLE_FRAMES,
    SYNTH_BLACK_LINE,
    SYNTH_WHITE_LINE,
    EngineWorker,
    GameSession,
    SyntheticCamera,
    apply_analysis,
    eval_text,
    find_orientation,
    read_placement,
)
from magnus.vision.aruco_detector import (  # noqa: E402
    ArucoDetector,
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
from magnus.vision.game_state import GameTracker  # noqa: E402
from magnus.vision.vision_node import CameraBackend, CameraError  # noqa: E402

WINDOW_TITLE = "MAGNUS - Vision"
KEYS_HELP = ("G iniciar partida · O observar · F girar vista · T girar mapeo · "
             "M silenciar voz · R resetear deteccion · Q salir")

# En modo sintético: frames entre medias-jugadas del guion.
SYNTH_PERIOD = 45


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
    last_activity = time.monotonic()
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
                        last_activity = time.monotonic()
                        if voice is not None and session.last_mover == "human":
                            # Solo dice algo si de verdad se avisó antes.
                            voice.confirm_board_fixed()
                            detail = session.last_move_detail
                            if config.VOICE_ANNOUNCE_HUMAN_MOVES and detail:
                                # La narración ya menciona captura y jaque, así
                                # que no se añaden reacciones sueltas encima.
                                voice.announce_move(detail, speaker="human")
                            elif detail:
                                if detail.is_capture:
                                    voice.react_to_capture()
                                if detail.is_check:
                                    voice.react_to_check()
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
                    if voice is not None:
                        # Distingue jugada ilegal de pieza en la mano o tablero
                        # revuelto, se calla los que no estén en
                        # VOICE_WARN_BOARD_PROBLEMS y no repite el aviso
                        # mientras el problema no cambie.
                        voice.warn_board_problem(session.tracker.placement(), placement)
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
                    else:
                        apply_analysis(voice, session, payload, source_fen)

            # --- Voz --------------------------------------------------- #
            if voice is not None and mode == "PARTIDA":
                # Recordatorio amable si al rival se le va el santo al cielo.
                human_to_move = session.board.turn != session.robot_color
                idle = time.monotonic() - last_activity
                if (config.VOICE_IDLE_PROMPT_S and human_to_move
                        and not session.board.is_game_over()
                        and idle > config.VOICE_IDLE_PROMPT_S
                        and not voice.is_speaking):
                    voice.say_waiting()
                    last_activity = time.monotonic()
                # Narrar la jugada planificada, una sola vez.
                if session.planned and session.planned.uci != session.announced_uci:
                    session.announced_uci = session.planned.uci
                    voice.announce_move(session.planned)
                    voice.say_your_turn()
                    last_activity = time.monotonic()
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
