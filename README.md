# MAGNUS — Robot de Ajedrez Autónomo

> Proyecto de feria científica (PRONAFECYT 2026) construido por un estudiante de 16 años.
> Un robot que detecta el estado del tablero con visión artificial, calcula la mejor
> jugada con un motor de ajedrez y la ejecuta físicamente con un brazo articulado.

---

## Índice

1. [Arquitectura del sistema](#arquitectura-del-sistema)
2. [Hardware](#hardware)
3. [Software — módulos](#software--módulos)
4. [Especificaciones físicas críticas](#especificaciones-físicas-críticas)
5. [Estrategia de movimiento del brazo](#estrategia-de-movimiento-del-brazo)
6. [Sistema de localización con ArUco](#sistema-de-localización-con-aruco)
7. [Protocolo de mensajes entre módulos](#protocolo-de-mensajes-entre-módulos)
8. [Estado actual y hoja de ruta](#estado-actual-y-hoja-de-ruta)
9. [Instalación](#instalación)
10. [Ejecución rápida](#ejecución-rápida)
11. [Tests](#tests)
12. [Convenciones del proyecto](#convenciones-del-proyecto)

---

## Arquitectura del sistema

MAGNUS sigue una arquitectura modular inspirada en ROS2: nodos independientes que
se comunican mediante mensajes tipados. Cada nodo puede desarrollarse, testearse y
reemplazarse sin afectar a los demás.

```
┌─────────────────┐    PositionRequest(FEN)    ┌──────────────────┐    MoveResponse    ┌──────────────────────┐
│   NODO VISIÓN   │ ──────────────────────────▶│  NODO DEL ENGINE │ ──────────────────▶│  NODO DEL BRAZO      │
│  (ArUco/OpenCV) │                            │  (Stockfish/UCI) │                    │  (CyberPi / mBot2)   │
│                 │                            │                  │                    │                      │
│  Tablero físico │                            │  Calcula jugada  │                    │  Busca movimiento    │
│  → FEN string   │                            │  + metadatos     │                    │  pregrabado y lo     │
│                 │                            │                  │                    │  reproduce           │
└─────────────────┘                            └──────────────────┘                    └──────────────────────┘
       ▲                                                                                          │
  Cámara cenital                                                                          (V2 futuro) ▼
  (MacBook Air M1 /                                                                      Corrección por visión
   Raspberry Pi 4/5)                                                                      del marcador del brazo
```

### Módulos y estado

| Módulo            | Ruta                  | Estado              | Descripción |
|-------------------|-----------------------|---------------------|-------------|
| **Mensajes/Core** | `magnus/core/`        | ✅ Completo          | Contratos de datos entre nodos (PositionRequest, MoveResponse) |
| **Engine**        | `magnus/engine/`      | ✅ Completo          | FEN → jugada con metadatos, via Stockfish UCI |
| **Visión**        | `magnus/vision/`      | ✅ Software completo | Detección → homografía → FEN exacta (turno/enroque por inferencia); falta validar con cámara/tablero reales |
| **Brazo robótico**| `magnus/arm/`         | 🔶 Software listo    | Secuencias pregrabadas implementadas y testeadas con backend falso; falta hardware (CyberPi + tabla de posiciones real) |

---

## Hardware

### Computación

| Dispositivo               | Rol                                    | Estado          |
|---------------------------|-----------------------------------------|-----------------|
| MacBook Air M1             | Desarrollo y procesamiento visual      | En uso          |
| Raspberry Pi 4 o 5         | Computación embebida (producción)      | Futuro          |
| CyberPi (placa mBot2)      | Control de motores/servos del brazo    | Pendiente integración Python |

### Cámara

- Montaje cenital (mirando hacia abajo sobre el tablero)
- Calibración de cámara **pendiente** (necesaria para la homografía de las
  esquinas del tablero y el rastreo del marcador del brazo)
- Se usa con OpenCV y el módulo `cv2.aruco`

### Tablero de ajedrez

- **Tamaño de casilla:** 32 mm × 32 mm
- **Área de juego total:** 256 mm × 256 mm (8×8 casillas)
- **Imanes en casillas:** cilíndrico 6×3 mm, uno por casilla, embutido en el
  centro — mantienen las piezas posicionadas
- **Marcadores ArUco en las 4 esquinas del tablero** (nuevo): permiten calcular
  la homografía tablero↔cámara, necesaria para la futura corrección de
  posición del brazo (ver [Sistema de localización con ArUco](#sistema-de-localización-con-aruco))
- Material: accesible / imprimible en 3D

### Piezas de ajedrez

- **Forma:** circular (tapa plana)
- **Diámetro:** 22.5 mm
- **Marcador ArUco:** integrado en la cara superior (diccionario `DICT_4X4_50`)
- **Anillo de contraste:** blanco, alrededor del marcador, para mejorar detección
- **Imán en pieza:** cilíndrico 10×2 mm, embutido en la base
- **Fabricación:** filamento PLA en BambuLab A1 mini

#### Asignación de IDs ArUco

Hay **tres roles distintos** de marcadores ArUco con rangos de ID separados,
definidos formalmente en `magnus/config.py`:

| Rol                      | Cantidad | Rango de ID | Estado |
|---------------------------|----------|---------------|--------|
| Piezas de ajedrez         | 32       | `0–31`        | ✅ mapeo oficial en `magnus/vision/piece_map.py` |
| Esquinas del tablero      | 4        | `40–43`       | ✅ 40=a8, 41=h8, 42=h1, 43=a1 |
| Marcador del brazo        | 1        | `44`          | ✅ reservado (rastreo V2) |

> **Mapeo ID→pieza oficial:** IDs 0–15 = piezas blancas, 16–31 = negras; dentro
> de cada color el orden es K, Q, R, R, B, B, N, N y 8 peones. Los PNG
> imprimibles de los 37 marcadores se generan con
> `python examples/generate_aruco_markers.py`.

### Brazo robótico — articulado, 2 grados de libertad + garra

| Actuador  | Articulación   | Función                                              |
|-----------|----------------|-------------------------------------------------------|
| Motor 1   | Hombro         | Primer eslabón del brazo (motor Encoder mBot2)        |
| Motor 2   | Codo           | Segundo eslabón del brazo (motor Encoder mBot2)       |
| Servo 3   | Garra/agarre   | Agarra y suelta las piezas                            |

- **Control:** CyberPi (placa del kit mBot2)
- **Sistema de agarre:** imán N52 (12×3 mm) en el extremo del brazo, asistido
  por el servo de agarre — ⚠️ **mecanismo exacto pendiente de confirmar**
  (¿el servo acerca/aleja el imán de la pieza? ¿controla una pinza mecánica?
  ¿controla el eje vertical de bajar/subir?)
- **Fabricación de partes estructurales:** PLA en BambuLab A1 mini
- **Comunicación Python ↔ CyberPi:** pendiente de documentar (protocolo serial/USB)

⚠️ **Cuidado con el imán N52:** es fuerte y su radio de influencia puede
desplazar piezas en casillas adyacentes si el brazo pasa muy cerca del tablero
en movimientos laterales. Ver [Estrategia de movimiento del brazo](#estrategia-de-movimiento-del-brazo).

---

## Software — módulos

### `magnus/core/` — Contratos de datos

Estructuras de datos puras sin dependencias de ajedrez. Cualquier nodo puede
importarlas sin arrastrar `python-chess`.

```python
# Petición al engine
PositionRequest(
    fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    difficulty="MEDIUM",  # o None para usar la dificultad por defecto del nodo
    movetime=None,        # segundos máximos de cálculo (None = usar preset)
    request_id="move-001"
)

# Respuesta del engine (incluye metadatos para el brazo)
MoveResponse(
    uci="e2e4",           # origen→destino para el brazo
    san="e4",             # notación algebraica
    is_capture=False,
    captured_square=None, # casilla donde está la pieza capturada (≠ to_square en al paso)
    is_castling=False,
    rook_from=None,       # para enroque: el brazo debe mover también la torre
    rook_to=None,
    is_en_passant=False,  # captura al paso: la pieza capturada NO está en to_square
    promotion=None,       # "q", "r", "b", "n"
    is_check=False,
    is_checkmate=False,
    resulting_fen="...",  # FEN tras la jugada (para verificación con visión)
    ...
)
```

### `magnus/engine/` — Nodo del engine

Recibe una `PositionRequest` (FEN + dificultad), usa Stockfish via protocolo UCI
y devuelve una `MoveResponse` con todos los metadatos relevantes para el brazo.

**Niveles de dificultad:**

| Nivel      | Elo aprox. | `movetime` | `Skill Level` |
|------------|-----------|------------|---------------|
| `BEGINNER` | ~1350     | 0.05 s     | 0             |
| `EASY`     | ~1500     | 0.10 s     | 3             |
| `MEDIUM`   | ~1800     | 0.20 s     | 8             |
| `HARD`     | ~2200     | 0.50 s     | 14            |
| `EXPERT`   | ~2600     | 1.00 s     | 18            |
| `MAXIMUM`  | máximo    | 2.00 s     | 20            |

El backend es intercambiable: cualquier motor UCI (Stockfish, Lc0, Komodo...)
funciona sin cambiar el resto del sistema.

### `magnus/vision/` — Nodo de visión

Tablero físico → placement → FEN exacta. Componentes:

- `aruco_detector.py` — detección con "enclavamiento" (un marcador debe verse N
  frames consecutivos antes de confirmarse; evita falsos positivos), con los
  tres roles de marcadores separados
- `board_pose.py` — homografía tablero(mm)↔imagen(px) con las 4 esquinas; mapea
  el centro de cada pieza a su casilla
- `piece_map.py` — mapeo oficial ID ArUco → símbolo FEN
- `fen_builder.py` — placement → texto FEN (módulo puro)
- `game_state.py` — `GameTracker`: deduce la jugada del humano comparando el
  placement detectado contra las jugadas legales de la partida → turno,
  enroque y al paso exactos, sin hardware extra
- `calibration.py` — calibración de cámara (opcional en v1)
- `synthetic.py` — genera imágenes sintéticas del tablero (tests/demos sin cámara)
- `vision_node.py` — `BoardVisionNode` con backend de cámara intercambiable
  (`OpenCVCameraBackend` real / `FakeCameraBackend` para tests)

```python
from magnus.vision import BoardVisionNode, OpenCVCameraBackend

with BoardVisionNode(camera=OpenCVCameraBackend(0)) as node:
    placement = node.get_board_placement()   # {"e4": "P", ...}
    fen = node.get_board_fen()               # FEN exacta tras la jugada del humano
```

**Pendiente:** validar con la cámara y el tablero físicos reales; rastreo del
marcador del brazo (V2).

### `magnus/arm/` — Nodo del brazo

Recibe una `MoveResponse` y reproduce los movimientos **pregrabados**
correspondientes. No calcula cinemática inversa.

- `backend.py` — `ArmBackend` (ABC) + `FakeArmBackend` (tests/demos, registra
  los comandos) + `CyberPiBackend` (stub hasta confirmar el protocolo)
- `positions_table.py` — carga/consulta de `positions.json` (64 casillas +
  zonas de descarte/intercambio)
- `arm_node.py` — `ArmNode.plan()` genera la secuencia (testeable) y
  `.execute()` la reproduce:
  - Si `is_capture`: mueve primero la pieza capturada a la zona de descarte
  - Si `is_castling`: rey primero, luego torre (`rook_from`/`rook_to`)
  - Si `is_en_passant`: retira el peón de `captured_square` (≠ destino)
  - Si `promotion`: peón a descarte + pieza promovida desde la zona de intercambio
  - Activa/desactiva la garra (servo 3) en los momentos correctos

```python
from magnus.arm import ArmNode, FakeArmBackend, make_fake_table

with ArmNode(backend=FakeArmBackend(), table=make_fake_table()) as arm:
    steps = arm.execute(resp)   # resp: MoveResponse del engine
```

**Pendiente (bloqueado por hardware):** implementar `CyberPiBackend` y grabar
la tabla `positions.json` real calibrando el brazo.

---

## Especificaciones físicas críticas

Estas constantes son fundamentales para todos los módulos. Están centralizadas
en **`magnus/config.py`** (única fuente de verdad — no hardcodearlas en módulos).

```python
# Tablero
SQUARE_SIZE_MM       = 32.0    # lado de cada casilla en mm
BOARD_SQUARES        = 8       # 8×8
BOARD_SIZE_MM        = SQUARE_SIZE_MM * BOARD_SQUARES  # 256 mm

# Piezas
PIECE_DIAMETER_MM    = 22.5
PIECE_MAGNET_D_MM    = 10.0    # diámetro imán pieza
PIECE_MAGNET_H_MM    = 2.0     # altura imán pieza

# Tablero — imanes de casilla
SQUARE_MAGNET_D_MM   = 6.0
SQUARE_MAGNET_H_MM   = 3.0

# Brazo — imán de agarre
ARM_MAGNET_D_MM      = 12.0
ARM_MAGNET_H_MM      = 3.0
ARM_MAGNET_GRADE     = "N52"   # fuerte: puede influir en piezas adyacentes

# ArUco — rangos de ID por rol (oficiales)
ARUCO_DICT           = "DICT_4X4_50"
ARUCO_IDS_PIECES      = range(0, 32)   # piezas de ajedrez
ARUCO_IDS_BOARD_CORNERS = (40, 41, 42, 43)  # esquinas del tablero
ARUCO_ID_ARM          = 44             # marcador en el extremo del brazo
DETECTION_CONFIRM_N   = 5              # detecciones consecutivas para confirmar presencia
```

---

## Estrategia de movimiento del brazo

> Esta es la decisión de diseño más importante del proyecto: MAGNUS **no**
> calcula cinemática inversa en tiempo real. En su lugar usa un enfoque de
> **"enseñar y reproducir" (teach & playback)**.

### ¿Por qué no cinemática inversa?

Con solo 64 casillas fijas, no es necesario resolver la trigonometría de un
brazo de 2 eslabones en cada jugada. Es más simple, más confiable para una
primera versión, y suficiente para una feria científica:

1. **Una sola vez**, se determinan y registran los ángulos de hombro y codo
   necesarios para que el brazo llegue al centro de cada una de las 64 casillas
2. Esos valores se guardan en una **tabla de consulta** (lookup table)
3. Durante el juego, el nodo del brazo simplemente **busca** los valores
   pregrabados para la casilla de origen y de destino, y reproduce esa
   secuencia de movimiento — sin ningún cálculo geométrico en vivo

### Formato de la tabla (`magnus/arm/positions.json`)

> Formato **implementado** en `magnus/arm/positions_table.py`: dos
> sub-posiciones por casilla. Las **unidades** (grados vs. pasos de encoder)
> quedan abiertas: la tabla y el backend deben usar las mismas, el código no
> las interpreta. La plantilla vacía se genera con
> `python examples/generate_positions_template.py`.

Dos sub-posiciones por casilla, para evitar que el brazo golpee piezas vecinas
al desplazarse:

```json
{
  "e4": {
    "approach": {"shoulder": 32.5, "elbow": 110.0},
    "engage":   {"shoulder": 35.0, "elbow": 118.0}
  },
  "e5": {
    "approach": {"shoulder": 30.0, "elbow": 108.0},
    "engage":   {"shoulder": 32.0, "elbow": 115.0}
  }
}
```

- **`approach`** = el brazo está sobre la casilla, a una altura segura (no toca piezas)
- **`engage`** = el brazo está bajado, en posición de agarrar/soltar la pieza

Una jugada típica (`e2` → `e4`, sin captura) se traduce en una secuencia como:

```
approach(e2) → engage(e2) → cerrar garra → approach(e2)
            → approach(e4) → engage(e4) → abrir garra → approach(e4)
```

### Cómo generar la tabla

> ⚠️ **Pendiente de decidir.** Opciones posibles:
> 1. Cálculo geométrico manual (con las medidas reales del brazo) y luego ajuste fino por prueba y error
> 2. Una herramienta de calibración: mover el brazo manualmente o con un script de control en vivo, y grabar la posición resultante para cada casilla
> 3. Una mezcla: geometría aproximada + corrección manual por casilla

Cualquiera que sea el método, el resultado final debe ser el mismo archivo
de datos (`positions.json` o similar), independiente de cómo se generó.

### Corrección automática de posición (V2 — futuro, no implementar todavía)

Si los movimientos pregrabados resultan imprecisos (por ejemplo, por
deslizamiento mecánico o desgaste), el plan a futuro es:

1. Calcular la posición **esperada** del marcador del brazo en la imagen
   (usando la homografía de las 4 esquinas del tablero + la casilla objetivo)
2. Detectar la posición **real** del marcador del brazo con la cámara
3. Calcular el **offset** (error) entre la posición esperada y la real
4. Ajustar levemente los ángulos de motor antes de ejecutar el siguiente movimiento

Este flujo de corrección **no es parte de la v1** del proyecto, pero el
sistema de marcadores ArUco (esquinas + marcador del brazo) se está
incorporando desde ahora para tenerlo listo cuando se necesite.

---

## Sistema de localización con ArUco

Tres roles de marcadores, cada uno con su propio propósito:

| Marcador              | Cantidad | Propósito                                                |
|------------------------|----------|------------------------------------------------------------|
| En cada pieza          | 12–32    | Identificar tipo/color de pieza → construir la FEN          |
| En las 4 esquinas del tablero | 4 | Calcular la homografía tablero↔cámara (pose del tablero)    |
| En el extremo del brazo | 1       | Rastrear la posición real del brazo (para corrección V2)    |

La homografía de las esquinas permite, en teoría, ubicar cualquier punto del
tablero en coordenadas de imagen y viceversa — esto es lo que hace posible,
a futuro, comparar "dónde debería estar el brazo" contra "dónde está
realmente" usando la cámara.

---

## Protocolo de mensajes entre módulos

Los módulos se comunican con objetos Python pasados directamente (mismo proceso)
o serializados a JSON (procesos separados / red).

```python
# Serialización
req_dict  = request.to_dict()           # -> dict JSON-serializable
req_again = PositionRequest.from_dict(req_dict)  # tolera claves extra

resp_dict = response.to_dict()          # MoveResponse también tiene to_dict()
```

---

## Estado actual y hoja de ruta

### ✅ Completado

- Contratos de datos entre módulos (`magnus/core/`)
- Nodo del engine completo con 6 niveles de dificultad (`magnus/engine/`)
- Backend UCI intercambiable (Stockfish por defecto)
- Constantes físicas centralizadas (`magnus/config.py`)
- Módulo de visión completo (`magnus/vision/`): detección con enclavamiento,
  homografía de esquinas, mapeo ID→pieza, construcción de FEN completa
- Detección del turno/enroque/al paso por inferencia (`GameTracker`)
- Módulo del brazo (`magnus/arm/`): tabla de posiciones + planificador y
  reproductor de secuencias (captura, al paso, enroque, promoción)
- Zona de piezas capturadas y de intercambio (lógica; falta la física)
- Integración de los tres nodos, demostrada sin hardware
  (`examples/run_full_pipeline_demo.py`)
- Suite de tests completa (76+) con backends falsos — corre sin ningún hardware
- CI en GitHub Actions (tests en cada push/PR)
- Generador de marcadores ArUco imprimibles y de la plantilla de posiciones

### 🔶 En progreso / bloqueado por hardware

- Validar la visión con la cámara y el tablero físicos reales
- Calibración de la cámara real (el código ya existe: `magnus/vision/calibration.py`)
- Definición del mecanismo exacto de la garra (servo 3)
- Protocolo de comunicación Python ↔ CyberPi → implementar `CyberPiBackend`
- Grabar la tabla `positions.json` real calibrando el brazo
- Zona de piezas capturadas (física)

### 🔴 Pendiente (V2 / mejoras)

- Rastreo del marcador del brazo (`magnus/vision/arm_tracker.py`)
- Corrección automática de posición por visión (V2)
- Interfaz de usuario / indicadores de estado (LEDs, pantalla CyberPi)
- Orquestador de partida completa con hardware real (bucle de juego)

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/LeoZ24/MAGNUS_Chess_Robot.git
cd MAGNUS_Chess_Robot

# 2. Dependencias Python
pip install -r requirements.txt
pip install -r requirements-dev.txt   # (opcional) pytest, para correr los tests

# 3. Motor de ajedrez (binario externo)
sudo apt install stockfish        # Linux / Raspberry Pi
brew install stockfish            # macOS

# O definir ruta manualmente:
export MAGNUS_ENGINE_PATH=/ruta/a/tu/motor_uci
```

### Dependencias

```
chess>=1.10                     # python-chess: FEN, reglas, protocolo UCI
opencv-contrib-python>=4.7      # cv2 + cv2.aruco (visión)
numpy>=1.23
```

---

## Ejecución rápida

### Engine solo (sin hardware)

```bash
# Jugada desde la posición inicial, dificultad media
python examples/run_engine_node.py

# Posición concreta + dificultad máxima, salida JSON
python examples/run_engine_node.py \
    --fen "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3" \
    --difficulty MAXIMUM --json

# Engine contra sí mismo (6 medias-jugadas)
python examples/run_engine_node.py --selfplay 6 --difficulty EASY
```

### Pipeline completo simulado (sin cámara, sin brazo, sin tablero)

Integra los TRES nodos: se renderiza una imagen sintética del tablero con
marcadores ArUco reales, la visión la procesa y deduce la jugada del "humano",
el engine responde y el brazo (falso) imprime su secuencia de comandos:

```bash
python examples/run_full_pipeline_demo.py
python examples/run_full_pipeline_demo.py --plies 8 --difficulty EASY -v
```

### Brazo solo (sin hardware)

El engine calcula jugadas y se muestra la secuencia exacta de comandos que
recibiría el brazo real (backend falso + tabla de posiciones falsa):

```bash
python examples/run_arm_demo.py                    # una jugada
python examples/run_arm_demo.py --selfplay 6       # auto-partida con secuencias
```

### Visión en vivo (con webcam)

Sucesor del prototipo `ArUco_Test.py`: muestra los marcadores por rol, la
casilla de cada pieza y la FEN en tiempo real. Requiere los marcadores impresos.

```bash
python examples/run_vision_demo.py
# 'R' resetea la memoria de detección, 'Q' sale
```

### Utilidades

```bash
# PNGs de los 37 marcadores ArUco listos para imprimir (piezas/esquinas/brazo)
python examples/generate_aruco_markers.py

# Plantilla de positions.json para calibrar el brazo (valores en null)
python examples/generate_positions_template.py
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/                   # los tests de integración se omiten sin Stockfish

pytest tests/ -v                # salida detallada
pytest tests/ -k "capture"      # solo tests de capturas
```

Ningún test necesita hardware: el engine usa `FakeBackend`, el brazo usa
`FakeArmBackend` + tabla falsa (valores 9999.x) y la visión usa imágenes
sintéticas con marcadores ArUco reales inyectadas por `FakeCameraBackend`.
Los tests de integración con Stockfish se marcan con
`@pytest.mark.skipif(not _engine_available(), ...)`. La suite corre también en
CI (GitHub Actions) en cada push y pull request.

---

## Convenciones del proyecto

### Código

- **Idioma de comentarios y docstrings:** español
- **Nombres de variables y funciones:** snake_case en inglés (convención Python estándar)
- **Nombres de módulos:** inglés
- **Type hints:** obligatorios en todas las funciones públicas
- **Dataclasses** para mensajes entre módulos (no dicts crudos)
- **Logging** via `logging.getLogger("magnus.<módulo>")` — no `print()` en módulos

### Estructura de directorios

```
magnus/
├── core/
│   ├── __init__.py
│   └── messages.py          # PositionRequest, MoveResponse
├── engine/
│   ├── __init__.py
│   ├── backend.py           # EngineBackend (ABC) + UCIEngineBackend
│   ├── chess_engine_node.py # ChessEngineNode (servicio principal)
│   └── difficulty.py        # DifficultyLevel, EngineConfig, presets
├── config.py                # ✅ constantes físicas + rangos de ID ArUco
├── vision/                  # ✅ implementado
│   ├── __init__.py
│   ├── aruco_detector.py    # detección + enclavamiento (3 roles separados)
│   ├── calibration.py       # corrección de distorsión de cámara
│   ├── board_pose.py        # homografía a partir de las 4 esquinas ArUco
│   ├── piece_map.py         # mapeo oficial ID ArUco → símbolo FEN
│   ├── fen_builder.py       # placement → texto FEN
│   ├── game_state.py        # GameTracker (inferencia de jugadas)
│   ├── synthetic.py         # imágenes sintéticas para tests/demos
│   └── vision_node.py       # BoardVisionNode + backends de cámara
└── arm/                     # 🔶 software listo; falta hardware
    ├── __init__.py
    ├── backend.py            # ArmBackend (ABC) + FakeArmBackend + CyberPiBackend (stub)
    ├── positions_table.py    # carga/consulta de positions.json
    └── arm_node.py           # MoveResponse → secuencia de movimientos
    (positions.json — se graba calibrando el brazo real)
examples/
tests/
```

### Patrones de diseño

`magnus/arm/` y `magnus/vision/` siguen el mismo patrón que `magnus/engine/`:
- Un nodo principal (`ArmNode`) que recibe el mensaje de alto nivel
- Un backend intercambiable (`ArmBackend` ABC + `CyberPiBackend` implementación)
- Separación entre la lógica de la secuencia de movimiento y la comunicación con el hardware
- **Sin cálculo de cinemática inversa** — toda posición viene de `positions.json`

---

## Contexto del proyecto

MAGNUS fue desarrollado para el **PRONAFECYT 2026** (Programa Nacional de Ferias
de Ciencia, Tecnología e Innovación de Costa Rica), en la categoría de
**Investigación y Desarrollo Tecnológico**.

El sistema integra visión artificial, inteligencia artificial (motor de ajedrez)
y robótica en un proyecto de bajo costo construido con componentes accesibles e
impresión 3D.
