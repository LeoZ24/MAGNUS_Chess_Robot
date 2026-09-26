"""Calibración manual sin conectar motores ni escribir mediciones reales."""

import json

import pytest
from unittest.mock import patch

from examples import record_arm_positions as recorder
from magnus.arm.backend import FakeArmBackend
from magnus.arm.positions_table import RECORDABLE_KEYS, REQUIRED_KEYS, PositionsTable


class FakeRecorderBackend(FakeArmBackend):
    """Encoders falsos para comprobar el formato que consume el reproductor."""

    def stop(self) -> None:
        self.commands.append(("stop",))

    def get_limits(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return ((0.0, 10000.0), (-10000.0, 0.0))

    def get_position(self) -> tuple[float, float]:
        self.commands.append(("get",))
        return (9999.0, -9999.0)


def test_one_reading_per_square_can_be_loaded(capsys):
    backend = FakeRecorderBackend()
    with patch.object(recorder, "CyberPiBackend", return_value=backend), \
         patch.object(recorder.sys, "argv", ["record_arm_positions.py", "--all"]), \
         patch("builtins.input", return_value="") as prompt:
        assert recorder.main() == 0
    # --all cubre tambien la zona de reposo, que es opcional pero grabable.
    assert prompt.call_count == 2 + len(RECORDABLE_KEYS)
    assert backend.commands.count(("get",)) == len(RECORDABLE_KEYS)
    assert backend.commands[:3] == [("connect",), ("home",), ("stop",)]
    assert backend.commands[-2:] == [("stop",), ("disconnect",)]
    assert not any(cmd[0] in ("move_to", "gripper") for cmd in backend.commands)
    output = capsys.readouterr().out
    raw = output.split("Mediciones (pueden estar incompletas):\n", 1)[1]
    captured, _ = json.JSONDecoder().raw_decode(raw)
    table = PositionsTable.from_dict(captured)
    assert table.get("e4").position.shoulder == 9999.0
    assert captured["e4"] == {"shoulder": 9999.0, "elbow": -9999.0}


# --------------------------------------------------------------------------- #
# Escritura de la tabla (--write)
# --------------------------------------------------------------------------- #

def test_write_merges_and_never_wipes_the_rest(tmp_path):
    """Grabar dos casillas no puede borrar las otras sesenta y cuatro."""
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({
        "a1": {"shoulder": 1.0, "elbow": -1.0},
        "e4": {"shoulder": 2.0, "elbow": -2.0},
    }), encoding="utf-8")

    recorder._write_positions(path, {"e4": {"shoulder": 9.0, "elbow": -9.0},
                                     "park": {"shoulder": 25.0, "elbow": -171.0}})

    resultado = json.loads(path.read_text(encoding="utf-8"))
    assert resultado["a1"] == {"shoulder": 1.0, "elbow": -1.0}   # intacta
    assert resultado["e4"] == {"shoulder": 9.0, "elbow": -9.0}   # actualizada
    assert resultado["park"] == {"shoulder": 25.0, "elbow": -171.0}


def test_write_leaves_a_backup_before_touching_anything(tmp_path):
    """Recalibrar una tabla entera son horas de brazo; aquí se pisa en un segundo."""
    path = tmp_path / "positions.json"
    original = json.dumps({"a1": {"shoulder": 1.0, "elbow": -1.0}})
    path.write_text(original, encoding="utf-8")

    backup = recorder._write_positions(path, {"a1": {"shoulder": 7.0, "elbow": -7.0}})

    assert backup.read_text(encoding="utf-8") == original
    assert json.loads(path.read_text(encoding="utf-8"))["a1"]["shoulder"] == 7.0


def test_write_survives_a_broken_table(tmp_path):
    """Un JSON roto no puede impedir guardar lo que se acaba de medir."""
    path = tmp_path / "positions.json"
    path.write_text("{ esto no es json", encoding="utf-8")

    backup = recorder._write_positions(path, {"park": {"shoulder": 1.0, "elbow": -1.0}})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "park": {"shoulder": 1.0, "elbow": -1.0}}
    assert backup.read_text(encoding="utf-8") == "{ esto no es json"


def test_write_creates_the_table_if_there_is_none(tmp_path):
    path = tmp_path / "positions.json"
    recorder._write_positions(path, {"park": {"shoulder": 1.0, "elbow": -1.0}})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "park": {"shoulder": 1.0, "elbow": -1.0}}


# --------------------------------------------------------------------------- #
# Modo --jog: ajustes, prueba de recogida y verificación
# --------------------------------------------------------------------------- #

LIMITS = ((0.0, 10000.0), (-10000.0, 0.0))


@pytest.mark.parametrize("orden, esperado", [
    ("h+5", (9005.0, -9000.0)),
    ("c-2.5", (9000.0, -9002.5)),
    ("h=100", (100.0, -9000.0)),
    ("c=-120", (9000.0, -120.0)),
    ("h+2 c-1", (9002.0, -9001.0)),
    ("h+2, c=-5", (9002.0, -5.0)),
])
def test_jog_line_accepts_steps_absolutes_and_several_axes(orden, esperado):
    assert recorder._parse_jog(orden, 9000.0, -9000.0) == esperado


@pytest.mark.parametrize("orden", ["x+5", "h5", "h+", "h+dos", "h+2 zz", "h=nan"])
def test_jog_line_that_is_not_understood_changes_nothing(orden):
    with pytest.raises(recorder.JogInputError):
        recorder._parse_jog(orden, 9000.0, -9000.0)


def _jog(backend, respuestas, seeds=None):
    captured = {}
    seeds = {"e4": (9000.0, -9000.0)} if seeds is None else seeds
    with patch("builtins.input", side_effect=respuestas):
        recorder._jog_capture(backend, ["e4"], LIMITS, seeds, captured)
    return captured


def test_jog_records_the_order_after_the_adjustments():
    backend = FakeRecorderBackend()
    backend.connect()
    captured = _jog(backend, ["h+2 c-1", ""])
    assert captured == {"e4": {"shoulder": 9002.0, "elbow": -9001.0}}
    # El imán empieza arriba, como cuando el brazo llega a una casilla jugando.
    assert backend.commands[1] == ("gripper", False)
    assert ("move_to", 9002.0, -9001.0) in backend.commands


def test_jog_try_pick_grabs_and_releases_without_changing_the_order():
    backend = FakeRecorderBackend()
    backend.connect()
    captured = _jog(backend, ["p", "", ""])
    grips = [c for c in backend.commands if c[0] == "gripper"]
    assert grips == [("gripper", False), ("gripper", True), ("gripper", False)]
    assert captured == {"e4": {"shoulder": 9000.0, "elbow": -9000.0}}


def test_jog_verify_comes_back_from_both_sides_like_in_a_game():
    backend = FakeRecorderBackend()
    backend.connect()
    captured = _jog(backend, ["v", "", "", ""])
    away = recorder.VERIFY_AWAY_DEG
    moves = [c for c in backend.commands if c[0] == "move_to"]
    assert moves == [
        ("move_to", 9000.0, -9000.0),                      # llega a la orden
        ("move_to", 9000.0 - away, -9000.0 - away),        # se aleja por un lado
        ("move_to", 9000.0, -9000.0),                      # vuelve
        ("move_to", 9000.0 + away, -9000.0 + away),        # por el otro
        ("move_to", 9000.0, -9000.0),                      # vuelve
        ("move_to", 9000.0, -9000.0),                      # sigue el ajuste
    ]
    assert captured == {"e4": {"shoulder": 9000.0, "elbow": -9000.0}}


def test_jog_verify_never_leaves_the_limits():
    assert recorder._away(5.0, -15.0, (0.0, 300.0)) == 0.0
    assert recorder._away(-5.0, 15.0, (-300.0, 0.0)) == 0.0
