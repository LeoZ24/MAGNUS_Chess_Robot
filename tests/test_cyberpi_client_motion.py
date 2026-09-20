"""Tests del control de movimiento que corre DENTRO de la CyberPi.

``examples/cyberpi_arm_client.py`` es el único trozo del proyecto que no se
puede importar: vive en la placa, usa ``cyberpi``/``usocket`` y al final tiene
el bucle de red.  Pero su parte delicada —llevar un eje al ángulo pedido con
motores que a pocas RPM no dan par— es lógica pura, y merece probarse sin
brazo igual que todo lo demás.

Así que aquí se carga el archivo como texto, se recorta el bloque de red, se
sustituyen las funciones ``_hw_*`` por un **motor simulado** y se comprueba que
el control converge.  El modelo del motor no pretende ser fiel al hardware: lo
que se verifica es que el algoritmo se recupera de los dos fallos reales que se
vieron en el brazo —quedarse a un cuarto de camino y ceder al moverse el otro
eje— en vez de darlos por buenos.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parents[1] / "examples" / "cyberpi_arm_client.py"


def load_client():
    """Ejecuta el cliente sin su bloque de red y devuelve el módulo."""
    source = CLIENT.read_text(encoding="utf-8")
    source = source.split("# ======================= RED =======================")[0]
    source = source.replace("import cyberpi\n", "").replace("import usocket\n", "")
    module = types.ModuleType("cyberpi_arm_client")
    exec(compile(source, str(CLIENT), "exec"), module.__dict__)
    return module


class Motor:
    """Motor encoder simulado, con los dos vicios del real.

    * El control interno es de VELOCIDAD: por debajo de ``stall_rpm`` el PWM no
      vence el peso y ``EM_turn`` solo cubre ``undershoot`` del giro pedido.
    * Con potencia cruda hay par a partir de ``stall_power``, pero al cortarla
      el eje sigue rodando ``coast_s`` segundos: de ahí el riesgo de pasarse.
    """

    def __init__(self, angle, *, stall_rpm=75.0, undershoot=0.25,
                 stall_power=15.0, deg_per_s_per_power=6.0, coast_s=0.02):
        self.angle = angle
        self.stall_rpm = stall_rpm
        self.undershoot = undershoot
        self.stall_power = stall_power
        self.deg_per_s_per_power = deg_per_s_per_power
        self.coast_s = coast_s
        self.power = 0.0
        self._coasting = 0.0

    # --- lo que ve el cliente ---
    def turn(self, delta, rpm):
        share = 1.0 if rpm >= self.stall_rpm else self.undershoot
        self.angle += delta * share

    def set_power(self, power):
        self.power = 0.0 if abs(power) < self.stall_power else power

    def stop(self):
        # Al cortar, el eje se lleva por delante lo que tenía de inercia.
        self._coasting = self.power
        self.power = 0.0

    def advance(self, dt):
        if self.power:
            self.angle += self.power * self.deg_per_s_per_power * dt
        elif self._coasting:
            rolled = min(dt, self.coast_s)
            self.angle += self._coasting * self.deg_per_s_per_power * rolled
            self._coasting = 0.0


class Bench:
    """Une el módulo del cliente con dos motores simulados y un reloj falso."""

    def __init__(self, shoulder=30.0, elbow=-215.0, **motor_kwargs):
        self.client = load_client()
        self.motors = {
            self.client.SHOULDER_PORT: Motor(shoulder, **motor_kwargs),
            self.client.ELBOW_PORT: Motor(elbow, **motor_kwargs),
        }
        self.elapsed = 0.0
        self.sag = 0.0          # grados que cede un eje mientras se mueve otro
        self._driving = None

        def sleep(seconds):
            self.elapsed += seconds
            for port, motor in self.motors.items():
                motor.advance(seconds)
                if self.sag and port != self._driving and self._driving:
                    motor.angle -= self.sag * seconds

        self.client.time = types.SimpleNamespace(sleep=sleep, time=lambda: self.elapsed)
        self.client.VERBOSE = False
        self.client._hw_get_angle = lambda port: round(self.motors[port].angle, 2)
        self.client._hw_turn = self._turn
        self.client._hw_set_power = self._set_power
        self.client._hw_stop_axis = lambda port: self.motors[port].stop()
        self.client._hw_stop = lambda: [m.stop() for m in self.motors.values()]

    def _turn(self, delta, rpm, port):
        self._driving = port
        self.motors[port].turn(delta, rpm)

    def _set_power(self, power, port):
        self._driving = port
        self.motors[port].set_power(power)

    def angles(self):
        return (self.motors[self.client.SHOULDER_PORT].angle,
                self.motors[self.client.ELBOW_PORT].angle)


# --------------------------------------------------------------------------- #
# El fallo que se vio en el brazo real
# --------------------------------------------------------------------------- #

def test_un_eje_que_se_queda_a_un_cuarto_de_camino_acaba_llegando():
    """c7 -> c5: el hombro se quedaba en 35° de los 50° pedidos.

    El motor solo cubre un cuarto del giro mientras se le pidan pocas RPM, que
    es justo lo que pasaba en el brazo. El control tiene que darse cuenta y
    subir, no seguir insistiendo igual.
    """
    bench = Bench()
    reached = bench.client._move_axis(
        50.0, bench.client.SHOULDER_PORT, bench.client.SHOULDER_LIM, "hombro")
    assert abs(reached - 50.0) <= bench.client.TOLERANCE_DEG


def test_no_confunde_quedarse_corto_con_estar_trabado():
    """La pista de que falta par es la pasada CORTA, no la pasada nula.

    Con el criterio anterior ("no se movió nada") un eje que cubre un cuarto
    del giro parecía sano y la velocidad no subía nunca.
    """
    bench = Bench()
    velocidades = []
    original = bench.client._hw_turn

    def spy(delta, rpm, port):
        velocidades.append(rpm)
        original(delta, rpm, port)

    bench.client._hw_turn = spy
    bench.client._move_axis(50.0, bench.client.SHOULDER_PORT,
                            bench.client.SHOULDER_LIM, "hombro")
    assert max(velocidades) > bench.client.MOVE_SPEED_RPM


# --------------------------------------------------------------------------- #
# Convergencia con motores de distinto carácter
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("perfil,kwargs", [
    ("sano",      dict(stall_rpm=0.0, undershoot=1.0)),
    ("perezoso",  dict(stall_rpm=75.0, undershoot=0.25)),
    ("muy duro",  dict(stall_rpm=110.0, undershoot=0.1, stall_power=25.0)),
    ("rapido",    dict(deg_per_s_per_power=14.0, coast_s=0.05)),
    ("lento",     dict(deg_per_s_per_power=2.0)),
])
def test_converge_con_motores_de_distinto_caracter(perfil, kwargs):
    """El control no puede depender de la velocidad concreta del brazo.

    Por eso los impulsos del último tramo se dimensionan con un ritmo MEDIDO en
    el impulso anterior y no con una duración fija: un motor rápido y uno lento
    tienen que acabar los dos dentro de tolerancia.
    """
    bench = Bench(**kwargs)
    shoulder, elbow = bench.client._move_both(50.0, -191.0)
    assert abs(shoulder - 50.0) <= bench.client.MOVE_FAIL_DEG, perfil
    assert abs(elbow + 191.0) <= bench.client.MOVE_FAIL_DEG, perfil


def test_recorre_el_tablero_entero_sin_acumular_error():
    """Encadena posiciones reales de positions.json como en una partida."""
    import json
    tabla = json.loads(
        (Path(__file__).resolve().parents[1] / "magnus" / "arm" / "positions.json")
        .read_text(encoding="utf-8"))
    casillas = ["e2", "e4", "c7", "c5", "discard", "g1", "f3", "exchange", "a8"]
    bench = Bench(shoulder=tabla["e2"]["shoulder"], elbow=tabla["e2"]["elbow"])
    for casilla in casillas:
        objetivo = tabla[casilla]
        shoulder, elbow = bench.client._move_both(objetivo["shoulder"], objetivo["elbow"])
        assert abs(shoulder - objetivo["shoulder"]) <= bench.client.MOVE_FAIL_DEG, casilla
        assert abs(elbow - objetivo["elbow"]) <= bench.client.MOVE_FAIL_DEG, casilla


# --------------------------------------------------------------------------- #
# Orden de los ejes y repaso final
# --------------------------------------------------------------------------- #

def test_el_hombro_se_mueve_antes_que_el_codo():
    """Estética y seguridad: el brazo gira sobre su base con el codo recogido."""
    bench = Bench()
    tocados = []
    original = bench.client._hw_turn

    def spy(delta, rpm, port):
        if port not in tocados:
            tocados.append(port)
        original(delta, rpm, port)

    bench.client._hw_turn = spy
    bench.client._move_both(50.0, -191.0)
    assert tocados[0] == bench.client.SHOULDER_PORT


def test_corrige_el_eje_que_cede_al_moverse_el_otro():
    """Al desplegar el codo, el hombro aguanta más brazo y puede caerse.

    Antes MOVE daba por bueno el hombro nada más moverlo y no volvía a mirarlo:
    la jugada terminaba con el hombro varios grados por debajo del destino.
    """
    bench = Bench()
    bench.sag = 4.0        # grados por segundo que cede el eje parado
    shoulder, elbow = bench.client._move_both(50.0, -191.0)
    assert abs(shoulder - 50.0) <= bench.client.MOVE_FAIL_DEG
    assert abs(elbow + 191.0) <= bench.client.MOVE_FAIL_DEG


def test_devuelve_hombro_y_codo_en_ese_orden_aunque_cambie_la_ejecucion():
    """El ACK del protocolo es siempre '<hombro> <codo>'."""
    bench = Bench()
    bench.client.MOVE_SHOULDER_FIRST = False
    shoulder, elbow = bench.client._move_both(50.0, -191.0)
    assert abs(shoulder - 50.0) <= bench.client.MOVE_FAIL_DEG
    assert abs(elbow + 191.0) <= bench.client.MOVE_FAIL_DEG


# --------------------------------------------------------------------------- #
# Cotas: nada de bucles sin salida ni motores empujando
# --------------------------------------------------------------------------- #

def test_un_eje_bloqueado_da_error_y_no_se_queda_colgado():
    """Si el brazo está trabado de verdad, hay que avisar, no insistir siempre.

    Una placa colgada en un bucle sin salida es una placa a la que cuesta
    subirle un programa nuevo.
    """
    bench = Bench()
    bench.client._hw_turn = lambda delta, rpm, port: None
    bench.client._hw_set_power = lambda power, port: None
    with pytest.raises(ValueError):
        bench.client._move_axis(50.0, bench.client.SHOULDER_PORT,
                                bench.client.SHOULDER_LIM, "hombro")
    assert bench.elapsed < 30.0     # ha terminado, no se ha quedado dentro


def test_el_eje_queda_parado_aunque_falle_a_mitad():
    """Nunca devolver el control con potencia aplicada."""
    bench = Bench()
    llamadas = {"stop": 0}
    original_stop = bench.client._hw_stop_axis

    def contar(port):
        llamadas["stop"] += 1
        original_stop(port)

    bench.client._hw_stop_axis = contar

    def explota(power, port):
        raise RuntimeError("el shield se ha quedado sin batería")

    bench.client._hw_set_power = explota
    with pytest.raises(RuntimeError):
        bench.client._creep_to(50.0, bench.client.SHOULDER_PORT, "hombro")
    assert llamadas["stop"] >= 1
    assert bench.motors[bench.client.SHOULDER_PORT].power == 0.0


def test_rechaza_un_angulo_fuera_de_limites_sin_mover_nada():
    """El recorrido útil va del lado contrario al tope: +x en el codo lo
    estrellaría contra él."""
    bench = Bench()
    antes = bench.angles()
    with pytest.raises(ValueError):
        bench.client._move_both(50.0, 30.0)
    assert bench.angles() == antes
