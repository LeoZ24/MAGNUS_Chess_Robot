"""Estado de la partida y el hilo del engine, compartidos por la app y los demos.

Aquí vive lo que no depende de NINGUNA interfaz concreta:

    * :class:`EngineWorker`    — Stockfish en un hilo aparte (jugar + analizar),
      con cambio de dificultad en caliente.
    * :class:`GameSession`     — envuelve el ``GameTracker`` con lo que hace
      falta para mostrar y narrar la partida.
    * :class:`SyntheticCamera` — tablero simulado para jugar sin cámara.
    * Utilidades puras: lectura del placement a partir de las detecciones,
      deducción de la orientación del tablero y reconstrucción de los
      metadatos de una jugada vista por la cámara.

``examples/run_vision_demo.py`` (ventana OpenCV) y ``magnus.app.controller``
(interfaz web) usan exactamente este código.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import Counter
from typing import Callable, Optional

import chess
import numpy as np

from .. import config
from ..core.messages import MoveResponse
from ..engine.difficulty import DIFFICULTY_PRESETS, DifficultyLevel
from ..vision.aruco_detector import Detection
from ..vision.board_pose import BoardPose
from ..vision.game_state import GameTracker, NoMatchingMoveError, board_placement
from ..vision.piece_map import ARUCO_TO_PIECE, PIECE_COUNTS
from ..vision.synthetic import render_board_image
from ..vision.vision_node import CameraBackend, CameraError
from ..voice.commentary import PositionEval

logger = logging.getLogger("magnus.app.session")

# Frames consecutivos con el mismo placement antes de intentar inferir la jugada.
STABLE_FRAMES = 8

# Guion del modo sintético (apertura italiana con captura y enroque).
SYNTH_WHITE_LINE = ["e2e4", "g1f3", "f1c4", "d2d4", "e1g1", "c2c3"]
SYNTH_BLACK_LINE = ["e7e5", "b8c6", "g8f6", "e5d4", "f8e7", "e8g8"]

# Descripción de cada nivel para mostrarla en la interfaz.
DIFFICULTY_DESCRIPTIONS: dict[str, str] = {
    "BEGINNER": "Juega casi al azar. Ideal para quien está aprendiendo.",
    "EASY": "Comete errores a menudo y calcula poco.",
    "MEDIUM": "Un club de barrio: sólido pero con fallos.",
    "HARD": "Exige concentración. Castiga los errores.",
    "EXPERT": "Nivel de maestro. Muy difícil de vencer.",
    "MAXIMUM": "Toda la fuerza de Stockfish. Sin piedad.",
}


def difficulty_catalog() -> list[dict]:
    """Los niveles disponibles con su Elo aproximado, para la interfaz."""
    catalog = []
    for level in DifficultyLevel:
        preset = DIFFICULTY_PRESETS[level]
        catalog.append({
            "name": level.name,
            "value": int(level),
            "elo": preset.elo,
            "description": DIFFICULTY_DESCRIPTIONS.get(level.name, ""),
        })
    return catalog


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

    def reset(self) -> None:
        """Vuelve a la posición inicial (nueva partida)."""
        self.board = chess.Board()
        self._frame = None


# --------------------------------------------------------------------------- #
# Stockfish en un hilo aparte (no congela la interfaz)
# --------------------------------------------------------------------------- #
class EngineWorker:
    """Calcula jugadas y analiza posiciones en segundo plano.

    Tres tipos de trabajo:

    * ``("move", fen)``          -> la jugada que el robot va a jugar, a la
      dificultad elegida.
    * ``("eval", fen)``          -> el análisis a **fuerza fija** que alimenta
      los comentarios de voz.  Va aparte a propósito: así el robot puede jugar
      en fácil para que le ganes y aun así juzgar las jugadas como un maestro.
    * ``("difficulty", nivel)``  -> cambia la dificultad en caliente; aplica a
      la siguiente jugada que se pida.

    ``node_factory`` permite inyectar un ``ChessEngineNode`` con backend falso
    en los tests (sin Stockfish instalado).
    """

    def __init__(self, difficulty: str = "MEDIUM",
                 node_factory: Optional[Callable[[str], object]] = None):
        self._difficulty = DifficultyLevel.parse(difficulty).name
        self._node_factory = node_factory
        self._requests: "queue.Queue[Optional[tuple[str, str]]]" = queue.Queue(maxsize=8)
        self._responses: "queue.Queue[tuple[str, object, str]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="magnus-engine")
        self.status = "iniciando"          # "iniciando" | "listo" | "no disponible"
        self.error: Optional[str] = None
        self.thinking = False              # hay una jugada calculándose

    @property
    def difficulty(self) -> str:
        return self._difficulty

    @property
    def label(self) -> Optional[str]:
        """Texto de la píldora del dashboard (``None`` hasta que arranca)."""
        return f"STOCKFISH · {self._difficulty}" if self.status == "listo" else None

    def start(self) -> "EngineWorker":
        self._thread.start()
        return self

    def _make_node(self):
        if self._node_factory is not None:
            return self._node_factory(self._difficulty)
        from ..engine import ChessEngineNode

        return ChessEngineNode(default_difficulty=self._difficulty)

    def _run(self) -> None:
        try:
            with self._make_node() as node:
                self.status = "listo"
                while True:
                    job = self._requests.get()
                    if job is None:
                        return
                    kind, payload = job
                    try:
                        if kind == "move":
                            self.thinking = True
                            try:
                                self._responses.put(
                                    ("move", node.compute_move_from_fen(payload), payload)
                                )
                            finally:
                                self.thinking = False
                        elif kind == "eval":
                            self._responses.put(("eval", node.analyse_fen(payload), payload))
                        elif kind == "difficulty":
                            node.set_difficulty(payload)
                            self._difficulty = DifficultyLevel.parse(payload).name
                    except Exception as exc:  # GameOverError, FEN inválida...
                        logger.warning("Engine: %s", exc)
        except Exception as exc:
            self.status = "no disponible"
            self.error = str(exc)
            logger.warning("Stockfish no disponible (%s). Se sigue sin engine.", exc)

    def _submit(self, kind: str, payload: str) -> bool:
        if self.status != "listo":
            return False
        try:
            self._requests.put_nowait((kind, payload))
            return True
        except queue.Full:
            return False

    def request(self, fen: str) -> None:
        """Pide la jugada para una FEN."""
        self._submit("move", fen)

    def request_analysis(self, fen: str) -> None:
        """Pide el análisis de una FEN (para comentar, no para jugar)."""
        self._submit("eval", fen)

    def set_difficulty(self, level: str) -> str:
        """Cambia la dificultad (validando el nombre) y devuelve el nombre canónico."""
        name = DifficultyLevel.parse(level).name
        if self.status == "listo":
            self._submit("difficulty", name)
        else:
            # Aún arrancando: el nodo se creará con este nivel.
            self._difficulty = name
        return name

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
# Estado de la partida
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
        self.last_move_detail: Optional[MoveResponse] = None
        self.announced_uci: Optional[str] = None       # jugada ya narrada
        self.pending_evals: dict[str, str] = {}        # fen -> "after_robot"|"after_human"
        self.end_announced = False
        # Última evaluación conocida, desde el punto de vista de las BLANCAS
        # (para la barra de evaluación de la interfaz).
        self.eval_cp_white: Optional[int] = None
        self.mate_in_white: Optional[int] = None

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

    def result(self) -> Optional[dict]:
        """Resultado de la partida terminada (``None`` si sigue en juego)."""
        board = self.board
        if not board.is_game_over():
            return None
        if board.is_checkmate():
            winner_color = not board.turn
            robot_won = winner_color == self.robot_color
            return {
                "kind": "checkmate",
                "winner": "white" if winner_color == chess.WHITE else "black",
                "robot_won": robot_won,
                "text": "Jaque mate" + (": gana MAGNUS" if robot_won else ": ganas tú"),
            }
        if board.is_stalemate():
            reason = "ahogado"
        elif board.is_insufficient_material():
            reason = "material insuficiente"
        else:
            reason = "tablas"
        return {"kind": "draw", "winner": None, "robot_won": False,
                "text": f"Tablas por {reason}"}

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
        self.last_move_detail = move_to_response(before, move, san, self.board)
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
            self.note_eval(resp.evaluation_cp, resp.mate_in,
                           side_to_move=resp.side_to_move)

    def note_eval(self, evaluation_cp: Optional[int], mate_in: Optional[int],
                  side_to_move: str) -> None:
        """Registra una evaluación (signo del lado que mueve) como vista por blancas."""
        sign = 1 if side_to_move == "white" else -1
        if mate_in is not None:
            self.mate_in_white = sign * mate_in if mate_in else (-sign)
            self.eval_cp_white = None
        elif evaluation_cp is not None:
            self.eval_cp_white = sign * evaluation_cp
            self.mate_in_white = None


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


def move_to_response(
    before: chess.Board, move: chess.Move, san: str, after: chess.Board
) -> MoveResponse:
    """Metadatos de una jugada vista por la cámara, para poder narrarla.

    El engine rellena esto para SUS jugadas; las del rival las deduce la visión,
    así que aquí se reconstruyen los mismos campos a partir del tablero de antes
    y de después (captura, al paso, enroque, promoción, jaque…).
    """
    moved = before.piece_at(move.from_square)
    captured = before.piece_at(move.to_square)
    if before.is_en_passant(move):
        # En la captura al paso la pieza comida NO está en la casilla de destino.
        captured = chess.Piece(chess.PAWN, not before.turn)
    return MoveResponse(
        uci=move.uci(),
        san=san,
        fen=before.fen(),
        resulting_fen=after.fen(),
        from_square=chess.square_name(move.from_square),
        to_square=chess.square_name(move.to_square),
        piece=moved.symbol() if moved else "",
        side_to_move="white" if before.turn == chess.WHITE else "black",
        is_capture=before.is_capture(move),
        captured_piece=captured.symbol() if captured else None,
        is_en_passant=before.is_en_passant(move),
        is_castling=before.is_castling(move),
        is_kingside_castle=before.is_kingside_castling(move),
        promotion=chess.piece_symbol(move.promotion) if move.promotion else None,
        is_check=after.is_check(),
        is_checkmate=after.is_checkmate(),
    )


def apply_analysis(voice, session: GameSession, analysis, fen: str) -> None:
    """Usa un análisis recién llegado para comentar (o para fijar la referencia).

    Tras la jugada del robot el análisis solo *fija la referencia*; tras la del
    humano se compara con ella y sale el comentario ("buena jugada", "eso fue un
    error"...).  ``voice`` puede ser ``None``: la evaluación se registra igual
    para la barra de la interfaz.
    """
    side = "white" if fen.split()[1] == "w" else "black"
    session.note_eval(analysis.evaluation_cp, analysis.mate_in, side_to_move=side)
    purpose = session.pending_evals.pop(fen, None)
    if purpose is None or voice is None:
        return
    position = PositionEval(
        evaluation_cp=analysis.evaluation_cp,
        mate_in=analysis.mate_in,
        side_to_move=side,
    )
    if purpose == "after_robot":
        voice.commentator.observe(position)
    else:
        voice.comment_human_move(position)
        voice.comment_advantage(position)


def eval_text(resp: MoveResponse) -> Optional[str]:
    """Texto corto de la evaluación de una respuesta del engine (``"+0.35"``, ``"M3"``)."""
    if resp.mate_in is not None:
        return f"M{abs(resp.mate_in)}"
    if resp.evaluation_cp is not None:
        return f"{resp.evaluation_cp / 100.0:+.2f}"
    return None
