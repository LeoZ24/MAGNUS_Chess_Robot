"""Tests de coherencia de config.py y del mapeo ArUco -> pieza."""

from magnus import config
from magnus.vision.piece_map import (
    ARUCO_TO_PIECE,
    PIECE_COUNTS,
    PIECE_TO_ARUCO,
    describe_id,
    id_for_piece,
    piece_for_id,
)


# --------------------------------------------------------------------------- #
# config.py
# --------------------------------------------------------------------------- #
def test_id_ranges_do_not_overlap():
    """Los tres roles de marcadores deben usar rangos de ID disjuntos."""
    pieces = set(config.ARUCO_IDS_PIECES)
    corners = set(config.ARUCO_IDS_BOARD_CORNERS)
    arm = {config.ARUCO_ID_ARM}
    assert pieces & corners == set()
    assert pieces & arm == set()
    assert corners & arm == set()


def test_ids_fit_in_dictionary():
    """DICT_4X4_50 solo tiene IDs 0-49."""
    all_ids = (
        set(config.ARUCO_IDS_PIECES)
        | set(config.ARUCO_IDS_BOARD_CORNERS)
        | {config.ARUCO_ID_ARM}
    )
    assert all(0 <= i < 50 for i in all_ids)


def test_board_dimensions():
    assert config.BOARD_SIZE_MM == config.SQUARE_SIZE_MM * config.BOARD_SQUARES
    # La pieza debe caber dentro de la casilla.
    assert config.PIECE_DIAMETER_MM < config.SQUARE_SIZE_MM


# --------------------------------------------------------------------------- #
# piece_map.py — mapeo POR TIPO: un ID por (tipo, color), copias comparten ID
# --------------------------------------------------------------------------- #
def test_piece_map_covers_all_piece_ids():
    assert set(ARUCO_TO_PIECE) == set(config.ARUCO_IDS_PIECES)


def test_piece_map_matches_physical_markers():
    """El mapeo debe ser EXACTAMENTE el de los marcadores impresos."""
    assert ARUCO_TO_PIECE == {
        0: "P", 1: "N", 2: "B", 6: "R", 8: "Q", 9: "K",
        12: "p", 15: "n", 16: "b", 18: "r", 21: "q", 23: "k",
    }


def test_piece_map_one_id_per_type():
    """12 tipos (6 por color), todos los símbolos distintos."""
    assert len(ARUCO_TO_PIECE) == 12
    symbols = list(ARUCO_TO_PIECE.values())
    assert len(set(symbols)) == 12
    assert {s for s in symbols if s.isupper()} == set("PNBRQK")
    assert {s for s in symbols if s.islower()} == set("pnbrqk")


def test_white_ids_below_black_ids():
    """IDs 0-9 blancas (mayúsculas), 12-23 negras (minúsculas)."""
    for aruco_id, sym in ARUCO_TO_PIECE.items():
        if aruco_id < 12:
            assert sym.isupper()
        else:
            assert sym.islower()


def test_piece_to_aruco_is_inverse():
    for aruco_id, sym in ARUCO_TO_PIECE.items():
        assert PIECE_TO_ARUCO[sym] == aruco_id
    assert len(PIECE_TO_ARUCO) == len(ARUCO_TO_PIECE)


def test_piece_counts_standard_set():
    """Copias físicas por tipo y color: 8 peones, 2 torres... 1 rey."""
    assert PIECE_COUNTS == {"K": 1, "Q": 1, "R": 2, "B": 2, "N": 2, "P": 8}
    assert sum(PIECE_COUNTS.values()) == 16     # 16 piezas por bando


def test_helpers():
    assert piece_for_id(0) == "P"
    assert piece_for_id(9) == "K"
    assert piece_for_id(23) == "k"
    assert id_for_piece("P") == 0
    assert id_for_piece("q") == 21
    assert describe_id(8) == "white queen (Q)"
    assert describe_id(12) == "black pawn (p)"
