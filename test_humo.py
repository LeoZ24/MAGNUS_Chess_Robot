"""
test_humo_motores.py — Prueba de humo QUE MUEVE LOS MOTORES del brazo.
=====================================================================
A diferencia de test_humo.py (que solo movía la garra), este mueve el
HOMBRO y el CODO a grados especificos. Por eso es mas riesgoso: usalo con
el brazo LIBRE de obstaculos y listo para apagar el shield si algo va mal.

COMO CORRERLO (desde VSCode):
    1. Enciende el shield (interruptor ON) con cyberpi_arm_client.py subido.
    2. Coloca este archivo en la RAIZ del proyecto MAGNUS.
    3. Despeja el espacio alrededor del brazo (sin piezas ni tablero cerca).
    4. Ten la mano cerca del interruptor del shield por si acaso.
    5. python3 test_humo_motores.py

QUE HACE (movimientos PEQUENOS y LENTOS a proposito):
    - Fija la pose actual como origen (0,0) con ZERO.
    - Mueve el codo a +15 grados, luego regresa a 0.
    - Mueve el hombro a +15 grados, luego regresa a 0.
    - Entre cada paso PIDE CONFIRMACION (Enter) para que tu controles el ritmo.

Los angulos son de MOTOR (encoder), absolutos, relativos al origen que fijaste.
15 grados es un movimiento chico y seguro para una primera prueba. Cuando
confies, sube ANGULO_PRUEBA.
"""

import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

from magnus.arm import CyberPiBackend

ANGULO_PRUEBA = 15.0    # grados de motor; chico y seguro para empezar


def paso(descripcion):
    """Pausa hasta que el usuario confirme, para controlar el ritmo."""
    input(f"\n>> {descripcion}\n   Presiona Enter para ejecutar (Ctrl+C aborta)... ")


def main():
    print("\n--- Test de humo CON MOTORES ---")
    print("Despeja el espacio alrededor del brazo antes de continuar.")
    print("Manten la mano cerca del interruptor del shield.\n")
    print("Esperando a que la CyberPi se conecte...\n")

    with CyberPiBackend() as arm:

        # 1. Fijar el origen en la pose actual.
        input(">> Pon el brazo en una pose comoda de reposo y Enter "
              "para fijar el cero... ")
        arm.zero_here()
        sh, el = arm.get_position()
        print(f"   Origen fijado. Ahora hombro={sh:.2f}  codo={el:.2f}\n")

        # 2. CODO: ir a +15, volver a 0.
        paso(f"Mover el CODO a +{ANGULO_PRUEBA}° (el hombro se queda en 0)")
        arm.move_to(shoulder=0.0, elbow=ANGULO_PRUEBA)
        sh, el = arm.get_position()
        print(f"   Ejecutado. Posicion: hombro={sh:.2f}  codo={el:.2f}")

        paso("Regresar el CODO a 0°")
        arm.move_to(shoulder=0.0, elbow=0.0)
        sh, el = arm.get_position()
        print(f"   Ejecutado. Posicion: hombro={sh:.2f}  codo={el:.2f}")

        # 3. HOMBRO: ir a +15, volver a 0.
        #    (El hombro tiene reductor 3:1; puede moverse mas despacio.)
        paso(f"Mover el HOMBRO a +{ANGULO_PRUEBA}° (el codo se queda en 0)")
        arm.move_to(shoulder=ANGULO_PRUEBA, elbow=0.0)
        sh, el = arm.get_position()
        print(f"   Ejecutado. Posicion: hombro={sh:.2f}  codo={el:.2f}")

        paso("Regresar el HOMBRO a 0°")
        arm.move_to(shoulder=0.0, elbow=0.0)
        sh, el = arm.get_position()
        print(f"   Ejecutado. Posicion: hombro={sh:.2f}  codo={el:.2f}")

        # 4. Garra, para cerrar la prueba completa.
        paso("Probar la garra (acercar y alejar el iman)")
        arm.set_gripper(True)
        time.sleep(1.0)
        arm.set_gripper(False)

        print("\n=== TEST DE HUMO CON MOTORES OK ===")
        print("Hombro, codo y garra respondieron. La cadena de control funciona.")

    print("Conexion cerrada limpiamente.\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAbortado por el usuario (Ctrl+C). "
              "Apaga el shield si el brazo quedo en mala posicion.")