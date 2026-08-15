#!/usr/bin/env python3
"""Demo del nodo del brazo de MAGNUS — SIN hardware.

El engine (Stockfish) calcula jugadas y el ``ArmNode`` las traduce a la
secuencia de comandos que se enviaría al brazo real.  Como todavía no hay
brazo, se usa ``FakeArmBackend`` (imprime/registra los comandos) y la tabla de
posiciones falsa (valores 9999.x, claramente irreales).

Ejemplos:

    # Una jugada desde la posición inicial y su secuencia de brazo
    python3 examples/run_arm_demo.py

    # Una posición con captura al paso disponible
    python3 examples/run_arm_demo.py --fen "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"

    # Auto-partida de 6 medias-jugadas mostrando la secuencia de cada una
    python3 examples/run_arm_demo.py --selfplay 6
"""

from __future__ import annotations

import argparse
import logging
import sys

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import chess  # noqa: E402

from magnus.arm import ArmNode, FakeArmBackend, make_fake_table  # noqa: E402
from magnus.core.messages import STARTING_FEN, MoveResponse  # noqa: E402
from magnus.engine import ChessEngineNode, DifficultyLevel  # noqa: E402


def describe_move(resp: MoveResponse) -> str:
    extras = []
    if resp.is_capture:
        extras.append(f"captura en {resp.captured_square}"
                      + (" [al paso]" if resp.is_en_passant else ""))
    if resp.is_castling:
        extras.append(f"enroque (torre {resp.rook_from}->{resp.rook_to})")
    if resp.promotion:
        extras.append(f"promoción a '{resp.promotion}'")
    return f"{resp.san} ({resp.uci})" + (f" — {', '.join(extras)}" if extras else "")


def show_arm_sequence(arm: ArmNode, backend: FakeArmBackend, resp: MoveResponse) -> None:
    print(f"\nJugada del engine: {describe_move(resp)}")
    backend.clear()
    steps = arm.execute(resp)
    print(f"Plan del brazo ({len(steps)} primitivas):")
    print("  " + " -> ".join(str(s) for s in steps))
    print(f"Comandos enviados al backend ({len(backend.commands)}):")
    for cmd in backend.commands:
        if cmd[0] == "move_to":
            print(f"  move_to(shoulder={cmd[1]}, elbow={cmd[2]})")
        elif cmd[0] == "gripper":
            print(f"  gripper({'AGARRAR' if cmd[1] else 'SOLTAR'})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo del brazo de MAGNUS (sin hardware)")
    parser.add_argument("--fen", default=STARTING_FEN, help="Posición en notación FEN")
    parser.add_argument("--difficulty", default="MEDIUM",
                        choices=[lvl.name for lvl in DifficultyLevel])
    parser.add_argument("--selfplay", type=int, metavar="N", default=0,
                        help="Juega N medias-jugadas mostrando cada secuencia del brazo")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs internos")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    print("NOTA: backend falso + tabla de posiciones falsa (valores 9999.x).")
    print("      Los comandos mostrados son los que recibiría el brazo real.\n")

    backend = FakeArmBackend()
    try:
        with ChessEngineNode(default_difficulty=args.difficulty) as engine, \
             ArmNode(backend=backend, table=make_fake_table()) as arm:

            if args.selfplay > 0:
                board = chess.Board()
                for i in range(args.selfplay):
                    if board.is_game_over():
                        print(f"\nPartida terminada: {board.result()}")
                        break
                    resp = engine.compute_move_from_fen(board.fen())
                    print(f"\n=== Media-jugada {i + 1} ({resp.side_to_move}) ===")
                    show_arm_sequence(arm, backend, resp)
                    board.push_uci(resp.uci)
            else:
                resp = engine.compute_move_from_fen(args.fen)
                show_arm_sequence(arm, backend, resp)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
