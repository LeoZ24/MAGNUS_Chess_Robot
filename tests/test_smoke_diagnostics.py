"""Diagnostico de llegada y retencion sin ejecutar la prueba fisica."""

from types import SimpleNamespace

import pytest

import test_humo as smoke
from magnus.arm.backend import ArmBackendError


class FakeDiagnosticArm:
    """Proporciona lecturas programadas y registra los movimientos solicitados."""

    def __init__(self, positions, error=None):
        self.positions = iter(positions)
        self.error = error
        self.moves = []

    def move_to(self, shoulder, elbow):
        self.moves.append((shoulder, elbow))
        if self.error:
            raise self.error

    def get_position(self):
        return next(self.positions)


@pytest.fixture(autouse=True)
def no_hardware_waits(monkeypatch):
    monkeypatch.setattr(smoke, "paso", lambda text: None)
    monkeypatch.setattr(smoke, "time", SimpleNamespace(sleep=lambda seconds: None))


def test_arrives_but_sags_at_rest_is_not_ok(capsys):
    arm = FakeDiagnosticArm([(30, 0), (24, 0), (0, 0)])
    assert not smoke.probar_eje(arm, "hombro", 0, 30)
    assert "deriva -6.00" in capsys.readouterr().out


def test_unmoved_shoulder_drift_during_elbow_test_is_detected():
    arm = FakeDiagnosticArm([(5, -30), (5, -30), (0, 0)])
    assert not smoke.probar_eje(arm, "codo", 1, -30)


def test_bad_return_to_zero_is_not_ok():
    arm = FakeDiagnosticArm([(30, 0), (30, 0), (5, 0)])
    assert not smoke.probar_eje(arm, "hombro", 0, 30)


def test_stable_reachable_position_passes():
    arm = FakeDiagnosticArm([(30, 0), (30, 0), (0, 0)])
    assert smoke.probar_eje(arm, "hombro", 0, 30)


def test_board_motion_error_prints_diagnostics_then_aborts(capsys):
    error = ArmBackendError("La CyberPi rechazo MOVE: ERR hombro no alcanzo 30")
    arm = FakeDiagnosticArm([(4, 0)], error)
    with pytest.raises(ArmBackendError):
        smoke.probar_eje(arm, "hombro", 0, 30)
    assert "apenas se movio" in capsys.readouterr().out
    assert arm.moves == [(30, 0)]


def test_timeout_aborts_without_reading_or_sending_another_command():
    arm = FakeDiagnosticArm([], ArmBackendError("Timeout esperando respuesta"))
    with pytest.raises(ArmBackendError, match="Timeout"):
        smoke.probar_eje(arm, "hombro", 0, 30)
    assert arm.moves == [(30, 0)]
