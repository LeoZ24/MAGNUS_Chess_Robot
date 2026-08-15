"""Tests unitarios del enclavamiento multi-instancia (DetectionLatch).

No usan cámara ni imágenes: se construyen ``Detection`` sintéticas directamente
para probar la lógica de pistas (tracks) con IDs repetidos — el caso real del
mapeo por tipo, donde los 8 peones blancos comparten el ID 0.
"""

import numpy as np

from magnus.vision.aruco_detector import (
    Detection,
    DetectionLatch,
    MarkerRole,
    corners_by_id,
    split_by_role,
)


def det(aruco_id: int, x: float, y: float, side: float = 40.0) -> Detection:
    """Crea una Detection cuadrada centrada en (x, y) con lado ``side`` px."""
    h = side / 2.0
    corners = np.array(
        [[x - h, y - h], [x + h, y - h], [x + h, y + h], [x - h, y + h]],
        dtype=np.float64,
    )
    return Detection(aruco_id=aruco_id, center_px=(x, y), corners_px=corners)


def centers(detections: list[Detection]) -> set[tuple[float, float]]:
    return {d.center_px for d in detections}


# --------------------------------------------------------------------------- #
# Multi-instancia: varios marcadores con el MISMO ID a la vez
# --------------------------------------------------------------------------- #
def test_confirms_multiple_instances_of_same_id():
    """Dos peones blancos (ID 0) en casillas distintas: ambos confirmados."""
    latch = DetectionLatch(confirm_n=3)
    frame = [det(0, 100, 100), det(0, 400, 100)]
    for _ in range(3):
        confirmed = latch.update(frame)
    assert len(confirmed) == 2
    assert centers(confirmed) == {(100.0, 100.0), (400.0, 100.0)}
    assert all(d.aruco_id == 0 for d in confirmed)


def test_eight_pawns_same_id_all_tracked():
    """El caso extremo real: 8 instancias del ID 0 en una fila."""
    latch = DetectionLatch(confirm_n=2)
    frame = [det(0, 80 + 100 * i, 200) for i in range(8)]
    latch.update(frame)
    confirmed = latch.update(frame)
    assert len(confirmed) == 8


def test_jitter_within_radius_keeps_single_track():
    """Pequeños movimientos de cámara no duplican la pista."""
    latch = DetectionLatch(confirm_n=2)
    latch.update([det(0, 100, 100)])
    confirmed = latch.update([det(0, 104, 97)])     # jitter de pocos px
    assert len(confirmed) == 1
    assert confirmed[0].center_px == (104.0, 97.0)  # posición micro-ajustada


def test_same_id_far_apart_are_separate_tracks():
    """Dos detecciones del mismo ID más allá del radio no se fusionan."""
    latch = DetectionLatch(confirm_n=1, match_radius_factor=1.5)
    # lado 40 px, factor 1.5 -> radio 60 px; separación 200 px.
    confirmed = latch.update([det(0, 100, 100), det(0, 300, 100)])
    assert len(confirmed) == 2


# --------------------------------------------------------------------------- #
# Racha de confirmación (comportamiento heredado del prototipo)
# --------------------------------------------------------------------------- #
def test_pending_streak_breaks_on_missed_frame():
    """Un marcador pendiente que desaparece un frame pierde su racha."""
    latch = DetectionLatch(confirm_n=3)
    latch.update([det(0, 100, 100)])
    latch.update([det(0, 100, 100)])
    latch.update([])                                # racha rota
    latch.update([det(0, 100, 100)])
    confirmed = latch.update([det(0, 100, 100)])
    assert confirmed == []                          # 2 frames, aún no llega a 3
    confirmed = latch.update([det(0, 100, 100)])
    assert len(confirmed) == 1


def test_not_confirmed_before_n_frames():
    latch = DetectionLatch(confirm_n=5)
    for _ in range(4):
        confirmed = latch.update([det(0, 100, 100)])
    assert confirmed == []
    assert len(latch.pending) == 1


# --------------------------------------------------------------------------- #
# Olvido de marcadores confirmados (pieza movida o capturada)
# --------------------------------------------------------------------------- #
def test_confirmed_survives_brief_occlusion():
    """Una mano que tapa la pieza un instante no borra la confirmación."""
    latch = DetectionLatch(confirm_n=2, forget_after=10)
    latch.update([det(0, 100, 100)])
    latch.update([det(0, 100, 100)])
    for _ in range(9):                              # 9 < forget_after
        confirmed = latch.update([])
    assert len(confirmed) == 1


def test_confirmed_forgotten_after_forget_frames():
    """Una pieza retirada desaparece de la memoria tras forget_after frames."""
    latch = DetectionLatch(confirm_n=2, forget_after=5)
    latch.update([det(0, 100, 100)])
    latch.update([det(0, 100, 100)])
    for _ in range(5):
        confirmed = latch.update([])
    assert confirmed == []


def test_forget_after_zero_never_forgets():
    latch = DetectionLatch(confirm_n=1, forget_after=0)
    latch.update([det(0, 100, 100)])
    for _ in range(50):
        confirmed = latch.update([])
    assert len(confirmed) == 1


def test_moved_piece_creates_new_track_and_old_expires():
    """Mover un peón e2->e4: pista nueva en e4, la de e2 caduca sola."""
    latch = DetectionLatch(confirm_n=2, forget_after=3)
    for _ in range(2):
        latch.update([det(0, 100, 100)])            # "e2" confirmada
    # La pieza aparece lejos (otra casilla): pista nueva del mismo ID.
    for _ in range(2):
        confirmed = latch.update([det(0, 500, 300)])
    assert centers(confirmed) == {(100.0, 100.0), (500.0, 300.0)}
    # La vieja caduca tras forget_after frames sin verse.
    confirmed = latch.update([det(0, 500, 300)])
    assert centers(confirmed) == {(500.0, 300.0)}


def test_reset_clears_memory():
    latch = DetectionLatch(confirm_n=1)
    latch.update([det(0, 100, 100)])
    latch.reset()
    assert latch.confirmed == []
    assert latch.pending == []


# --------------------------------------------------------------------------- #
# Esquinas: estáticas, únicas y no se olvidan
# --------------------------------------------------------------------------- #
def test_corner_is_never_forgotten_when_covered():
    """Una torre puede tapar la esquina: se recuerda su última posición.

    Ni la cámara ni el tablero se mueven durante la partida, así que perder el
    marcador de esquina no debe tumbar la homografía.
    """
    latch = DetectionLatch(confirm_n=2, forget_after=5)
    for _ in range(2):
        latch.update([det(40, 10, 10)])
    for _ in range(200):                            # muchísimos frames tapada
        confirmed = latch.update([])
    assert centers(confirmed) == {(10.0, 10.0)}


def test_piece_is_forgotten_but_corner_is_remembered():
    latch = DetectionLatch(confirm_n=2, forget_after=3)
    frame = [det(0, 100, 100), det(40, 10, 10)]     # una pieza y una esquina
    for _ in range(2):
        latch.update(frame)
    for _ in range(3):
        confirmed = latch.update([])
    assert [d.aruco_id for d in confirmed] == [40]


def test_occluded_lists_only_markers_not_seen_now():
    latch = DetectionLatch(confirm_n=1)
    latch.update([det(0, 100, 100), det(40, 10, 10)])
    assert latch.occluded == []
    latch.update([det(0, 100, 100)])                # la esquina se tapa
    assert [d.aruco_id for d in latch.occluded] == [40]


def test_duplicate_corner_id_far_away_is_ignored():
    """Un marcador de esquina suelto en la mesa no puede robarle el sitio al bueno."""
    latch = DetectionLatch(confirm_n=1)
    latch.update([det(40, 10, 10)])                             # esquina confirmada
    confirmed = latch.update([det(40, 10, 10), det(40, 900, 700)])
    assert centers(confirmed) == {(10.0, 10.0)}


def test_duplicate_piece_id_far_away_still_creates_a_track():
    """La regla anterior es SOLO para IDs únicos: las piezas comparten ID."""
    latch = DetectionLatch(confirm_n=1)
    latch.update([det(0, 100, 100)])
    confirmed = latch.update([det(0, 100, 100), det(0, 900, 700)])
    assert len(confirmed) == 2


def test_forget_after_by_role_is_configurable():
    latch = DetectionLatch(
        confirm_n=1, forget_after=0, forget_after_by_role={MarkerRole.CORNER: 2}
    )
    latch.update([det(40, 10, 10)])
    latch.update([])
    assert len(latch.confirmed) == 1
    assert latch.update([]) == []                   # olvidada al 2º frame


# --------------------------------------------------------------------------- #
# Utilidades por rol
# --------------------------------------------------------------------------- #
def test_split_by_role_keeps_duplicated_ids():
    dets = [det(0, 100, 100), det(0, 200, 100), det(40, 10, 10), det(44, 300, 300)]
    by_role = split_by_role(dets)
    assert len(by_role[MarkerRole.PIECE]) == 2
    assert len(by_role[MarkerRole.CORNER]) == 1
    assert len(by_role[MarkerRole.ARM]) == 1


def test_corners_by_id_ignores_duplicates_and_non_corners():
    dets = [det(40, 10, 10), det(40, 500, 500), det(41, 600, 10), det(0, 300, 300)]
    corners = corners_by_id(dets)
    assert set(corners) == {40, 41}
    assert corners[40].center_px == (10.0, 10.0)    # la primera instancia gana


def test_stats_counts_by_role():
    latch = DetectionLatch(confirm_n=1)
    latch.update([det(0, 100, 100), det(0, 300, 100), det(40, 10, 10)])
    stats = latch.stats()
    assert stats[MarkerRole.PIECE] == 2
    assert stats[MarkerRole.CORNER] == 1
    assert stats[MarkerRole.ARM] == 0
