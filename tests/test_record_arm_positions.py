"""Calibración manual sin conectar motores ni escribir mediciones reales."""

import json
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
    raw = output.split("Mediciones para copiar (pueden estar incompletas):\n", 1)[1]
    captured, _ = json.JSONDecoder().raw_decode(raw)
    table = PositionsTable.from_dict(captured)
    assert table.get("e4").position.shoulder == 9999.0
    assert captured["e4"] == {"shoulder": 9999.0, "elbow": -9999.0}
