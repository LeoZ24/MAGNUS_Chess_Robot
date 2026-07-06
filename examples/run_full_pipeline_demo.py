#!/usr/bin/env python3
"""Demo del pipeline COMPLETO de MAGNUS — sin cámara, sin brazo, sin tablero.

Simula una partida humano-vs-robot integrando los TRES nodos:

    1. VISIÓN: se renderiza una imagen sintética del tablero (con marcadores
       ArUco de verdad, generados con cv2.aruco) que la "cámara falsa" entrega
       al ``BoardVisionNode``.  El nodo detecta las piezas, deduce la jugada
       del humano con el ``GameTracker`` y produce la FEN exacta.
    2. ENGINE: Stockfish recibe la FEN y responde con la ``MoveResponse``.
    3. BRAZO: el ``ArmNode`` traduce la respuesta a la secuencia de primitivas
       que ejecutaría el brazo real (backend falso).

Las jugadas "del humano" se toman de una partida corta predefinida (o de
Stockfish mismo con --human-random).

Uso:

    python examples/run_full_pipeline_demo.py
    python examples/run_full_pipeline_demo.py --plies 8 --difficulty EASY -v
"""

from __future__ import annotations

import argparse
import logging
import sys

# Permite ejecutar el script directamente sin instalar el paquete.
sys.path.insert(0, __file__.rsplit("/examples/", 1)[0])

import chess  # noqa: E402

from magnus.arm import ArmNode, FakeArmBackend, make_fake_table  # noqa: E402
from magnus.engine import ChessEngineNode, DifficultyLevel  # noqa: E402
from magnus.vision.game_state import GameTracker, board_placement  # noqa: E402
from magnus.vision.synthetic import render_board_image  # noqa: E402
from magnus.vision.vision_node import BoardVisionNode, FakeCameraBackend  # noqa: E402

# Apertura corta que el "humano" (blancas) jugará contra el robot (negras).
HUMAN_OPENING = ["e2e4", "g1f3", "f1c4", "d2d4", "e1g1"]  # incluye un enroque


class SyntheticCamera(FakeCameraBackend):
    """Cámara falsa cuyo frame se re-renderiza según el tablero simulado."""

    def __init__(self, sim_board: chess.Board):
        self._sim = sim_board
        super().__init__(frames=[render_board_image(board_placement(sim_board))])

    def refresh(self) -> None:
        """Re-renderiza el frame tras un cambio en el tablero simulado."""
        self._frames = [render_board_image(board_placement(self._sim))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline completo de MAGNUS (simulado)")
    parser.add_argument("--plies", type=int, default=6,
                        help="Medias-jugadas totales a simular (humano+robot)")
    parser.add_argument("--difficulty", default="MEDIUM",
                        choices=[lvl.name for lvl in DifficultyLevel])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    # El "mundo físico" simulado: un tablero real que el humano y el brazo modifican.
    physical_board = chess.Board()
    camera = SyntheticCamera(physical_board)
    arm_backend = FakeArmBackend()
    human_moves = iter(HUMAN_OPENING)

    print("=" * 70)
    print("MAGNUS — pipeline completo simulado (visión + engine + brazo)")
    print("=" * 70)

    try:
        with BoardVisionNode(camera=camera, tracker=GameTracker()) as vision, \
             ChessEngineNode(default_difficulty=args.difficulty) as engine, \
             ArmNode(backend=arm_backend, table=make_fake_table()) as arm:

            for ply in range(args.plies):
                if physical_board.is_game_over():
                    print(f"\nPartida terminada: {physical_board.result()}")
                    break

                if physical_board.turn == chess.WHITE:
                    # --- Turno del HUMANO: mueve una pieza en el "mundo físico" ---
                    try:
                        uci = next(human_moves)
                    except StopIteration:
                        print("\n(Se acabaron las jugadas predefinidas del humano.)")
                        break
                    physical_board.push_uci(uci)
                    camera.refresh()
                    print(f"\n[HUMANO] mueve {uci} (en el tablero físico simulado)")

                    # --- NODO DE VISIÓN: detecta el cambio y produce la FEN ---
                    fen = vision.get_board_fen()
                    detected = vision.tracker.board.peek().uci()
                    print(f"[VISIÓN] jugada detectada: {detected}")
                    print(f"[VISIÓN] FEN: {fen}")
                    assert fen == physical_board.fen(), "¡La visión desincronizó!"
                else:
                    # --- NODO DEL ENGINE: calcula la respuesta del robot ---
                    resp = engine.compute_move_from_fen(physical_board.fen())
                    print(f"\n[ENGINE] responde: {resp.san} ({resp.uci})"
                          + (f" — captura en {resp.captured_square}" if resp.is_capture else ""))

                    # --- NODO DEL BRAZO: reproduce la secuencia pregrabada ---
                    arm_backend.clear()
                    steps = arm.execute(resp)
                    print(f"[BRAZO]  {len(steps)} primitivas: "
                          + " -> ".join(str(s) for s in steps[:4]) + " -> ...")
                    print(f"[BRAZO]  {len(arm_backend.commands)} comandos enviados al backend falso")

                    # El brazo "movió" la pieza en el mundo físico simulado.
                    physical_board.push_uci(resp.uci)
                    camera.refresh()
                    vision.notify_robot_move(resp.uci)

            print("\n" + "=" * 70)
            print(f"FEN final:      {physical_board.fen()}")
            print(f"FEN del tracker: {vision.tracker.fen()}")
            ok = physical_board.fen() == vision.tracker.fen()
            print(f"Sincronización visión<->mundo físico: {'OK' if ok else 'ERROR'}")
            print("=" * 70)
            return 0 if ok else 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
