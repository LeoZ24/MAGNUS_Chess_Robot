#!/usr/bin/env python3
"""Genera la plantilla de la tabla de posiciones del brazo.

Crea ``positions.template.json`` con las 64 casillas + las zonas especiales
(descarte e intercambio), todas con valores ``null`` para rellenar durante la
calibración del brazo real (teach & playback: mover el brazo a cada casilla y
anotar los valores de hombro/codo).

Cuando esté rellena, renombrarla a ``magnus/arm/positions.json``.

Uso:

    python3 examples/generate_positions_template.py
    python3 examples/generate_positions_template.py --output mi_tabla.json
"""

from __future__ import annotations

import argparse
import json
import sys

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

from magnus import config  # noqa: E402
from magnus.arm.positions_table import ALL_SQUARES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Plantilla de positions.json")
    parser.add_argument("--output", default="positions.template.json",
                        help="Archivo de salida")
    args = parser.parse_args()

    entry = {
        "approach": {"shoulder": None, "elbow": None},
        "engage": {"shoulder": None, "elbow": None},
    }
    keys = list(ALL_SQUARES) + [config.ZONE_DISCARD, config.ZONE_EXCHANGE]
    template = {key: json.loads(json.dumps(entry)) for key in keys}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2)

    print(f"Plantilla escrita en {args.output} ({len(keys)} entradas: "
          f"64 casillas + '{config.ZONE_DISCARD}' + '{config.ZONE_EXCHANGE}').")
    print("Rellena los valores calibrando el brazo y guárdala como "
          "magnus/arm/positions.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
