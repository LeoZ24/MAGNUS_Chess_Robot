"""Regresiones del cliente MicroPython con un shield falso, sin red ni motores."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


CLIENT_PATH = Path(__file__).resolve().parents[1] / "examples/cyberpi_arm_client.py"


class StartupReached(BaseException):
    """Detiene el arranque simulado antes de abrir red o mover motores."""


@pytest.mark.parametrize("module_name", ["__main__", "user_program", None])
def test_complete_uploaded_script_starts_without_requiring_main(monkeypatch, module_name):
    messages = []
    colors = []

    def stop_before_network():
        assert any("MAGNUS" in message for message in messages)
        assert colors == ["blue"]
        raise StartupReached

    cyberpi = SimpleNamespace(
        console=SimpleNamespace(clear=lambda: None, println=messages.append),
        led=SimpleNamespace(on=colors.append),
        wifi=SimpleNamespace(is_connect=stop_before_network),
    )
    monkeypatch.setitem(sys.modules, "cyberpi", cyberpi)
    monkeypatch.setitem(sys.modules, "usocket", SimpleNamespace())
    namespace = {} if module_name is None else {"__name__": module_name}
    with pytest.raises(StartupReached):
        exec(compile(CLIENT_PATH.read_text(), str(CLIENT_PATH), "exec"), namespace)
    assert any("MAGNUS" in message for message in messages)


class FakeMbot2:
    """Simula encoders y retencion; permite introducir carga y atasco."""

    def __init__(self):
        self.angles = {"EM1": 0.0, "EM2": 0.0}
        self.locked = {"EM1": False, "EM2": False}
        self.turns = []
        self.events = []
        # fraction: parte del giro que EM_turn llega a cubrir. El brazo real se
        # quedaba en un cuarto porque a pocas RPM no hay par.
        self.fraction = 1.0
        # stall_rpm: a partir de esta velocidad SI cubre el giro entero. None =
        # fraction se aplica siempre (motor trabado, pase lo que pase).
        self.stall_rpm = None
        # power_gain: grados por segundo y por punto de potencia cruda.
        # 0 = la potencia no mueve nada (los impulsos no sirven de nada).
        self.power_gain = 0.0
        self.pending_power = {}
        self.last_sleep = 0.0
        self.after_turn = lambda port: None

    def EM_get_angle(self, port):
        return self.angles[port]

    def EM_lock(self, enabled, port):
        self.events.append(("lock", enabled, port))
        for axis in self.angles if port == "all" else (port,):
            self.locked[axis] = enabled

    def EM_turn(self, delta, speed, port):
        self.turns.append((delta, speed, port))
        if self.stall_rpm is None or speed < self.stall_rpm:
            share = self.fraction
        else:
            share = 1.0
        self.angles[port] += delta * share
        self.after_turn(port)

    def EM_stop(self, port):
        self.events.append(("stop", port))
        # La potencia cruda no mueve el eje "al instante": mueve mientras esta
        # aplicada. Se contabiliza al cortarla, con la duracion del ultimo
        # sleep, que es justo lo que el cliente usa para medir el ritmo.
        for axis in (list(self.angles) if port == "all" else [port]):
            power = self.pending_power.pop(axis, 0.0)
            if power and self.power_gain:
                self.angles[axis] += power * self.power_gain * self.last_sleep

    def EM_reset_angle(self, port):
        assert not self.locked[port], "No resetear un encoder con un objetivo viejo"
        self.angles[port] = 0.0

    def motor_set(self, power, port):
        self.events.append(("assist", power, port))

    def EM_set_power(self, power, port):
        assert not self.locked[port], "No empujar contra la retencion"
        self.events.append(("power", power, port))
        self.pending_power[port] = power


@pytest.fixture
def client(monkeypatch):
    hardware = FakeMbot2()

    def stop_before_network():
        raise StartupReached

    cyberpi = SimpleNamespace(
        mbot2=hardware,
        console=SimpleNamespace(clear=lambda: None, println=lambda text: None),
        led=SimpleNamespace(on=lambda color: None),
        wifi=SimpleNamespace(is_connect=stop_before_network),
    )
    monkeypatch.setitem(sys.modules, "cyberpi", cyberpi)
    monkeypatch.setitem(sys.modules, "usocket", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("arm_client_test", CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Ejecutar tambien el arranque real, pero interrumpir ANTES de la red.
    # No alterar el archivo de produccion para facilitar su importacion.
    with pytest.raises(StartupReached):
        spec.loader.exec_module(module)
    def sleep(seconds):
        hardware.last_sleep = seconds

    module.time = SimpleNamespace(sleep=sleep)
    module.VERBOSE = False
    return module, hardware


def test_move_holds_both_axes_and_returns_final_shoulder_reading(client):
    module, hardware = client

    def change_load(port):
        assert all(hardware.locked.values())
        if port == "EM2":
            hardware.angles["EM1"] -= 0.5

    hardware.after_turn = change_load
    assert module.handle("MOVE 30 -20") == "ACK MOVE 29.5 -20.0"
    assert all(hardware.locked.values())


def test_shoulder_that_sags_while_the_elbow_moves_is_put_back(client):
    """Ceder unos grados al cambiar la carga no arruina la jugada: se corrige.

    Antes esto se rechazaba. Pero si el eje vuelve a su sitio la pose final es
    correcta, y abortar deja la pieza a medio camino, que es peor.
    """
    module, hardware = client

    def change_load(port):
        if port == "EM2":
            hardware.angles["EM1"] -= 10.0        # cede una vez, recuperable

    hardware.after_turn = change_load
    assert module.handle("MOVE 30 -20") == "ACK MOVE 30.0 -20.0"
    assert all(hardware.locked.values())


def test_move_rejects_a_shoulder_that_cannot_hold_its_position(client):
    """Lo que si es un fallo: que el eje ceda y NO se deje recuperar.

    Significa que la retencion no esta haciendo su trabajo, y hay que decirlo
    en vez de seguir moviendo el brazo a ciegas.
    """
    module, hardware = client

    def change_load(port):
        if port == "EM2":
            hardware.angles["EM1"] -= 10.0
            hardware.fraction = 0.0               # el hombro deja de responder

    hardware.after_turn = change_load
    with pytest.raises(ValueError, match="hombro no mantuvo"):
        module.handle("MOVE 30 -20")


def test_move_at_current_pose_also_enables_hold(client):
    module, hardware = client
    module.handle("MOVE 0 0")
    assert all(hardware.locked.values())
    assert hardware.turns == []


def test_stop_and_manual_zero_release_and_next_move_reenables_hold(client):
    module, hardware = client
    module.handle("MOVE 30 -20")
    assert module.handle("STOP") == "ACK STOP"
    assert not any(hardware.locked.values())
    module.handle("MOVE 30 -20")
    assert all(hardware.locked.values())
    assert module.handle("ZERO") == "ACK ZERO"
    assert hardware.angles == {"EM1": 0.0, "EM2": 0.0}
    assert not any(hardware.locked.values())


@pytest.mark.parametrize("command", ["MOVE 30 20", "MOVE 30 nan", "MOVE inf -20"])
def test_invalid_either_target_never_moves_the_other_axis(client, command):
    module, hardware = client
    with pytest.raises(ValueError, match="fuera de limites"):
        module.handle(command)
    assert hardware.turns == []


def test_stalled_motor_has_a_bounded_number_of_attempts(client):
    """Un eje trabado de verdad da ERR; nunca se queda insistiendo sin fin.

    Una placa colgada en un bucle sin salida es una placa a la que cuesta
    subirle un programa nuevo.
    """
    module, hardware = client
    hardware.fraction = 0.0
    with pytest.raises(ValueError, match="hombro no alcanzo"):
        module.handle("MOVE 30 -20")
    assert 0 < len(hardware.turns) <= module.MOVE_MAX_PASSES
    assert all(turn[2] == "EM1" for turn in hardware.turns)
    # Y antes de rendirse prueba a subir la velocidad, en vez de repetir cuatro
    # veces exactamente el mismo giro inutil.
    velocidades = [turn[1] for turn in hardware.turns]
    assert velocidades == sorted(velocidades)
    assert velocidades[-1] > velocidades[0]


def test_both_axes_start_at_the_gross_speed(client):
    """Ningun eje arranca "fino".

    Antes un tramo corto se pedia a 40 rpm y el hombro cargado no llegaba; el
    parche era darle al hombro su propia velocidad fina. Ya no hace falta: los
    dos empiezan a la velocidad gruesa y solo sube el que demuestre que se
    queda corto, que es una regla sin casos especiales por eje.
    """
    module, hardware = client
    module.handle("MOVE 10 -10")
    assert hardware.turns == [(10.0, module.MOVE_SPEED_RPM, "EM1"),
                              (-10.0, module.MOVE_SPEED_RPM, "EM2")]


def test_backlash_stays_in_limits_and_rereads_before_relative_correction(client):
    module, hardware = client
    hardware.angles["EM1"] = 10.0
    module.BACKLASH_DEG = 5.0
    module.handle("MOVE 2 0")
    assert [turn[0] for turn in hardware.turns] == [-10.0, 2.0]
    assert hardware.angles["EM1"] == 2.0


def test_home_keeps_the_other_axis_held_and_relocks_at_new_zero(client):
    module, hardware = client
    axes = []

    def seek(port, sign, power, name):
        axes.append(port)
        other = "EM1" if port == "EM2" else "EM2"
        assert hardware.locked[other]
        module._hw_hold(False, port)
        module._hw_stop_axis(port)

    module._seek_stop = seek
    module.handle("HOME")
    assert axes == ["EM2", "EM2", "EM1", "EM1"]
    assert all(hardware.locked.values())
    assert hardware.angles == {"EM1": 0.0, "EM2": 0.0}


def test_seek_stop_disables_hold_and_stops_on_timeout(client):
    module, hardware = client
    module._hw_hold(True, "all")
    elapsed = iter([0.0, module.HOME_TIMEOUT_S + 1.0])
    module.time.time = lambda: next(elapsed)
    with pytest.raises(ValueError, match="no encontre el tope"):
        module._seek_stop("EM2", 1, 30, "codo")
    assert hardware.locked == {"EM1": True, "EM2": False}
    assert ("stop", "EM2") in hardware.events


def test_missing_lock_api_reports_error_before_motion_and_still_allows_stop(client):
    module, hardware = client
    hardware.EM_lock = None
    with pytest.raises(ValueError, match="EM_lock no disponible"):
        module.handle("MOVE 30 -20")
    assert hardware.turns == []
    assert module.handle("STOP") == "ACK STOP"


@pytest.mark.parametrize("stall", [False, True])
def test_session_releases_on_disconnect_and_on_motion_error(client, stall):
    module, hardware = client
    hardware.fraction = 0.0 if stall else 1.0
    replies = []
    closed = []
    packets = iter([b"MOVE 30 -20\n", b""])

    def receive(size):
        if replies:
            assert all(hardware.locked.values()) is (not stall)
        return next(packets)

    sock = SimpleNamespace(
        connect=lambda address: None, recv=receive,
        send=lambda data: replies.append(data), close=lambda: closed.append(True),
    )
    module.usocket = SimpleNamespace(AF_INET=2, SOCK_STREAM=1, socket=lambda *args: sock)
    module.sesion()
    assert replies[0].startswith(b"ERR " if stall else b"ACK MOVE ")
    assert not any(hardware.locked.values())
    assert closed == [True]


# --------------------------------------------------------------------------- #
# El fallo que se vio en el brazo real (c7 -> c5)
# --------------------------------------------------------------------------- #

def test_short_passes_escalate_until_the_axis_arrives(client):
    """Regresion: el hombro paraba en 35 de los 50 pedidos yendo de c7 a c5.

    El motor cubre un cuarto del giro mientras se le pidan pocas RPM. Con el
    criterio viejo ("no se ha movido nada") eso parecia un eje sano y la
    velocidad no subia nunca: cuatro pasadas identicas y ERR.
    """
    module, hardware = client
    hardware.angles["EM1"] = 30.0
    hardware.angles["EM2"] = -215.0
    hardware.fraction = 0.25        # se queda a un cuarto de camino...
    hardware.stall_rpm = 90         # ...mientras no se le pidan 90 rpm
    got_sh, got_el = module._move_all(50.0, -191.0)
    assert abs(got_sh - 50.0) <= module.TOLERANCE_DEG
    assert abs(got_el + 191.0) <= module.TOLERANCE_DEG


def test_power_pulses_finish_what_the_turns_cannot(client):
    """Si ni a la velocidad maxima llega, los ultimos grados van por potencia.

    Es la misma tecnica con la que HOME empuja contra el tope: la potencia
    cruda da par aunque falte medio grado, que es donde el control de velocidad
    no lo da.
    """
    module, hardware = client
    hardware.fraction = 0.25        # EM_turn nunca termina el trabajo
    hardware.power_gain = 1.0       # pero la potencia si mueve
    got_sh, _ = module._move_all(30.0, 0.0)
    assert abs(got_sh - 30.0) <= module.TOLERANCE_DEG
    assert any(event[0] == "power" for event in hardware.events)


@pytest.mark.parametrize("power_gain", [0.3, 1.0, 4.0])
def test_pulses_do_not_overshoot_whatever_the_speed_of_the_arm(client, power_gain):
    """La duracion del impulso se MIDE, no se fija a ojo.

    Un brazo rapido y uno lento tienen que acabar los dos dentro de tolerancia
    con las mismas constantes: por eso cada impulso deja un ritmo en
    grados/segundo y el siguiente se dimensiona con el.
    """
    module, hardware = client
    hardware.fraction = 0.0         # solo la potencia mueve
    hardware.power_gain = power_gain
    hardware.angles["EM1"] = 20.0
    reached = module._creep_to(30.0, "EM1", "hombro")
    assert abs(reached - 30.0) <= module.TOLERANCE_DEG


def test_creep_releases_the_hold_to_push_and_restores_it_after(client):
    """La retencion pelearia contra la potencia cruda, asi que se suelta.

    Pero hay que volver a activarla: si no, el eje queda libre justo despues de
    colocarlo y se cae mientras el host piensa la siguiente jugada.
    """
    module, hardware = client
    hardware.fraction = 0.0
    hardware.power_gain = 1.0
    hardware.angles["EM1"] = 20.0
    module._creep_to(30.0, "EM1", "hombro")
    assert hardware.locked["EM1"]
    # Que mientras empujaba NO estuviera retenido lo verifica el propio shield
    # falso, con su assert dentro de EM_set_power.
    assert any(event[0] == "power" for event in hardware.events)


def test_an_axis_that_nothing_can_move_still_gives_up(client):
    """Ni EM_turn ni la potencia mueven: tiene que terminar con ERR igualmente."""
    module, hardware = client
    hardware.fraction = 0.0
    hardware.power_gain = 0.0
    with pytest.raises(ValueError, match="hombro no alcanzo"):
        module.handle("MOVE 30 -20")


# --------------------------------------------------------------------------- #
# Ayuda del geekservo del hombro (opcional)
# --------------------------------------------------------------------------- #

def test_assist_is_off_by_default(client):
    """Desactivada mientras no se sepa en que puerto esta el geekservo.

    Inventarse el puerto o el nombre de la API moveria un actuador real a
    ciegas; es justo lo que este proyecto no hace.
    """
    module, hardware = client
    assert module.ASSIST_ENABLED is False
    module.handle("MOVE 30 -20")
    assert not any(event[0] == "assist" for event in hardware.events)


def test_assist_pushes_with_the_shoulder_and_stops_after(client):
    """Empuja en el sentido del movimiento y se para al terminar."""
    module, hardware = client
    module.ASSIST_ENABLED = True
    module.handle("MOVE 30 -20")
    empujes = [event for event in hardware.events if event[0] == "assist"]
    assert empujes, "no empujo nada"
    assert empujes[0][1] > 0            # el hombro va a +30: ayuda en positivo
    assert empujes[-1][1] == 0          # y queda parado


def test_assist_only_helps_the_shoulder(client):
    """El codo no lleva geekservo: no hay nada que empujar por el."""
    module, hardware = client
    module.ASSIST_ENABLED = True
    module._move_axis(-20.0, module.ELBOW_PORT, module.ELBOW_LIM, "codo")
    assert not any(event[0] == "assist" for event in hardware.events)


def test_assist_stops_even_if_the_shoulder_fails(client):
    """Un fallo a media jugada no puede dejar el geekservo empujando solo."""
    module, hardware = client
    module.ASSIST_ENABLED = True
    hardware.fraction = 0.0
    with pytest.raises(ValueError):
        module.handle("MOVE 30 -20")
    empujes = [event for event in hardware.events if event[0] == "assist"]
    assert empujes and empujes[-1][1] == 0
