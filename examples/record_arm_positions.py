#!/usr/bin/env python3
"""Lee posiciones colocadas a mano e imprime JSON para copiar a positions.json.

Se ejecuta EN EL ORDENADOR, con cyberpi_arm_client.py ya subido y funcionando
en la CyberPi (misma Wi-Fi y MAC_IP configurada). Cierra play.py/test_humo.py
antes: solo un servidor puede ocupar el puerto del brazo.

    python3 examples/record_arm_positions.py e4
    python3 examples/record_arm_positions.py a1 a2 discard exchange
    python3 examples/record_arm_positions.py --all
    python3 examples/record_arm_positions.py --all --jog --home-each
    python3 examples/record_arm_positions.py --all --home-each   (a mano)

Hay dos modos:

  --jog  (RECOMENDADO) el brazo se mueve SOLO y tú lo ajustas a pasos con
         h+5 / c-3 hasta que la punta cae en la casilla; se graba la ORDEN.
         Es lo único que da una tabla fiel: colocado a mano, con los motores
         sueltos, el brazo no flexa igual que cuando lo empujan los motores,
         así que una tabla medida a mano se queda corta de forma sistemática
         al reproducirla — sin que el encoder note nada y sin ningún error.
         Parte de lo ya grabado, así que recalibrar es solo corregir.
         `p` prueba a recoger la pieza de verdad y `v` comprueba que la orden
         repite llegando desde lejos, como en la partida.
         Con --home-each hace HOME antes de cada destino; `r` lo hace a mano.

  (por defecto) referencia con HOME, envía STOP y tú colocas el brazo a mano;
         se leen los dos encoders. Más rápido, menos fiel. Sostén el peso del
         brazo al detener los motores. Si el hardware sigue frenado tras STOP,
         no lo fuerces: este protocolo no ofrece un comando para soltar el freno.

Incluye la zona `park` (reposo fuera del tablero), a la que el brazo se retira
al terminar cada jugada:  python3 examples/record_arm_positions.py park --jog

Cada destino requiere una sola lectura de hombro y codo. S1 se calibra aparte
con tres ángulos comunes a todas las piezas (agarrar, levantar y soltar, en el
cliente de la CyberPi). No apagues el shield ni reinicies la CyberPi entre
lecturas: perderías el cero. Nunca se envía ZERO; MOVE y la garra solo en modo
--jog. Solo escribe positions.json con --write. Ctrl+C termina y muestra lo
medido hasta ese momento.
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
  h=85        llevar el hombro a 85 exactos (c=-120 para el codo)
  h+2 c-1     varios ajustes en la misma línea
  p           PROBAR: recoger la pieza (agarra y levanta) y volver a soltarla
  v           VERIFICAR: alejarse por un lado y por el otro y volver, como
              en la partida; si la punta no cae igual, sube BACKLASH_DEG
  r           REFERENCIAR: HOME y volver a la misma orden. Si la punta ya no
              cae donde caía, el cero se había corrido (no era la tabla)
  Enter       grabar esta posición y pasar a la siguiente
  s           saltar esta posición sin grabarla
  q           terminar
"""

# Cuánto se aleja `v` antes de volver. Lo bastante para que el eje llegue con
# un giro largo (como jugando), no con un paso de ajuste.
VERIFY_AWAY_DEG = 15.0


class JogInputError(ValueError):
    """Una línea de ajuste que no se entiende."""


def _parse_jog(orden: str, shoulder: float, elbow: float) -> "tuple[float, float]":
    """Aplica una línea de ajustes (``h+5``, ``c=-120``, ``h+2 c-1``).

    Devuelve la nueva orden, o lanza ``JogInputError`` sin aplicar nada si
    alguna parte no se entiende: medio ajuste aplicado confunde más que ninguno.
    """
    tokens = orden.replace(",", " ").split()
    if not tokens:
        raise JogInputError("línea vacía")
    for token in tokens:
        eje, resto = token[:1], token[1:].strip()
        if eje not in ("h", "c") or not resto or resto[0] not in "+-=":
            raise JogInputError(f"no entiendo {token!r}")
        try:
            valor = float(resto[1:] if resto[0] == "=" else resto)
        except ValueError:
            raise JogInputError(f"no entiendo {token!r}") from None
        if not math.isfinite(valor):
            raise JogInputError(f"no entiendo {token!r}")
        if eje == "h":
            shoulder = valor if resto[0] == "=" else shoulder + valor
        else:
            elbow = valor if resto[0] == "=" else elbow + valor
    return shoulder, elbow


def _away(angle: float, delta: float, limits: "tuple[float, float]") -> float:
    """``angle + delta`` sin salirse de los límites del eje."""
    low, high = limits
    return max(low, min(high, angle + delta))


def _verify(backend, shoulder: float, elbow: float, limits) -> None:
    """Llega a la orden desde los dos lados, con giros largos, y la enseña.

    Así llega el brazo jugando: desde otra casilla, no con un paso de ajuste.
    Si la punta cae en sitios distintos según el lado, la reductora tiene más
    juego del que compensa el cliente (``BACKLASH_DEG``).
    """
    for lado, signo in (("negativo", -1.0), ("positivo", 1.0)):
        backend.move_to(_away(shoulder, signo * VERIFY_AWAY_DEG, limits[0]),
                        _away(elbow, signo * VERIFY_AWAY_DEG, limits[1]))
        backend.move_to(shoulder, elbow)
        reached = backend.get_position()
        print(f"   llegando desde el lado {lado}: encoder "
              f"{reached[0]:+.1f} {reached[1]:+.1f}")
        input("   Mira dónde cae la punta y pulsa Enter: ")
    print("   Si cayó en el MISMO sitio las dos veces, la orden repite. Si no,\n"
          "   sube BACKLASH_DEG en el cliente de la CyberPi y vuelve a subirlo.")


def _try_pick(backend) -> None:
    """Recoge la pieza como en la partida (agarra y levanta) y la suelta.

    Es la prueba de verdad de una casilla: si el imán no la coge centrada, o
    la arrastra al levantarla, la orden aún no está bien.
    """
    backend.set_gripper(True)
    input("   Pieza levantada? Enter la suelta: ")
    backend.set_gripper(False)


def _rehome(backend) -> None:
    """HOME con el imán arriba, para no arrastrar piezas al buscar los topes."""
    print("   Referenciando (HOME)...", flush=True)
    backend.set_gripper(False)
    backend.home()


def _jog_capture(backend, squares, limits, seeds, captured,
                 home_each: bool = False) -> None:
    """Graba llevando el brazo con los motores, no colocándolo a mano.

    POR QUÉ IMPORTA: colocado a mano, con los motores sueltos, el brazo no
    aguanta su propio peso igual que cuando lo empujan los motores. La
    estructura flexa y la transmisión tiene juego, así que el mismo ángulo de
    encoder deja la punta en dos sitios distintos según cómo se llegue. Una
    tabla medida a mano y reproducida con motores se queda corta de forma
    sistemática, sin que el encoder note nada raro y sin que salte ningún error.

    Grabando así, lo que se guarda es la ORDEN que deja la punta en la casilla,
    con la flexión ya dentro. Es la misma orden que se mandará jugando.

    Con ``home_each`` se referencia antes de cada destino (menos el primero,
    que acaba de referenciar ``main``): así cada orden se mide desde un cero
    recién puesto y un error de encoder no se arrastra de casilla en casilla.
    """
    print("\nModo JOG: el brazo se mueve solo; no lo empujes con la mano.")
    print(JOG_HELP)
    # Imán arriba, como cuando el brazo llega a una casilla jugando.
    backend.set_gripper(False)
    for index, square in enumerate(squares):
        if home_each and index > 0:
            _rehome(backend)
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
            if orden in ("p", "v", "r"):
                try:
                    if orden == "p":
                        _try_pick(backend)
                    elif orden == "r":
                        # El bucle vuelve a mandar la misma orden al continuar.
                        _rehome(backend)
                    else:
                        _verify(backend, shoulder, elbow, limits)
                except ArmBackendError as exc:
                    print(f"   Falló: {exc}")
                continue
            try:
                shoulder, elbow = _parse_jog(orden, shoulder, elbow)
            except JogInputError as exc:
                print(f"   No te he entendido ({exc})." + JOG_HELP)


# Una lectura a menos de esto del cero en los dos ejes es casi seguro un Enter
# pulsado con el brazo aún en HOME, no una casilla: se pide confirmación.
HOME_READING_DEG = 5.0


class _GoBack(Exception):
    """El usuario pidió repetir el destino anterior (``b``)."""


def _ask(prompt: str) -> str:
    """``input`` que convierte ``b`` en volver al destino anterior."""
    answer = input(prompt).strip().lower()
    if answer == "b":
        raise _GoBack
    return answer


def _hand_capture(backend, squares, limits, captured,
                  home_each: bool = False) -> None:
    """Graba colocando el brazo a mano con los motores sueltos.

    Con ``home_each`` cada destino empieza con HOME + STOP (menos el primero,
    que acaba de referenciar ``main``): el cero se repone antes de cada lectura
    y un encoder que pierde cuentas al empujarlo a mano no contamina la
    siguiente casilla. Una lectura fuera de límites se descarta y se repite.

    En cualquier pregunta, ``b`` vuelve al destino anterior y lo regraba
    (sobrescribe su lectura): un Enter de más no obliga a empezar de nuevo.
    """
    print("Coloca a mano ambos ejes y mantenlos quietos al pulsar Enter.")
    print("En cualquier momento: 'b' + Enter repite la casilla ANTERIOR.")
    index = 0
    # Tras volver atrás hay que referenciar aunque sea el primer destino: los
    # motores están sueltos y el brazo, donde lo dejó la casilla siguiente.
    force_home = False
    while index < len(squares):
        square = squares[index]
        try:
            if force_home or (home_each and index > 0):
                _ask(f"\nSuelta el brazo y despeja su recorrido; Enter hace "
                     f"HOME para {square}: ")
                _rehome(backend)
                force_home = False
                _ask("HOME terminado. Sostén el brazo; Enter detiene los motores: ")
                backend.stop()
            while True:
                _ask(f"\nColoca el brazo en {square} → Enter para leer: ")
                shoulder, elbow = backend.get_position()
                try:
                    _check_angles(shoulder, elbow, limits)
                except ArmBackendError as exc:
                    print(f"   {exc} Vuelve a colocarlo.")
                    continue
                if (abs(shoulder) < HOME_READING_DEG
                        and abs(elbow) < HOME_READING_DEG):
                    answer = _ask(f"   Lectura casi en el cero ({shoulder:+.1f} "
                                  f"{elbow:+.1f}): parece el brazo en HOME. "
                                  f"'g' + Enter la graba igual, Enter repite: ")
                    if answer != "g":
                        continue
                break
        except _GoBack:
            if index == 0:
                print("   No hay casilla anterior.")
                continue
            index -= 1
            force_home = home_each
            print(f"   Volvemos a {squares[index]} (se sobrescribirá).")
            continue
        angles = {"shoulder": round(shoulder, 2), "elbow": round(elbow, 2)}
        captured[square] = angles
        print(f'"{square}": {json.dumps(angles, allow_nan=False)}', flush=True)
        index += 1


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
    parser.add_argument("--home-each", action="store_true",
                        help="Hacer HOME antes de cada destino, para medirlo "
                             "siempre desde un cero recién puesto")
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
                         captured, home_each=args.home_each)
        else:
            _hand_capture(backend, squares, limits, captured,
                          home_each=args.home_each)
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
