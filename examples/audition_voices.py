#!/usr/bin/env python3
"""Audición de voces: genera el mismo texto con varias voces para elegir una.

La voz del robot es una decisión de oído, no de código: este script sintetiza
las mismas frases de una partida real con cada voz candidata y deja los WAV en
una carpeta para que los escuches y decidas.

Uso típico (la primera vez descarga los modelos, necesita internet)::

    python examples/audition_voices.py
    python examples/audition_voices.py --play             # además los reproduce
    python examples/audition_voices.py --voices es_MX-claude-high es_ES-davefx-medium
    python examples/audition_voices.py --length-scale 0.9 # habla más rápido

Cuando tengas la elegida, ponla en ``magnus/config.py``::

    VOICE_PIPER_MODEL = "la_que_te_guste"

Ver todas las voces disponibles: https://huggingface.co/rhasspy/piper-voices
(carpetas ``es/es_ES`` y ``es/es_MX``).  Si una candidata ya no existe, el
script lo dice y sigue con las demás.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

from magnus import config  # noqa: E402
from magnus.voice.backend import PiperBackend, _play_wav  # noqa: E402

# Candidatas en español.  Son las habituales del catálogo de Piper; si alguna no
# está disponible se salta sin romper la audición.
CANDIDATE_VOICES = (
    "es_ES-davefx-medium",      # España, masculina
    "es_ES-sharvard-medium",    # España, femenina
    "es_MX-claude-high",        # México, masculina (alta calidad)
    "es_MX-ald-medium",         # México, masculina
    "es_AR-daniela-high",       # Argentina, femenina
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Audición de voces para MAGNUS")
    parser.add_argument("--voices", nargs="*", default=list(CANDIDATE_VOICES),
                        help="Voces de Piper a probar")
    parser.add_argument("--data-dir", default="voices",
                        help="Carpeta donde se guardan los modelos")
    parser.add_argument("--output", default="voices/audicion",
                        help="Carpeta donde se dejan los WAV generados")
    parser.add_argument("--length-scale", type=float, default=config.VOICE_LENGTH_SCALE,
                        help="Velocidad del habla (<1 más rápido, >1 más lento)")
    parser.add_argument("--play", action="store_true",
                        help="Reproducir cada frase según se genera")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.output)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []
    for voice_name in args.voices:
        print(f"\n▶ {voice_name}")
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
    print("Luego ponla en magnus/config.py:  VOICE_PIPER_MODEL = \"…\"")
    print("Consejo: júzgalas con el ruido de una feria en mente — prima que se "
          "entienda por encima de que suene bonita.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
