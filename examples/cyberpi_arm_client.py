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
#   LIMITS                          ACK LIMITS <sh_lo> <sh_hi> <el_lo> <el_hi>
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

# ⚠️ ESTE ARCHIVO ESTA EN UN REPOSITORIO PUBLICO: no pongas aqui la clave de
# verdad de tu red. Rellena estos tres valores EN MBLOCK, justo antes de subir
# el programa a la placa, y deja los marcadores al copiar el archivo de vuelta.
SSID   = "TU_RED_WIFI"       # hotspot de 2.4 GHz (la CyberPi no ve 5 GHz)
PASS   = "TU_CLAVE_WIFI"     # NO la subas al repositorio
MAC_IP = "0.0.0.0"           # IP que imprime el CyberPiBackend al arrancar
PORT   = 5555

# --- Hardware (confirmado) ---
SHOULDER_PORT = "EM1"     # motor encoder del hombro (transmision directa)
ELBOW_PORT    = "EM2"     # motor encoder del codo   (transmision directa)
GRIPPER_PORT  = "S1"      # servo que acerca/aleja el iman N52

# --- Movimiento ---
# El control interno del motor encoder es de VELOCIDAD, no de par: a pocas RPM
# el PWM que aplica no vence el peso del brazo ni la friccion. El motor
# "quiere" moverse, no puede, y EM_turn devuelve el control con el eje a medio
# camino. Por eso cada movimiento tiene DOS FASES:
#
#   1. TRAMO GRUESO (_drive_turns): EM_turn, que tiene rampa y va suave. Si una
#      pasada no mueve el eje, se SUBE la velocidad (mas RPM pedidas = mas PWM =
#      mas par). Bajarla al acercarse al objetivo, que es lo intuitivo, es justo
#      lo que dejaba el hombro clavado a 15 grados del destino.
#   2. ULTIMO TRAMO (_creep_to): impulsos de POTENCIA cruda, la misma tecnica
#      con la que HOME empuja contra el tope. La potencia no depende del error,
#      asi que hay par aunque falte medio grado. Como no sabe frenar sola, se
#      aplica en impulsos cortos leyendo el encoder entre uno y otro.
MOVE_SPEED_RPM      = 60      # velocidad de la pasada gruesa
MOVE_SPEED_STEP_RPM = 30      # cuanto sube si una pasada se queda corta
MOVE_SPEED_MAX_RPM  = 120     # techo de esa escalada
MOVE_SPEED_FINE_RPM = 40      # tramos sin carga (retrocesos del referenciado)
TOLERANCE_DEG       = 1.0     # objetivo alcanzado si el error es menor
MOVE_MAX_PASSES     = 4       # pasadas gruesas antes de pasar a los impulsos
MOVE_PROGRESS_RATIO = 0.6     # fraccion del tramo pedido que una pasada debe
                              # cubrir para considerarla buena. El fallo real no
                              # es que el eje no se mueva, es que se queda a un
                              # cuarto de camino: con "no se movio nada" no se
                              # detectaba nunca y la velocidad no subia.
MOVE_FAIL_DEG       = 3.0     # error final que se considera fallo -> ERR
MOVE_SETTLE_S       = 0.15    # dejar que el encoder se asiente entre pasadas
MOVE_SAG_DEG        = 2.0     # repaso final: cuanto puede ceder un eje mientras
                              # se mueve el otro antes de volver a corregirlo

# Orden de los ejes dentro de un MOVE. Con el hombro primero el brazo gira
# sobre su base y despues se despliega: se ve mejor y ademas el codo sigue
# recogido durante el giro, asi que barre menos tablero. El repaso final
# corrige al hombro si cede al desplegarse el codo.
MOVE_SHOULDER_FIRST = True

# --- Impulsos de potencia del ultimo tramo ---
# La potencia cruda da par pero no sabe frenar: si el impulso dura de mas, el
# eje se pasa de largo. Como no sabemos a que velocidad gira este brazo a una
# potencia dada (depende de la pose, del peso y de la bateria), NO se fija la
# duracion a ojo: se MIDE. Cada impulso deja un ritmo en grados/segundo, y el
# siguiente dura lo justo para cubrir CREEP_AIM de lo que falta. Apuntar a
# menos del 100% es lo que hace que el eje se acerque sin cruzar el objetivo.
#
# Si el brazo se queda corto al final, sube CREEP_POWER_MAX; si se pasa de
# largo, baja CREEP_AIM o CREEP_PULSE_MIN_S.
CREEP_POWER         = 18      # % de potencia del primer impulso
CREEP_POWER_STEP    = 6       # subida cuando un impulso no mueve el eje
CREEP_POWER_MAX     = 40      # techo: por encima no es falta de par
CREEP_AIM           = 0.6     # a que fraccion de lo que falta apunta el impulso
CREEP_PULSE_MIN_S   = 0.05    # impulso mas corto (y el que mide el ritmo)
CREEP_PULSE_MAX_S   = 0.20    # nunca empujar mas de esto sin volver a mirar
CREEP_REST_S        = 0.06    # pausa para que el encoder se asiente
CREEP_MIN_DELTA_DEG = 0.3     # impulso que mueve menos que esto = falta par
CREEP_MAX_PULSES    = 30      # cota dura: nunca un bucle sin salida
CREEP_MAX_FLIPS     = 4       # veces que puede cruzar el objetivo antes de dejarlo

# Juego de la transmision (backlash). Si el brazo no repite al llegar a un
# angulo desde un lado o desde el otro, sube esto: el ultimo tramo entrara
# siempre en el mismo sentido (APPROACH_SIGN). 0 = desactivado.
BACKLASH_DEG  = 0.0
APPROACH_SIGN = 1             # +1 = el tramo final siempre va en positivo

# --- Limites de seguridad (grados de MOTOR) ---
# ⚠️ REGLA QUE CUESTA UN BRAZO SI SE OLVIDA: al referenciar, el cero queda EN
# el tope, asi que TODO el recorrido util esta del lado CONTRARIO al sentido
# de busqueda. Si un eje busca su tope en sentido positivo, sus angulos validos
# son NEGATIVOS, y mandarle +30 lo empuja contra el tope.
#
# Por eso los limites NO se escriben a mano: se derivan del sentido de
# referenciado, para que no se puedan contradecir. Lo unico que hay que medir
# es cuanto recorrido tiene cada eje desde su tope.
SHOULDER_TRAVEL_DEG = 300.0   # recorrido util del hombro desde su tope
ELBOW_TRAVEL_DEG    = 300.0   # recorrido util del codo desde su tope


def _limits_from_home(home_sign, travel):
    """Rango valido de un eje: del cero hacia el lado opuesto al tope."""
    if home_sign < 0:            # busca en negativo -> se trabaja en positivo
        return (0.0, travel)
    return (-travel, 0.0)        # busca en positivo -> se trabaja en negativo

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
HOME_SHOULDER_SIGN = -1       # sentido hacia el tope del hombro (calibrado)
HOME_ELBOW_SIGN    = 1        # sentido hacia el tope del codo  (calibrado)
HOME_SAMPLE_S      = 0.12     # periodo de muestreo del encoder
HOME_STILL_N       = 3        # muestras quietas seguidas = tope tocado
HOME_MIN_DELTA_DEG = 0.8      # menos que esto entre muestras = quieto
HOME_START_GRACE_S = 1.0      # arranque: no declarar tope antes de esto
HOME_BACKOFF_DEG   = 8.0      # separarse del tope entre las dos pasadas
HOME_TIMEOUT_S     = 12.0     # por eje y por pasada
# Tras tocar el tope, separarse esto y declarar el cero AHI. Asi la posicion 0
# no deja el motor apoyado contra el tope forzando la transmision, y "volver a
# 0" es una orden segura.
HOME_ZERO_OFFSET_DEG = 5.0

# Los limites se calculan cuando ya se conocen los sentidos de referenciado.
SHOULDER_LIM = _limits_from_home(HOME_SHOULDER_SIGN, SHOULDER_TRAVEL_DEG)
ELBOW_LIM    = _limits_from_home(HOME_ELBOW_SIGN, ELBOW_TRAVEL_DEG)

# --- Garra: acerca/aleja el iman N52 ---
# Calibrar estos dos angulos empiricamente al conectar S1. Son posiciones
# ABSOLUTAS, comunes a todas las piezas: recoger y volver al reposo para soltar.
# Hombro y codo solo sitúan el brazo en la casilla; no controlan la altura.
GRIPPER_ENGAGE_ANGLE  = 90    # iman CERCA de la pieza (la agarra)
GRIPPER_RELEASE_ANGLE = 0     # iman LEJOS de la pieza (la suelta)
GRIPPER_SETTLE_S      = 0.4   # tiempo para que el servo llegue

# Segundos que se espera al hotspot antes de rendirse y reintentar. Sin este
# limite la placa se cuelga esperando Wi-Fi y cuesta subirle un programa nuevo.
WIFI_TIMEOUT_S = 20

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
        raise ValueError(
            name + " fuera de limites: " + str(v) + " no esta en ["
            + str(lo) + ", " + str(hi) + "]. Recuerda que el recorrido util va"
            + " del lado contrario al tope.")
    return v


def _drive_turns(target, port, current, name):
    """Tramo grueso con EM_turn, subiendo la velocidad si una pasada se queda
    corta.

    EM_turn es RELATIVO y bajo carga no cubre el giro entero, asi que se repite
    la correccion en vez de mandar un solo giro y confiar. La clave esta en el
    criterio: una pasada que cubre menos de MOVE_PROGRESS_RATIO de lo pedido no
    es un eje trabado, es un eje al que a esa velocidad no le llega el par, y la
    siguiente pasada va mas RAPIDA (mas PWM). OJO: el eje suele avanzar algo (un
    cuarto del camino), asi que "no se ha movido nada" no lo detecta; bajar la
    velocidad al acercarse al objetivo, que es lo intuitivo, lo empeora.

    Devuelve el angulo alcanzado; los ultimos grados los cierra _creep_to.
    """
    speed = MOVE_SPEED_RPM
    for _ in range(MOVE_MAX_PASSES):
        delta = target - current
        if abs(delta) <= TOLERANCE_DEG:
            break
        _hw_turn(delta, speed, port)
        time.sleep(MOVE_SETTLE_S)
        previous = current
        current = _hw_get_angle(port)
        # Una pasada "buena" cubre casi todo lo que se le pidio. Si se queda a
        # un cuarto de camino no es que el eje este trabado: es que a esa
        # velocidad no hay par. La siguiente va mas rapida.
        if abs(current - previous) < abs(delta) * MOVE_PROGRESS_RATIO:
            if speed >= MOVE_SPEED_MAX_RPM:
                break          # ya va al maximo: que lo termine _creep_to
            speed = min(speed + MOVE_SPEED_STEP_RPM, MOVE_SPEED_MAX_RPM)
            _log("  " + name + " corto, subo a " + str(speed) + " rpm")
    return current


def _creep_to(target, port, name):
    """Cierra el ultimo tramo a impulsos de POTENCIA cruda.

    Aqui no se usa EM_turn a proposito: pedir "gira 2 grados" se traduce en
    muy pocas RPM, y a pocas RPM el PWM no vence el peso del brazo. La potencia
    cruda no depende del error (es la misma con la que HOME empuja contra el
    tope), asi que hay par aunque falte medio grado. A cambio no sabe frenar
    sola: se aplica en impulsos cortos, leyendo el encoder entre uno y otro.

    Si un impulso no mueve el eje, sube la potencia; si se pasa de largo, vuelve
    a la minima. Siempre termina: CREEP_MAX_PULSES acota el bucle.
    """
    current = _hw_get_angle(port)
    power = CREEP_POWER
    rate = 0.0          # grados/segundo medidos; 0 = todavia no se sabe
    last_sign = 0
    flips = 0
    try:
        for _ in range(CREEP_MAX_PULSES):
            error = target - current
            if abs(error) <= TOLERANCE_DEG:
                break
            sign = 1 if error > 0 else -1
            if last_sign != 0 and sign != last_sign:
                # Hemos cruzado el objetivo: bajar a la potencia minima para no
                # quedarnos oscilando a un lado y otro. El ritmo medido SI se
                # conserva: es lo unico que evita repetir el impulso que se
                # paso de largo.
                flips += 1
                if flips > CREEP_MAX_FLIPS:
                    break
                power = CREEP_POWER
            last_sign = sign

            # Duracion del impulso: la que hace falta, al ritmo medido en el
            # impulso anterior, para cubrir CREEP_AIM de lo que queda. Sin
            # medida todavia, el mas corto posible (que es el que la toma).
            if rate > 0.0:
                pulse = abs(error) * CREEP_AIM / rate
                pulse = max(CREEP_PULSE_MIN_S, min(pulse, CREEP_PULSE_MAX_S))
            else:
                pulse = CREEP_PULSE_MIN_S

            _hw_set_power(power * sign, port)
            time.sleep(pulse)
            _hw_stop_axis(port)
            time.sleep(CREEP_REST_S)

            previous = current
            current = _hw_get_angle(port)
            moved = abs(current - previous)
            if moved < CREEP_MIN_DELTA_DEG:
                # El impulso no ha movido nada: falta par, no duracion.
                if power >= CREEP_POWER_MAX:
                    break       # ni a tope se mueve: el problema no es el PWM
                power = min(power + CREEP_POWER_STEP, CREEP_POWER_MAX)
                rate = 0.0      # a otra potencia, el ritmo anterior no vale
                _log("  " + name + " impulso a " + str(power) + "%")
            else:
                # Incluye lo que el eje rueda tras cortar la potencia, asi que
                # el ritmo sale algo alto y los impulsos, algo cortos. Mejor
                # quedarse corto y repetir que pasarse.
                rate = moved / pulse
    finally:
        _hw_stop_axis(port)     # que un fallo nunca deje el motor empujando
    return current


def _move_axis(target, port, lim, name):
    """Lleva el eje a ``target`` (grados de motor absolutos) y lo VERIFICA.

    Dos fases: el tramo grueso con EM_turn y, si aun falta, el ultimo tramo a
    impulsos de potencia. Devuelve el angulo realmente alcanzado.
    """
    target = _clamp(target, lim, name)
    start = _hw_get_angle(port)

    # Compensacion de juego: si vinieramos "del lado contrario", pasarse un
    # poco para que el tramo final entre siempre en el mismo sentido.
    if BACKLASH_DEG > 0 and (target - start) * APPROACH_SIGN < 0:
        pre = target - APPROACH_SIGN * BACKLASH_DEG
        _hw_turn(pre - start, MOVE_SPEED_RPM, port)
        time.sleep(MOVE_SETTLE_S)

    current = _drive_turns(target, port, _hw_get_angle(port), name)
    if abs(target - current) > TOLERANCE_DEG:
        current = _creep_to(target, port, name)

    error = target - current
    if VERBOSE:
        _log("  " + name + " " + str(start) + "->" + str(current)
             + " obj " + str(target) + " err " + str(round(error, 2)))
    if abs(error) > MOVE_FAIL_DEG:
        raise ValueError(
            name + " no alcanzo " + str(target) + " (quedo en " + str(current)
            + "). Bateria baja, potencia insuficiente o tope mecanico?")
    return current


def _move_both(shoulder, elbow):
    """Mueve los dos ejes en el orden configurado y repasa el resultado.

    El orden importa dos veces. Estetico: con el hombro primero el brazo gira
    sobre su base y luego se despliega (y de paso barre menos tablero, porque
    el codo sigue recogido durante el giro). Y fisico: mover el segundo eje
    cambia el par que aguanta el primero — al desplegar el codo el hombro tiene
    que sostener mas brazo — asi que el primero puede ceder unos grados justo
    despues de darlo por bueno. Por eso al final se releen los dos encoders y
    se corrige el que se haya ido.

    Devuelve siempre ``(hombro, codo)``, sea cual sea el orden de ejecucion.
    """
    shoulder_axis = (shoulder, SHOULDER_PORT, SHOULDER_LIM, "hombro")
    elbow_axis = (elbow, ELBOW_PORT, ELBOW_LIM, "codo")
    if MOVE_SHOULDER_FIRST:
        order = (shoulder_axis, elbow_axis)
    else:
        order = (elbow_axis, shoulder_axis)

    # Comprobar los DOS angulos antes de mover nada: si el segundo esta fuera
    # de limites, el brazo ya se habria movido a medias y la jugada se queda a
    # mitad con la pieza en el aire. Mejor fallar en seco.
    for target, port, lim, name in order:
        _clamp(target, lim, name)

    reached = {}
    for target, port, lim, name in order:
        reached[name] = _move_axis(target, port, lim, name)

    # Repaso final. El umbral es MOVE_SAG_DEG y no TOLERANCE_DEG para no gastar
    # tiempo persiguiendo el ruido del encoder en cada jugada.
    for target, port, lim, name in order:
        if abs(target - _hw_get_angle(port)) > MOVE_SAG_DEG:
            _log("  " + name + " cedio, repaso")
            reached[name] = _move_axis(target, port, lim, name)

    return reached["hombro"], reached["codo"]


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
    """Referencia un eje en dos pasadas y declara el cero cerca del tope.

    El cero NO queda exactamente en el tope sino ``HOME_ZERO_OFFSET_DEG``
    separado de el: asi la posicion 0 es una orden segura y no deja el motor
    apoyado forzando la transmision.
    """
    _log("home " + name + "...")
    _seek_stop(port, sign, HOME_POWER, name)          # pasada rapida
    _hw_turn(-sign * HOME_BACKOFF_DEG, MOVE_SPEED_FINE_RPM, port)
    time.sleep(MOVE_SETTLE_S)
    _seek_stop(port, sign, HOME_POWER_FINE, name)     # pasada lenta y precisa
    time.sleep(0.2)
    # Separarse del tope ANTES de fijar el cero.
    _hw_turn(-sign * HOME_ZERO_OFFSET_DEG, MOVE_SPEED_FINE_RPM, port)
    time.sleep(MOVE_SETTLE_S)
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
        sh = _hw_get_angle(SHOULDER_PORT)
        el = _hw_get_angle(ELBOW_PORT)
        return "ACK HOME " + str(sh) + " " + str(el)

    if cmd == "LIMITS":
        # Para que el host sepa hacia que lado puede mover cada eje sin
        # tener que adivinar el sentido de referenciado.
        return ("ACK LIMITS " + str(SHOULDER_LIM[0]) + " " + str(SHOULDER_LIM[1])
                + " " + str(ELBOW_LIM[0]) + " " + str(ELBOW_LIM[1]))

    if cmd == "ZERO":
        _hw_reset_angle(SHOULDER_PORT)
        _hw_reset_angle(ELBOW_PORT)
        return "ACK ZERO"

    if cmd == "MOVE":
        if len(parts) != 3:
            return "ERR MOVE requiere 2 argumentos"
        got_sh, got_el = _move_both(float(parts[1]), float(parts[2]))
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
    """Conecta al hotspot. Devuelve True si lo consiguio, False si no.

    ⚠️ CON TIEMPO LIMITE A PROPOSITO. Un bucle "hasta que conecte" sin salida
    deja la placa colgada si el hotspot no esta encendido, y entonces cuesta
    que mBlock recupere el puerto para subir un programa nuevo. Mejor rendirse,
    avisar en pantalla y reintentar desde el bucle principal.
    """
    cyberpi.console.println("WiFi: " + SSID)
    cyberpi.led.on("yellow")
    cyberpi.wifi.connect(SSID, PASS)
    t0 = time.time()
    while not cyberpi.wifi.is_connect():
        if time.time() - t0 > WIFI_TIMEOUT_S:
            cyberpi.console.println("Sin WiFi, reintento")
            cyberpi.led.on("red")
            return False
        time.sleep(0.5)
    cyberpi.console.println("WiFi OK")
    time.sleep(3)   # dar tiempo a DHCP
    return True

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

# El Wi-Fi se reintenta DENTRO del bucle: asi la placa nunca se queda
# atascada antes de llegar a un punto donde se la puede interrumpir.
while True:
    try:
        if cyberpi.wifi.is_connect() or conectar_wifi():
            sesion()
    except Exception as e:
        cyberpi.console.println("Sin host, reintento")
        cyberpi.led.on("red")
    _hw_stop()          # que un fallo de red nunca deje un motor empujando
    time.sleep(2)       # esperar antes de reintentar
