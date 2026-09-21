"""Tests de los ajustes persistentes de la aplicación."""

import json

import pytest

from magnus.app.settings import ARM_MODES, AppSettings


def test_defaults_are_valid():
    settings = AppSettings()
    settings.validate()
    assert settings.arm_mode == "off"
    assert settings.arm_auto_execute is False


def test_round_trip_file(tmp_path):
    path = tmp_path / "s.json"
    original = AppSettings(difficulty="HARD", robot_side="white", arm_mode="simulated")
    original.save(path)
    loaded = AppSettings.load(path)
    assert loaded == original


def test_missing_file_gives_defaults(tmp_path):
    assert AppSettings.load(tmp_path / "nope.json") == AppSettings()


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert AppSettings.load(path) == AppSettings()


def test_unknown_keys_are_ignored():
    settings = AppSettings.from_dict({"difficulty": "EASY", "future_field": 1})
    assert settings.difficulty == "EASY"


def test_invalid_values_are_rejected():
    with pytest.raises(ValueError):
        AppSettings.from_dict({"robot_side": "green"})
    with pytest.raises(ValueError):
        AppSettings.from_dict({"arm_mode": "warp"})
    with pytest.raises(ValueError):
        AppSettings(board_turns=7).validate()


def test_update_returns_validated_copy():
    base = AppSettings()
    changed = base.update(difficulty="EXPERT", arm_mode=ARM_MODES[1])
    assert base.difficulty == "MEDIUM"
    assert changed.difficulty == "EXPERT"
    assert changed.arm_mode == "simulated"
    assert json.loads(json.dumps(changed.to_dict()))["arm_mode"] == "simulated"


def test_auto_home_is_on_by_default():
    """Sin referenciar, la tabla de posiciones apunta a otro sitio cada vez."""
    assert AppSettings().arm_auto_home is True


def test_old_settings_file_without_auto_home_still_loads(tmp_path):
    """Compatibilidad: un magnus_settings.json anterior no tiene el campo."""
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"difficulty": "EASY", "arm_mode": "simulated"}),
                    encoding="utf-8")
    loaded = AppSettings.load(path)
    assert loaded.difficulty == "EASY" and loaded.arm_auto_home is True
