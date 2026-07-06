"""Tests de la tabla de posiciones pregrabadas (magnus/arm/positions_table.py).

Todos los valores usados aquí son deliberadamente falsos (9999.0) — la tabla
real se generará calibrando el brazo físico.
"""

import json

import pytest

from magnus import config
from magnus.arm.positions_table import (
    ALL_SQUARES,
    FAKE_VALUE,
    JointAngles,
    PositionsTable,
    PositionsTableError,
    UnknownPositionError,
    make_fake_table,
)


def _fake_entry(i: float = 0.0) -> dict:
    return {
        "approach": {"shoulder": FAKE_VALUE + i, "elbow": -FAKE_VALUE - i},
        "engage": {"shoulder": FAKE_VALUE + i + 0.5, "elbow": -FAKE_VALUE - i - 0.5},
    }


def test_all_squares_has_64():
    assert len(ALL_SQUARES) == 64
    assert "a1" in ALL_SQUARES and "h8" in ALL_SQUARES


def test_fake_table_complete():
    table = make_fake_table()
    for sq in ALL_SQUARES:
        pos = table.get(sq)
        assert isinstance(pos.approach, JointAngles)
        # Los valores falsos deben ser inconfundibles con datos reales.
        assert abs(pos.approach.shoulder) >= FAKE_VALUE
    assert table.has(config.ZONE_DISCARD)
    assert table.has(config.ZONE_EXCHANGE)


def test_load_from_json(tmp_path):
    raw = {sq: _fake_entry(i) for i, sq in enumerate(ALL_SQUARES)}
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    table = PositionsTable.load(path)
    assert table.get("a1").approach.shoulder == FAKE_VALUE + 0
    assert not table.has(config.ZONE_DISCARD)  # zonas no incluidas en este JSON


def test_missing_square_raises(tmp_path):
    raw = {sq: _fake_entry() for sq in ALL_SQUARES if sq != "e4"}
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PositionsTableError, match="Faltan"):
        PositionsTable.load(path)


def test_malformed_entry_raises(tmp_path):
    raw = {sq: _fake_entry() for sq in ALL_SQUARES}
    raw["e4"] = {"approach": {"shoulder": FAKE_VALUE}}  # falta elbow y engage
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PositionsTableError, match="e4"):
        PositionsTable.load(path)


def test_nonexistent_file_raises(tmp_path):
    with pytest.raises(PositionsTableError, match="No existe"):
        PositionsTable.load(tmp_path / "no_such_file.json")


def test_unknown_position_raises():
    table = make_fake_table(include_zones=False)
    with pytest.raises(UnknownPositionError):
        table.get(config.ZONE_DISCARD)
