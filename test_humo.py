"""
test_humo_brazo.py — Prueba de humo del brazo MAGNUS (host ↔ CyberPi).
=====================================================================
Verifica que la tuberia host <-> CyberPi funciona de punta a punta,
SIN mover los motores del brazo (solo la garra y lectura de posicion).
Asi confirmas la conexion sin arriesgar chocar el brazo.

COMO CORRERLO (desde VSCode):
    1. Enciende el shield del mBot2 (interruptor en ON) con el
       cyberpi_arm_client.py ya subido a la CyberPi.
    2. Coloca este archivo en la RAIZ del proyecto MAGNUS
       (la carpeta que contiene la carpeta 'magnus/').
    3. Abre una terminal en VSCode (menu Terminal > New Terminal) y corre:
           python3 test_humo_brazo.py
       (o el boton "Run" de arriba a la derecha con el archivo abierto)
    4. Mira el log de la terminal Y la pantalla de la CyberPi.

Que esperar:
    - El log imprime la IP del host. Debe coincidir con MAC_IP del cliente.
    - La pantalla de la CyberPi pasa a "CONECTADO" (verde).
    - Se imprime la posicion actual (ACK POS ...).
    - El iman se acerca y luego se aleja (2 movimientos de la garra).
    - Termina con "TEST DE HUMO OK".
"""

import logging
import time

# Log detallado para ver cada paso (incluye la IP del host).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

from magnus.arm import CyberPiBackend


def main():
    print("\n--- Iniciando test de humo del brazo ---")
    print("Esperando a que la CyberPi se conecte...\n")

    # 'with' llama a connect() al entrar y disconnect() al salir.
    with CyberPiBackend() as arm:

        # 1. Leer la posicion actual del encoder (no mueve nada).
        sh, el = arm.get_position()
        print(f"\n>> Posicion actual del brazo: hombro={sh}  codo={el}\n")

        # 2. Probar la garra: acercar el iman.
        print(">> Acercando el iman (GRIPPER 1)...")
        arm.set_gripper(True)
        time.sleep(1.0)

        # 3. Probar la garra: alejar el iman.
        print(">> Alejando el iman (GRIPPER 0)...")
        arm.set_gripper(False)
        time.sleep(1.0)

        print("\n=== TEST DE HUMO OK ===")
        print("La tuberia host <-> CyberPi funciona de punta a punta.")

    print("Conexion cerrada limpiamente.\n")


if __name__ == "__main__":
    main()