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
| **Visión**        | `ArUco_Test.py`       | 🔶 Prototipo         | Detección de marcadores ArUco; falta mapeo a FEN, homografía y rastreo del brazo |
| **Brazo robótico**| *(pendiente)*         | 🔴 Por implementar   | Tabla de movimientos pregrabados (hombro/codo/garra) + reproductor de secuencias |

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

Con la incorporación de marcadores de esquina y del brazo, ahora hay **tres
roles distintos** de marcadores ArUco que deben usar rangos de ID separados
para no confundirse entre ellos (definir formalmente en `config.py`):

| Rol                      | Cantidad | Rango de ID sugerido | Estado |
|---------------------------|----------|------------------------|--------|
| Piezas de ajedrez         | 12+ (hasta 32) | `0–31`           | 🔶 prototipo usa 12 IDs sueltos |
| Esquinas del tablero      | 4        | `40–43`               | 🔴 pendiente |
| Marcador del brazo        | 1        | `44`                  | 🔴 pendiente |

> ⚠️ **Pendiente de definir oficialmente:** el mapeo ID→pieza (tipo + color).
> El prototipo actual usa `{0, 1, 2, 6, 8, 9, 12, 15, 16, 18, 21, 23}` sin
> asignación documentada todavía.

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

### `ArUco_Test.py` — Prototipo de visión

Detección con sistema de "enclavamiento": un marcador debe detectarse N veces
consecutivas antes de registrarse como válido. Evita falsos positivos.

**Pendiente en el módulo de visión:**
- [ ] Calibración de cámara (matriz intrínseca + distorsión)
- [ ] Homografía tablero→imagen usando las 4 esquinas ArUco
- [ ] Mapeo ID ArUco → tipo de pieza → casilla del tablero → carácter FEN
- [ ] Construcción de la FEN completa a partir de la detección
- [ ] Detección de turno (blancas/negras) — posiblemente por marcador externo al tablero
- [ ] Rastreo en vivo del marcador del brazo (base para corrección V2)
- [ ] Encapsular en `magnus/vision/` con la misma estructura que `magnus/engine/`

### Nodo del brazo — `magnus/arm/` *(por implementar)*

Recibirá una `MoveResponse` y reproducirá los movimientos **pregrabados**
correspondientes (ver siguiente sección). No calcula cinemática inversa.

Responsabilidades previstas:
- Buscar en la tabla de posiciones los ángulos de hombro/codo para cada casilla involucrada
- Si `is_capture`: mover primero la pieza capturada a la zona de descarte
- Si `is_castling`: ejecutar dos secuencias (rey + torre, usando `rook_from`/`rook_to`)
- Si `is_en_passant`: retirar el peón de `captured_square` (≠ destino)
- Si `promotion`: llevar pieza a zona de intercambio
- Activar/desactivar la garra (servo 3) en los momentos correctos de cada secuencia

---

## Especificaciones físicas críticas

Estas constantes son fundamentales para todos los módulos. **Centralizar en
`magnus/config.py`** (pendiente).

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

# ArUco — rangos de ID por rol (pendiente de fijar oficialmente)
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

### Formato propuesto de la tabla (`magnus/arm/positions.json`)

> ⚠️ Formato **a confirmar** con quien construye el brazo: ¿grados o pasos
> de encoder? ¿una posición por casilla, o dos (segura + de agarre)?

Recomendación técnica — dos sub-posiciones por casilla, para evitar que el
brazo golpee piezas vecinas al desplazarse:

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
- Suite de tests del engine con backend falso (sin Stockfish necesario)
- Demo de detección ArUco con sistema de enclavamiento
- Decisión de arquitectura del brazo: 2 motores (hombro/codo) + servo de garra,
  movimientos pregrabados (sin IK en v1)
- Decisión de arquitectura de visión: 3 roles de marcadores ArUco (piezas,
  esquinas, brazo)

### 🔶 En progreso

- Módulo de visión: detección → FEN completa
- Calibración de cámara
- Definición del mecanismo exacto de la garra (servo 3)
- Definición del método para generar la tabla de posiciones pregrabadas

### 🔴 Pendiente

- Módulo del brazo (`magnus/arm/`): tabla de posiciones + reproductor de secuencias
- API de comunicación con CyberPi desde Python
- Homografía de las 4 esquinas del tablero (`magnus/vision/board_pose.py`)
- Rastreo del marcador del brazo (`magnus/vision/arm_tracker.py`)
- Integración completa de los tres nodos
- Zona de piezas capturadas (física y lógica)
- Detección del turno del jugador humano
- Corrección automática de posición por visión (V2)
- Interfaz de usuario / indicadores de estado (LEDs, pantalla CyberPi)

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/magnus.git
cd magnus

# 2. Dependencias Python
pip install -r requirements.txt

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

### Detección ArUco (prototipo)

```bash
python ArUco_Test.py
# Presiona 'R' para resetear la memoria del tablero
# Presiona 'Q' para salir
```

---

## Tests

```bash
pip install pytest
pytest tests/                   # los tests de integración se omiten sin Stockfish

pytest tests/ -v                # salida detallada
pytest tests/ -k "capture"      # solo tests de capturas
```

Los tests unitarios usan `FakeBackend` (devuelve una jugada determinista) y no
requieren Stockfish instalado. Los tests de integración se marcan con
`@pytest.mark.skipif(not _engine_available(), ...)`.

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
├── vision/                  # 🔴 pendiente de crear
│   ├── __init__.py
│   ├── aruco_detector.py    # detección + enclavamiento
│   ├── calibration.py       # corrección de distorsión de cámara
│   ├── board_pose.py        # homografía a partir de las 4 esquinas ArUco
│   ├── arm_tracker.py       # rastreo del marcador del brazo (V2)
│   └── board_parser.py      # ID ArUco de pieza → FEN
└── arm/                     # 🔴 pendiente de crear
    ├── __init__.py
    ├── backend.py            # ArmBackend (ABC) + CyberPiBackend
    ├── positions.json        # tabla de posiciones pregrabadas (64 casillas)
    ├── positions_table.py    # carga/consulta de positions.json
    └── arm_node.py           # MoveResponse → secuencia de movimientos
config.py          # 🔴 por crear — todas las constantes físicas centralizadas
examples/
tests/
docs/
```

### Patrones de diseño a seguir

Cuando se implemente `magnus/arm/`, debe seguir el mismo patrón que `magnus/engine/`:
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
