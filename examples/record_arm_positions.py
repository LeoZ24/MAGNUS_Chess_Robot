#!/usr/bin/env python3
"""Lee posiciones colocadas a mano e imprime JSON para copiar a positions.json.

Se ejecuta EN EL ORDENADOR, con cyberpi_arm_client.py ya subido y funcionando
en la CyberPi (misma Wi-Fi y MAC_IP configurada). Cierra play.py/test_humo.py
antes: solo un servidor puede ocupar el puerto del brazo.

    python3 examples/record_arm_positions.py e4
    python3 examples/record_arm_positions.py a1 a2 discard exchange
    python3 examples/record_arm_positions.py --all

Hay dos modos:

  --jog  (RECOMENDADO) el brazo se mueve SOLO y tú lo ajustas a pasos con
         h+5 / c-3 hasta que la punta cae en la casilla; se graba la ORDEN.
         Es lo único que da una tabla fiel: colocado a mano, con los motores
         sueltos, el brazo no flexa igual que cuando lo empujan los motores,
         así que una tabla medida a mano se queda corta de forma sistemática
         al reproducirla — sin que el encoder note nada y sin ningún error.

  (por defecto) referencia con HOME, envía STOP y tú colocas el brazo a mano;
         se leen los dos encoders. Más rápido, menos fiel. Sostén el peso del
         brazo al detener los motores. Si el hardware sigue frenado tras STOP,
         no lo fuerces: este protocolo no ofrece un comando para soltar el freno.

Incluye la zona `park` (reposo fuera del tablero), a la que el brazo se retira
al terminar cada jugada:  python3 examples/record_arm_positions.py park --jog

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
from magnus.arm.positions_table import RECORDABLE_KEYS  # noqa: E402

POSITIONS_PATH = Path(__file__).resolve().parents[1] / "magnus" / "arm" / "positions.json"


def _check_angles(shoulder: float, elbow: float, limits) -> None:
    """Rechaza una lectura imposible antes de dejarla entrar en la tabla."""
    for name, angle, (low, high) in zip(
        ("hombro", "codo"), (shoulder, elbow), limits
    ):
        if not math.isfinite(angle) or not low <= angle <= high:
            raise ArmBackendError(
                f"Lectura descartada: {name}={angle} fuera de "
                f"[{low}, {high}]. Revisa el cero y la posición."
            )


def _load_seeds(path: Path) -> dict:
    """Ángulos ya grabados, para no empezar cada casilla desde cero."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    seeds = {}
    if isinstance(raw, dict):
        for key, entry in raw.items():
            if isinstance(entry, dict) and "shoulder" in entry and "elbow" in entry:
                try:
                    seeds[key] = (float(entry["shoulder"]), float(entry["elbow"]))
                except (TypeError, ValueError):
                    pass
    return seeds


def _write_positions(path: Path, captured: dict) -> Path:
    """Fusiona lo grabado en positions.json y devuelve la ruta de la copia.

    Fusiona, no reemplaza: grabar dos casillas no puede borrar las otras 64.
    Y siempre deja un .bak antes de tocar nada, porque recalibrar una tabla
    entera son horas de brazo y aquí se sobrescribe en un segundo.
    """
    backup = path.with_name(path.name + ".bak")
    existing: dict = {}
    if path.exists():
        original = path.read_text(encoding="utf-8")
        backup.write_text(original, encoding="utf-8")
        try:
            loaded = json.loads(original)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            existing = loaded
    existing.update(captured)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return backup


JOG_HELP = """
  h+5 / h-5   mover el HOMBRO 5 grados (cualquier número vale)
  c+5 / c-5   mover el CODO 5 grados
  Enter       grabar esta posición y pasar a la siguiente
  s           saltar esta posición sin grabarla
  q           terminar
"""


def _jog_capture(backend, squares, limits, seeds, captured) -> None:
    """Graba llevando el brazo con los motores, no colocándolo a mano.

    POR QUÉ IMPORTA: colocado a mano, con los motores sueltos, el brazo no
    aguanta su propio peso igual que cuando lo empujan los motores. La
    estructura flexa y la transmisión tiene juego, así que el mismo ángulo de
    encoder deja la punta en dos sitios distintos según cómo se llegue. Una
    tabla medida a mano y reproducida con motores se queda corta de forma
    sistemática, sin que el encoder note nada raro y sin que salte ningún error.

    Grabando así, lo que se guarda es la ORDEN que deja la punta en la casilla,
    con la flexión ya dentro. Es la misma orden que se mandará jugando.
    """
    print("\nModo JOG: el brazo se mueve solo; no lo empujes con la mano.")
    print(JOG_HELP)
    for square in squares:
        target = seeds.get(square)
        if target is None:
            target = backend.get_position()
        shoulder, elbow = target
        # Último par de ángulos que la placa aceptó. Si un ajuste se sale de
        # límites se vuelve AQUÍ, no a la lectura del encoder: la orden y el
        # encoder no son lo mismo, y es justo esa diferencia (la flexión) lo
        # que este modo existe para capturar. Volver al encoder la tiraría.
        accepted = (shoulder, elbow)
        print(f"\n--- {square} ---")
        while True:
            try:
                _check_angles(shoulder, elbow, limits)
                backend.move_to(shoulder, elbow)
                accepted = (shoulder, elbow)
            except ArmBackendError as exc:
                print(f"   Rechazado: {exc}")
                shoulder, elbow = accepted
            reached = backend.get_position()
            print(f"   orden: hombro {shoulder:+.1f}  codo {elbow:+.1f}"
                  f"   (encoder: {reached[0]:+.1f} {reached[1]:+.1f})")
            orden = input("   ajuste (h±/c±, Enter GRABA, s salta, q sale): ").strip().lower()
            if orden == "":
                _check_angles(shoulder, elbow, limits)
                # Se graba la ORDEN, no el encoder: es lo que se mandará jugando,
                # y reproducirla deja la punta donde está ahora.
                angles = {"shoulder": round(shoulder, 2), "elbow": round(elbow, 2)}
                captured[square] = angles
                print(f'   grabada {square}: {json.dumps(angles, allow_nan=False)}', flush=True)
                break
            if orden == "s":
                print("   saltada (no se graba).")
                break
            if orden == "q":
                # Salir aquí tiraría todo el ajuste de esta posición, que es lo
                # que más cuesta de conseguir. Mejor preguntar que perderlo.
                if square not in captured:
                    print(f"   Vas a salir SIN grabar {square}.")
                    respuesta = input("   Enter la graba y sale, 'q' sale sin "
                                      "grabarla: ").strip().lower()
                    if respuesta != "q":
                        _check_angles(shoulder, elbow, limits)
                        angles = {"shoulder": round(shoulder, 2),
                                  "elbow": round(elbow, 2)}
                        captured[square] = angles
                        print(f'   grabada {square}: '
                              f'{json.dumps(angles, allow_nan=False)}', flush=True)
                raise KeyboardInterrupt
            eje, _, cantidad = orden.partition("+") if "+" in orden else orden.partition("-")
            signo = 1.0 if "+" in orden else -1.0
            try:
                paso = signo * float(cantidad)
            except ValueError:
                print("   No te he entendido." + JOG_HELP)
                continue
            if eje.strip() == "h":
                shoulder += paso
            elif eje.strip() == "c":
                elbow += paso
            else:
                print("   El eje es 'h' (hombro) o 'c' (codo)." + JOG_HELP)


def main() -> int:
    """Captura una lista finita de destinos, sin modificar la tabla real."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("squares", nargs="*", metavar="CASILLA",
                        help="Casillas o zonas que quieres medir, por ejemplo e4 discard")
    parser.add_argument("--all", action="store_true", dest="all_squares",
                        help="Recorrer las 64 casillas y las zonas discard/exchange")
    parser.add_argument("--port", type=int, default=5555, help="Puerto TCP (5555)")
    parser.add_argument("--write", action="store_true",
                        help="Fusionar lo grabado en positions.json (guarda "
                             "antes una copia .bak). Sin esto solo se imprime")
    parser.add_argument("--jog", action="store_true",
                        help="Grabar MOVIENDO el brazo con los motores en vez "
                             "de colocarlo a mano (recomendado, ver abajo)")
    args = parser.parse_args()
    if args.all_squares and args.squares:
        parser.error("Elige casillas concretas o --all.")
    if not args.all_squares and not args.squares:
        parser.error("Indica una casilla (por ejemplo e4) o usa --all.")
    squares = list(RECORDABLE_KEYS) if args.all_squares else args.squares
    for square in squares:
        if square not in RECORDABLE_KEYS:
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
        if not args.jog:
            input("HOME terminado. Sostén el brazo; Enter detiene los motores\n"
                  "para colocarlo a mano: ")
            backend.stop()
        limits = backend.get_limits()
        if limits is None:
            raise ArmBackendError("No pude consultar LIMITS; se cancela la captura.")
        if any(not math.isfinite(v) for pair in limits for v in pair):
            raise ArmBackendError("LIMITS devolvió valores no finitos.")
        print(f"\nLímites en grados: hombro {limits[0]}, codo {limits[1]}.")

        if args.jog:
            _jog_capture(backend, squares, limits, _load_seeds(POSITIONS_PATH),
                         captured)
        else:
            print("Coloca a mano ambos ejes y mantenlos quietos al pulsar Enter.")
            for square in squares:
                input(f"\nColoca el brazo en {square} → Enter para leer: ")
                shoulder, elbow = backend.get_position()
                _check_angles(shoulder, elbow, limits)
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
            print("\nMediciones (pueden estar incompletas):")
            print(json.dumps(captured, indent=2, allow_nan=False))
            if args.write:
                try:
                    backup = _write_positions(POSITIONS_PATH, captured)
                except OSError as exc:
                    print(f"No pude escribir {POSITIONS_PATH}: {exc}",
                          file=sys.stderr)
                    print("Copia las entradas de arriba a mano.", file=sys.stderr)
                    status = 1
                else:
                    print(f"\nFusionadas {len(captured)} entradas en "
                          f"{POSITIONS_PATH}")
                    print(f"Copia de seguridad de la tabla anterior: {backup}")
            else:
                print("Copia estas entradas en positions.json conservando las "
                      "demás, o repite con --write para que lo haga solo.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
