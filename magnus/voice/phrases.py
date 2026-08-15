"""Frases que dice MAGNUS, en español y con variantes.

Un robot que repite siempre la misma frase deja de tener gracia a la tercera
jugada.  Aquí cada situación tiene varias formulaciones y :class:`PhrasePicker`
se encarga de ir alternándolas sin repetir la última.

El tono buscado es **robot amable**: frases cortas, directas, un punto secas
pero nunca bordes — el rival suele ser alguien que está aprendiendo, y muchas
veces es un niño o un miembro del jurado que juega por primera vez.

Módulo **puro**: solo texto.  Ni audio ni ajedrez.
"""

from __future__ import annotations

import random
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Calidad de la jugada del humano (ver commentary.MoveQuality)
# --------------------------------------------------------------------------- #
BLUNDER = (
    "Uy. Eso fue un error grave.",
    "Ese movimiento te va a costar caro.",
    "Ahí cometiste un error importante.",
    "Ay. Esa jugada me deja muy bien a mí.",
    "Creo que no viste lo que venía. Mala jugada.",
    "Eso fue un regalo. Gracias.",
    "Con esa jugada me lo pusiste fácil.",
)
MISTAKE = (
    "Esa jugada no fue buena.",
    "Ahí te equivocaste.",
    "Mmm. Esa no era la mejor idea.",
    "No me convence esa jugada.",
    "Esa te la voy a aprovechar.",
    "Había opciones mejores.",
)
INACCURACY = (
    "Había algo mejor, pero no pasa nada.",
    "Jugada imprecisa.",
    "Se puede jugar mejor esa posición.",
    "Casi. Esa no era del todo precisa.",
    "Aceptable, aunque no la mejor.",
)
GOOD = (
    "Buena jugada. Sigue así.",
    "Bien jugado.",
    "Me gusta esa jugada.",
    "Correcto. Muy bien.",
    "Buena elección.",
    "Esa estuvo bien pensada.",
)
GREAT = (
    "Excelente. No me esperaba eso.",
    "Muy buena. Me complicaste la vida.",
    "Gran jugada. Así se hace.",
    "Vaya. Esa fue de nivel.",
    "Me sorprendiste. Muy bien jugado.",
    "Esa jugada es de las buenas. En serio.",
)

# --------------------------------------------------------------------------- #
# Estado de la partida según la ventaja
# --------------------------------------------------------------------------- #
ROBOT_WINNING = (
    "Voy tomando ventaja.",
    "La posición me favorece ahora.",
    "Creo que tengo la ventaja.",
    "Ahora estoy mejor.",
    "La partida se me está poniendo cómoda.",
)
ROBOT_LOSING = (
    "Vas ganando. Bien ahí.",
    "Tienes ventaja, lo reconozco.",
    "Me estás complicando la partida.",
    "Estás mejor que yo. Enhorabuena.",
    "Voy por detrás. Tendré que espabilar.",
)
EQUAL = (
    "La partida está igualada.",
    "Vamos parejos.",
    "Esto está muy equilibrado.",
    "Ninguno de los dos manda todavía.",
    "Partida pareja hasta ahora.",
)

# --------------------------------------------------------------------------- #
# Momentos de la partida
# --------------------------------------------------------------------------- #
GREETING = (
    "Hola. Soy MAGNUS, un robot que juega ajedrez. Vamos allá.",
    "Listo para jugar. Suerte.",
    "Empezamos. Que gane el mejor.",
    "Hola. Coloca tu jugada cuando quieras.",
    "Todo listo. Te dejo empezar.",
)
YOUR_TURN = (
    "Te toca.",
    "Tu turno.",
    "Adelante, mueve.",
    "Es tu jugada.",
    "Te escucho. Digo, te miro.",
)
THINKING = (
    "Déjame pensar.",
    "Estoy calculando.",
    "Un momento.",
    "Dame un segundo.",
)
WAITING = (
    "Tómate tu tiempo.",
    "Sigo esperando tu jugada.",
    "Cuando quieras, mueve.",
    "No hay prisa, piénsalo bien.",
)
ROBOT_WINS = (
    "Jaque mate. Gané esta partida. Buen intento.",
    "Jaque mate. Muy buena partida, de verdad.",
    "Es mate. Gracias por jugar conmigo.",
    "Jaque mate. La próxima te saldrá mejor.",
)
HUMAN_WINS = (
    "Jaque mate. Ganaste. Bien jugado, en serio.",
    "Me ganaste. Enhorabuena.",
    "Jaque mate. Me has superado. Bien hecho.",
    "Perdí. Y con méritos. Felicidades.",
)
DRAW = (
    "Tablas. Partida pareja.",
    "Empate. Nadie pudo con el otro.",
    "Tablas. Buen equilibrio.",
)

# --------------------------------------------------------------------------- #
# Reacciones durante la partida
# --------------------------------------------------------------------------- #
ROBOT_IN_CHECK = (
    "Me diste jaque.",
    "Jaque. Tengo que atender a mi rey.",
    "Buen jaque.",
)
ROBOT_LOST_PIECE = (
    "Me comiste una pieza.",
    "Esa me dolió.",
    "Ahí perdí material.",
    "Bien capturado.",
)
ENCOURAGEMENT = (
    "Sigue intentándolo, vas aprendiendo.",
    "No te desanimes, queda partida.",
    "Aguanta, todavía hay opciones.",
)

# --------------------------------------------------------------------------- #
# Problemas con el tablero (ver commentary.BoardProblem)
# --------------------------------------------------------------------------- #
ILLEGAL_MOVE = (
    "Esa jugada no es legal. Devuelve la pieza a su sitio, por favor.",
    "No puedes mover ahí. Inténtalo de nuevo.",
    "Esa jugada no está permitida. Prueba otra.",
    "Ojo, esa pieza no se mueve así. Corrígela, por favor.",
)
PIECE_IN_HAND = (
    "¿Tienes una pieza en la mano? Colócala cuando decidas.",
    "Falta una pieza en el tablero. Ponla donde quieras jugar.",
    "Sigo viendo un hueco. Coloca la pieza para continuar.",
)
POSITION_CONFUSING = (
    "No reconozco la posición. ¿Puedes revisar el tablero?",
    "Algo no cuadra en el tablero. Revísalo, por favor.",
    "Me perdí con las piezas. Comprueba que estén bien colocadas.",
)
BOARD_FIXED = (
    "Ahora sí. Seguimos.",
    "Perfecto, ya lo veo bien.",
    "Listo. Continuamos.",
)

# Compatibilidad: nombre antiguo del grupo de posición irreconocible.
ILLEGAL_POSITION = POSITION_CONFUSING


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
