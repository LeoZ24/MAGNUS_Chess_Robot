"""Constantes físicas y de configuración de MAGNUS.

Única fuente de verdad para todas las constantes del sistema: dimensiones del
tablero y las piezas, imanes, rangos de ID de los marcadores ArUco y parámetros
de detección.  **Ningún módulo debe hardcodear estos valores** — siempre
importarlos desde aquí.

Las medidas provienen de las especificaciones físicas del proyecto (ver
README.md § "Especificaciones físicas críticas").
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Tablero
# --------------------------------------------------------------------------- #
SQUARE_SIZE_MM: float = 32.0            # lado de cada casilla en mm
BOARD_SQUARES: int = 8                  # tablero de 8×8 casillas
BOARD_SIZE_MM: float = SQUARE_SIZE_MM * BOARD_SQUARES  # 256 mm de lado

# Imanes embutidos en el centro de cada casilla (mantienen las piezas fijas).
SQUARE_MAGNET_D_MM: float = 6.0
SQUARE_MAGNET_H_MM: float = 3.0

# --------------------------------------------------------------------------- #
# Piezas
# --------------------------------------------------------------------------- #
PIECE_DIAMETER_MM: float = 22.5         # piezas circulares de tapa plana
PIECE_MAGNET_D_MM: float = 10.0         # imán en la base de cada pieza
PIECE_MAGNET_H_MM: float = 2.0

# --------------------------------------------------------------------------- #
# Brazo — imán de agarre
# --------------------------------------------------------------------------- #
ARM_MAGNET_D_MM: float = 12.0
ARM_MAGNET_H_MM: float = 3.0
ARM_MAGNET_GRADE: str = "N52"           # muy fuerte: puede influir en piezas adyacentes

# Zonas especiales de la tabla de posiciones del brazo (además de las 64
# casillas).  Son claves del positions.json:
ZONE_DISCARD: str = "discard"           # zona de descarte de piezas capturadas
ZONE_EXCHANGE: str = "exchange"         # zona de intercambio para promociones

# --------------------------------------------------------------------------- #
# ArUco — un mismo diccionario, tres roles con rangos de ID separados
# --------------------------------------------------------------------------- #
ARUCO_DICT_NAME: str = "DICT_4X4_50"

# Piezas de ajedrez: el marcador identifica el TIPO de pieza (tipo + color),
# no la pieza individual — todos los peones blancos llevan el mismo ID, etc.
# Estos son los 12 IDs físicamente impresos (mapeo oficial ID -> símbolo FEN
# en magnus/vision/piece_map.py):
#
#   blancas: 0=peón  1=caballo  2=alfil  6=torre  8=dama  9=rey
#   negras: 12=peón 15=caballo 16=alfil 18=torre 21=dama 23=rey
ARUCO_IDS_PIECES: frozenset[int] = frozenset({0, 1, 2, 6, 8, 9, 12, 15, 16, 18, 21, 23})

# Esquinas del tablero, para la homografía tablero↔cámara.  El orden define a
# qué esquina física corresponde cada ID (ver magnus/vision/board_pose.py):
#   40 -> esquina de a8   41 -> esquina de h8
#   43 -> esquina de a1   42 -> esquina de h1
ARUCO_IDS_BOARD_CORNERS: tuple[int, int, int, int] = (40, 41, 42, 43)

# Marcador en el extremo del brazo (rastreo/corrección V2, aún no implementada).
ARUCO_ID_ARM: int = 44

# Detecciones consecutivas necesarias para confirmar un marcador (enclavamiento).
DETECTION_CONFIRM_N: int = 5

# Como varios marcadores comparten ID (todos los peones blancos son el ID 0),
# el enclavamiento rastrea cada instancia POR POSICIÓN: una detección se asocia
# a una pista existente del mismo ID si está a menos de
# `lado_del_marcador × DETECTION_MATCH_RADIUS_FACTOR` píxeles.  Con marcadores
# de ~14 mm y casillas de 32 mm, 1.5 mantiene separadas dos piezas iguales en
# casillas adyacentes y a la vez tolera el jitter de la cámara.
DETECTION_MATCH_RADIUS_FACTOR: float = 1.5

# Un marcador confirmado que deja de verse este número de frames consecutivos
# se olvida (la pieza se movió o se retiró).  0 = no olvidar nunca.
DETECTION_FORGET_FRAMES: int = 20

# Las esquinas son ESTÁTICAS: ni la cámara ni el tablero se mueven durante la
# partida, así que una esquina confirmada se recuerda en su última posición
# aunque una torre la tape (0 = no olvidar nunca).  Si de verdad se mueve la
# cámara o el tablero hay que llamar a DetectionLatch.reset() / BoardVisionNode
# .reset_board_pose() (tecla R en el demo de visión).
DETECTION_FORGET_FRAMES_CORNERS: int = 0

# Tolerancia al borde del tablero (mm).  Un marcador de pieza cuyo centro cae
# fuera del área de juego pero a menos de esta distancia se asigna a la casilla
# del borde: absorbe el error de la homografía y de las esquinas mal centradas.
# Más allá, la pieza está FUERA del tablero y se ignora — es lo normal para las
# piezas capturadas en la zona de descarte o para marcadores sueltos en la mesa.
# Debe quedar muy por debajo de media casilla (16 mm) para no "meter" en el
# tablero marcadores que están al lado.
BOARD_EDGE_TOLERANCE_MM: float = 6.0


# --------------------------------------------------------------------------- #
# Voz y comentarios de la partida
# --------------------------------------------------------------------------- #
# Umbrales (en centipeones) para etiquetar la jugada del humano.  Se comparan
# contra Δ = eval_después − eval_antes, medida SIEMPRE a favor del robot: si la
# posición del robot mejora mucho tras la jugada del rival, el rival se equivocó.
# Son generosos a propósito: es preferible callar que acusar de un error que no
# lo fue delante del jurado.
COMMENT_BLUNDER_CP: int = 300        # error grave
COMMENT_MISTAKE_CP: int = 150        # error
COMMENT_INACCURACY_CP: int = 50      # imprecisión
COMMENT_GOOD_CP: int = 50            # buena jugada (Δ negativo de esta magnitud)
COMMENT_GREAT_CP: int = 150          # muy buena jugada

# A partir de esta ventaja se considera que alguien está ganando.
COMMENT_ADVANTAGE_CP: int = 200
# Jugadas mínimas entre dos comentarios de "quién va ganando" (no cansar).
COMMENT_ADVANTAGE_EVERY_MOVES: int = 4

# Voz de Piper: elegida de oído con examples/audition_voices.py.
# El sufijo del nombre es el NIVEL DE CALIDAD del modelo y se nota: `x_low` y
# `low` van a 16 kHz y suenan apagadas, `medium` sube a 22 kHz y `high` usa
# además una red mayor.  Si cambias de voz, prueba primero las `-high`.
# Los modelos NO están en el repo (pesan 60-110 MB): se descargan con
#   python3 -m piper.download_voices es_ES-davefx-medium --data-dir voices/
VOICE_PIPER_MODEL: str = "es_ES-davefx-medium"

# Velocidad del habla: 1.0 = exactamente la cadencia natural del modelo.
# NO se aplica ningún efecto de audio a la voz (ni filtros, ni tono, ni
# resampleo): se busca que suene lo más humana y clara posible.  Bajarlo la
# acelera y subirlo la ralentiza, pero alejarse de 1.0 le quita naturalidad.
VOICE_LENGTH_SCALE: float = 1.0
# Voz del comando `say` de macOS (respaldo cuando no hay modelo de Piper).
# MAGNUS habla en masculino, así que por defecto se usa una voz masculina.
# Habituales en español: Juan (es_MX), Jorge (es_ES), Diego (es_AR); femeninas:
# Paulina (es_MX), Mónica (es_ES).  Si la configurada no está instalada, el
# backend busca otra en español en vez de fallar — lista las tuyas con:
#   say -v '?' | grep es_
# En Ajustes > Accesibilidad > Contenido hablado se descargan las "mejoradas".
VOICE_MACOS_VOICE: str = "Juan"
VOICE_MACOS_RATE: int = 185          # palabras por minuto

# Frases en cola como máximo; si se llena, se descartan las más viejas para no
# quedarse hablando de jugadas que ya pasaron.  Ahora el robot habla bastante
# (narra ambas jugadas y comenta), así que la cola aguanta un poco más.
VOICE_QUEUE_MAX: int = 5

# Narrar también las jugadas del rival ("Moviste el peón de e dos a e cuatro").
# Hace al robot mucho más conversador, pero repetir en voz alta lo que el rival
# acaba de hacer delante de él resulta redundante en una partida real, así que
# viene desactivado.  Con esto en False el robot sigue reaccionando a las
# capturas y a los jaques, que sí aportan.
VOICE_ANNOUNCE_HUMAN_MOVES: bool = False

# Avisar por voz de problemas en el tablero (pieza en la mano, jugada ilegal,
# posición irreconocible).
#
# Desactivado por defecto: con poca luz la detección de algunas piezas parpadea
# — se detectan, se pierden un par de segundos y vuelven —, y cada parpadeo se
# interpretaba como una pieza retirada del tablero.  El robot acababa avisando
# de faltas que no existían.  La lógica sigue disponible (`diagnose_placement`)
# para reactivarla cuando la iluminación sea fiable.
VOICE_WARN_BOARD_PROBLEMS: bool = False

# Segundos sin que el rival mueva antes de recordárselo con amabilidad.
# 0 desactiva el recordatorio.
VOICE_IDLE_PROMPT_S: float = 45.0
