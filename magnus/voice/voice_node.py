"""Nodo de voz de MAGNUS: dice en voz alta lo que pasa en la partida.

Sigue el patrón del resto de nodos (backend intercambiable + context manager),
con una particularidad importante: **habla en un hilo aparte**.  Sintetizar y
reproducir una frase tarda entre medio segundo y dos; si eso ocurriera en el
bucle principal, la visión (que corre a ~14 fps) se congelaría en cada jugada.

    visión/engine  --(frases)-->  cola  -->  hilo de voz  -->  backend TTS

Reglas de convivencia pensadas para una demostración en vivo:

    * La cola es corta (``config.VOICE_QUEUE_MAX``): si el robot se queda atrás,
      se descartan las frases **más viejas**, no las nuevas.  Comentar una
      jugada de hace tres turnos es peor que callarse.
    * :meth:`say_now` vacía lo pendiente: para avisos importantes (jaque mate)
      que no deben esperar detrás de la cola.
    * :attr:`last_phrase` guarda lo último dicho, para mostrarlo como subtítulo
      en el dashboard — en una feria ruidosa, leerlo salva la demo.
    * Silenciar (:meth:`set_muted`) no rompe nada: sigue registrando la frase
      para el subtítulo, simplemente no suena.
    * Un fallo del motor de voz **nunca** interrumpe la partida: se registra y
      el robot sigue jugando en silencio.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

from .. import config
from ..core.messages import MoveResponse
from . import phrases
from .backend import FakeSpeechBackend, SpeechBackend, SpeechError, default_backend
from .commentary import Commentator, PositionEval
from .phrases import PhrasePicker
from .speech_text import describe_move

logger = logging.getLogger("magnus.voice.node")

_STOP = object()        # centinela para terminar el hilo


class VoiceNode:
    """Cola de frases + hilo que las va diciendo.

    Ejemplo::

        with VoiceNode(robot_side="black") as voz:
            voz.greet()
            voz.announce_move(resp)                  # "Muevo el caballo…"
            voz.comment_human_move(evaluacion)       # "Buena jugada."
    """

    def __init__(
        self,
        backend: Optional[SpeechBackend] = None,
        robot_side: str = "black",
        muted: bool = False,
        queue_max: int = config.VOICE_QUEUE_MAX,
        picker: Optional[PhrasePicker] = None,
    ):
        self.backend = backend or default_backend()
        self.picker = picker or PhrasePicker()
        self.commentator = Commentator(robot_side=robot_side, picker=self.picker)
        self._muted = muted
        self._queue: "queue.Queue" = queue.Queue(maxsize=max(1, queue_max))
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_phrase: Optional[str] = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    def start(self) -> "VoiceNode":
        if self._started:
            return self
        self._thread = threading.Thread(target=self._run, name="magnus-voice", daemon=True)
        self._thread.start()
        self._started = True
        logger.info("VoiceNode listo (%s).", type(self.backend).__name__)
        return self

    def shutdown(self, wait: bool = True) -> None:
        """Termina el hilo; con ``wait`` deja acabar la frase en curso."""
        if not self._started:
            return
        self._queue.put(_STOP)
        if wait and self._thread is not None:
            self._thread.join(timeout=5.0)
        self._started = False
        self.backend.close()
        logger.info("VoiceNode detenido.")

    def __enter__(self) -> "VoiceNode":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    @property
    def last_phrase(self) -> Optional[str]:
        """Última frase dicha (para subtitularla en el dashboard)."""
        with self._lock:
            return self._last_phrase

    @property
    def is_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        logger.info("Voz %s.", "silenciada" if muted else "activada")

    def toggle_mute(self) -> bool:
        self.set_muted(not self._muted)
        return self._muted

    @property
    def is_speaking(self) -> bool:
        """``True`` si queda algo por decir."""
        return not self._queue.empty()

    # ------------------------------------------------------------------ #
    # Emisión
    # ------------------------------------------------------------------ #
    def say(self, text: Optional[str]) -> None:
        """Encola una frase.  Si la cola está llena, descarta la más vieja."""
        if not text:
            return
        with self._lock:
            self._last_phrase = text
        if not self._started:
            self.start()
        while True:
            try:
                self._queue.put_nowait(text)
                return
            except queue.Full:
                try:
                    descartada = self._queue.get_nowait()
                    logger.debug("Cola de voz llena; se descarta: %r", descartada)
                except queue.Empty:      # otro hilo la vació entre medias
                    continue

    def say_now(self, text: Optional[str]) -> None:
        """Dice algo importante ya: descarta lo pendiente y encola esto."""
        if not text:
            return
        self.clear()
        self.say(text)

    def clear(self) -> None:
        """Vacía lo pendiente (no corta la frase que ya está sonando)."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # ------------------------------------------------------------------ #
    # Eventos de la partida
    # ------------------------------------------------------------------ #
    def greet(self) -> None:
        self.say(self.picker.pick(phrases.GREETING))

    def announce_move(self, resp: MoveResponse, speaker: str = "robot") -> None:
        """Narra una jugada ("Muevo el caballo de ge uno a efe tres")."""
        try:
            self.say(describe_move(resp, speaker=speaker))
        except Exception as exc:            # noqa: BLE001 - la voz nunca corta la partida
            logger.warning("No se pudo narrar la jugada %s: %s", resp.uci, exc)

    def comment_human_move(self, position: PositionEval) -> Optional[str]:
        """Comenta la jugada del rival según cuánto cambió la evaluación."""
        frase = self.commentator.comment_human_move(position)
        self.say(frase)
        return frase

    def comment_advantage(self, position: PositionEval) -> Optional[str]:
        """Comenta quién va ganando (solo cuando cambia, y no muy a menudo)."""
        frase = self.commentator.comment_advantage(position)
        self.say(frase)
        return frase

    def announce_game_end(self, is_checkmate: bool, winner_is_robot: bool = False) -> None:
        self.say_now(self.commentator.comment_game_end(is_checkmate, winner_is_robot))

    def new_game(self) -> None:
        """Reinicia el estado de comentarios (evaluaciones y frases usadas)."""
        self.clear()
        self.commentator.reset()

    # ------------------------------------------------------------------ #
    # Hilo
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            if self._muted:
                continue
            try:
                self.backend.speak(item)
            except SpeechError as exc:
                logger.warning("La voz falló (%s); se sigue en silencio.", exc)
            except Exception as exc:        # noqa: BLE001 - un fallo de audio no tumba nada
                logger.warning("Error inesperado en la voz: %s", exc)


def silent_voice(robot_side: str = "black") -> VoiceNode:
    """VoiceNode que no suena (tests y demos sin audio)."""
    return VoiceNode(backend=FakeSpeechBackend(), robot_side=robot_side)
