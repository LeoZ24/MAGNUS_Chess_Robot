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


def _park() -> list[str]:
    """Toda jugada termina retirando el brazo fuera del tablero."""
    return [f"move({config.ZONE_PARK})"]


def _pick_place(src: str, dst: str) -> list[str]:
    """Secuencia esperada de un pick & place simple."""
    return [
        f"move({src})", "grip_on", f"move({dst})", "grip_off",
    ]


def test_simple_move(arm):
    resp = MoveResponse(uci="e2e4", from_square="e2", to_square="e4")
    assert _steps_as_strings(arm.plan(resp)) == (
        ["grip_off"] + _pick_place("e2", "e4") + _park()
    )


def test_capture_removes_piece_first(arm):
    resp = MoveResponse(
        uci="e4d5", from_square="e4", to_square="d5",
        is_capture=True, captured_square="d5",
    )
    expected = _pick_place("d5", config.ZONE_DISCARD) + _pick_place("e4", "d5")
    assert _steps_as_strings(arm.plan(resp)) == ["grip_off"] + expected + _park()


def test_en_passant_uses_captured_square(arm):
    """En al paso el peón capturado NO está en to_square: está en captured_square."""
    resp = MoveResponse(
        uci="e5d6", from_square="e5", to_square="d6",
        is_capture=True, is_en_passant=True, captured_square="d5",
    )
    expected = _pick_place("d5", config.ZONE_DISCARD) + _pick_place("e5", "d6")
    assert _steps_as_strings(arm.plan(resp)) == ["grip_off"] + expected + _park()


def test_kingside_castle_moves_king_then_rook(arm):
    resp = MoveResponse(
        uci="e1g1", from_square="e1", to_square="g1",
        is_castling=True, is_kingside_castle=True, rook_from="h1", rook_to="f1",
    )
    expected = _pick_place("e1", "g1") + _pick_place("h1", "f1")
    assert _steps_as_strings(arm.plan(resp)) == ["grip_off"] + expected + _park()


def test_queenside_castle(arm):
    resp = MoveResponse(
        uci="e8c8", from_square="e8", to_square="c8",
        is_castling=True, rook_from="a8", rook_to="d8",
    )
    expected = _pick_place("e8", "c8") + _pick_place("a8", "d8")
    assert _steps_as_strings(arm.plan(resp)) == ["grip_off"] + expected + _park()


def test_promotion_swaps_piece(arm):
    resp = MoveResponse(
        uci="e7e8q", from_square="e7", to_square="e8", promotion="q",
    )
    expected = (
        _pick_place("e7", config.ZONE_DISCARD)
        + _pick_place(config.ZONE_EXCHANGE, "e8")
    )
    assert _steps_as_strings(arm.plan(resp)) == ["grip_off"] + expected + _park()


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
    assert _steps_as_strings(arm.plan(resp)) == ["grip_off"] + expected + _park()


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

    # S1 vuelve a reposo, dos posiciones y recoger/soltar: sin alturas.
    # Y al final la retirada fuera del tablero.
    e2 = table.get("e2").position
    e4 = table.get("e4").position
    park = table.get(config.ZONE_PARK).position
    assert backend.commands == [
        ("connect",),
        ("gripper", False),
        ("move_to", e2.shoulder, e2.elbow),
        ("gripper", True),
        ("move_to", e4.shoulder, e4.elbow),
        ("gripper", False),
        ("move_to", park.shoulder, park.elbow),
        ("disconnect",),
    ]


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


def test_home_delegates_to_the_backend():
    """El nodo no calcula nada: solo pide la referencia al backend."""
    backend = FakeArmBackend()
    arm = ArmNode(backend=backend, table=make_fake_table())
    arm.start()
    arm.home()
    assert ("home",) in backend.commands


def test_home_connects_first_if_needed():
    backend = FakeArmBackend()
    arm = ArmNode(backend=backend, table=make_fake_table())
    arm.home()                       # sin start() previo
    assert backend.commands == [("connect",), ("home",)]


# --------------------------------------------------------------------------- #
# Retirada del tablero
# --------------------------------------------------------------------------- #

def test_every_move_ends_outside_the_board(arm):
    """El ultimo paso de CUALQUIER jugada es retirarse.

    Plantado donde termino la jugada, el brazo le estorba al rival y le tapa el
    tablero a la camara, que es como la vision se entera de la jugada siguiente.
    """
    jugadas = [
        MoveResponse(uci="e2e4", from_square="e2", to_square="e4"),
        MoveResponse(uci="e4d5", from_square="e4", to_square="d5",
                     is_capture=True, captured_square="d5"),
        MoveResponse(uci="e1g1", from_square="e1", to_square="g1",
                     is_castling=True, rook_from="h1", rook_to="f1"),
        MoveResponse(uci="e7e8q", from_square="e7", to_square="e8", promotion="q"),
    ]
    for resp in jugadas:
        pasos = _steps_as_strings(arm.plan(resp))
        assert pasos[-1] == f"move({config.ZONE_PARK})", resp.uci
        # Y solo una vez, al final: no se pasa por reposo a media jugada.
        assert pasos.count(f"move({config.ZONE_PARK})") == 1, resp.uci


def test_a_table_without_park_still_plays():
    """Una tabla grabada antes de que existiera el reposo sigue sirviendo.

    Si `park` fuese obligatorio, las tablas ya calibradas pasarian a estar
    "incompletas" y la interfaz dejaria de permitir el brazo real.
    """
    table = make_fake_table()
    del table._positions[config.ZONE_PARK]      # tabla "antigua"
    arm = ArmNode(backend=FakeArmBackend(), table=table)
    resp = MoveResponse(uci="e2e4", from_square="e2", to_square="e4")
    pasos = _steps_as_strings(arm.plan(resp))
    assert pasos == ["grip_off"] + _pick_place("e2", "e4")
    assert not any(config.ZONE_PARK in paso for paso in pasos)
