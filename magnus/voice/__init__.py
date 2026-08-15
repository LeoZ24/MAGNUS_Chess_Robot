"""Módulo de voz de MAGNUS: el robot narra y comenta la partida.

Piezas:

    * ``backend.py``     — motores de voz: Piper (local, recomendado), ``say``
      de macOS y uno falso para tests.
    * ``speech_text.py`` — notación de ajedrez -> texto pronunciable en español
      ("g1" -> "ge uno", "N" -> "caballo").
    * ``phrases.py``     — catálogo de frases con variantes, para no repetirse.
    * ``commentary.py``  — decide QUÉ comentar comparando evaluaciones.
    * ``voice_node.py``  — nodo con cola e hilo propio: hablar nunca bloquea la
      visión ni el brazo.

El nodo de voz **consume** información (``MoveResponse`` y evaluaciones); no
produce estado de la partida ni habla directamente con visión o con el brazo.
"""

from .backend import (
    FakeSpeechBackend,
    MacSayBackend,
    PiperBackend,
    SpeechBackend,
    SpeechError,
    default_backend,
    find_piper_model,
)
from .commentary import (
    Advantage,
    Commentator,
    MoveQuality,
    PositionEval,
    classify_advantage,
    classify_delta,
    to_robot_pov,
)
from .phrases import PhrasePicker
from .speech_text import describe_move, piece_name, square_to_speech
from .voice_node import VoiceNode, silent_voice

__all__ = [
    "SpeechBackend",
    "PiperBackend",
    "MacSayBackend",
    "FakeSpeechBackend",
    "SpeechError",
    "default_backend",
    "find_piper_model",
    "square_to_speech",
    "piece_name",
    "describe_move",
    "PhrasePicker",
    "PositionEval",
    "MoveQuality",
    "Advantage",
    "Commentator",
    "classify_delta",
    "classify_advantage",
    "to_robot_pov",
    "VoiceNode",
    "silent_voice",
]
