"""Tests del módulo del engine de MAGNUS.

La mayoría usan un backend falso (``FakeBackend``) para validar de forma
determinista la construcción de metadatos de la jugada (captura, enroque,
al paso, promoción) sin depender de Stockfish.  Hay además un test de
integración que usa el motor real y se omite si no está instalado.
"""

import os
import sys

import chess
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from magnus.core.messages import PositionRequest, STARTING_FEN
from magnus.engine import (
    ChessEngineNode,
    DifficultyLevel,
    GameOverError,
    InvalidPositionError,
    get_config,
)
from magnus.engine.backend import EngineBackend, EnginePlayResult, find_engine_binary


class FakeBackend(EngineBackend):
    """Backend de prueba: devuelve siempre la jugada UCI que le indiques."""

    def __init__(self, move_uci: str):
        self.move_uci = move_uci
        self.started = False

    def start(self):
        self.started = True

    def select_move(self, board, config):
        move = chess.Move.from_uci(self.move_uci)
        assert move in board.legal_moves, f"{self.move_uci} no es legal en {board.fen()}"
        return EnginePlayResult(move=move, evaluation_cp=42, mate_in=None, depth=7)

    def quit(self):
        self.started = False


def make_node(move_uci: str) -> ChessEngineNode:
    return ChessEngineNode(backend=FakeBackend(move_uci)).start()


# --------------------------------------------------------------------------- #
# Dificultad
# --------------------------------------------------------------------------- #
def test_difficulty_parse_variants():
    assert DifficultyLevel.parse("MEDIUM") is DifficultyLevel.MEDIUM
    assert DifficultyLevel.parse("medium") is DifficultyLevel.MEDIUM
    assert DifficultyLevel.parse(3) is DifficultyLevel.MEDIUM
    assert DifficultyLevel.parse(DifficultyLevel.HARD) is DifficultyLevel.HARD
    with pytest.raises(ValueError):
        DifficultyLevel.parse("imposible")


def test_every_level_has_preset():
    for lvl in DifficultyLevel:
        cfg = get_config(lvl)
        assert cfg.name == lvl.name


def test_movetime_override():
    cfg = get_config("MEDIUM").with_movetime(5.0)
    assert cfg.movetime == 5.0
    # no debe mutar el preset original
    assert get_config("MEDIUM").movetime != 5.0


# --------------------------------------------------------------------------- #
# Mensajes
# --------------------------------------------------------------------------- #
def test_position_request_roundtrip():
    req = PositionRequest(fen=STARTING_FEN, difficulty="HARD", request_id="abc")
    again = PositionRequest.from_dict(req.to_dict())
    assert again == req
    # tolera claves extra
    PositionRequest.from_dict({**req.to_dict(), "extra": 1})


# --------------------------------------------------------------------------- #
# Construcción de la respuesta (metadatos)
# --------------------------------------------------------------------------- #
def test_simple_move_metadata():
    node = make_node("e2e4")
    resp = node.compute_move_from_fen(STARTING_FEN, request_id="r1")
    assert resp.uci == "e2e4"
    assert resp.san == "e4"
    assert resp.from_square == "e2" and resp.to_square == "e4"
    assert resp.piece == "P"
    assert resp.side_to_move == "white"
    assert not resp.is_capture and not resp.is_castling
    assert resp.difficulty == "MEDIUM"
    assert resp.request_id == "r1"
    assert resp.evaluation_cp == 42 and resp.depth == 7
    # FEN resultante coherente
    assert resp.resulting_fen.startswith("rnbqkbnr/pppppppp/8/8/4P3")
    node.shutdown()


def test_capture_metadata():
    # Blancas pueden capturar: exd5
    fen = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2"
    node = make_node("e4d5")
    resp = node.compute_move_from_fen(fen)
    assert resp.is_capture
    assert resp.captured_square == "d5"
    assert resp.captured_piece == "p"
    assert not resp.is_en_passant
    node.shutdown()


def test_en_passant_metadata():
    # Tras 1.e4 e6 2.e5 f5, blancas juegan exf6 al paso.
    fen = "rnbqkbnr/pppp2pp/4p3/4Pp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"
    node = make_node("e5f6")
    resp = node.compute_move_from_fen(fen)
    assert resp.is_capture and resp.is_en_passant
    # El peón capturado está en f5, NO en el destino f6.
    assert resp.captured_square == "f5"
    assert resp.captured_piece == "p"
    node.shutdown()


def test_kingside_castle_metadata():
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    node = make_node("e1g1")
    resp = node.compute_move_from_fen(fen)
    assert resp.is_castling and resp.is_kingside_castle
    assert resp.rook_from == "h1" and resp.rook_to == "f1"
    node.shutdown()


def test_queenside_castle_metadata():
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    node = make_node("e1c1")
    resp = node.compute_move_from_fen(fen)
    assert resp.is_castling and not resp.is_kingside_castle
    assert resp.rook_from == "a1" and resp.rook_to == "d1"
    node.shutdown()


def test_promotion_metadata():
    fen = "8/P7/8/8/8/8/8/k6K w - - 0 1"
    node = make_node("a7a8q")
    resp = node.compute_move_from_fen(fen)
    assert resp.promotion == "q"
    assert resp.uci == "a7a8q"
    node.shutdown()


def test_checkmate_detection():
    # Mate del pastor: Dxf7#
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 1"
    # Dama en h5 hace falta; usemos una posición de mate en 1 directa:
    fen = "6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1"
    node = make_node("e1e8")
    resp = node.compute_move_from_fen(fen)
    assert resp.is_check and resp.is_checkmate and resp.is_game_over
    node.shutdown()


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #
def test_invalid_fen_raises():
    node = make_node("e2e4")
    with pytest.raises(InvalidPositionError):
        node.compute_move_from_fen("esto no es una fen")
    node.shutdown()


def test_game_over_raises():
    # Posición de mate (negras en jaque mate): no hay jugada legal.
    fen = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
    node = make_node("e2e4")
    with pytest.raises(GameOverError):
        node.compute_move_from_fen(fen)
    node.shutdown()


# --------------------------------------------------------------------------- #
# Integración con el motor real (se omite si no está instalado)
# --------------------------------------------------------------------------- #
def _engine_available() -> bool:
    try:
        find_engine_binary()
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _engine_available(), reason="No hay binario de motor UCI instalado")
def test_real_engine_returns_legal_move():
    with ChessEngineNode(default_difficulty="EASY") as node:
        resp = node.compute_move_from_fen(STARTING_FEN)
        board = chess.Board(STARTING_FEN)
        assert chess.Move.from_uci(resp.uci) in board.legal_moves


@pytest.mark.skipif(not _engine_available(), reason="No hay binario de motor UCI instalado")
def test_real_engine_difficulty_levels_all_work():
    with ChessEngineNode() as node:
        for lvl in DifficultyLevel:
            resp = node.compute_move_from_fen(STARTING_FEN, difficulty=lvl)
            assert resp.uci  # devuelve algo
            assert resp.difficulty == lvl.name
