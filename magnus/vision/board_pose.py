"""Pose del tablero: homografía tablero(mm) <-> imagen(px).

A partir de los 4 marcadores ArUco de las esquinas del tablero se calcula una
homografía que permite convertir cualquier punto entre el sistema de
coordenadas físico del tablero (milímetros) y el de la imagen (píxeles).  Con
ella, el centro de cada marcador de pieza se traduce a la casilla donde está.

Sistema de coordenadas del tablero (en mm)::

    origen (0,0) = esquina exterior de a8      x+ -> hacia la columna h
                                               y+ -> hacia la fila 1

    ID 40 (a8) ─────────── ID 41 (h8)          (vista cenital con las blancas
        │                      │                abajo, como un diagrama de
    ID 43 (a1) ─────────── ID 42 (h1)           ajedrez estándar)

Los marcadores de esquina deben colocarse de modo que su **centro** coincida
con la esquina exterior del área de juego correspondiente.

**La orientación de la cámara da igual.**  Como cada esquina se identifica por
su ID, la homografía sale correcta con la cámara en horizontal, en vertical,
desde el lado de las blancas o desde el de las negras: el tablero puede estar
girado cualquier ángulo dentro de la imagen.

Lo que sí importa es el **orden cíclico** de los IDs alrededor del tablero
(40 → 41 → 42 → 43 recorre el borde, no es un zig-zag).  Colocar los cuatro
marcadores en "orden de lectura" (40 y 41 arriba, 42 y 43 abajo) cruza dos
esquinas y produce una homografía degenerada: TODAS las piezas caen "fuera del
tablero" y no se genera ninguna FEN.  Por eso :func:`deduce_corner_layout`
deduce la colocación real a partir de la geometría en vez de darla por
supuesta, y avisa cuando no coincide con la esperada.
"""

from __future__ import annotations

import logging
import math
from typing import Mapping, NamedTuple, Optional

import cv2
import numpy as np

from .. import config

logger = logging.getLogger("magnus.vision.board_pose")

# Nombre de las 4 esquinas del área de juego, en orden cíclico alrededor del
# borde (el mismo que siguen ARUCO_IDS_BOARD_CORNERS: 40=a8, 41=h8, 42=h1,
# 43=a1).  El orden es lo único que importa: recorrer el borde sin cruzarse.
CORNER_NAMES: tuple[str, str, str, str] = ("a8", "h8", "h1", "a1")

# Coordenada mm (esquina exterior del área de juego) de cada esquina.
CORNER_MM_BY_NAME: dict[str, tuple[float, float]] = {
    "a8": (0.0, 0.0),
    "h8": (config.BOARD_SIZE_MM, 0.0),
    "h1": (config.BOARD_SIZE_MM, config.BOARD_SIZE_MM),
    "a1": (0.0, config.BOARD_SIZE_MM),
}

# Colocación esperada: ID -> esquina física.
EXPECTED_LAYOUT: dict[int, str] = dict(zip(config.ARUCO_IDS_BOARD_CORNERS, CORNER_NAMES))

_FILES = "abcdefgh"

# Área mínima del cuadrilátero de esquinas, relativa al lado medio al cuadrado.
# Un cuadrado perfecto vale 1.0; por debajo de esto los 4 marcadores están casi
# alineados (detección falsa) y la homografía no es fiable.
_MIN_QUAD_AREA_RATIO = 0.15

# Última disposición avisada por log: la pose se recalcula en cada frame, así
# que sin esto el aviso saldría decenas de veces por segundo.
_last_layout_logged: Optional[str] = None


class BoardPoseError(Exception):
    """No se pudo calcular la pose del tablero."""


class CornerLayout(NamedTuple):
    """Qué esquina física del tablero ocupa realmente cada marcador ArUco.

    Attributes:
        corner_by_id: ``{40: "a8", 41: "h8", ...}`` — deducido de la geometría.
        matches: cuántos de los 4 marcadores coinciden con :data:`EXPECTED_LAYOUT`.
    """

    corner_by_id: dict[int, str]
    matches: int

    @property
    def is_expected(self) -> bool:
        """``True`` si los 4 marcadores están donde su etiqueta indica."""
        return self.matches == 4

    def describe(self) -> str:
        """Descripción corta de la colocación real, p. ej. ``"40=a8 41=h8 …"``."""
        return " ".join(
            f"{i}={self.corner_by_id[i]}" for i in sorted(self.corner_by_id)
        )

    def warning_text(self) -> Optional[str]:
        """Aviso legible si la colocación no es la esperada, o ``None``."""
        if self.is_expected:
            return None
        wrong = sorted(i for i, name in self.corner_by_id.items()
                       if EXPECTED_LAYOUT.get(i) != name)
        return (
            f"esquinas {', '.join(str(i) for i in wrong)} mal colocadas: "
            f"se usa la disposicion real ({self.describe()})"
        )


# --------------------------------------------------------------------------- #
# Geometría del cuadrilátero de esquinas
# --------------------------------------------------------------------------- #
def _cyclic_order(centers: Mapping[int, tuple[float, float]]) -> list[int]:
    """IDs ordenados por su ángulo alrededor del centro del tablero.

    Recorre el borde del cuadrilátero sin cruzarse, en el mismo sentido en que
    ``CORNER_NAMES`` recorre el tablero en mm (la cámara mira el tablero desde
    arriba, así que la proyección conserva el sentido de giro).
    """
    cx = sum(x for x, _ in centers.values()) / len(centers)
    cy = sum(y for _, y in centers.values()) / len(centers)
    return sorted(centers, key=lambda i: math.atan2(centers[i][1] - cy,
                                                    centers[i][0] - cx))


def _validate_quad(points: list[tuple[float, float]]) -> None:
    """Comprueba que los 4 puntos formen un cuadrilátero convexo y con área.

    Un tablero real visto desde cualquier ángulo siempre se proyecta como un
    cuadrilátero convexo; si no lo es, alguno de los "marcadores de esquina" no
    está en el tablero (marcador suelto, reflejo, detección falsa).
    """
    n = len(points)
    area = 0.5 * abs(sum(
        points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
        for i in range(n)
    ))
    sides = [math.dist(points[i], points[(i + 1) % n]) for i in range(n)]
    mean_side = sum(sides) / n
    if mean_side <= 0 or area < _MIN_QUAD_AREA_RATIO * mean_side ** 2:
        raise BoardPoseError(
            "Los 4 marcadores de esquina no forman un cuadrilátero válido "
            f"(área {area:.0f} px² para un lado medio de {mean_side:.0f} px). "
            "¿Hay un marcador de esquina duplicado fuera del tablero?"
        )
    signs = []
    for i in range(n):
        ax, ay = points[i]
        bx, by = points[(i + 1) % n]
        cx, cy = points[(i + 2) % n]
        signs.append(math.copysign(1.0, (bx - ax) * (cy - by) - (by - ay) * (cx - bx)))
    if len(set(signs)) != 1:
        raise BoardPoseError(
            "Los 4 marcadores de esquina no forman un cuadrilátero convexo. "
            "¿Alguno no está en una esquina del tablero?"
        )


def _layout_is_new(layout: CornerLayout) -> bool:
    """``True`` la primera vez que se ve esta disposición (evita spam de logs)."""
    global _last_layout_logged
    described = layout.describe()
    if described == _last_layout_logged:
        return False
    _last_layout_logged = described
    return True


def deduce_corner_layout(
    corner_centers_px: Mapping[int, tuple[float, float]],
) -> CornerLayout:
    """Deduce qué esquina física ocupa cada marcador a partir de la geometría.

    Se recorre el cuadrilátero real de la imagen (orden cíclico) y se prueban
    las 4 rotaciones posibles del ciclo ``a8 → h8 → h1 → a1``, quedándose con
    la que coincide con más etiquetas de ID.  Así:

    * Con los marcadores bien colocados el resultado es idéntico a
      :data:`EXPECTED_LAYOUT` (coincidencia 4/4), gire como gire la cámara.
    * Con dos marcadores contiguos intercambiados (el error típico: colocarlos
      en orden de lectura en vez de recorriendo el borde) se respeta la mayoría
      y se corrige el par cruzado, en vez de generar una homografía degenerada.

    El único caso que no se puede resolver solo con los marcadores es
    intercambiar dos esquinas *opuestas*: las dos hipótesis (una girada 180°
    respecto de la otra) explican igual de bien las etiquetas.  Se elige la que
    respeta el marcador ``ARUCO_IDS_BOARD_CORNERS[0]`` y se avisa; la
    orientación termina de fijarse con la posición inicial de la partida o
    girando el mapeo (:meth:`BoardPose.rotated`).

    Raises:
        BoardPoseError: si los 4 puntos no forman un cuadrilátero utilizable.
    """
    ids = list(config.ARUCO_IDS_BOARD_CORNERS)
    order = _cyclic_order(corner_centers_px)
    _validate_quad([corner_centers_px[i] for i in order])

    best_k, best_score, best_anchored = 0, -1, False
    for k in range(4):
        score = sum(order[i] == ids[(i + k) % 4] for i in range(4))
        # Desempate: preferir la rotación que deja el marcador "a8" en a8.
        anchored = any(order[i] == ids[0] and (i + k) % 4 == 0 for i in range(4))
        if score > best_score or (score == best_score and anchored and not best_anchored):
            best_k, best_score, best_anchored = k, score, anchored

    layout = CornerLayout(
        corner_by_id={order[i]: CORNER_NAMES[(i + best_k) % 4] for i in range(4)},
        matches=best_score,
    )
    is_new = _layout_is_new(layout)          # siempre, para no perder cambios
    if not layout.is_expected and is_new:
        logger.warning(
            "Los marcadores de esquina no siguen el orden esperado "
            "(%d/4 coinciden). Disposición deducida: %s. "
            "Recorriendo el borde del tablero deberían ir %s.",
            layout.matches, layout.describe(),
            " ".join(f"{i}={n}" for i, n in EXPECTED_LAYOUT.items()),
        )
    return layout


class BoardPose:
    """Homografía tablero(mm) <-> imagen(px) y mapeo píxel -> casilla."""

    def __init__(
        self,
        homography_mm_to_px: np.ndarray,
        layout: Optional[CornerLayout] = None,
        corner_centers_px: Optional[Mapping[int, tuple[float, float]]] = None,
    ):
        self._h = np.asarray(homography_mm_to_px, dtype=np.float64)
        self._h_inv = np.linalg.inv(self._h)
        self.layout = layout
        self._corner_centers_px = dict(corner_centers_px) if corner_centers_px else None

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #
    @classmethod
    def from_corner_centers(
        cls,
        corner_centers_px: Mapping[int, tuple[float, float]],
        auto_layout: bool = True,
        quarter_turns: int = 0,
    ) -> "BoardPose":
        """Crea la pose a partir de los centros (px) de los 4 marcadores de esquina.

        Args:
            corner_centers_px: ``{id_aruco: (x_px, y_px)}`` — deben estar los 4
                IDs de ``config.ARUCO_IDS_BOARD_CORNERS``.
            auto_layout: deducir la colocación real de los marcadores
                (:func:`deduce_corner_layout`) en vez de confiar en sus
                etiquetas.  Recomendado: evita la homografía degenerada si dos
                marcadores están intercambiados.
            quarter_turns: giros de 90° a aplicar al mapeo de casillas (útil si
                los marcadores están bien colocados en el borde pero girados
                respecto al tablero impreso: el tablero sale rotado 90/180°).
        """
        missing = set(config.ARUCO_IDS_BOARD_CORNERS) - set(corner_centers_px)
        if missing:
            raise BoardPoseError(
                f"Faltan marcadores de esquina: {sorted(missing)}. "
                f"Se necesitan los 4 IDs {config.ARUCO_IDS_BOARD_CORNERS}."
            )
        centers = {i: corner_centers_px[i] for i in config.ARUCO_IDS_BOARD_CORNERS}
        if auto_layout:
            layout = deduce_corner_layout(centers)
        else:
            layout = CornerLayout(corner_by_id=dict(EXPECTED_LAYOUT), matches=4)
        layout = _rotate_layout(layout, quarter_turns)

        ids = list(centers)
        src_mm = np.array([CORNER_MM_BY_NAME[layout.corner_by_id[i]] for i in ids],
                          dtype=np.float32)
        dst_px = np.array([centers[i] for i in ids], dtype=np.float32)
        h = cv2.getPerspectiveTransform(src_mm, dst_px)
        logger.debug("Homografía calculada: %s", layout.describe())
        return cls(h, layout=layout, corner_centers_px=centers)

    def rotated(self, quarter_turns: int = 1) -> "BoardPose":
        """Nueva pose con el mapeo de casillas girado ``quarter_turns`` × 90°.

        Sirve para corregir en caliente un tablero que sale girado (los 4
        marcadores están bien puestos en el borde, pero el ciclo empieza en otra
        esquina) sin tener que recolocar ni reimprimir nada.
        """
        if self._corner_centers_px is None or self.layout is None:
            raise BoardPoseError(
                "Esta pose no guarda los centros de las esquinas; no se puede girar."
            )
        layout = _rotate_layout(self.layout, quarter_turns)
        ids = list(self._corner_centers_px)
        src_mm = np.array([CORNER_MM_BY_NAME[layout.corner_by_id[i]] for i in ids],
                          dtype=np.float32)
        dst_px = np.array([self._corner_centers_px[i] for i in ids], dtype=np.float32)
        return BoardPose(
            cv2.getPerspectiveTransform(src_mm, dst_px),
            layout=layout,
            corner_centers_px=self._corner_centers_px,
        )

    @property
    def layout_warning(self) -> Optional[str]:
        """Aviso si los marcadores de esquina no están donde su etiqueta dice."""
        return self.layout.warning_text() if self.layout else None

    # ------------------------------------------------------------------ #
    # Transformaciones
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
        vec = h @ np.array([x, y, 1.0])
        return float(vec[0] / vec[2]), float(vec[1] / vec[2])

    def mm_to_pixel(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """Punto del tablero (mm) -> coordenadas de imagen (px)."""
        return self._apply(self._h, x_mm, y_mm)

    def pixel_to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Coordenadas de imagen (px) -> punto del tablero (mm)."""
        return self._apply(self._h_inv, x_px, y_px)

    # ------------------------------------------------------------------ #
    # Casillas
    # ------------------------------------------------------------------ #
    @staticmethod
    def square_center_mm(square: str) -> tuple[float, float]:
        """Centro (mm) de una casilla, p. ej. ``"e4"`` -> ``(144.0, 144.0)``."""
        file_idx = _FILES.index(square[0])
        rank = int(square[1])
        x = (file_idx + 0.5) * config.SQUARE_SIZE_MM
        y = ((config.BOARD_SQUARES - rank) + 0.5) * config.SQUARE_SIZE_MM
        return x, y

    def square_center_px(self, square: str) -> tuple[float, float]:
        """Centro de una casilla en coordenadas de imagen."""
        return self.mm_to_pixel(*self.square_center_mm(square))

    def pixel_to_square(
        self, x_px: float, y_px: float, tolerance_mm: float = 0.0
    ) -> Optional[str]:
        """Casilla en la que cae un píxel, o ``None`` si está fuera del tablero.

        Args:
            tolerance_mm: margen exterior admitido.  Un punto que cae fuera del
                área de juego pero a menos de estos mm se asigna a la casilla
                del borde (absorbe el error de la homografía y las esquinas mal
                centradas); más allá, está fuera del tablero y se descarta —
                p. ej. las piezas capturadas en la zona de descarte.
        """
        x_mm, y_mm = self.pixel_to_mm(x_px, y_px)
        if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
            return None
        size = config.BOARD_SIZE_MM
        if not (-tolerance_mm <= x_mm <= size + tolerance_mm):
            return None
        if not (-tolerance_mm <= y_mm <= size + tolerance_mm):
            return None
        last = config.BOARD_SQUARES - 1
        file_idx = min(last, max(0, int(x_mm // config.SQUARE_SIZE_MM)))
        row = min(last, max(0, int(y_mm // config.SQUARE_SIZE_MM)))
        rank = config.BOARD_SQUARES - row
        return f"{_FILES[file_idx]}{rank}"


def _rotate_layout(layout: CornerLayout, quarter_turns: int) -> CornerLayout:
    """Gira la asignación ID -> esquina ``quarter_turns`` × 90° por el ciclo."""
    turns = quarter_turns % 4
    if not turns:
        return layout
    rotated = {
        i: CORNER_NAMES[(CORNER_NAMES.index(name) + turns) % 4]
        for i, name in layout.corner_by_id.items()
    }
    matches = sum(EXPECTED_LAYOUT.get(i) == name for i, name in rotated.items())
    return CornerLayout(corner_by_id=rotated, matches=matches)
