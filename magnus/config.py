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

# Piezas de ajedrez: IDs 0-31 (mapeo oficial en magnus/vision/piece_map.py).
ARUCO_IDS_PIECES: range = range(0, 32)

# Esquinas del tablero, para la homografía tablero↔cámara.  El orden define a
# qué esquina física corresponde cada ID (ver magnus/vision/board_pose.py):
#   40 -> esquina de a8   41 -> esquina de h8
#   43 -> esquina de a1   42 -> esquina de h1
ARUCO_IDS_BOARD_CORNERS: tuple[int, int, int, int] = (40, 41, 42, 43)

# Marcador en el extremo del brazo (rastreo/corrección V2, aún no implementada).
ARUCO_ID_ARM: int = 44

# Detecciones consecutivas necesarias para confirmar un marcador (enclavamiento).
DETECTION_CONFIRM_N: int = 5
