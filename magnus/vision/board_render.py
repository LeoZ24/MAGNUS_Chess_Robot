"""Representación visual profesional del tablero y dashboard de MAGNUS.

Módulo de **visualización pura**: convierte el estado que produce el pipeline
de visión (placement, jugadas, métricas de detección) en imágenes BGR listas
para ``cv2.imshow`` o para guardar a disco.  No detecta nada, no depende de
``python-chess`` ni de la cámara — solo necesita OpenCV y NumPy.

Componentes:

    * :class:`BoardRenderer` — tablero 2D estilo diagrama con iconos de pieza
      dibujados vectorialmente (o PNGs personalizados si existen), coordenadas,
      resaltado de la última jugada, flecha de la jugada planificada, jaque y
      casillas pendientes de confirmar.
    * :class:`DashboardState` + :func:`compose_dashboard` — panel de control
      completo: cámara en vivo + tablero renderizado + estado de la partida
      (turno, historial, capturas, FEN, métricas de detección).

Iconos personalizados: si se pasa ``icon_dir``, el renderer busca PNGs con
nombres ``wP.png, wN.png, wB.png, wR.png, wQ.png, wK.png`` y ``bP.png ...
bK.png`` (idealmente cuadrados y con canal alfa).  Cualquier archivo ausente
usa el icono vectorial integrado, así que se pueden reemplazar de a poco.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import cv2
import numpy as np

logger = logging.getLogger("magnus.vision.board_render")

_FILES = "abcdefgh"

# Una jugada para resaltar/planificar: UCI ("e2e4") o tupla ("e2", "e4").
MoveLike = Union[str, tuple[str, str]]


# --------------------------------------------------------------------------- #
# Tema de colores (todo en BGR, como OpenCV)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BoardTheme:
    """Paleta del tablero y del dashboard (colores BGR)."""

    # Tablero
    light_square: tuple[int, int, int] = (181, 217, 240)    # crema
    dark_square: tuple[int, int, int] = (99, 136, 181)      # marrón
    frame: tuple[int, int, int] = (35, 47, 61)              # marco de madera oscura
    coord_text: tuple[int, int, int] = (163, 201, 216)
    # Resaltados
    last_move_tint: tuple[int, int, int] = (0, 199, 155)    # verde-amarillo
    last_move_alpha: float = 0.38
    planned_color: tuple[int, int, int] = (90, 160, 56)     # verde flecha
    planned_alpha: float = 0.80
    check_color: tuple[int, int, int] = (44, 58, 226)       # rojo jaque
    pending_color: tuple[int, int, int] = (150, 150, 150)
    # Piezas vectoriales
    white_fill: tuple[int, int, int] = (246, 247, 248)
    white_line: tuple[int, int, int] = (48, 52, 58)
    black_fill: tuple[int, int, int] = (40, 38, 36)
    black_line: tuple[int, int, int] = (210, 214, 220)
    # Dashboard
    bg: tuple[int, int, int] = (26, 22, 20)
    panel: tuple[int, int, int] = (42, 36, 32)
    panel_border: tuple[int, int, int] = (78, 64, 56)
    text: tuple[int, int, int] = (240, 235, 232)
    text_dim: tuple[int, int, int] = (172, 158, 148)
    accent: tuple[int, int, int] = (138, 195, 76)           # verde MAGNUS
    warn: tuple[int, int, int] = (10, 165, 229)             # ámbar
    error: tuple[int, int, int] = (82, 82, 224)             # rojo


DEFAULT_THEME = BoardTheme()

# Factor de sobre-muestreo al dibujar los iconos (suaviza los bordes).
_GLYPH_SS = 4


# --------------------------------------------------------------------------- #
# Geometría de los iconos vectoriales (coordenadas normalizadas 0-100, y hacia
# abajo).  Cada pieza es una lista de formas que se rellenan y luego se
# contornean; los trazos de "detalle" solo se contornean.
# --------------------------------------------------------------------------- #
_Shape = tuple  # ("poly", pts) | ("circle", (cx, cy), r) | ("ellipse", (cx, cy), (ax, ay))
_BASE = ("poly", [(22, 84), (78, 84), (74, 93), (26, 93)])

_GLYPH_SHAPES: dict[str, dict[str, list[_Shape]]] = {
    "P": {
        "body": [
            ("circle", (50, 27), 12),
            ("poly", [(41, 42), (59, 42), (55, 50), (45, 50)]),
            ("poly", [(44, 50), (56, 50), (67, 84), (33, 84)]),
            _BASE,
        ],
        "detail": [],
    },
    "R": {
        "body": [
            ("poly", [(28, 12), (38, 12), (38, 19), (45, 19), (45, 12), (55, 12),
                      (55, 19), (62, 19), (62, 12), (72, 12), (72, 27), (66, 33),
                      (34, 33), (28, 27)]),
            ("poly", [(36, 33), (64, 33), (66, 70), (34, 70)]),
            ("poly", [(30, 70), (70, 70), (72, 79), (28, 79)]),
            ("poly", [(22, 79), (78, 79), (75, 93), (25, 93)]),
        ],
        "detail": [("line", (36, 42), (64, 42))],
    },
    # Cabeza de caballo de perfil mirando a la izquierda: hocico largo, quijada
    # marcada, oreja separada de la frente por una muesca, crin por detrás y
    # cuello arqueado que baja a la peana.
    "N": {
        "body": [
            ("poly", [
                (30, 76),   # base del cuello (izquierda)
                (29, 67),   # pecho
                (33, 59),   # garganta
                (31, 51),   # mandíbula
                (22, 56),   # quijada
                (14, 56),   # barbilla
                (8, 50),    # boca
                (9, 40),    # punta del hocico
                (17, 35),   # caña de la nariz
                (26, 29),   # cara
                (34, 22),   # frente
                (42, 17),
                (48, 15),   # alto de la cabeza
                (54, 19),   # muesca delante de la oreja
                (60, 4),    # punta de la oreja
                (66, 20),   # detrás de la oreja
                (71, 32),   # crin
                (75, 46),   # dorso del cuello
                (77, 61),
                (78, 76),   # base del cuello (derecha)
            ]),
            ("poly", [(27, 76), (77, 76), (79, 84), (21, 84)]),      # peana
            _BASE,
        ],
        "detail": [
            ("dot", (33, 30), 2.6),          # ojo
            ("dot", (15, 43), 1.8),          # ollar
            ("line", (9, 47), (19, 51)),     # boca
            ("line", (62, 24), (71, 50)),    # crin
        ],
    },
    "B": {
        "body": [
            ("circle", (50, 9), 5),
            ("ellipse", (50, 32), (13, 18)),
            ("poly", [(36, 52), (64, 52), (61, 58), (39, 58)]),
            ("poly", [(42, 58), (58, 58), (67, 84), (33, 84)]),
            _BASE,
        ],
        "detail": [("line", (44, 36), (57, 22))],
    },
    "Q": {
        "body": [
            ("poly", [(24, 38), (25, 15), (35, 31), (39, 11), (46, 29), (50, 8),
                      (54, 29), (61, 11), (65, 31), (75, 15), (76, 38), (70, 46),
                      (30, 46)]),
            ("poly", [(38, 46), (62, 46), (68, 82), (32, 82)]),
            _BASE,
        ],
        "detail": [
            ("dot", (25, 13), 3.2), ("dot", (39, 9), 3.2), ("dot", (50, 6), 3.2),
            ("dot", (61, 9), 3.2), ("dot", (75, 13), 3.2),
            ("line", (32, 54), (68, 54)),
        ],
    },
    "K": {
        "body": [
            ("poly", [(47, 4), (53, 4), (53, 10), (59, 10), (59, 16), (53, 16),
                      (53, 23), (47, 23), (47, 16), (41, 16), (41, 10), (47, 10)]),
            ("ellipse", (50, 34), (16, 12)),
            ("poly", [(35, 45), (65, 45), (62, 52), (38, 52)]),
            ("poly", [(40, 52), (60, 52), (68, 82), (32, 82)]),
            _BASE,
        ],
        "detail": [("line", (34, 60), (66, 60))],
    },
}

# Nombres de archivo aceptados para iconos personalizados: wP.png, bK.png, ...
def _icon_filename(symbol: str) -> str:
    color = "w" if symbol.isupper() else "b"
    return f"{color}{symbol.upper()}.png"


# --------------------------------------------------------------------------- #
# Utilidades de dibujo
# --------------------------------------------------------------------------- #
def _rounded_rect(
    img: np.ndarray,
    p1: tuple[int, int],
    p2: tuple[int, int],
    color: tuple[int, int, int],
    radius: int = 8,
    thickness: int = -1,
) -> None:
    """Rectángulo de esquinas redondeadas (relleno o contorno)."""
    x1, y1 = p1
    x2, y2 = p2
    r = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    if thickness < 0:
        cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), color, -1, cv2.LINE_AA)
        cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), color, -1, cv2.LINE_AA)
        for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r), (x1 + r, y2 - r), (x2 - r, y2 - r)):
            cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)
    else:
        cv2.line(img, (x1 + r, y1), (x2 - r, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1 + r, y2), (x2 - r, y2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y1 + r), (x1, y2 - r), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x2, y1 + r), (x2, y2 - r), color, thickness, cv2.LINE_AA)
        for cx, cy, a0 in ((x1 + r, y1 + r, 180), (x2 - r, y1 + r, 270),
                           (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90)):
            cv2.ellipse(img, (cx, cy), (r, r), 0, a0, a0 + 90, color, thickness, cv2.LINE_AA)


def _put_text(
    img: np.ndarray,
    text: str,
    org: tuple[int, int],
    scale: float,
    color: tuple[int, int, int],
    thickness: int = 1,
    font: int = cv2.FONT_HERSHEY_SIMPLEX,
) -> int:
    """``cv2.putText`` con antialiasing; devuelve el ancho del texto en px."""
    cv2.putText(img, text, org, font, scale, color, thickness, cv2.LINE_AA)
    (w, _), _ = cv2.getTextSize(text, font, scale, thickness)
    return w


def _text_width(text: str, scale: float, thickness: int = 1,
                font: int = cv2.FONT_HERSHEY_SIMPLEX) -> int:
    (w, _), _ = cv2.getTextSize(text, font, scale, thickness)
    return w


def _wrap_text(text: str, max_width_px: int, scale: float,
               thickness: int = 1) -> list[str]:
    """Parte un texto en líneas que quepan en ``max_width_px`` (corte por palabras)."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and _text_width(candidate, scale, thickness) > max_width_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _normalize_move(move: Optional[MoveLike]) -> Optional[tuple[str, str]]:
    """Acepta UCI ("e2e4", "e7e8q") o tupla ("e2","e4") y normaliza a tupla."""
    if move is None:
        return None
    if isinstance(move, str):
        if len(move) < 4:
            raise ValueError(f"Jugada UCI inválida: {move!r}")
        return move[:2], move[2:4]
    return move[0], move[1]


# --------------------------------------------------------------------------- #
# Renderer del tablero
# --------------------------------------------------------------------------- #
class BoardRenderer:
    """Dibuja el tablero 2D con iconos de pieza, resaltados y flechas.

    Args:
        square_px: lado de cada casilla en píxeles.
        margin_px: marco alrededor del área de juego (donde van las coordenadas).
        orientation: ``"white"`` (blancas abajo) o ``"black"`` (negras abajo).
        icon_dir: carpeta opcional con PNGs personalizados (``wP.png``...); los
            que falten usan el icono vectorial integrado.
        theme: paleta de colores.
    """

    def __init__(
        self,
        square_px: int = 66,
        margin_px: int = 26,
        orientation: str = "white",
        icon_dir: Optional[Union[str, Path]] = None,
        theme: BoardTheme = DEFAULT_THEME,
    ):
        if orientation not in ("white", "black"):
            raise ValueError(f"Orientación inválida: {orientation!r}")
        self.square_px = square_px
        self.margin_px = margin_px
        self.orientation = orientation
        self.theme = theme
        self._icon_dir = Path(icon_dir) if icon_dir else None
        # Cachés: (símbolo, tamaño) -> (BGR float32, alfa float32 H×W×1)
        self._glyph_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------------ #
    # Geometría
    # ------------------------------------------------------------------ #
    @property
    def board_px(self) -> int:
        """Lado total de la imagen del tablero (casillas + marco)."""
        return self.square_px * 8 + self.margin_px * 2

    def flip(self) -> None:
        """Invierte la orientación (blancas/negras abajo)."""
        self.orientation = "black" if self.orientation == "white" else "white"

    def square_origin(self, square: str) -> tuple[int, int]:
        """Esquina superior izquierda (px) de una casilla, según la orientación."""
        file_idx = _FILES.index(square[0])
        rank = int(square[1])
        if self.orientation == "white":
            col, row = file_idx, 8 - rank
        else:
            col, row = 7 - file_idx, rank - 1
        return self.margin_px + col * self.square_px, self.margin_px + row * self.square_px

    def square_center(self, square: str) -> tuple[int, int]:
        x, y = self.square_origin(square)
        return x + self.square_px // 2, y + self.square_px // 2

    # ------------------------------------------------------------------ #
    # Iconos de pieza
    # ------------------------------------------------------------------ #
    def _load_custom_icon(self, symbol: str, size: int) -> Optional[tuple[np.ndarray, np.ndarray]]:
        """Carga un PNG personalizado si existe; devuelve (BGR, alfa) o None."""
        if self._icon_dir is None:
            return None
        path = self._icon_dir / _icon_filename(symbol)
        if not path.is_file():
            return None
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            logger.warning("No se pudo leer el icono personalizado %s.", path)
            return None
        raw = cv2.resize(raw, (size, size), interpolation=cv2.INTER_AREA)
        if raw.ndim == 3 and raw.shape[2] == 4:
            bgr = raw[:, :, :3].astype(np.float32)
            alpha = (raw[:, :, 3:4].astype(np.float32)) / 255.0
        else:
            if raw.ndim == 2:
                raw = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
            bgr = raw[:, :, :3].astype(np.float32)
            alpha = np.ones((size, size, 1), dtype=np.float32)
        logger.debug("Icono personalizado cargado: %s", path)
        return bgr, alpha

    def _draw_builtin_glyph(self, symbol: str, size: int) -> tuple[np.ndarray, np.ndarray]:
        """Dibuja el icono vectorial integrado con sobre-muestreo."""
        theme = self.theme
        is_white = symbol.isupper()
        fill = theme.white_fill if is_white else theme.black_fill
        line = theme.white_line if is_white else theme.black_line

        big = size * _GLYPH_SS
        canvas = np.zeros((big, big, 3), dtype=np.uint8)
        mask = np.zeros((big, big), dtype=np.uint8)
        k = big / 100.0

        def pt(p: Sequence[float]) -> tuple[int, int]:
            return int(round(p[0] * k)), int(round(p[1] * k))

        spec = _GLYPH_SHAPES[symbol.upper()]
        outline_w = max(2, int(round(2.4 * k)))
        detail_w = max(2, int(round(2.0 * k)))

        def draw_shape(shape: _Shape, color, thickness: int, target=None) -> None:
            kind = shape[0]
            imgs = [(canvas, color), (mask, 255)] if target is None else [(target[0], target[1])]
            for img, col in imgs:
                if kind == "poly":
                    pts = np.array([pt(p) for p in shape[1]], dtype=np.int32)
                    if thickness < 0:
                        cv2.fillPoly(img, [pts], col, cv2.LINE_AA)
                    else:
                        cv2.polylines(img, [pts], True, col, thickness, cv2.LINE_AA)
                elif kind == "circle":
                    r = int(round(shape[2] * k))
                    cv2.circle(img, pt(shape[1]), r, col, thickness, cv2.LINE_AA)
                elif kind == "ellipse":
                    axes = (int(round(shape[2][0] * k)), int(round(shape[2][1] * k)))
                    cv2.ellipse(img, pt(shape[1]), axes, 0, 0, 360, col, thickness, cv2.LINE_AA)

        # 1. Rellenos (también alimentan la máscara de alfa).
        for shape in spec["body"]:
            draw_shape(shape, fill, -1)
        # 2. Contornos de cada forma (las intersecciones marcan collares/bandas).
        for shape in spec["body"]:
            draw_shape(shape, line, outline_w)
            draw_shape(shape, 255, outline_w, target=(mask, 255))
        # 3. Detalles (ojo del caballo, mitra del alfil, bandas...).
        for detail in spec["detail"]:
            if detail[0] == "line":
                cv2.line(canvas, pt(detail[1]), pt(detail[2]), line, detail_w, cv2.LINE_AA)
                cv2.line(mask, pt(detail[1]), pt(detail[2]), 255, detail_w, cv2.LINE_AA)
            elif detail[0] == "dot":
                r = int(round(detail[2] * k))
                cv2.circle(canvas, pt(detail[1]), r, line, -1, cv2.LINE_AA)
                cv2.circle(mask, pt(detail[1]), r, 255, -1, cv2.LINE_AA)

        # Reducir con INTER_AREA = antialiasing del contorno.
        bgr = cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
        alpha = cv2.resize(mask, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
        alpha = (alpha / 255.0)[:, :, np.newaxis]
        return bgr, alpha

    def _glyph(self, symbol: str, size: int) -> tuple[np.ndarray, np.ndarray]:
        if symbol.upper() not in _GLYPH_SHAPES:
            raise ValueError(f"Símbolo de pieza desconocido: {symbol!r}")
        key = (symbol, size)
        if key not in self._glyph_cache:
            glyph = self._load_custom_icon(symbol, size) or self._draw_builtin_glyph(symbol, size)
            self._glyph_cache[key] = glyph
        return self._glyph_cache[key]

    def blit_piece(self, dst: np.ndarray, symbol: str, x: int, y: int, size: int) -> None:
        """Mezcla el icono de ``symbol`` sobre ``dst`` con (x, y) como esquina."""
        bgr, alpha = self._glyph(symbol, size)
        h, w = dst.shape[:2]
        if x < 0 or y < 0 or x + size > w or y + size > h:
            return
        roi = dst[y : y + size, x : x + size].astype(np.float32)
        dst[y : y + size, x : x + size] = (roi * (1.0 - alpha) + bgr * alpha).astype(np.uint8)

    # ------------------------------------------------------------------ #
    # Capas del tablero
    # ------------------------------------------------------------------ #
    def _draw_frame_and_squares(self, img: np.ndarray) -> None:
        theme = self.theme
        img[:] = theme.frame
        for rank in range(1, 9):
            for file_idx in range(8):
                square = f"{_FILES[file_idx]}{rank}"
                x, y = self.square_origin(square)
                # Paridad estándar: a1 oscura, h1 clara.
                is_light = (file_idx + rank) % 2 == 0
                color = theme.light_square if is_light else theme.dark_square
                # cv2.rectangle incluye la esquina final: -1 para no pisar la vecina.
                cv2.rectangle(img, (x, y),
                              (x + self.square_px - 1, y + self.square_px - 1), color, -1)

    def _draw_coordinates(self, img: np.ndarray) -> None:
        theme = self.theme
        scale = max(0.34, self.square_px / 160.0)
        files = _FILES if self.orientation == "white" else _FILES[::-1]
        ranks = "87654321" if self.orientation == "white" else "12345678"
        for i, f in enumerate(files):
            cx = self.margin_px + i * self.square_px + self.square_px // 2
            w = _text_width(f, scale)
            y = self.board_px - max(7, (self.margin_px - 10) // 2)
            _put_text(img, f, (cx - w // 2, y), scale, theme.coord_text, 1)
        for i, r in enumerate(ranks):
            cy = self.margin_px + i * self.square_px + self.square_px // 2
            _put_text(img, r, (max(5, self.margin_px // 2 - 4), cy + 4), scale,
                      theme.coord_text, 1)

    def _tint_square(self, img: np.ndarray, square: str,
                     color: tuple[int, int, int], alpha: float) -> None:
        x, y = self.square_origin(square)
        roi = img[y : y + self.square_px, x : x + self.square_px]
        overlay = np.full_like(roi, color)
        cv2.addWeighted(overlay, alpha, roi, 1.0 - alpha, 0, dst=roi)

    def _draw_check_glow(self, img: np.ndarray, square: str) -> None:
        """Halo radial rojo bajo el rey en jaque."""
        s = self.square_px
        x, y = self.square_origin(square)
        yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
        d = np.sqrt((xx - s / 2) ** 2 + (yy - s / 2) ** 2) / (s * 0.72)
        alpha = np.clip(1.0 - d, 0.0, 1.0)[:, :, np.newaxis] * 0.9
        roi = img[y : y + s, x : x + s].astype(np.float32)
        color = np.array(self.theme.check_color, dtype=np.float32)
        img[y : y + s, x : x + s] = (roi * (1 - alpha) + color * alpha).astype(np.uint8)

    def _draw_pending_marker(self, img: np.ndarray, square: str) -> None:
        """Casilla cuya detección aún no está confirmada: círculo punteado."""
        cx, cy = self.square_center(square)
        r = int(self.square_px * 0.30)
        for a0 in range(0, 360, 45):
            cv2.ellipse(img, (cx, cy), (r, r), 0, a0, a0 + 26,
                        self.theme.pending_color, 2, cv2.LINE_AA)

    def _draw_arrow(self, img: np.ndarray, from_sq: str, to_sq: str) -> None:
        """Flecha de jugada estilo análisis (con codo en saltos de caballo)."""
        theme = self.theme
        s = self.square_px
        overlay = img.copy()
        start = np.array(self.square_center(from_sq), dtype=np.float32)
        end = np.array(self.square_center(to_sq), dtype=np.float32)

        df = _FILES.index(to_sq[0]) - _FILES.index(from_sq[0])
        dr = int(to_sq[1]) - int(from_sq[1])
        knight_move = (abs(df), abs(dr)) in ((1, 2), (2, 1))

        shaft = max(4, int(s * 0.19))
        head_len = s * 0.42
        head_half = s * 0.30

        if knight_move:
            # Primero la pata larga, luego la corta (como en lichess).
            if abs(dr) > abs(df):
                elbow = np.array([start[0], end[1]], dtype=np.float32)
            else:
                elbow = np.array([end[0], start[1]], dtype=np.float32)
            segments = [(start, elbow), (elbow, end)]
            cv2.circle(overlay, tuple(np.int32(elbow)), shaft // 2,
                       theme.planned_color, -1, cv2.LINE_AA)
        else:
            segments = [(start, end)]

        # Acortar el último tramo para dejar sitio a la punta.
        a, b = segments[-1]
        direction = b - a
        norm = float(np.linalg.norm(direction))
        if norm < 1e-3:
            return
        direction /= norm
        base = b - direction * head_len
        segments[-1] = (a, base)

        cv2.circle(overlay, tuple(np.int32(start)), shaft // 2,
                   theme.planned_color, -1, cv2.LINE_AA)
        for seg_a, seg_b in segments:
            cv2.line(overlay, tuple(np.int32(seg_a)), tuple(np.int32(seg_b)),
                     theme.planned_color, shaft, cv2.LINE_AA)
        perp = np.array([-direction[1], direction[0]], dtype=np.float32)
        head = np.array([b, base + perp * head_half, base - perp * head_half], dtype=np.int32)
        cv2.fillPoly(overlay, [head], theme.planned_color, cv2.LINE_AA)

        cv2.addWeighted(overlay, theme.planned_alpha, img, 1.0 - theme.planned_alpha, 0, dst=img)

    # ------------------------------------------------------------------ #
    # Render principal
    # ------------------------------------------------------------------ #
    def render(
        self,
        placement: dict[str, str],
        last_move: Optional[MoveLike] = None,
        planned_move: Optional[MoveLike] = None,
        check_square: Optional[str] = None,
        pending_squares: Iterable[str] = (),
    ) -> np.ndarray:
        """Renderiza el tablero completo y devuelve la imagen BGR.

        Args:
            placement: ``{"e4": "P", ...}`` — casilla -> símbolo FEN.
            last_move: última jugada ejecutada (se tiñen origen y destino).
            planned_move: jugada que el robot va a jugar (flecha).
            check_square: casilla del rey en jaque (halo rojo).
            pending_squares: casillas detectadas pero aún sin confirmar.
        """
        img = np.zeros((self.board_px, self.board_px, 3), dtype=np.uint8)
        self._draw_frame_and_squares(img)

        last = _normalize_move(last_move)
        if last is not None:
            for sq in last:
                self._tint_square(img, sq, self.theme.last_move_tint,
                                  self.theme.last_move_alpha)
        if check_square:
            self._draw_check_glow(img, check_square)

        self._draw_coordinates(img)

        piece_size = int(self.square_px * 0.86)
        offset = (self.square_px - piece_size) // 2
        for square, symbol in sorted(placement.items()):
            x, y = self.square_origin(square)
            self.blit_piece(img, symbol, x + offset, y + offset, piece_size)

        for square in pending_squares:
            self._draw_pending_marker(img, square)

        planned = _normalize_move(planned_move)
        if planned is not None:
            self._draw_arrow(img, planned[0], planned[1])

        return img


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@dataclass
class DashboardState:
    """Todo lo que el dashboard muestra además del tablero.

    Los campos son opcionales a propósito: el dashboard pinta "—" o oculta la
    sección correspondiente cuando faltan datos.
    """

    mode: str = "OBSERVACION"                   # "OBSERVACION" | "PARTIDA" | texto libre
    turn: Optional[str] = None                  # "w" / "b"
    corners_found: int = 0
    corners_remembered: int = 0                 # esquinas tapadas, en su última posición
    pieces_confirmed: int = 0
    pieces_pending: int = 0
    pieces_off_board: int = 0                   # marcadores de pieza fuera del tablero
    arm_seen: bool = False
    camera_label: str = "CAMARA"
    engine_label: Optional[str] = None          # p. ej. "Stockfish · MEDIUM"
    fen: Optional[str] = None
    last_move_san: Optional[str] = None         # última jugada detectada/ejecutada
    planned_san: Optional[str] = None           # lo que el robot va a jugar
    planned_uci: Optional[str] = None
    planned_eval: Optional[str] = None          # "+0.45" / "M3"
    history_san: list[str] = field(default_factory=list)   # medias-jugadas en orden
    captured_by_white: list[str] = field(default_factory=list)  # símbolos negros capturados
    captured_by_black: list[str] = field(default_factory=list)  # símbolos blancos capturados
    message: Optional[str] = None               # aviso (ámbar)
    error: Optional[str] = None                 # error (rojo)
    fps: Optional[float] = None
    voice_phrase: Optional[str] = None          # lo último que dijo el robot
    voice_muted: bool = False


def _fitted_rect(frame_shape: tuple[int, ...], box_w: int,
                 box_h: int) -> tuple[int, int, int, int]:
    """``(x0, y0, ancho, alto)`` que ocupa la imagen dentro de su caja."""
    h, w = frame_shape[:2]
    scale = min(box_w / w, box_h / h)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    return (box_w - new_w) // 2, (box_h - new_h) // 2, new_w, new_h


def _fit_into(frame: np.ndarray, box_w: int, box_h: int,
              bg: tuple[int, int, int]) -> np.ndarray:
    """Escala ``frame`` para caber en (box_w × box_h) con letterboxing."""
    out = np.full((box_h, box_w, 3), bg, dtype=np.uint8)
    x0, y0, new_w, new_h = _fitted_rect(frame.shape, box_w, box_h)
    out[y0 : y0 + new_h, x0 : x0 + new_w] = cv2.resize(
        frame, (new_w, new_h), interpolation=cv2.INTER_AREA
    )
    return out


class Dashboard:
    """Compositor del panel de control de MAGNUS.

    Combina la vista de la cámara, el tablero renderizado y un panel lateral de
    estado en una sola imagen lista para ``cv2.imshow``.
    """

    HEADER_H = 62
    FOOTER_H = 46
    SIDE_W = 330
    PAD = 14

    def __init__(self, renderer: Optional[BoardRenderer] = None,
                 theme: BoardTheme = DEFAULT_THEME):
        self.renderer = renderer or BoardRenderer(theme=theme)
        self.theme = theme

    # ------------------------------------------------------------------ #
    # Bloques
    # ------------------------------------------------------------------ #
    def _panel(self, img: np.ndarray, x: int, y: int, w: int, h: int,
               title: Optional[str] = None) -> int:
        """Dibuja una tarjeta y devuelve la Y donde empieza su contenido."""
        theme = self.theme
        _rounded_rect(img, (x, y), (x + w, y + h), theme.panel, radius=10)
        _rounded_rect(img, (x, y), (x + w, y + h), theme.panel_border, radius=10, thickness=1)
        if title:
            _put_text(img, title, (x + 12, y + 22), 0.46, theme.text_dim, 1)
            return y + 34
        return y + 12

    def _pill(self, img: np.ndarray, x: int, y: int, text: str,
              color: tuple[int, int, int], filled: bool = False) -> int:
        """Píldora de estado; devuelve la X del borde derecho."""
        scale, th = 0.42, 1
        w = _text_width(text, scale, th) + 26
        h = 24
        bg = color if filled else self.theme.panel
        _rounded_rect(img, (x, y), (x + w, y + h), bg, radius=12)
        _rounded_rect(img, (x, y), (x + w, y + h), color, radius=12, thickness=1)
        txt_color = self.theme.bg if filled else color
        _put_text(img, text, (x + 13, y + 16), scale, txt_color, th)
        return x + w

    def _header(self, img: np.ndarray, width: int, state: DashboardState) -> None:
        theme = self.theme
        cv2.rectangle(img, (0, 0), (width, self.HEADER_H), theme.panel, -1)
        cv2.line(img, (0, self.HEADER_H), (width, self.HEADER_H), theme.panel_border, 1)
        x = self.PAD + 4
        x += _put_text(img, "MAGNUS", (x, 40), 1.05, theme.accent, 2,
                       cv2.FONT_HERSHEY_DUPLEX) + 14
        _put_text(img, "robot de ajedrez  ·  vision en vivo", (x, 38), 0.5,
                  theme.text_dim, 1)

        # Píldoras de estado a la derecha.
        pills: list[tuple[str, tuple[int, int, int], bool]] = []
        board_ok = state.corners_found >= 4
        # Las esquinas tapadas se recuerdan: se marcan con "MEM" en vez de perderse.
        board_text = f"TABLERO {state.corners_found}/4"
        if state.corners_remembered:
            board_text += f" ·{state.corners_remembered} MEM"
        pills.append((board_text, theme.accent if board_ok else theme.warn, board_ok))
        pills.append((f"PIEZAS {state.pieces_confirmed}", theme.accent
                      if state.pieces_confirmed else theme.text_dim, False))
        if state.pieces_pending:
            pills.append((f"DETECTANDO {state.pieces_pending}", theme.warn, False))
        if state.pieces_off_board:
            pills.append((f"FUERA {state.pieces_off_board}", theme.warn, False))
        if state.engine_label:
            pills.append((state.engine_label, theme.accent, False))
        if state.arm_seen:
            pills.append(("BRAZO", theme.accent, False))
        total = 0
        widths = []
        for text, _, _ in pills:
            w = _text_width(text, 0.42, 1) + 26
            widths.append(w)
            total += w + 8
        px = width - self.PAD - total
        py = (self.HEADER_H - 24) // 2
        for (text, color, filled), w in zip(pills, widths):
            self._pill(img, px, py, text, color, filled)
            px += w + 8

    def _footer(self, img: np.ndarray, width: int, y: int, state: DashboardState,
                keys_help: str) -> None:
        theme = self.theme
        cv2.rectangle(img, (0, y), (width, y + self.FOOTER_H), theme.panel, -1)
        cv2.line(img, (0, y), (width, y), theme.panel_border, 1)
        fen = state.fen or "—"
        x = self.PAD + 4
        x += _put_text(img, "FEN", (x, y + 20), 0.42, theme.text_dim, 1) + 10
        _put_text(img, fen, (x, y + 20), 0.44, theme.text, 1)
        _put_text(img, keys_help, (self.PAD + 4, y + 38), 0.4, theme.text_dim, 1)
        if state.fps is not None:
            text = f"{state.fps:4.1f} fps"
            w = _text_width(text, 0.42, 1)
            _put_text(img, text, (width - self.PAD - w, y + 38), 0.42, theme.text_dim, 1)

    def _captured_row(self, img: np.ndarray, x: int, y: int, label: str,
                      symbols: list[str]) -> None:
        theme = self.theme
        _put_text(img, label, (x, y + 14), 0.42, theme.text_dim, 1)
        size = 24
        px = x + 74
        for sym in symbols:
            self.renderer.blit_piece(img, sym, px, y - 4, size)
            px += int(size * 0.72)
        if not symbols:
            _put_text(img, "—", (x + 74, y + 14), 0.42, theme.text_dim, 1)

    def _side_panel(self, img: np.ndarray, x: int, y: int, w: int, h: int,
                    state: DashboardState) -> None:
        theme = self.theme
        cy = self._panel(img, x, y, w, h, "ESTADO DE LA PARTIDA")

        # Modo + turno.
        cy += 8
        mode_color = theme.accent if state.mode == "PARTIDA" else theme.text_dim
        right = self._pill(img, x + 12, cy, state.mode, mode_color,
                           filled=state.mode == "PARTIDA")
        if state.turn in ("w", "b"):
            turn_text = "MUEVEN BLANCAS" if state.turn == "w" else "MUEVEN NEGRAS"
            self._pill(img, right + 8, cy, turn_text, theme.text, False)
        cy += 40

        # Jugada planificada (lo que el robot va a jugar).
        cy = self._sub_title(img, x, cy, "ROBOT VA A JUGAR")
        if state.planned_san:
            text = state.planned_san
            if state.planned_uci:
                text += f"  ({state.planned_uci})"
            x2 = x + 12 + _put_text(img, text, (x + 12, cy + 20), 0.72, theme.accent, 2)
            if state.planned_eval:
                _put_text(img, state.planned_eval, (x2 + 12, cy + 20), 0.5,
                          theme.text_dim, 1)
        else:
            _put_text(img, "—", (x + 12, cy + 20), 0.7, theme.text_dim, 1)
        cy += 40

        # Última jugada detectada.
        cy = self._sub_title(img, x, cy, "ULTIMA JUGADA")
        _put_text(img, state.last_move_san or "—", (x + 12, cy + 18), 0.58,
                  theme.text, 1)
        cy += 36

        # Capturas.
        cy = self._sub_title(img, x, cy, "CAPTURAS")
        self._captured_row(img, x + 12, cy + 8, "Blancas", state.captured_by_white)
        cy += 32
        self._captured_row(img, x + 12, cy + 8, "Negras", state.captured_by_black)
        cy += 40

        # Historial (dos columnas: nº, blanca, negra).
        cy = self._sub_title(img, x, cy, "HISTORIAL")
        rows_available = max(0, (y + h - cy - 46) // 22)
        moves = state.history_san
        pairs = [(i // 2 + 1, moves[i], moves[i + 1] if i + 1 < len(moves) else "")
                 for i in range(0, len(moves), 2)]
        for num, white, black in pairs[-rows_available:] if rows_available else []:
            _put_text(img, f"{num:>2}.", (x + 12, cy + 18), 0.46, theme.text_dim, 1)
            _put_text(img, white, (x + 52, cy + 18), 0.46, theme.text, 1)
            _put_text(img, black, (x + 140, cy + 18), 0.46, theme.text, 1)
            cy += 22
        if not pairs:
            _put_text(img, "sin jugadas", (x + 12, cy + 18), 0.44, theme.text_dim, 1)
            cy += 22

        # Avisos (al pie del panel, en varias líneas si hacen falta).
        text, color = ((state.error, theme.error) if state.error
                       else (state.message, theme.warn))
        if text:
            lines = _wrap_text(text, w - 24, 0.44)
            msg_y = y + h - 14 - 16 * (len(lines) - 1)
            for line in lines:
                _put_text(img, line, (x + 12, msg_y), 0.44, color, 1)
                msg_y += 16

    def _subtitle(self, img: np.ndarray, x: int, bottom: int, width: int,
                  state: DashboardState) -> None:
        """Banda inferior con la última frase dicha por el robot."""
        if not state.voice_phrase:
            return
        theme = self.theme
        prefix = "[voz en silencio] " if state.voice_muted else ""
        lines = _wrap_text(prefix + state.voice_phrase, width - 28, 0.5)[-2:]
        band_h = 12 + 20 * len(lines)
        top = max(0, bottom - band_h)
        # Fondo translúcido para que el texto se lea sobre cualquier imagen.
        overlay = img[top:bottom, x : x + width].copy()
        cv2.rectangle(overlay, (0, 0), (width, band_h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, img[top:bottom, x : x + width], 0.45, 0,
                        dst=img[top:bottom, x : x + width])
        color = theme.text_dim if state.voice_muted else theme.accent
        for i, line in enumerate(lines):
            _put_text(img, line, (x + 14, top + 22 + 20 * i), 0.5, color, 1)

    def _sub_title(self, img: np.ndarray, x: int, cy: int, text: str) -> int:
        _put_text(img, text, (x + 12, cy + 12), 0.4, self.theme.text_dim, 1)
        return cy + 16

    # ------------------------------------------------------------------ #
    # Composición
    # ------------------------------------------------------------------ #
    def compose(
        self,
        board_img: np.ndarray,
        camera_img: Optional[np.ndarray] = None,
        state: Optional[DashboardState] = None,
        keys_help: str = "G iniciar partida · F girar tablero · R resetear deteccion · Q salir",
    ) -> np.ndarray:
        """Compone el dashboard completo y devuelve la imagen BGR final."""
        state = state or DashboardState()
        theme = self.theme
        pad = self.PAD

        board_h = board_img.shape[0]
        content_h = max(board_h, 480)
        cam_w = int(content_h * 4 / 3)

        width = pad + cam_w + pad + board_img.shape[1] + pad + self.SIDE_W + pad
        height = self.HEADER_H + pad + content_h + pad + self.FOOTER_H
        canvas = np.full((height, width, 3), theme.bg, dtype=np.uint8)

        self._header(canvas, width, state)
        top = self.HEADER_H + pad

        # Panel de la cámara.
        cam_x = pad
        cam_title_h = 30
        self._panel(canvas, cam_x, top, cam_w, content_h, None)
        _put_text(canvas, state.camera_label, (cam_x + 12, top + 20), 0.46,
                  theme.text_dim, 1)
        box_w, box_h = cam_w - 16, content_h - cam_title_h - 12
        if camera_img is not None:
            fitted = _fit_into(camera_img, box_w, box_h, theme.bg)
        else:
            fitted = np.full((box_h, box_w, 3), theme.bg, dtype=np.uint8)
            msg = "sin camara"
            w = _text_width(msg, 0.6, 1)
            _put_text(fitted, msg, ((box_w - w) // 2, box_h // 2), 0.6, theme.text_dim, 1)
        canvas[top + cam_title_h : top + cam_title_h + box_h,
               cam_x + 8 : cam_x + 8 + box_w] = fitted
        # Subtítulo de lo que dice el robot, ceñido a la imagen de la cámara
        # (no a su caja): en una feria ruidosa se lee aunque no se oiga.
        img_x, img_y, img_w, img_h = _fitted_rect(
            (camera_img if camera_img is not None else fitted).shape, box_w, box_h
        )
        self._subtitle(canvas, cam_x + 8 + img_x,
                       top + cam_title_h + img_y + img_h, img_w, state)

        # Tablero.
        board_x = cam_x + cam_w + pad
        board_y = top + (content_h - board_h) // 2
        canvas[board_y : board_y + board_h, board_x : board_x + board_img.shape[1]] = board_img

        # Panel lateral.
        side_x = board_x + board_img.shape[1] + pad
        self._side_panel(canvas, side_x, top, self.SIDE_W, content_h, state)

        self._footer(canvas, width, self.HEADER_H + pad + content_h + pad, state, keys_help)
        return canvas


# --------------------------------------------------------------------------- #
# Conveniencia
# --------------------------------------------------------------------------- #
def render_board(placement: dict[str, str], **kwargs) -> np.ndarray:
    """Atajo: renderiza el tablero con un :class:`BoardRenderer` por defecto."""
    return BoardRenderer().render(placement, **kwargs)
