"""Frases que dice MAGNUS, en español y con variantes.

Un robot que repite siempre la misma frase deja de tener gracia a la tercera
jugada.  Aquí cada situación tiene varias formulaciones y :class:`PhrasePicker`
se encarga de ir alternándolas sin repetir la última.

El tono buscado es **robot amable**: frases cortas, directas, un punto secas
pero nunca borde — el rival suele ser alguien que está aprendiendo.

Módulo **puro**: solo texto.  Ni audio ni ajedrez.
"""

from __future__ import annotations

import random
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Catálogo de frases por situación
# --------------------------------------------------------------------------- #
# Calidad de la jugada del humano (ver commentary.MoveQuality).
BLUNDER = (
    "Uy. Eso fue un error grave.",
    "Ese movimiento te va a costar caro.",
    "Ahí cometiste un error importante.",
)
MISTAKE = (
    "Esa jugada no fue buena.",
    "Ahí te equivocaste.",
    "Mmm. Esa no era la mejor idea.",
)
INACCURACY = (
    "Había algo mejor, pero no pasa nada.",
    "Jugada imprecisa.",
    "Se puede jugar mejor esa posición.",
)
GOOD = (
    "Buena jugada. Sigue así.",
    "Bien jugado.",
    "Me gusta esa jugada.",
)
GREAT = (
    "Excelente. No me esperaba eso.",
    "Muy buena. Me complicaste la vida.",
    "Gran jugada. Así se hace.",
)

# Estado de la partida según la ventaja.
ROBOT_WINNING = (
    "Voy tomando ventaja.",
    "La posición me favorece ahora.",
    "Creo que tengo la ventaja.",
)
ROBOT_LOSING = (
    "Vas ganando. Bien ahí.",
    "Tienes ventaja, lo reconozco.",
    "Me estás complicando la partida.",
)
EQUAL = (
    "La partida está igualada.",
    "Vamos parejos.",
    "Esto está muy equilibrado.",
)

# Momentos de la partida.
GREETING = (
    "Hola. Soy MAGNUS. Vamos a jugar.",
    "Listo para jugar. Suerte.",
    "Empezamos. Que gane el mejor.",
)
YOUR_TURN = (
    "Te toca.",
    "Tu turno.",
    "Adelante, mueve.",
)
THINKING = (
    "Déjame pensar.",
    "Estoy calculando.",
    "Un momento.",
)
ROBOT_WINS = (
    "Jaque mate. Gané esta partida. Buen intento.",
    "Jaque mate. Muy buena partida, de verdad.",
)
HUMAN_WINS = (
    "Jaque mate. Ganaste. Bien jugado, en serio.",
    "Me ganaste. Enhorabuena.",
)
DRAW = (
    "Tablas. Partida pareja.",
    "Empate. Nadie pudo con el otro.",
)
ILLEGAL_POSITION = (
    "No reconozco esa posición. ¿Puedes revisar el tablero?",
    "Algo no cuadra en el tablero. Revísalo, por favor.",
)


class PhrasePicker:
    """Elige frases de un grupo evitando repetir la última usada.

    Con ``rng`` fijo (``random.Random(0)``) el resultado es determinista, que es
    justo lo que necesitan los tests.
    """

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()
        self._last: dict[int, str] = {}     # id(grupo) -> última frase dicha

    def pick(self, options: Iterable[str]) -> str:
        """Devuelve una frase del grupo, distinta de la anterior si se puede."""
        choices = tuple(options)
        if not choices:
            raise ValueError("No hay frases entre las que elegir.")
        key = id(choices) if len(choices) == 1 else hash(choices)
        previous = self._last.get(key)
        pool = [c for c in choices if c != previous] or list(choices)
        chosen = self._rng.choice(pool)
        self._last[key] = chosen
        return chosen

    def reset(self) -> None:
        """Olvida el histórico (nueva partida)."""
        self._last.clear()
