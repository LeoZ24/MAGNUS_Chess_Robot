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
