"""Tests de coherencia de config.py y del mapeo ArUco -> pieza."""

from collections import Counter

from magnus import config
from magnus.vision.piece_map import ARUCO_TO_PIECE, describe_id, piece_for_id


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
# piece_map.py
# --------------------------------------------------------------------------- #
def test_piece_map_covers_all_piece_ids():
    assert set(ARUCO_TO_PIECE) == set(config.ARUCO_IDS_PIECES)


def test_piece_map_full_armies():
    """Cada bando: 1 rey, 1 dama, 2 torres, 2 alfiles, 2 caballos, 8 peones."""
    expected = {"K": 1, "Q": 1, "R": 2, "B": 2, "N": 2, "P": 8}
    counts = Counter(ARUCO_TO_PIECE.values())
    for sym, n in expected.items():
        assert counts[sym] == n, f"blancas: {sym}"
        assert counts[sym.lower()] == n, f"negras: {sym.lower()}"


def test_white_ids_below_black_ids():
    """IDs 0-15 blancas (mayúsculas), 16-31 negras (minúsculas)."""
    for aruco_id, sym in ARUCO_TO_PIECE.items():
        if aruco_id < 16:
            assert sym.isupper()
        else:
            assert sym.islower()


def test_helpers():
    assert piece_for_id(0) == "K"
    assert piece_for_id(16) == "k"
    assert describe_id(1) == "white queen (Q)"
    assert describe_id(31) == "black pawn (p)"
