"""Tests del informe de cobertura de positions.json (sin lanzar)."""

import json

from magnus import config
from magnus.arm.positions_table import (
    ALL_SQUARES,
    REQUIRED_KEYS,
    inspect_positions_file,
)


def _entry(value):
    return {"approach": {"shoulder": value, "elbow": value},
            "engage": {"shoulder": value, "elbow": value}}


def test_required_keys_are_64_squares_plus_zones():
    assert len(REQUIRED_KEYS) == 66
    assert set(ALL_SQUARES) <= set(REQUIRED_KEYS)
    assert config.ZONE_DISCARD in REQUIRED_KEYS and config.ZONE_EXCHANGE in REQUIRED_KEYS


def test_missing_file():
    report = inspect_positions_file("/no/existe/positions.json")
    assert not report.exists and not report.complete
    assert len(report.missing) == 66
    assert report.to_dict()["calibrated"] == 0


def test_template_with_nulls_counts_as_uncalibrated(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({k: _entry(None) for k in REQUIRED_KEYS}), encoding="utf-8")
    report = inspect_positions_file(path)
    assert report.exists and not report.complete
    assert report.calibrated == [] and len(report.missing) == 66


def test_partial_calibration(tmp_path):
    raw = {k: _entry(None) for k in REQUIRED_KEYS}
    for sq in ("a1", "b1", "discard"):
        raw[sq] = _entry(12.5)
    raw["h8"] = {"approach": {"shoulder": 1.0}}       # mal formada
    raw["parking"] = _entry(3.0)                       # clave desconocida
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    report = inspect_positions_file(path)
    assert set(report.calibrated) == {"a1", "b1", "discard"}
    assert report.invalid == ["h8"]
    assert report.unknown == ["parking"]
    assert len(report.missing) == 66 - 3 - 1
    assert not report.complete


def test_complete_table(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({k: _entry(10) for k in REQUIRED_KEYS}), encoding="utf-8")
    report = inspect_positions_file(path)
    assert report.complete
    assert report.to_dict()["calibrated"] == 66


def test_broken_json(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text("{", encoding="utf-8")
    report = inspect_positions_file(path)
    assert report.exists and report.error and not report.complete
