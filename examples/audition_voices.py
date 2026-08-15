#!/usr/bin/env python3
"""Audición de voces: escucha las mismas frases con varias voces y elige una.

La voz del robot es una decisión de oído, no de código: este script sintetiza
frases reales de una partida con cada voz candidata para que decidas tú.

Dos motores:

    * ``say`` de macOS  — no requiere instalar nada; las voces ya están en el
      sistema (y se pueden descargar más, mejores, en Ajustes > Accesibilidad >
      Contenido hablado).
    * Piper             — voz neuronal local, la que se usará en la Raspberry
      Pi.  Requiere ``pip install piper-tts`` y descargar los modelos.

Uso::

    python3 examples/audition_voices.py                 # el motor disponible
    python3 examples/audition_voices.py --engine say    # solo voces de macOS
    python3 examples/audition_voices.py --engine say --all-spanish
    python3 examples/audition_voices.py --engine piper --play
    python3 examples/audition_voices.py --list          # solo listar, sin hablar

Cuando elijas, ponla en ``magnus/config.py``:

    VOICE_MACOS_VOICE  = "Juan"                # si usas `say`
    VOICE_PIPER_MODEL  = "es_MX-claude-high"   # si usas Piper
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

from magnus import config  # noqa: E402
from magnus.voice.backend import (  # noqa: E402
    MacSayBackend,
    PiperBackend,
    _play_wav,
    list_system_voices,
    model_quality,
)

# Candidatas de Piper en español, LAS DE MÁS CALIDAD PRIMERO.  El sufijo del
# nombre es el nivel de calidad y se nota mucho: `high` suena natural; `medium`
# y sobre todo `low`/`x_low` suenan apagadas, "como debajo del agua".  Si alguna
# ya no existe en el catálogo, se salta sin romper la audición.
CANDIDATE_PIPER_VOICES = (
    "es_MX-claude-high",        # México, masculina — la mejor calidad
    "es_AR-daniela-high",       # Argentina, femenina — alta calidad
    "es_ES-davefx-medium",      # España, masculina
    "es_ES-sharvard-medium",    # España, femenina
    "es_MX-ald-medium",         # México, masculina
)

# Frases reales de una partida: conviene juzgar la voz con lo que va a decir de
# verdad, no con un "hola mundo".
SAMPLE_LINES = (
    "Hola. Soy MAGNUS. Vamos a jugar.",
    "Muevo el caballo de ge uno a efe tres.",
    "Capturo el peón en e cinco con el alfil desde ce cuatro. Jaque.",
    "Uy. Eso fue un error grave.",
    "Buena jugada. Sigue así.",
    "Jaque mate. Ganaste. Bien jugado, en serio.",
)


# --------------------------------------------------------------------------- #
# Comprobaciones previas
# --------------------------------------------------------------------------- #
def piper_is_importable() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


def explain_missing_piper() -> None:
    """El fallo típico: piper instalado en OTRO Python distinto del que corre."""
    print("\n⚠️  Este Python no tiene piper-tts instalado:")
    print(f"      {sys.executable}")
    print("\n   Suele pasar por tener varios Python: `pip install piper-tts` lo")
    print("   instaló en uno y el script corre con otro. Instálalo en ESTE:")
    print(f"\n      {sys.executable} -m pip install piper-tts\n")
    print("   Mientras tanto puedes audicionar las voces del sistema:")
    print("      python3 examples/audition_voices.py --engine say\n")


# --------------------------------------------------------------------------- #
# macOS `say`
# --------------------------------------------------------------------------- #
def audition_say(args) -> int:
    voices = list_system_voices(spanish_only=True)
    if not voices:
        print("No hay voces en español instaladas (o no estás en macOS).")
        print("Descárgalas en Ajustes > Accesibilidad > Contenido hablado > "
              "Voz del sistema > Español.")
        return 2

    # Por defecto se audicionan las masculinas (MAGNUS habla en masculino).
    candidatas = [v for v in voices if v.probably_male] if not args.all_spanish else voices
    if not candidatas:
        print("No se reconoció ninguna voz masculina; se audicionan todas.")
        candidatas = voices

    print(f"Voces en español instaladas ({len(voices)}):")
    for v in voices:
        marca = "♂" if v.probably_male else " "
        print(f"  {marca} {v.name:<28} {v.locale}")
    if args.list:
        return 0

    print(f"\nAudicionando {len(candidatas)}. Ctrl-C para parar.\n")
    for voice in candidatas:
        print(f"▶ {voice.name} ({voice.locale})")
        backend = MacSayBackend(voice=voice.name, rate=args.rate)
        for line in SAMPLE_LINES:
            print(f"    «{line}»")
            backend.speak(line)
    print("\nElige la que más te guste y ponla en magnus/config.py:")
    print('      VOICE_MACOS_VOICE = "…"')
    return 0


# --------------------------------------------------------------------------- #
# Piper
# --------------------------------------------------------------------------- #
def download_voice(name: str, data_dir: Path) -> bool:
    """Descarga una voz si no está ya; ``False`` si no se pudo."""
    if (data_dir / f"{name}.onnx").is_file():
        return True
    print(f"  descargando {name}…")
    result = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", name, "--data-dir", str(data_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        print(f"  ⚠️  no se pudo descargar {name}: {detail[-1] if detail else 'error'}")
        return False
    return True


def audition_piper(args) -> int:
    if not piper_is_importable():
        explain_missing_piper()
        return 2

    data_dir = Path(args.data_dir)
    out_dir = Path(args.output)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Ninguna voz lleva efectos: sale tal cual la sintetiza el modelo.")
    print("Si alguna suena apagada o 'bajo el agua', es su nivel de calidad —\n"
          "quédate con una `high`.\n")

    generated: list[str] = []
    # Primero las de mayor calidad, que son las que interesan.
    orden = {"high": 0, "medium": 1, "low": 2, "x_low": 3, "desconocida": 4}
    for voice_name in sorted(args.voices, key=lambda n: orden[model_quality(n)]):
        print(f"\n▶ {voice_name}   (calidad: {model_quality(voice_name)})")
        if not download_voice(voice_name, data_dir):
            continue
        backend = PiperBackend(
            model=str(data_dir / f"{voice_name}.onnx"),
            length_scale=args.length_scale,
        )
        for i, line in enumerate(SAMPLE_LINES, 1):
            wav_path = out_dir / f"{voice_name}_{i}.wav"
            try:
                backend.synthesize_to_file(line, wav_path)
            except Exception as exc:                     # noqa: BLE001
                print(f"  ⚠️  falló al sintetizar: {exc}")
                break
            print(f"  {wav_path}  «{line}»")
            generated.append(str(wav_path))
            if args.play:
                _play_wav(wav_path)
        backend.close()

    if not generated:
        print("\nNo se generó ningún audio. ¿Hay internet para descargar las voces?")
        return 2

    print(f"\n{len(generated)} audios en {out_dir}/. Escúchalos y quédate con una.")
    print('Luego ponla en magnus/config.py:  VOICE_PIPER_MODEL = "…"')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audición de voces para MAGNUS")
    parser.add_argument("--engine", choices=["auto", "say", "piper"], default="auto",
                        help="Motor a audicionar (auto: Piper si está, si no say)")
    parser.add_argument("--list", action="store_true",
                        help="Solo listar las voces disponibles, sin hablar")
    # Opciones de `say`
    parser.add_argument("--all-spanish", action="store_true",
                        help="say: audicionar todas las voces en español, no solo las masculinas")
    parser.add_argument("--rate", type=int, default=config.VOICE_MACOS_RATE,
                        help="say: velocidad en palabras por minuto")
    # Opciones de Piper
    parser.add_argument("--voices", nargs="*", default=list(CANDIDATE_PIPER_VOICES),
                        help="piper: voces a probar")
    parser.add_argument("--data-dir", default="voices",
                        help="piper: carpeta donde se guardan los modelos")
    parser.add_argument("--output", default="voices/audicion",
                        help="piper: carpeta donde se dejan los WAV generados")
    parser.add_argument("--length-scale", type=float, default=config.VOICE_LENGTH_SCALE,
                        help="piper: velocidad del habla (<1 más rápido)")
    parser.add_argument("--play", action="store_true",
                        help="piper: reproducir cada frase según se genera")
    args = parser.parse_args()

    engine = args.engine
    if engine == "auto":
        engine = "piper" if piper_is_importable() else "say"
        print(f"(motor elegido automáticamente: {engine})")
        if engine == "say":
            explain_missing_piper()

    return audition_say(args) if engine == "say" else audition_piper(args)


if __name__ == "__main__":
    raise SystemExit(main())
