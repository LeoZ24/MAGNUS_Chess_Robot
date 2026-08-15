"""Tests del renderer del tablero y del dashboard (sin ventanas, solo NumPy)."""

import cv2
import numpy as np
import pytest

from magnus.vision.board_render import (
    BoardRenderer,
    Dashboard,
    DashboardState,
    render_board,
)


def square_crop(renderer: BoardRenderer, img: np.ndarray, square: str) -> np.ndarray:
    x, y = renderer.square_origin(square)
    s = renderer.square_px
    return img[y : y + s, x : x + s]


# --------------------------------------------------------------------------- #
# Tablero
# --------------------------------------------------------------------------- #
def test_render_shape_and_dtype():
    r = BoardRenderer(square_px=50, margin_px=20)
    img = r.render({})
    assert img.shape == (50 * 8 + 40, 50 * 8 + 40, 3)
    assert img.dtype == np.uint8


def test_pieces_change_their_square():
    r = BoardRenderer()
    empty = r.render({})
    with_piece = r.render({"e4": "P"})
    assert not np.array_equal(square_crop(r, empty, "e4"), square_crop(r, with_piece, "e4"))
    # Las demás casillas no cambian.
    assert np.array_equal(square_crop(r, empty, "a1"), square_crop(r, with_piece, "a1"))


def test_all_twelve_symbols_render():
    placement = dict(zip(
        ["a1", "b1", "c1", "d1", "e1", "f1", "a8", "b8", "c8", "d8", "e8", "f8"],
        ["P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k"],
    ))
    img = render_board(placement)
    assert img is not None and img.size > 0


def test_orientation_flip_moves_piece():
    white = BoardRenderer(orientation="white")
    black = BoardRenderer(orientation="black")
    placement = {"a1": "R"}
    img_w = white.render(placement)
    img_b = black.render(placement)
    # Con blancas abajo, a1 está abajo-izquierda; con negras abajo, arriba-derecha.
    assert white.square_origin("a1") == (white.margin_px, white.margin_px + 7 * white.square_px)
    assert black.square_origin("a1") == (black.margin_px + 7 * black.square_px, black.margin_px)
    # La casilla a1 (torre sobre casilla oscura) se ve idéntica en ambas
    # orientaciones — solo cambia DÓNDE cae dentro de la imagen.
    assert np.array_equal(square_crop(white, img_w, "a1"), square_crop(black, img_b, "a1"))
    # En la imagen invertida, la región abajo-izquierda ahora es h8 (vacía):
    # distinta de la misma región en la imagen con blancas abajo (la torre).
    region_w = square_crop(white, img_w, "a1")
    region_b = square_crop(white, img_b, "a1")   # mismas coordenadas de píxel
    assert not np.array_equal(region_w, region_b)


def test_flip_toggles():
    r = BoardRenderer(orientation="white")
    r.flip()
    assert r.orientation == "black"
    r.flip()
    assert r.orientation == "white"


def test_last_move_tints_squares():
    r = BoardRenderer()
    plain = r.render({})
    tinted = r.render({}, last_move="e2e4")
    for sq in ("e2", "e4"):
        assert not np.array_equal(square_crop(r, plain, sq), square_crop(r, tinted, sq))
    assert np.array_equal(square_crop(r, plain, "a8"), square_crop(r, tinted, "a8"))


def test_planned_arrow_draws_on_board():
    r = BoardRenderer()
    plain = r.render({})
    arrow = r.render({}, planned_move=("g1", "f3"))   # salto de caballo (con codo)
    assert not np.array_equal(plain, arrow)


def test_check_glow_changes_king_square():
    r = BoardRenderer()
    plain = r.render({"e1": "K"})
    check = r.render({"e1": "K"}, check_square="e1")
    assert not np.array_equal(square_crop(r, plain, "e1"), square_crop(r, check, "e1"))


def test_pending_marker_changes_square():
    r = BoardRenderer()
    plain = r.render({})
    pending = r.render({}, pending_squares=["d5"])
    assert not np.array_equal(square_crop(r, plain, "d5"), square_crop(r, pending, "d5"))


def test_invalid_symbol_raises():
    with pytest.raises(ValueError):
        render_board({"e4": "X"})


def test_invalid_orientation_raises():
    with pytest.raises(ValueError):
        BoardRenderer(orientation="sideways")


def test_invalid_uci_raises():
    with pytest.raises(ValueError):
        render_board({}, last_move="e4")


# --------------------------------------------------------------------------- #
# Iconos personalizados
# --------------------------------------------------------------------------- #
def test_custom_icon_overrides_builtin(tmp_path):
    # Un PNG rojo puro con alfa total como peón blanco.
    icon = np.zeros((32, 32, 4), dtype=np.uint8)
    icon[:, :, 2] = 255      # rojo (BGR)
    icon[:, :, 3] = 255
    cv2.imwrite(str(tmp_path / "wP.png"), icon)

    r = BoardRenderer(icon_dir=tmp_path)
    img = r.render({"e4": "P", "d4": "N"})   # N no tiene PNG: usa el integrado
    crop = square_crop(r, img, "e4")
    # El centro de e4 debe ser rojo puro (el icono personalizado).
    cx = crop.shape[0] // 2
    assert tuple(crop[cx, cx]) == (0, 0, 255)


def test_missing_icon_dir_falls_back(tmp_path):
    r = BoardRenderer(icon_dir=tmp_path / "no_existe")
    img = r.render({"e4": "P"})
    assert img is not None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def test_dashboard_composes_without_camera():
    dash = Dashboard(renderer=BoardRenderer(square_px=40, margin_px=16))
    board = dash.renderer.render({"e1": "K", "e8": "k"})
    out = dash.compose(board, camera_img=None, state=DashboardState())
    assert out.dtype == np.uint8
    assert out.shape[0] > board.shape[0]
    assert out.shape[1] > board.shape[1]


def test_dashboard_composes_with_full_state():
    dash = Dashboard()
    board = dash.renderer.render({"e1": "K", "e8": "k"})
    camera = np.full((480, 640, 3), 90, dtype=np.uint8)
    state = DashboardState(
        mode="PARTIDA", turn="b", corners_found=4, pieces_confirmed=32,
        pieces_pending=3, arm_seen=True, engine_label="STOCKFISH · MEDIUM",
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        last_move_san="e4", planned_san="e5", planned_uci="e7e5",
        planned_eval="-0.12", history_san=["e4", "e5", "Nf3", "Nc6"],
        captured_by_white=["p", "n"], captured_by_black=["P"],
        message="aviso de prueba", fps=30.0,
    )
    out = dash.compose(board, camera, state)
    assert out.dtype == np.uint8 and out.ndim == 3


def test_dashboard_larger_camera_is_letterboxed():
    dash = Dashboard()
    board = dash.renderer.render({})
    huge = np.full((2000, 3000, 3), 120, dtype=np.uint8)
    out = dash.compose(board, huge)
    assert out.shape[0] < 2000
