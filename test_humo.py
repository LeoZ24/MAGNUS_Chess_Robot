"""test_humo.py — Prueba de humo y DIAGNOSTICO de los motores del brazo.
=====================================================================
Mueve el HOMBRO y el CODO a angulos concretos y mide cuanto se movieron de
verdad. Por eso es mas riesgoso que probar solo la garra: usalo con el brazo
LIBRE de obstaculos y listo para apagar el shield si algo va mal.

COMO CORRERLO (desde VSCode):
    1. Enciende el shield (interruptor ON) con cyberpi_arm_client.py subido.
    2. Despeja el espacio alrededor del brazo (sin piezas ni tablero cerca).
    3. Ten la mano cerca del interruptor del shield por si acaso.
    4. python3 test_humo.py                (con topes montados: referencia solo)
       python3 test_humo.py --sin-topes    (sin topes: pide fijar el cero a mano)

QUE HACE:
    0. Conecta y saluda (PING).
    1. Referencia el brazo (HOME): busca los topes y fija ahi el cero.
       Con --sin-topes vuelve al metodo viejo: colocar el brazo y pulsar Enter.
    2. Prueba de par por eje: manda un angulo y COMPARA con lo que llego a
       moverse. Si el error es grande, imprime el diagnostico probable en vez
       de dejarte adivinando.
    3. Repetibilidad del cero: referencia otra vez y repite el mismo angulo
       para que compruebes a ojo si el brazo cae en el mismo sitio.
    4. Garra.

Entre cada paso PIDE CONFIRMACION (Enter) para que tu controles el ritmo.

Los angulos son de MOTOR (encoder), absolutos, respecto del cero referenciado.
Ambos ejes son de transmision directa: no hay reductor en ninguno, asi que un
grado de motor es un grado de eslabon.

⚠️ SIGNO DEL ANGULO DE PRUEBA: al referenciar, el cero queda junto al tope, y
todo el recorrido util esta del lado CONTRARIO al sentido de busqueda. Un eje
que busca su tope en positivo solo admite angulos NEGATIVOS. Este script
pregunta sus limites a la placa (comando LIMITS) y elige el signo de cada eje
solo, asi que no hay que acordarse de esto.
"""

import argparse
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

from magnus.arm import CyberPiBackend
from magnus.arm.backend import ArmBackendError

ANGULO_PRUEBA = 30.0    # grados de motor
ERROR_OK_DEG = 2.0      # error por debajo del cual damos el eje por bueno


def paso(descripcion):
    """Pausa hasta que el usuario confirme, para controlar el ritmo."""
    input(f"\n>> {descripcion}\n   Presiona Enter para ejecutar (Ctrl+C aborta)... ")


def _diagnostico(nombre, pedido, logrado):
    """Explica por que un eje no llego, en vez de dejar el error crudo."""
    error = pedido - logrado
    recorrido = abs(logrado)
    print(f"   {nombre}: pedido {pedido:+.1f}°, logrado {logrado:+.1f}°, "
          f"error {error:+.1f}°")
    if abs(error) <= ERROR_OK_DEG:
        print(f"   OK: el {nombre} llego a donde se le pidio.")
        return True
    if recorrido < abs(pedido) * 0.25:
        print(f"   FALLO: el {nombre} apenas se movio. Causas por orden de "
              "probabilidad:")
        print("     1. Bateria del shield baja (los motores NO se alimentan "
              "del USB).")
        print("     2. MOVE_SPEED_RPM demasiado bajo en el cliente CyberPi: a "
              "pocas RPM")
        print("        el control interno no aplica par suficiente. Sube a "
              "60-80.")
        print("     3. Tope mecanico o cable tirando en ese sentido.")
    else:
        print(f"   PARCIAL: el {nombre} se quedo corto. Suele ser par justo: "
              "sube")
        print("     MOVE_SPEED_RPM o MOVE_MAX_PASSES en el cliente CyberPi.")
    print("   OJO: si el ENCODER marca el angulo correcto pero el brazo casi "
          "no se mueve,")
    print("   el problema no es electrico sino de transmision (revisa que el "
          "eje no patine).")
    return False


def angulo_seguro(magnitud, limites, nombre):
    """Elige el signo del angulo de prueba segun hacia donde puede moverse el eje.

    ``limites`` es ``(lo, hi)`` tal como lo informa la placa, o ``None`` si el
    cliente es anterior al comando LIMITS.
    """
    if limites is None:
        print(f"   (la placa no informa limites del {nombre}: se usa +{magnitud}°;"
              " si choca con el tope, prueba con --angulo negativo)")
        return magnitud
    lo, hi = limites
    if lo <= magnitud <= hi:
        return magnitud
    if lo <= -magnitud <= hi:
        print(f"   ({nombre}: su tope esta del lado positivo, se prueba con "
              f"-{magnitud}°)")
        return -magnitud
    # El recorrido es menor que el angulo pedido: usar la mitad del rango.
    elegido = round((lo + hi) / 2.0, 1)
    print(f"   ({nombre}: {magnitud}° no cabe en [{lo}, {hi}], se prueba con "
          f"{elegido}°)")
    return elegido


def probar_eje(arm, nombre, indice, angulo):
    """Manda un movimiento de un solo eje y mide lo que llego a moverse."""
    objetivo = [0.0, 0.0]
    objetivo[indice] = angulo
    paso(f"Mover el {nombre.upper()} a {angulo:+.1f}° (el otro eje se queda en 0)")
    arm.move_to(shoulder=objetivo[0], elbow=objetivo[1])
    pos = arm.get_position()
    ok = _diagnostico(nombre, angulo, pos[indice])

    paso(f"Regresar el {nombre.upper()} a 0°")
    arm.move_to(shoulder=0.0, elbow=0.0)
    pos = arm.get_position()
    print(f"   De vuelta en hombro={pos[0]:.2f}  codo={pos[1]:.2f}")
    return ok


def referenciar(arm, sin_topes):
    """Fija el cero: automatico contra los topes, o a mano si no los hay."""
    if sin_topes:
        print("\n--- Cero MANUAL (--sin-topes) ---")
        print("Recuerda: colocar el brazo a ojo NO es repetible. Cada partida")
        print("empezara con un cero distinto y la tabla de posiciones apuntara")
        print("a un sitio distinto. Monta los topes en cuanto puedas.")
        input(">> Pon el brazo en su pose de reposo y Enter para fijar el cero... ")
        arm.zero_here()
    else:
        paso("Referenciar el brazo (HOME): buscara los topes empujando despacio")
        arm.home()
    sh, el = arm.get_position()
    print(f"   Cero fijado. Ahora hombro={sh:.2f}  codo={el:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Prueba de humo de los motores")
    parser.add_argument("--sin-topes", action="store_true",
                        help="fijar el cero a mano (sin referenciado automatico)")
    parser.add_argument("--angulo", type=float, default=ANGULO_PRUEBA,
                        help=f"angulo de prueba en grados de motor (def. {ANGULO_PRUEBA})")
    args = parser.parse_args()
    angulo = args.angulo

    print("\n--- Test de humo CON MOTORES ---")
    print("Despeja el espacio alrededor del brazo antes de continuar.")
    print("Manten la mano cerca del interruptor del shield.\n")
    print("Esperando a que la CyberPi se conecte...\n")

    with CyberPiBackend() as arm:

        # 1. Fijar el origen.
        referenciar(arm, args.sin_topes)

        # 2. Preguntar a la placa hacia que lado puede moverse cada eje.
        limites = arm.get_limits()
        if limites is None:
            lim_hombro = lim_codo = None
        else:
            lim_hombro, lim_codo = limites
            print(f"   Limites de la placa: hombro {lim_hombro}, codo {lim_codo}")
        ang_codo = angulo_seguro(angulo, lim_codo, "codo")
        ang_hombro = angulo_seguro(angulo, lim_hombro, "hombro")

        # 3. Prueba de par de cada eje, por separado.
        ok_codo = probar_eje(arm, "codo", indice=1, angulo=ang_codo)
        ok_hombro = probar_eje(arm, "hombro", indice=0, angulo=ang_hombro)

        # 4. Repetibilidad del cero: lo que hace util al teach & playback.
        if not args.sin_topes:
            paso("Comprobar la repetibilidad: referenciar otra vez y repetir "
                 "el mismo angulo")
            print("   Fijate donde queda la punta del brazo en el paso "
                  "siguiente:")
            arm.home()
            arm.move_to(shoulder=ang_hombro, elbow=0.0)
            print("   Si cae en el MISMO punto que antes, el cero es repetible")
            print("   y positions.json se puede grabar con confianza.")
            paso("Volver a 0°")
            arm.move_to(shoulder=0.0, elbow=0.0)

        # 5. Garra, para cerrar la prueba completa.
        paso("Probar la garra (acercar y alejar el iman)")
        arm.set_gripper(True)
        time.sleep(1.0)
        arm.set_gripper(False)

        if ok_codo and ok_hombro:
            print("\n=== TEST DE HUMO CON MOTORES OK ===")
            print("Hombro, codo y garra llegaron a donde se les pidio.")
        else:
            print("\n=== TEST DE HUMO CON AVISOS ===")
            print("Algun eje no llego. Revisa el diagnostico de arriba antes")
            print("de grabar positions.json: una tabla medida con un eje que")
            print("se queda corto no sirve de nada.")

    print("Conexion cerrada limpiamente.\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAbortado por el usuario (Ctrl+C). "
              "Apaga el shield si el brazo quedo en mala posicion.")
    except ArmBackendError as exc:
        print(f"\n\nFallo del brazo: {exc}")
