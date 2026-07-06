"""Tests del nodo del brazo (magnus/arm/arm_node.py).

Usa ``FakeArmBackend`` (registra comandos, no mueve nada) y la tabla falsa de
posiciones (valores 9999.0).  Se verifica la SECUENCIA de primitivas planeada
para cada tipo de jugada — que es exactamente lo que el brazo reproducirá.
"""

import pytest

from magnus import config
from magnus.arm import ArmNode, FakeArmBackend, IncompleteMoveError, make_fake_table
from magnus.core.messages import MoveResponse


@pytest.fixture
def arm():
    return ArmNode(backend=FakeArmBackend(), table=make_fake_table())


def _steps_as_strings(steps) -> list[str]:
    return [str(s) for s in steps]


def _pick_place(src: str, dst: str) -> list[str]:
    """Secuencia esperada de un pick & place simple."""
    return [
        f"approach({src})", f"engage({src})", "grip_on", f"approach({src})",
        f"approach({dst})", f"engage({dst})", "grip_off", f"approach({dst})",
    ]


def test_simple_move(arm):
    resp = MoveResponse(uci="e2e4", from_square="e2", to_square="e4")
    assert _steps_as_strings(arm.plan(resp)) == _pick_place("e2", "e4")


def test_capture_removes_piece_first(arm):
    resp = MoveResponse(
        uci="e4d5", from_square="e4", to_square="d5",
        is_capture=True, captured_square="d5",
    )
    expected = _pick_place("d5", config.ZONE_DISCARD) + _pick_place("e4", "d5")
    assert _steps_as_strings(arm.plan(resp)) == expected


def test_en_passant_uses_captured_square(arm):
    """En al paso el peón capturado NO está en to_square: está en captured_square."""
    resp = MoveResponse(
        uci="e5d6", from_square="e5", to_square="d6",
        is_capture=True, is_en_passant=True, captured_square="d5",
    )
    expected = _pick_place("d5", config.ZONE_DISCARD) + _pick_place("e5", "d6")
    assert _steps_as_strings(arm.plan(resp)) == expected


def test_kingside_castle_moves_king_then_rook(arm):
    resp = MoveResponse(
        uci="e1g1", from_square="e1", to_square="g1",
        is_castling=True, is_kingside_castle=True, rook_from="h1", rook_to="f1",
    )
    expected = _pick_place("e1", "g1") + _pick_place("h1", "f1")
    assert _steps_as_strings(arm.plan(resp)) == expected


def test_queenside_castle(arm):
    resp = MoveResponse(
        uci="e8c8", from_square="e8", to_square="c8",
        is_castling=True, rook_from="a8", rook_to="d8",
    )
    expected = _pick_place("e8", "c8") + _pick_place("a8", "d8")
    assert _steps_as_strings(arm.plan(resp)) == expected


def test_promotion_swaps_piece(arm):
    resp = MoveResponse(
        uci="e7e8q", from_square="e7", to_square="e8", promotion="q",
    )
    expected = (
        _pick_place("e7", config.ZONE_DISCARD)
        + _pick_place(config.ZONE_EXCHANGE, "e8")
    )
    assert _steps_as_strings(arm.plan(resp)) == expected


def test_promotion_with_capture(arm):
    resp = MoveResponse(
        uci="e7d8q", from_square="e7", to_square="d8", promotion="q",
        is_capture=True, captured_square="d8",
    )
    expected = (
        _pick_place("d8", config.ZONE_DISCARD)
        + _pick_place("e7", config.ZONE_DISCARD)
        + _pick_place(config.ZONE_EXCHANGE, "d8")
    )
    assert _steps_as_strings(arm.plan(resp)) == expected


def test_incomplete_response_raises(arm):
    with pytest.raises(IncompleteMoveError):
        arm.plan(MoveResponse(uci="e2e4"))  # sin from/to
    with pytest.raises(IncompleteMoveError):
        arm.plan(MoveResponse(uci="e4d5", from_square="e4", to_square="d5", is_capture=True))


def test_execute_sends_commands_to_backend():
    backend = FakeArmBackend()
    table = make_fake_table()
    with ArmNode(backend=backend, table=table) as arm:
        resp = MoveResponse(uci="e2e4", from_square="e2", to_square="e4")
        arm.execute(resp)

    # connect + 8 primitivas (6 move_to + 2 garra) + disconnect
    assert backend.commands[0] == ("connect",)
    assert backend.commands[-1] == ("disconnect",)
    inner = backend.commands[1:-1]
    assert len(inner) == 8
    assert inner[2] == ("gripper", True)
    assert inner[6] == ("gripper", False)
    # Los move_to deben usar exactamente los valores de la tabla.
    e2 = table.get("e2")
    assert inner[0] == ("move_to", e2.approach.shoulder, e2.approach.elbow)
    assert inner[1] == ("move_to", e2.engage.shoulder, e2.engage.elbow)


def test_plan_fails_fast_if_zone_missing():
    """Si falta la zona de descarte en la tabla, plan() debe fallar ANTES de mover."""
    backend = FakeArmBackend()
    arm = ArmNode(backend=backend, table=make_fake_table(include_zones=False))
    resp = MoveResponse(
        uci="e4d5", from_square="e4", to_square="d5",
        is_capture=True, captured_square="d5",
    )
    with pytest.raises(Exception):
        arm.plan(resp)
    assert backend.commands == []  # no se envió ningún comando
