"""Tests del pipeline de visión completo, con imágenes sintéticas.

Se renderiza una vista cenital del tablero con marcadores ArUco reales
(generados con cv2.aruco) y se inyecta en el nodo mediante
``FakeCameraBackend`` — sin cámara ni tablero físico.
"""

import chess
import numpy as np
import pytest

from magnus import config
from magnus.core.messages import STARTING_FEN
from magnus.vision.game_state import GameTracker, board_placement
from magnus.vision.synthetic import SyntheticBoardError, render_board_image
from magnus.vision.vision_node import (
    BoardNotFoundError,
    BoardVisionNode,
    CameraBackend,
    FakeCameraBackend,
)

# Marcadores de esquina puestos en "orden de lectura" (40 y 41 arriba, 42 y 43
# abajo) en vez de recorriendo el borde: el error de montaje más natural.
READING_ORDER_CORNERS = {40: "a8", 41: "h8", 42: "a1", 43: "h1"}


def _node_for(placement: dict[str, str], **node_kwargs) -> BoardVisionNode:
    frame = render_board_image(placement)
    return BoardVisionNode(camera=FakeCameraBackend([frame]), **node_kwargs)


class SwitchableCamera(CameraBackend):
    """Cámara falsa cuyo frame se puede cambiar entre escaneos."""

    def __init__(self, frame: np.ndarray):
        self.frame = frame

    def open(self) -> None:
        pass

    def read(self) -> np.ndarray:
        return self.frame

    def close(self) -> None:
        pass


def test_detects_sparse_position():
    placement = {"e1": "K", "e8": "k", "d4": "Q", "a7": "p"}
    with _node_for(placement) as node:
        assert node.get_board_placement() == placement


def test_detects_full_starting_position():
    """Las 32 piezas de la posición inicial deben detectarse en su casilla.

    Regresión clave del mapeo por tipo: los 8 peones blancos comparten el
    ID 0 (y así con cada tipo) — TODAS las instancias deben detectarse a la
    vez, no solo una por ID.
    """
    placement = board_placement(chess.Board())
    with _node_for(placement) as node:
        assert node.get_board_placement() == placement


def test_detects_many_instances_of_same_marker_id():
    """8 peones blancos = 8 marcadores con el MISMO ID, en casillas distintas."""
    placement = {f"{f}2": "P" for f in "abcdefgh"}
    with _node_for(placement) as node:
        assert node.get_board_placement() == placement


def test_detects_promoted_second_queen():
    """Una promoción pone en juego una segunda dama con el mismo ID (8)."""
    placement = {"e1": "K", "e8": "k", "d4": "Q", "a8": "Q"}
    with _node_for(placement) as node:
        assert node.get_board_placement() == placement


def test_get_board_fen_starting_position():
    placement = board_placement(chess.Board())
    with _node_for(placement) as node:
        assert node.get_board_fen() == STARTING_FEN


def test_full_move_cycle_with_tracker():
    """Humano juega e2e4 (visto por cámara) y el robot responde e7e5 (via engine)."""
    board = chess.Board()
    board.push_uci("e2e4")
    tracker = GameTracker()

    with _node_for(board_placement(board), tracker=tracker) as node:
        fen = node.get_board_fen()
        assert fen == board.fen()          # turno y al paso exactos
        node.notify_robot_move("e7e5")     # jugada del robot (del engine)
        board.push_uci("e7e5")
        assert tracker.fen() == board.fen()


# --------------------------------------------------------------------------- #
# Montaje real: cámara girada, esquinas cruzadas, esquinas tapadas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("quarter_turns", [0, 1, 2, 3])
def test_camera_orientation_does_not_matter(quarter_turns):
    """Cámara apaisada o desde el lado de las negras: la FEN sale igual."""
    placement = board_placement(chess.Board())
    frame = np.ascontiguousarray(np.rot90(render_board_image(placement), quarter_turns))
    with BoardVisionNode(camera=FakeCameraBackend([frame])) as node:
        assert node.get_board_placement() == placement


def test_corners_in_reading_order_still_produce_the_fen():
    """Regresión del fallo real: con las esquinas cruzadas no se detectaba NADA.

    La homografía salía degenerada y las 32 piezas caían "fuera del tablero":
    tablero digital vacío y sin FEN, pese a detectarse los 36 marcadores.
    """
    placement = board_placement(chess.Board())
    frame = render_board_image(placement, corner_layout=READING_ORDER_CORNERS)
    with BoardVisionNode(camera=FakeCameraBackend([frame])) as node:
        assert node.get_board_fen() == STARTING_FEN
        assert node.board_pose.layout_warning is not None   # avisa del cruce


def test_pose_is_remembered_when_a_rook_covers_a_corner():
    """Perder un marcador de esquina no debe tumbar el tablero."""
    placement = board_placement(chess.Board())
    camera = SwitchableCamera(render_board_image(placement))
    with BoardVisionNode(camera=camera) as node:
        assert node.get_board_placement() == placement      # memoriza la pose
        camera.frame = render_board_image(placement, hidden_corners=[43])
        assert node.get_board_placement() == placement      # sigue funcionando
        node.reset_board_pose()
        with pytest.raises(BoardNotFoundError):             # ya no la recuerda
            node.get_board_placement()


def test_markers_outside_the_board_are_ignored():
    """Piezas capturadas o marcadores sueltos en la mesa no entran en el placement."""
    placement = {"e1": "K", "e8": "k", "d4": "Q"}
    size = config.BOARD_SIZE_MM
    frame = render_board_image(
        placement,
        extra_markers=[(12, (-18.0, 128.0)), (8, (size + 18.0, 60.0))],
    )
    with BoardVisionNode(camera=FakeCameraBackend([frame])) as node:
        assert node.get_board_placement() == placement


def test_missing_corners_raises():
    # Imagen totalmente blanca: sin esquinas ni piezas.
    import numpy as np

    blank = np.full((400, 400, 3), 255, dtype=np.uint8)
    with BoardVisionNode(camera=FakeCameraBackend([blank])) as node:
        with pytest.raises(BoardNotFoundError):
            node.get_board_placement()


# --------------------------------------------------------------------------- #
# Backend de cámara real: arranque lento y frames perdidos
# --------------------------------------------------------------------------- #
class FakeVideoCapture:
    """Stub de ``cv2.VideoCapture``: falla las primeras ``blank_reads`` lecturas.

    Reproduce el comportamiento de las cámaras virtuales (Iriun, EpocCam, OBS):
    el dispositivo abre antes de estar transmitiendo, así que las primeras
    lecturas devuelven ``(False, None)``.
    """

    opened_indices: list[int] = []

    def __init__(self, index, blank_reads=0, opens=True, blank_forever=False):
        self.index = index
        self.blank_reads = blank_reads
        self.blank_forever = blank_forever
        self._opened = opens
        self.released = False
        FakeVideoCapture.opened_indices.append(index)

    def isOpened(self):                                     # noqa: N802 (API de cv2)
        return self._opened

    def read(self):
        if self.blank_forever or self.blank_reads > 0:
            self.blank_reads -= 1
            return False, None
        return True, np.zeros((48, 64, 3), dtype=np.uint8)

    def release(self):
        self.released = True
        self._opened = False


@pytest.fixture
def capture_factory(monkeypatch):
    """Sustituye ``cv2.VideoCapture`` y devuelve las instancias creadas."""
    import cv2

    created: list[FakeVideoCapture] = []
    FakeVideoCapture.opened_indices = []

    def install(**kwargs):
        def factory(index, *_args, **_kwargs):
            cap = FakeVideoCapture(index, **kwargs)
            created.append(cap)
            return cap

        monkeypatch.setattr(cv2, "VideoCapture", factory)
        return created

    return install


def test_open_waits_for_the_first_frame(capture_factory):
    """Una cámara virtual tarda en transmitir: abrir no es estar dando imagen."""
    from magnus.vision.vision_node import OpenCVCameraBackend

    capture_factory(blank_reads=8)
    camera = OpenCVCameraBackend(0, warmup_s=5.0, retry_delay_s=0.0)
    camera.open()                                   # no debe lanzar
    assert camera.read().shape == (48, 64, 3)
    camera.close()


def test_open_explains_why_there_is_no_image(capture_factory):
    """Si nunca llega imagen, el error apunta a permisos / índice / app virtual."""
    from magnus.vision.vision_node import CameraError, OpenCVCameraBackend

    created = capture_factory(blank_forever=True)
    camera = OpenCVCameraBackend(0, warmup_s=0.05, retry_delay_s=0.0)
    with pytest.raises(CameraError) as exc:
        camera.open()
    assert "Privacidad" in str(exc.value) and "--list-cameras" in str(exc.value)
    assert created[0].released                      # no deja la cámara ocupada


def test_read_survives_a_dropped_frame(capture_factory):
    """Un frame suelto perdido (wifi) no debe tumbar la sesión."""
    from magnus.vision.vision_node import OpenCVCameraBackend

    capture_factory()
    camera = OpenCVCameraBackend(0, warmup_s=1.0, read_retries=5, retry_delay_s=0.0)
    camera.open()
    camera._cap.blank_reads = 3                     # se pierden 3 frames seguidos
    assert camera.read().shape == (48, 64, 3)


def test_read_gives_up_after_the_retries(capture_factory):
    from magnus.vision.vision_node import CameraError, OpenCVCameraBackend

    capture_factory()
    camera = OpenCVCameraBackend(0, warmup_s=1.0, read_retries=3, retry_delay_s=0.0)
    camera.open()
    camera._cap.blank_forever = True                # la cámara se desconecta
    with pytest.raises(CameraError):
        camera.read()


def test_probe_cameras_lists_only_the_ones_with_image(monkeypatch):
    """Con cámara integrada + Iriun, el índice bueno no tiene por qué ser el 0."""
    import cv2

    from magnus.vision.vision_node import probe_cameras

    def factory(index, *_args, **_kwargs):
        # Solo el índice 1 entrega imagen.
        return FakeVideoCapture(index, opens=index <= 1, blank_forever=index != 1)

    monkeypatch.setattr(cv2, "VideoCapture", factory)
    assert probe_cameras(max_index=3, warmup_s=0.05) == [(1, (64, 48))]


def test_invalid_piece_symbol_rejected():
    """El renderizador sintético rechaza símbolos que no son piezas FEN."""
    with pytest.raises(SyntheticBoardError):
        render_board_image({"e1": "X"})
