"""Backends de síntesis de voz (TTS) para MAGNUS.

Mismo patrón que ``magnus/engine/backend.py`` y ``magnus/arm/backend.py``: una
interfaz abstracta y varias implementaciones, para poder testear sin hardware
de audio.

    * :class:`PiperBackend`  — voz neuronal **local** (recomendado).  Funciona
      sin internet, así que no depende del wifi de la feria, y el mismo código
      sirve en el Mac y en una Raspberry Pi si el proyecto continúa.  Necesita
      el paquete ``piper-tts`` y un modelo de voz descargado (60-110 MB, fuera
      de git).
    * :class:`MacSayBackend` — el comando ``say`` de macOS.  Cero instalación,
      pero solo existe en Mac: sirve de respaldo mientras se prepara Piper.
    * :class:`FakeSpeechBackend` — no suena nada, solo guarda lo que se dijo.
      Es el que usan los tests y el modo silencioso.

Descargar una voz de Piper (una vez, con internet)::

    python3 -m piper.download_voices es_MX-claude-high --data-dir voices/

Y luego ``PiperBackend(model="voices/es_MX-claude-high.onnx")``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
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
@dataclass(frozen=True)
class SystemVoice:
    """Una voz instalada en el sistema (``say -v '?'``)."""

    name: str
    locale: str          # "es_MX", "es_ES", ...
    sample: str = ""     # frase de ejemplo que imprime el propio `say`

    @property
    def language(self) -> str:
        return self.locale.split("_")[0]

    @property
    def is_spanish(self) -> bool:
        return self.language == "es"

    @property
    def probably_male(self) -> bool:
        """Heurística por nombre: ``say`` no dice el género de la voz."""
        return self.name.split()[0] in MALE_SPANISH_VOICES


# Voces masculinas en español conocidas de macOS.  `say` no expone el género,
# así que esta lista solo sirve para *sugerir* — la última palabra la tiene el
# oído (examples/audition_voices.py).
MALE_SPANISH_VOICES: frozenset[str] = frozenset({
    "Jorge",      # es_ES
    "Juan",       # es_MX
    "Diego",      # es_AR
    "Carlos",     # es_MX (en algunas versiones)
    "Enrique",    # es_ES (en algunas versiones)
})


def list_system_voices(spanish_only: bool = False) -> list[SystemVoice]:
    """Voces instaladas en macOS, leídas de ``say -v '?'``.

    Devuelve lista vacía si no hay ``say`` (no es macOS) o si falla.
    """
    if shutil.which("say") is None:
        return []
    try:
        output = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.debug("No se pudieron listar las voces del sistema: %s", exc)
        return []

    voices: list[SystemVoice] = []
    for line in output.splitlines():
        head, _, sample = line.partition("#")
        parts = head.split()
        if len(parts) < 2:
            continue
        # El nombre puede llevar espacios ("Eddy (Spanish (Mexico))"), así que
        # el idioma es siempre el ÚLTIMO campo y el nombre todo lo anterior.
        voice = SystemVoice(
            name=" ".join(parts[:-1]), locale=parts[-1], sample=sample.strip()
        )
        if spanish_only and not voice.is_spanish:
            continue
        voices.append(voice)
    return voices


class MacSayBackend(SpeechBackend):
    """Voz mediante el comando ``say`` de macOS (respaldo sin instalación).

    Las voces en español instaladas se listan con ``say -v '?' | grep es_``.
    En Ajustes > Accesibilidad > Contenido hablado se pueden descargar voces
    "mejoradas"/"premium", bastante mejores que las que vienen de serie.

    Si la voz configurada no está instalada **no falla**: busca otra en español
    (masculina si la hay, porque MAGNUS habla en masculino) y avisa por log.
    Quedarse sin voz a mitad de una demostración por un nombre mal escrito sería
    absurdo.
    """

    def __init__(
        self,
        voice: str = config.VOICE_MACOS_VOICE,
        rate: int = config.VOICE_MACOS_RATE,
        prefer_male: bool = True,
    ):
        self.rate = rate
        self.prefer_male = prefer_male
        self.voice = self._resolve_voice(voice)

    def _resolve_voice(self, wanted: str) -> Optional[str]:
        """Devuelve la voz a usar: la pedida si existe, o la mejor alternativa."""
        installed = list_system_voices()
        if not installed:                      # no es macOS, o `say` no responde
            return wanted
        names = {v.name for v in installed}
        if wanted in names:
            return wanted

        spanish = [v for v in installed if v.is_spanish]
        candidates = [v for v in spanish if v.probably_male] if self.prefer_male else []
        candidates = candidates or spanish
        if not candidates:
            logger.warning(
                "No hay ninguna voz en español instalada; se usa la voz por "
                "defecto del sistema. Descárgalas en Ajustes > Accesibilidad > "
                "Contenido hablado > Voz del sistema."
            )
            return None
        chosen = candidates[0].name
        logger.warning(
            "La voz %r no está instalada; se usa %r (%s). Voces en español "
            "disponibles: %s",
            wanted, chosen, candidates[0].locale,
            ", ".join(v.name for v in spanish),
        )
        return chosen

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
def model_quality(name: str) -> str:
    """Nivel de calidad que declara el nombre del modelo (``…-high`` -> "high").

    Piper publica cada voz en varios niveles y **se nota muchísimo**: ``x_low``
    y ``low`` van a 16 kHz y suenan apagadas, "como debajo del agua"; ``medium``
    sube a 22 kHz; ``high`` usa además una red mayor y es la única que suena
    realmente natural.  Para MAGNUS interesa ``high``.
    """
    for quality in ("x_low", "low", "medium", "high"):
        if name.endswith(f"-{quality}"):
            return quality
    return "desconocida"


def find_piper_model(
    name: str = config.VOICE_PIPER_MODEL, explicit: Optional[str] = None
) -> Optional[Path]:
    """Busca el ``.onnx`` de una voz de Piper.

    Orden: ruta explícita -> ``$MAGNUS_VOICES_DIR`` -> ``./voices`` ->
    ``~/.local/share/magnus/voices``.  Si la voz configurada no está descargada
    pero hay **otra** en esas carpetas, se usa esa (y se avisa): tener una voz
    distinta a la esperada es mucho mejor que quedarse mudo.
    """
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None

    directories = [d for d in _VOICE_DIRS if d is not None]
    for directory in directories:
        candidate = directory / f"{name}.onnx"
        if candidate.is_file():
            return candidate

    for directory in directories:
        if not directory.is_dir():
            continue
        alternatives = sorted(directory.glob("*.onnx"))
        if alternatives:
            logger.warning(
                "La voz %r no está descargada; se usa %s. Para descargar la "
                "configurada: %s -m piper.download_voices %s --data-dir %s",
                name, alternatives[0].name, sys.executable, name, directory,
            )
            return alternatives[0]
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
                "No se encontró ningún modelo de voz de Piper. Descarga uno con:\n"
                f"  {sys.executable} -m piper.download_voices "
                f"{config.VOICE_PIPER_MODEL} --data-dir voices/"
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise SpeechError(
                "Falta el paquete de Piper en ESTE Python. Instálalo con:\n"
                f"  {sys.executable} -m pip install piper-tts"
            ) from exc
        logger.info("Cargando voz de Piper: %s", self.model_path)
        self._voice = PiperVoice.load(str(self.model_path))
        return self._voice

    def synthesize_to_file(self, text: str, path: Path) -> Path:
        """Sintetiza ``text`` en un WAV (sin reproducirlo).

        **No se aplica ningún efecto de audio**: ni filtros, ni cambios de tono,
        ni resampleo.  Lo único que se toca es la velocidad (``length_scale``),
        y con el valor por defecto (1.0) ni eso — sale exactamente la voz del
        modelo.  Si suena rara, es el modelo: prueba uno de calidad ``high``.
        """
        from piper import SynthesisConfig

        voice = self._load()
        syn_config = SynthesisConfig(length_scale=self.length_scale)
        with wave.open(str(path), "wb") as wav_file:
            # set_wav_format=True (por defecto) hace que Piper escriba la
            # cabecera con SU frecuencia de muestreo.  Escribirla a mano con
            # otra frecuencia es justo lo que produce el efecto "bajo el agua".
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
        "Ningún motor de voz disponible; MAGNUS jugará en silencio. Para tener "
        "voz:\n  %s -m pip install piper-tts\n"
        "  %s -m piper.download_voices %s --data-dir voices/",
        sys.executable, sys.executable, config.VOICE_PIPER_MODEL,
    )
    return FakeSpeechBackend()
