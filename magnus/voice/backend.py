"""Backends de síntesis de voz (TTS) para MAGNUS.

Mismo patrón que ``magnus/engine/backend.py`` y ``magnus/arm/backend.py``: una
interfaz abstracta y varias implementaciones, para poder testear sin hardware
de audio.

    * :class:`PiperBackend`  — voz neuronal **local** (recomendado).  Funciona
      sin internet y corre igual en el Mac y en la Raspberry Pi, así que el
      código sobrevive la migración.  Necesita el paquete ``piper-tts`` y un
      modelo de voz descargado.
    * :class:`MacSayBackend` — el comando ``say`` de macOS.  Cero instalación,
      pero solo existe en Mac: sirve de respaldo mientras se prepara Piper.
    * :class:`FakeSpeechBackend` — no suena nada, solo guarda lo que se dijo.
      Es el que usan los tests y el modo silencioso.

Descargar una voz de Piper (una vez, con internet)::

    python -m piper.download_voices es_ES-davefx-medium --data-dir voices/

Y luego ``PiperBackend(model="voices/es_ES-davefx-medium.onnx")``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

from .. import config

logger = logging.getLogger("magnus.voice.backend")

# Carpetas donde se buscan los modelos de Piper si no se indica una ruta.
_VOICE_DIRS = (
    Path(os.environ.get("MAGNUS_VOICES_DIR", "")) if os.environ.get("MAGNUS_VOICES_DIR")
    else None,
    Path("voices"),
    Path.home() / ".local" / "share" / "magnus" / "voices",
)

# Reproductores de WAV por sistema, en orden de preferencia.
_WAV_PLAYERS = (
    ("afplay", ()),          # macOS
    ("aplay", ("-q",)),      # Linux/ALSA
    ("paplay", ()),          # Linux/PulseAudio
    ("ffplay", ("-nodisp", "-autoexit", "-loglevel", "quiet")),
)


class SpeechError(Exception):
    """No se pudo sintetizar o reproducir la voz."""


class SpeechBackend(ABC):
    """Interfaz de cualquier motor de voz para MAGNUS."""

    @abstractmethod
    def speak(self, text: str) -> None:
        """Dice ``text`` en voz alta.  **Bloquea** hasta terminar de hablar.

        El :class:`~magnus.voice.voice_node.VoiceNode` lo llama desde su propio
        hilo, así que bloquear aquí no congela la partida.
        """

    @property
    def is_available(self) -> bool:
        """``True`` si el backend puede hablar de verdad en esta máquina."""
        return True

    def close(self) -> None:
        """Libera recursos (por defecto no hay nada que liberar)."""

    def __enter__(self) -> "SpeechBackend":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Falso (tests y modo silencioso)
# --------------------------------------------------------------------------- #
class FakeSpeechBackend(SpeechBackend):
    """No emite sonido: registra las frases para poder comprobarlas en tests."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        logger.debug("[voz simulada] %s", text)

    @property
    def last(self) -> Optional[str]:
        return self.spoken[-1] if self.spoken else None


# --------------------------------------------------------------------------- #
# macOS `say`
# --------------------------------------------------------------------------- #
class MacSayBackend(SpeechBackend):
    """Voz mediante el comando ``say`` de macOS (respaldo sin instalación).

    Las voces en español instaladas se listan con ``say -v '?' | grep es_``.
    En Ajustes > Accesibilidad > Contenido hablado se pueden descargar voces
    "mejoradas"/"premium", bastante mejores que las que vienen de serie.
    """

    def __init__(
        self,
        voice: str = config.VOICE_MACOS_VOICE,
        rate: int = config.VOICE_MACOS_RATE,
    ):
        self.voice = voice
        self.rate = rate

    @property
    def is_available(self) -> bool:
        return shutil.which("say") is not None

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        command = ["say", "-r", str(self.rate)]
        if self.voice:
            command += ["-v", self.voice]
        command.append(text)
        try:
            subprocess.run(command, check=True)
        except FileNotFoundError as exc:
            raise SpeechError("El comando `say` no existe (¿no estás en macOS?).") from exc
        except subprocess.CalledProcessError as exc:
            raise SpeechError(
                f"`say` falló ({exc.returncode}). ¿Existe la voz {self.voice!r}? "
                "Lista las disponibles con: say -v '?'"
            ) from exc


# --------------------------------------------------------------------------- #
# Piper (neuronal, local)
# --------------------------------------------------------------------------- #
def find_piper_model(
    name: str = config.VOICE_PIPER_MODEL, explicit: Optional[str] = None
) -> Optional[Path]:
    """Busca el ``.onnx`` de una voz de Piper.

    Orden: ruta explícita -> ``$MAGNUS_VOICES_DIR`` -> ``./voices`` ->
    ``~/.local/share/magnus/voices``.  Devuelve ``None`` si no aparece.
    """
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    for directory in _VOICE_DIRS:
        if directory is None:
            continue
        candidate = directory / f"{name}.onnx"
        if candidate.is_file():
            return candidate
    return None


def _play_wav(path: Path) -> None:
    """Reproduce un WAV con el primer reproductor disponible del sistema."""
    for player, args in _WAV_PLAYERS:
        if shutil.which(player):
            subprocess.run([player, *args, str(path)], check=True)
            return
    raise SpeechError(
        "No hay ningún reproductor de audio disponible "
        f"(se buscaron: {', '.join(p for p, _ in _WAV_PLAYERS)})."
    )


class PiperBackend(SpeechBackend):
    """Voz neuronal local con `Piper <https://github.com/OHF-Voice/piper1-gpl>`_.

    Es la opción recomendada: offline (no depende del wifi de la feria), gratis
    y con el mismo código en macOS y en la Raspberry Pi.

    Args:
        model: ruta al ``.onnx``; si es ``None`` se busca por nombre.
        model_name: nombre de la voz a buscar (p. ej. ``"es_ES-davefx-medium"``).
        length_scale: velocidad del habla (``<1`` más rápido, ``>1`` más lento).
    """

    def __init__(
        self,
        model: Optional[str] = None,
        model_name: str = config.VOICE_PIPER_MODEL,
        length_scale: float = config.VOICE_LENGTH_SCALE,
    ):
        self.model_path = find_piper_model(model_name, model)
        self.length_scale = length_scale
        self._voice = None          # se carga perezosamente (tarda ~1 s)

    @property
    def is_available(self) -> bool:
        if self.model_path is None:
            return False
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._voice is not None:
            return self._voice
        if self.model_path is None:
            raise SpeechError(
                "No se encontró ningún modelo de voz de Piper. Descarga uno con: "
                f"python -m piper.download_voices {config.VOICE_PIPER_MODEL} "
                "--data-dir voices/"
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise SpeechError(
                "Falta el paquete de Piper. Instálalo con: pip install piper-tts"
            ) from exc
        logger.info("Cargando voz de Piper: %s", self.model_path)
        self._voice = PiperVoice.load(str(self.model_path))
        return self._voice

    def synthesize_to_file(self, text: str, path: Path) -> Path:
        """Sintetiza ``text`` en un WAV (sin reproducirlo)."""
        from piper import SynthesisConfig

        voice = self._load()
        syn_config = SynthesisConfig(length_scale=self.length_scale)
        with wave.open(str(path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        return path

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            self.synthesize_to_file(text, wav_path)
            _play_wav(wav_path)
        except subprocess.CalledProcessError as exc:
            raise SpeechError(f"No se pudo reproducir el audio: {exc}") from exc
        finally:
            wav_path.unlink(missing_ok=True)

    def close(self) -> None:
        self._voice = None


# --------------------------------------------------------------------------- #
# Selección automática
# --------------------------------------------------------------------------- #
def default_backend(
    prefer: Sequence[str] = ("piper", "say"), **kwargs
) -> SpeechBackend:
    """Devuelve el mejor backend disponible en esta máquina.

    Prueba Piper primero (offline y portable) y cae en ``say`` de macOS si no
    hay modelo descargado.  Si no hay ninguno, devuelve
    :class:`FakeSpeechBackend`: el robot se queda mudo pero **nada falla**, que
    es justo lo que se quiere en mitad de una demostración.
    """
    builders = {
        "piper": lambda: PiperBackend(**kwargs),
        "say": lambda: MacSayBackend(),
    }
    for name in prefer:
        builder = builders.get(name)
        if builder is None:
            continue
        try:
            backend = builder()
        except Exception as exc:                     # noqa: BLE001 - nunca romper por la voz
            logger.debug("Backend de voz %s no utilizable: %s", name, exc)
            continue
        if backend.is_available:
            logger.info("Voz: usando %s.", type(backend).__name__)
            return backend
    logger.warning(
        "Ningún motor de voz disponible; MAGNUS jugará en silencio. "
        "Para tener voz: pip install piper-tts && "
        f"python -m piper.download_voices {config.VOICE_PIPER_MODEL} --data-dir voices/"
    )
    return FakeSpeechBackend()
