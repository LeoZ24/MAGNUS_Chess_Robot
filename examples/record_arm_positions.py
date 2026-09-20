#!/usr/bin/env python3
"""Lee posiciones colocadas a mano e imprime JSON para copiar a positions.json.

Se ejecuta EN EL ORDENADOR, con cyberpi_arm_client.py ya subido y funcionando
en la CyberPi (misma Wi-Fi y MAC_IP configurada). Cierra play.py/test_humo.py
antes: solo un servidor puede ocupar el puerto del brazo.

    python3 examples/record_arm_positions.py e4
    python3 examples/record_arm_positions.py a1 a2 discard exchange
    python3 examples/record_arm_positions.py --all

Primero referencia con HOME usando los topes y el cero del cliente habitual.
Después envía STOP: coloca el brazo a mano, mantenlo quieto y pulsa Enter para
leer los dos encoders. Sostén el peso del brazo al detener los motores. Si el
hardware sigue frenado tras STOP, no lo fuerces: este protocolo no ofrece un
comando adicional para desconectar el freno.

Cada destino requiere una sola lectura de hombro y codo. S1 se calibra aparte
con dos ángulos comunes a todas las piezas (recoger y soltar). No apagues
el shield ni reinicies la CyberPi entre lecturas: perderías el cero. No se envía
ZERO, MOVE ni órdenes a la garra. No escribe archivos. Ctrl+C termina y muestra
lo medido hasta ese momento.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magnus.arm.backend import ArmBackendError, CyberPiBackend  # noqa: E402
from magnus.arm.positions_table import REQUIRED_KEYS  # noqa: E402


def main() -> int:
    """Captura una lista finita de destinos, sin modificar la tabla real."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("squares", nargs="*", metavar="CASILLA",
                        help="Casillas o zonas que quieres medir, por ejemplo e4 discard")
    parser.add_argument("--all", action="store_true", dest="all_squares",
                        help="Recorrer las 64 casillas y las zonas discard/exchange")
    parser.add_argument("--port", type=int, default=5555, help="Puerto TCP (5555)")
    args = parser.parse_args()
    if args.all_squares and args.squares:
        parser.error("Elige casillas concretas o --all.")
    if not args.all_squares and not args.squares:
        parser.error("Indica una casilla (por ejemplo e4) o usa --all.")
    squares = list(REQUIRED_KEYS) if args.all_squares else args.squares
    for square in squares:
        if square not in REQUIRED_KEYS:
            parser.error(f"Destino desconocido: {square}")
    if len(set(squares)) != len(squares):
        parser.error("Hay destinos repetidos.")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    backend = CyberPiBackend(port=args.port)
    captured: dict[str, dict[str, float]] = {}
    connected = False
    status = 0
    try:
        backend.connect()
        connected = True
        input("\nHOME moverá el brazo contra sus topes. Despeja su recorrido.\n"
              "Pulsa Enter para referenciar (Ctrl+C cancela): ")
        backend.home()
        input("HOME terminado. Sostén el brazo; Enter detiene los motores\n"
              "para colocarlo a mano: ")
        backend.stop()
        limits = backend.get_limits()
        if limits is None:
            raise ArmBackendError("No pude consultar LIMITS; se cancela la captura.")
        if any(not math.isfinite(v) for pair in limits for v in pair):
            raise ArmBackendError("LIMITS devolvió valores no finitos.")
        print(f"\nLímites en grados: hombro {limits[0]}, codo {limits[1]}.")
        print("Coloca a mano ambos ejes y mantenlos quietos al pulsar Enter.")
        for square in squares:
            input(f"\nColoca el brazo en {square} → Enter para leer: ")
            shoulder, elbow = backend.get_position()
            for name, angle, (low, high) in zip(
                ("hombro", "codo"), (shoulder, elbow), limits
            ):
                if not math.isfinite(angle) or not low <= angle <= high:
                    raise ArmBackendError(
                        f"Lectura descartada: {name}={angle} fuera de "
                        f"[{low}, {high}]. Revisa el cero y la posición."
                    )
            angles = {"shoulder": round(shoulder, 2), "elbow": round(elbow, 2)}
            captured[square] = angles
            print(f'"{square}": {json.dumps(angles, allow_nan=False)}',
                  flush=True)
    except (KeyboardInterrupt, EOFError):
        print("\nCaptura terminada por el usuario.")
    except (ArmBackendError, OSError, ValueError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        status = 1
    finally:
        try:
            if connected:
                backend.stop()
        except (ArmBackendError, OSError) as exc:
            print(f"No se pudo confirmar STOP: {exc}", file=sys.stderr)
            status = 1
        finally:
            backend.disconnect()
        if captured:
            print("\nMediciones para copiar (pueden estar incompletas):")
            print(json.dumps(captured, indent=2, allow_nan=False))
            print("Copia estas entradas en positions.json conservando las demás.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
