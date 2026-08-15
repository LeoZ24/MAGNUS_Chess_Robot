"""Decide QUÉ comentar de la partida (sin decirlo en voz alta).

La idea es la misma que usan Lichess o Chess.com para etiquetar jugadas:
comparar la evaluación **antes** y **después** de la jugada del humano.  Si la
posición mejora mucho para el robot, el humano se equivocó; si empeora, jugó
bien::

    Δ = eval_después(robot) − eval_antes(robot)      [en centipeones]

    Δ ≥ +300  -> error grave        Δ ≤ −150 -> muy buena jugada
    Δ ≥ +150  -> error              Δ ≤  −50 -> buena jugada
    Δ ≥  +50  -> imprecisión        |Δ| < 50 -> normal (no se comenta)

Las evaluaciones deben venir del **análisis a fuerza fija**
(``ChessEngineNode.analyse_fen``), no de la jugada de juego: así el robot puede
jugar en fácil y aun así juzgar como un maestro.

Módulo **puro**: ni audio ni ``python-chess``.  Las posiciones entran como
:class:`PositionEval`, así que todo esto se testea sin motor ni tarjeta de
sonido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from .. import config
from . import phrases
from .phrases import PhrasePicker

logger = logging.getLogger("magnus.voice.commentary")

# Puntuación equivalente a un mate, para poder comparar mates con centipeones.
MATE_SCORE_CP = 10_000


class MoveQuality(Enum):
    """Etiqueta de calidad de una jugada del humano."""

    BLUNDER = "error grave"
    MISTAKE = "error"
    INACCURACY = "imprecisión"
    NEUTRAL = "normal"
    GOOD = "buena"
    GREAT = "muy buena"

    @property
    def is_worth_saying(self) -> bool:
        """Las jugadas normales no se comentan: hablar de más cansa."""
        return self is not MoveQuality.NEUTRAL


class Advantage(Enum):
    """Quién está mejor en la posición."""

    ROBOT = "robot"
    EQUAL = "igualada"
    HUMAN = "humano"


class BoardProblem(Enum):
    """Por qué el tablero detectado no encaja con ninguna jugada legal."""

    ILLEGAL_MOVE = "jugada ilegal"        # una pieza cambió de sitio, pero no vale
    PIECE_IN_HAND = "pieza levantada"     # falta una pieza: la tiene en la mano
    CONFUSING = "posición irreconocible"  # demasiadas diferencias para adivinar


def diagnose_placement(
    expected: Mapping[str, str], detected: Mapping[str, str]
) -> BoardProblem:
    """Deduce QUÉ pasa cuando el tablero no corresponde a ninguna jugada legal.

    No decide si la jugada es legal —de eso ya se encargó el ``GameTracker``—,
    solo mira en qué se diferencian las dos posiciones para poder avisar de algo
    útil en vez de un genérico "no entiendo el tablero":

    * falta una pieza y no hay ninguna nueva -> la tiene en la mano
    * una pieza salió de una casilla y apareció en otra -> intentó una jugada ilegal
    * cualquier otra cosa -> posición irreconocible (mano encima, piezas caídas…)

    Función **pura** sobre dos diccionarios ``{casilla: símbolo}``: se testea sin
    tablero, sin cámara y sin motor.
    """
    vacated = [sq for sq in expected if sq not in detected]
    appeared = [sq for sq in detected if sq not in expected]
    replaced = [sq for sq in detected if sq in expected and detected[sq] != expected[sq]]

    if not appeared and not replaced and len(vacated) == 1:
        return BoardProblem.PIECE_IN_HAND
    if len(vacated) <= 1 and len(appeared) + len(replaced) <= 1:
        return BoardProblem.ILLEGAL_MOVE
    return BoardProblem.CONFUSING


@dataclass(frozen=True)
class PositionEval:
    """Evaluación de una posición, tal y como la devuelve el análisis.

    Attributes:
        evaluation_cp: centipeones **desde el lado que mueve** (``None`` si hay
            mate forzado, en cuyo caso manda ``mate_in``).
        mate_in: mate en N desde el lado que mueve (negativo = le hacen mate).
        side_to_move: ``"white"`` o ``"black"`` — de quién es el turno.
    """

    evaluation_cp: Optional[int] = None
    mate_in: Optional[int] = None
    side_to_move: str = "white"


def to_robot_pov(position: PositionEval, robot_side: str) -> Optional[int]:
    """Pasa una evaluación a centipeones **a favor del robot**.

    Positivo = el robot está mejor, gane quien gane el turno.  Devuelve ``None``
    si la posición no trae ninguna evaluación utilizable.
    """
    if position.mate_in is not None:
        # Un mate cercano vale más que uno lejano.
        magnitude = MATE_SCORE_CP - min(abs(position.mate_in), 99) * 100
        # Convenio UCI: mate_in > 0 = el lado que mueve DA mate; mate_in < 0 = se
        # lo hacen.  El caso 0 es "ya está mateado", así que también es perder —
        # de ahí el `> 0` y no `>= 0`: con `>=` el robot cantaba "error grave"
        # justo cuando acababan de darle mate a él.
        score = magnitude if position.mate_in > 0 else -magnitude
    elif position.evaluation_cp is not None:
        score = int(position.evaluation_cp)
    else:
        return None
    # La evaluación viene desde el lado que mueve; si no es el robot, se invierte.
    return score if position.side_to_move == robot_side else -score


def classify_delta(delta_cp: int) -> MoveQuality:
    """Etiqueta una jugada del humano por cuánto mejoró la posición del robot."""
    if delta_cp >= config.COMMENT_BLUNDER_CP:
        return MoveQuality.BLUNDER
    if delta_cp >= config.COMMENT_MISTAKE_CP:
        return MoveQuality.MISTAKE
    if delta_cp >= config.COMMENT_INACCURACY_CP:
        return MoveQuality.INACCURACY
    if delta_cp <= -config.COMMENT_GREAT_CP:
        return MoveQuality.GREAT
    if delta_cp <= -config.COMMENT_GOOD_CP:
        return MoveQuality.GOOD
    return MoveQuality.NEUTRAL


def classify_advantage(robot_cp: int) -> Advantage:
    """Traduce una evaluación (a favor del robot) en quién va ganando."""
    if robot_cp >= config.COMMENT_ADVANTAGE_CP:
        return Advantage.ROBOT
    if robot_cp <= -config.COMMENT_ADVANTAGE_CP:
        return Advantage.HUMAN
    return Advantage.EQUAL


_QUALITY_PHRASES = {
    MoveQuality.BLUNDER: phrases.BLUNDER,
    MoveQuality.MISTAKE: phrases.MISTAKE,
    MoveQuality.INACCURACY: phrases.INACCURACY,
    MoveQuality.GOOD: phrases.GOOD,
    MoveQuality.GREAT: phrases.GREAT,
}

_PROBLEM_PHRASES = {
    BoardProblem.ILLEGAL_MOVE: phrases.ILLEGAL_MOVE,
    BoardProblem.PIECE_IN_HAND: phrases.PIECE_IN_HAND,
    BoardProblem.CONFUSING: phrases.POSITION_CONFUSING,
}

_ADVANTAGE_PHRASES = {
    Advantage.ROBOT: phrases.ROBOT_WINNING,
    Advantage.HUMAN: phrases.ROBOT_LOSING,
    Advantage.EQUAL: phrases.EQUAL,
}


class Commentator:
    """Convierte evaluaciones en comentarios, sin repetirse ni hablar de más.

    Guarda la evaluación de la posición anterior, así que basta con irle
    pasando la de cada posición nueva::

        com = Commentator(robot_side="black")
        com.observe(eval_tras_jugada_del_robot)      # posición de referencia
        frase = com.comment_human_move(eval_tras_jugada_del_humano)
    """

    def __init__(
        self,
        robot_side: str = "black",
        picker: Optional[PhrasePicker] = None,
        advantage_every: int = config.COMMENT_ADVANTAGE_EVERY_MOVES,
    ):
        if robot_side not in ("white", "black"):
            raise ValueError(f"Color del robot inválido: {robot_side!r}.")
        self.robot_side = robot_side
        self.picker = picker or PhrasePicker()
        self.advantage_every = advantage_every
        self._previous_cp: Optional[int] = None
        self._last_advantage: Optional[Advantage] = None
        self._moves_since_advantage = 0
        self._last_problem: Optional[BoardProblem] = None

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    def observe(self, position: PositionEval) -> None:
        """Fija la posición de referencia (normalmente, tras mover el robot)."""
        robot_cp = to_robot_pov(position, self.robot_side)
        if robot_cp is not None:
            self._previous_cp = robot_cp

    def reset(self) -> None:
        """Nueva partida: olvida evaluaciones y frases usadas."""
        self._previous_cp = None
        self._last_advantage = None
        self._moves_since_advantage = 0
        self._last_problem = None
        self.picker.reset()

    # ------------------------------------------------------------------ #
    # Comentarios
    # ------------------------------------------------------------------ #
    def judge_human_move(self, position: PositionEval) -> Optional[MoveQuality]:
        """Etiqueta la jugada del humano comparando con la posición anterior.

        Devuelve ``None`` la primera vez (todavía no hay con qué comparar).
        Actualiza la referencia, así que se llama una vez por jugada.
        """
        robot_cp = to_robot_pov(position, self.robot_side)
        if robot_cp is None:
            return None
        previous = self._previous_cp
        self._previous_cp = robot_cp
        if previous is None:
            return None

        quality = classify_delta(robot_cp - previous)
        logger.debug(
            "Jugada del humano: %+d cp (antes %+d, ahora %+d) -> %s",
            robot_cp - previous, previous, robot_cp, quality.value,
        )
        return quality

    def comment_human_move(self, position: PositionEval) -> Optional[str]:
        """Frase sobre la jugada del humano, o ``None`` si no merece comentario."""
        quality = self.judge_human_move(position)
        if quality is None or not quality.is_worth_saying:
            return None
        return self.picker.pick(_QUALITY_PHRASES[quality])

    def comment_advantage(self, position: PositionEval) -> Optional[str]:
        """Frase sobre quién va ganando, si toca decirla.

        Solo habla cuando **cambia** quién está mejor y han pasado unas cuantas
        jugadas desde el último comentario de este tipo: repetir "voy ganando"
        cada jugada es insoportable.
        """
        robot_cp = to_robot_pov(position, self.robot_side)
        if robot_cp is None:
            return None
        self._moves_since_advantage += 1
        advantage = classify_advantage(robot_cp)
        if advantage is self._last_advantage:
            return None
        if self._moves_since_advantage < self.advantage_every:
            return None
        self._last_advantage = advantage
        self._moves_since_advantage = 0
        return self.picker.pick(_ADVANTAGE_PHRASES[advantage])

    def comment_board_problem(
        self, expected: Mapping[str, str], detected: Mapping[str, str]
    ) -> Optional[str]:
        """Avisa de por qué el tablero no encaja, sin repetirse.

        Devuelve ``None`` si ya se avisó de ese mismo problema y todavía no se
        ha resuelto: durante una jugada larga el aviso se dispararía en bucle.
        """
        problem = diagnose_placement(expected, detected)
        if problem is self._last_problem:
            return None
        self._last_problem = problem
        return self.picker.pick(_PROBLEM_PHRASES[problem])

    def board_is_fine_again(self) -> Optional[str]:
        """Frase de "ya está bien" si venía de un aviso; ``None`` si no."""
        if self._last_problem is None:
            return None
        self._last_problem = None
        return self.picker.pick(phrases.BOARD_FIXED)

    def comment_game_end(
        self, is_checkmate: bool, winner_is_robot: bool = False
    ) -> str:
        """Frase de cierre de partida."""
        if not is_checkmate:
            return self.picker.pick(phrases.DRAW)
        group = phrases.ROBOT_WINS if winner_is_robot else phrases.HUMAN_WINS
        return self.picker.pick(group)
