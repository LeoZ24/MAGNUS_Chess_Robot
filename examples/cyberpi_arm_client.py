# cyberpi_arm_client.py — Cliente del brazo MAGNUS (lado CyberPi, PRODUCCION)
# ===========================================================================
# Pegar en el editor Python de mBlock (modo UPLOAD) y subir a la CyberPi.
# Corre en MicroPython SIN sobrescribir el firmware.
#
# Este archivo vive en el repositorio SOLO para tenerlo versionado y poder
# revisarlo junto con el resto del codigo: la CyberPi no lo importa desde aqui.
# Si lo editas en mBlock, copia el resultado de vuelta a este archivo.
#
# ARQUITECTURA: la CyberPi es CLIENTE. Se conecta al host (Mac/Pi) que corre
# el servidor (CyberPiBackend). Asi nunca necesitamos la IP de la CyberPi.
# Si el socket se cae, la CyberPi reintenta conectarse sola.
#
# PROTOCOLO (lineas de texto terminadas en \n):
#   Host -> CyberPi                 CyberPi -> Host
#   ---------------------------     -----------------------------------
#   PING                            ACK PONG
#   HOME                            ACK HOME <hombro> <codo>
#   ZERO                            ACK ZERO       (pose actual = 0,0)
#   MOVE <hombro> <codo>            ACK MOVE <hombro> <codo>  (logrados)
#   GRIPPER <0|1>                   ACK GRIPPER    (1=acerca iman, 0=aleja iman)
#   GET                             ACK POS <hombro> <codo>
#   STOP                            ACK STOP
#   (invalido / fallo)              ERR <mensaje>
#
# UNIDADES: grados de MOTOR (lo que lee el encoder), NO del eslabon. Ambas
# articulaciones son de transmision DIRECTA (no hay reductor en ninguna), asi
# que grado de motor = grado de eslabon. positions.json se graba directamente
# en grados de motor. Cero conversiones = cero bugs.

import cyberpi
import time
import usocket

# ======================= CONFIGURACION =======================

SSID   = "S24+ de Leo"     # hotspot del S24+ (2.4 GHz)
PASS   = "FamiliaZannoni4"
MAC_IP = "10.136.57.84"    # IP que imprime el CyberPiBackend al arrancar
PORT   = 5555

# --- Hardware (confirmado) ---
SHOULDER_PORT = "EM1"     # motor encoder del hombro (transmision directa)
ELBOW_PORT    = "EM2"     # motor encoder del codo   (transmision directa)
GRIPPER_PORT  = "S1"      # servo que acerca/aleja el iman N52

# --- Movimiento ---
# OJO con bajar demasiado la velocidad: el control interno del motor encoder
# es de VELOCIDAD, y a pocas RPM el PWM que aplica no vence el peso del brazo
# ni la friccion. El motor "quiere" moverse, no puede, y EM_turn devuelve el
# control con el eje a medio camino. Si el brazo se queda corto, SUBE esto.
MOVE_SPEED_RPM      = 60      # velocidad de la pasada principal
MOVE_SPEED_FINE_RPM = 40      # velocidad de las pasadas de correccion
MOVE_FINE_BELOW_DEG = 15.0    # por debajo de este error se usa la fina
TOLERANCE_DEG       = 1.0     # objetivo alcanzado si el error es menor
MOVE_MAX_PASSES     = 4       # correcciones antes de rendirse
MOVE_FAIL_DEG       = 3.0     # error final que se considera fallo -> ERR
MOVE_SETTLE_S       = 0.15    # dejar que el encoder se asiente entre pasadas

# Juego de la transmision (backlash). Si el brazo no repite al llegar a un
# angulo desde un lado o desde el otro, sube esto: el ultimo tramo entrara
# siempre en el mismo sentido (APPROACH_SIGN). 0 = desactivado.
BACKLASH_DEG  = 0.0
APPROACH_SIGN = 1             # +1 = el tramo final siempre va en positivo

# Limites de seguridad en grados de MOTOR (ajustar tras calibrar).
SHOULDER_LIM = (-2000.0, 2000.0)
ELBOW_LIM    = (-2000.0, 2000.0)

# --- Referenciado automatico (HOME) ---
# Los motores encoder NO tienen cero absoluto: el contador arranca en 0 alla
# donde este el brazo al encender. Para que positions.json signifique siempre
# lo mismo, al arrancar se busca un TOPE FISICO fijo al chasis y se declara
# cero ahi. Es el mismo procedimiento de una impresora 3D o un brazo
# industrial sin encoder absoluto.
#
# Sin topes montados todavia: pon HOMING_ENABLED = False y usa ZERO a mano.
HOMING_ENABLED     = True
HOME_POWER         = 30       # % de potencia de la pasada de busqueda
HOME_POWER_FINE    = 18       # % de la segunda pasada (mas precisa y suave)
HOME_SHOULDER_SIGN = -1       # sentido hacia el tope del hombro (calibrar)
HOME_ELBOW_SIGN    = -1       # sentido hacia el tope del codo  (calibrar)
HOME_SAMPLE_S      = 0.12     # periodo de muestreo del encoder
HOME_STILL_N       = 3        # muestras quietas seguidas = tope tocado
HOME_MIN_DELTA_DEG = 0.8      # menos que esto entre muestras = quieto
HOME_START_GRACE_S = 1.0      # arranque: no declarar tope antes de esto
HOME_BACKOFF_DEG   = 8.0      # separarse del tope entre las dos pasadas
HOME_TIMEOUT_S     = 12.0     # por eje y por pasada

# --- Garra: acerca/aleja el iman N52 ---
# Calibrar estos dos angulos empiricamente:
GRIPPER_ENGAGE_ANGLE  = 90    # iman CERCA de la pieza (la agarra)
GRIPPER_RELEASE_ANGLE = 0     # iman LEJOS de la pieza (la suelta)
GRIPPER_SETTLE_S      = 0.4   # tiempo para que el servo llegue

# Poner en False para dejar de imprimir el detalle de cada movimiento en la
# pantalla de la CyberPi una vez que el brazo este afinado.
VERBOSE = True

# =============================================================
# Nombres de API del shield mBot2: verificar en el autocompletado de mBlock
# que coincidan. Si alguno difiere, ajustar SOLO estas funciones _hw_*.
# =============================================================

def _hw_get_angle(port):
    return cyberpi.mbot2.EM_get_angle(port)

def _hw_turn(delta_deg, speed_rpm, port):
    cyberpi.mbot2.EM_turn(delta_deg, speed_rpm, port)   # RELATIVO, bloquea

def _hw_set_power(power_pct, port):
    # TODO(verificar en mBlock): potencia cruda, sin control de posicion.
    # Es lo que permite empujar contra el tope durante el referenciado.
    cyberpi.mbot2.EM_set_power(power_pct, port)

def _hw_reset_angle(port):
    cyberpi.mbot2.EM_reset_angle(port)

def _hw_stop_axis(port):
    cyberpi.mbot2.EM_stop(port)

def _hw_stop():
    cyberpi.mbot2.EM_stop("all")

def _hw_servo(angle):
    cyberpi.mbot2.servo_set(angle, GRIPPER_PORT)


def _log(text):
    if VERBOSE:
        cyberpi.console.println(text)


# ======================= MOVIMIENTO =======================

def _clamp(v, lim, name):
    lo, hi = lim
    if v < lo or v > hi:
        raise ValueError(name + " fuera de limites: " + str(v))
    return v


def _move_axis(target, port, lim, name):
    """Lleva el eje a ``target`` (grados de motor absolutos) y lo VERIFICA.

    EM_turn es relativo y bajo carga se queda corto: decelera al acercarse al
    objetivo y el ultimo tramo puede no tener par suficiente. Por eso aqui se
    repite la correccion hasta entrar en tolerancia, en vez de mandar un solo
    giro y confiar. Devuelve el angulo realmente alcanzado.
    """
    target = _clamp(target, lim, name)
    start = _hw_get_angle(port)

    # Compensacion de juego: si vinieramos "del lado contrario", pasarse un
    # poco para que el tramo final entre siempre en el mismo sentido.
    if BACKLASH_DEG > 0 and (target - start) * APPROACH_SIGN < 0:
        pre = target - APPROACH_SIGN * BACKLASH_DEG
        _hw_turn(pre - start, MOVE_SPEED_RPM, port)
        time.sleep(MOVE_SETTLE_S)

    current = start
    for _ in range(MOVE_MAX_PASSES):
        delta = target - current
        if abs(delta) <= TOLERANCE_DEG:
            break
        speed = MOVE_SPEED_FINE_RPM if abs(delta) < MOVE_FINE_BELOW_DEG else MOVE_SPEED_RPM
        _hw_turn(delta, speed, port)
        time.sleep(MOVE_SETTLE_S)
        current = _hw_get_angle(port)

    error = target - current
    if VERBOSE:
        _log("  " + name + " " + str(start) + "->" + str(current)
             + " obj " + str(target) + " err " + str(round(error, 2)))
    if abs(error) > MOVE_FAIL_DEG:
        raise ValueError(
            name + " no alcanzo " + str(target) + " (quedo en " + str(current)
            + "). Bateria baja, velocidad insuficiente o tope mecanico?")
    return current


# ======================= REFERENCIADO (HOME) =======================

def _seek_stop(port, sign, power, name):
    """Empuja el eje contra su tope y para cuando el encoder deja de cambiar.

    Deteccion de tope sin sensor: si el motor tiene potencia aplicada y el
    angulo no cambia durante varias muestras seguidas, esta apoyado.
    """
    _hw_set_power(power * sign, port)
    last = _hw_get_angle(port)
    still = 0
    moved = False
    t0 = time.time()
    try:
        while True:
            time.sleep(HOME_SAMPLE_S)
            now = _hw_get_angle(port)
            if abs(now - last) >= HOME_MIN_DELTA_DEG:
                moved = True
                still = 0
            else:
                still += 1
            last = now
            elapsed = time.time() - t0
            # No declarar tope durante el arranque (el motor tarda en romper
            # la inercia y las primeras muestras parecen "quieto").
            if still >= HOME_STILL_N and elapsed > HOME_START_GRACE_S and moved:
                return now
            if elapsed > HOME_TIMEOUT_S:
                raise ValueError(
                    "referenciado de " + name + ": no encontre el tope en "
                    + str(HOME_TIMEOUT_S) + "s. Sentido invertido o potencia baja?")
    finally:
        _hw_stop_axis(port)


def _home_axis(port, sign, name):
    """Referencia un eje en dos pasadas y declara el cero en el tope."""
    _log("home " + name + "...")
    _seek_stop(port, sign, HOME_POWER, name)          # pasada rapida
    _hw_turn(-sign * HOME_BACKOFF_DEG, MOVE_SPEED_FINE_RPM, port)
    time.sleep(MOVE_SETTLE_S)
    _seek_stop(port, sign, HOME_POWER_FINE, name)     # pasada lenta y precisa
    time.sleep(0.2)
    _hw_reset_angle(port)
    _log("  " + name + " cero fijado")


def _home_all():
    """Referencia los dos ejes. El CODO primero: se recoge sobre si mismo y
    el brazo no barre el tablero mientras el hombro busca su tope."""
    _home_axis(ELBOW_PORT, HOME_ELBOW_SIGN, "codo")
    _home_axis(SHOULDER_PORT, HOME_SHOULDER_SIGN, "hombro")


# ======================= LOGICA DE COMANDOS =======================

def handle(line):
    parts = line.split()
    if not parts:
        return None
    cmd = parts[0].upper()

    if cmd == "PING":
        return "ACK PONG"

    if cmd == "HOME":
        if not HOMING_ENABLED:
            return "ERR referenciado desactivado (HOMING_ENABLED=False)"
        _home_all()
        return "ACK HOME 0.0 0.0"

    if cmd == "ZERO":
        _hw_reset_angle(SHOULDER_PORT)
        _hw_reset_angle(ELBOW_PORT)
        return "ACK ZERO"

    if cmd == "MOVE":
        if len(parts) != 3:
            return "ERR MOVE requiere 2 argumentos"
        sh = float(parts[1])
        el = float(parts[2])
        got_sh = _move_axis(sh, SHOULDER_PORT, SHOULDER_LIM, "hombro")
        got_el = _move_axis(el, ELBOW_PORT, ELBOW_LIM, "codo")
        return "ACK MOVE " + str(got_sh) + " " + str(got_el)

    if cmd == "GRIPPER":
        if len(parts) != 2:
            return "ERR GRIPPER requiere 1 argumento"
        engaged = parts[1] == "1"
        _hw_servo(GRIPPER_ENGAGE_ANGLE if engaged else GRIPPER_RELEASE_ANGLE)
        time.sleep(GRIPPER_SETTLE_S)
        return "ACK GRIPPER"

    if cmd == "GET":
        sh = _hw_get_angle(SHOULDER_PORT)
        el = _hw_get_angle(ELBOW_PORT)
        return "ACK POS " + str(sh) + " " + str(el)

    if cmd == "STOP":
        _hw_stop()
        return "ACK STOP"

    return "ERR comando desconocido: " + cmd


# ======================= RED =======================

def conectar_wifi():
    cyberpi.console.println("WiFi: " + SSID)
    cyberpi.led.on("yellow")
    cyberpi.wifi.connect(SSID, PASS)
    while not cyberpi.wifi.is_connect():
        time.sleep(0.5)
    cyberpi.console.println("WiFi OK")
    time.sleep(3)   # dar tiempo a DHCP

def sesion():
    """Una conexion al host. Vuelve cuando se cae, para reintentar."""
    cyberpi.console.println("-> host " + MAC_IP)
    sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    sock.connect((MAC_IP, PORT))
    cyberpi.console.println("CONECTADO")
    cyberpi.led.on("green")

    buf = b""
    try:
        while True:
            data = sock.recv(128)
            if not data:
                break                      # host cerro
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode().strip()
                if not line:
                    continue
                cyberpi.console.println("> " + line)
                try:
                    resp = handle(line)
                except Exception as e:
                    _hw_stop()             # nunca dejar un motor empujando
                    resp = "ERR " + str(e)
                if resp:
                    cyberpi.console.println("< " + resp)
                    sock.send((resp + "\n").encode())
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ======================= ARRANQUE =======================

cyberpi.console.clear()
cyberpi.console.println("MAGNUS arm client")
conectar_wifi()

while True:
    try:
        sesion()
    except Exception as e:
        cyberpi.console.println("Sin host, reintento")
        cyberpi.led.on("red")
    time.sleep(2)   # esperar antes de reintentar
