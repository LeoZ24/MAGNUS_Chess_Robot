#!/usr/bin/env python3
"""Demo del nodo del engine de MAGNUS.

Permite enviar una posición (FEN) y un nivel de dificultad al engine y ver la
jugada que responde, con todos sus metadatos.  Más adelante, en lugar de pasar
la FEN por la línea de comandos, será el nodo de visión quien la envíe.

Ejemplos:

    # Jugada desde la posición inicial, dificultad media
    python examples/run_engine_node.py

    # Posición concreta + dificultad máxima, salida JSON
    python examples/run_engine_node.py --fen "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3" \
        --difficulty MAXIMUM --json

    # Mini-partida del engine contra sí mismo
    python examples/run_engine_node.py --selfplay 6 --difficulty EASY
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import chess  # noqa: E402

from magnus.core.messages import STARTING_FEN  # noqa: E402
from magnus.engine import ChessEngineNode, DifficultyLevel  # noqa: E402


def print_move(resp) -> None:
    print(f"  Jugada (UCI): {resp.uci}")
    print(f"  Jugada (SAN): {resp.san}")
    print(f"  {resp.from_square} -> {resp.to_square}  (pieza '{resp.piece}', mueven {resp.side_to_move})")
    if resp.is_capture:
        print(f"  Captura en {resp.captured_square} (pieza '{resp.captured_piece}')"
              + ("  [al paso]" if resp.is_en_passant else ""))
    if resp.is_castling:
        lado = "corto" if resp.is_kingside_castle else "largo"
        print(f"  Enroque {lado}: torre {resp.rook_from} -> {resp.rook_to}")
    if resp.promotion:
        print(f"  Promoción a '{resp.promotion}'")
    flags = [n for n, v in (("jaque", resp.is_check), ("mate", resp.is_checkmate),
                            ("ahogado", resp.is_stalemate)) if v]
    if flags:
        print(f"  Estado: {', '.join(flags)}")
    eval_txt = (f"mate en {resp.mate_in}" if resp.mate_in is not None
                else (f"{resp.evaluation_cp} cp" if resp.evaluation_cp is not None else "n/d"))
    print(f"  Eval: {eval_txt} | prof: {resp.depth} | dificultad: {resp.difficulty} | t: {resp.compute_time}s")


def run_single(node: ChessEngineNode, fen: str, as_json: bool) -> None:
    resp = node.compute_move_from_fen(fen)
    if as_json:
        print(json.dumps(asdict(resp), indent=2, ensure_ascii=False))
    else:
        print(f"Posición: {fen}")
        print_move(resp)


def run_selfplay(node: ChessEngineNode, plies: int) -> None:
    board = chess.Board()
    print(f"Auto-partida ({plies} jugadas, dificultad {node.get_difficulty().name}):\n")
    for i in range(plies):
        if board.is_game_over():
            print(f"Partida terminada: {board.result()}")
            break
        resp = node.compute_move_from_fen(board.fen())
        print(f"{i + 1:>2}. {resp.side_to_move:<5} {resp.san:<8} ({resp.uci})")
        board.push_uci(resp.uci)
    print(f"\nFEN final: {board.fen()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Nodo del engine de ajedrez MAGNUS")
    parser.add_argument("--fen", default=STARTING_FEN, help="Posición en notación FEN")
    parser.add_argument("--difficulty", default="MEDIUM",
                        choices=[lvl.name for lvl in DifficultyLevel],
                        help="Nivel de dificultad")
    parser.add_argument("--engine-path", default=None, help="Ruta al binario del motor (opcional)")
    parser.add_argument("--selfplay", type=int, metavar="N", default=0,
                        help="Juega N medias-jugadas del engine contra sí mismo")
    parser.add_argument("--json", action="store_true", help="Salida en JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs del engine")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        with ChessEngineNode(engine_path=args.engine_path,
                             default_difficulty=args.difficulty) as node:
            if args.selfplay > 0:
                run_selfplay(node, args.selfplay)
            else:
                run_single(node, args.fen, args.json)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
