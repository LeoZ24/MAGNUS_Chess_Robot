# CLAUDE.md — Contexto para Claude Code

Este archivo es para Claude Code. Contiene todo lo que necesitas saber para
trabajar eficientemente en este proyecto sin preguntar cosas básicas.

---

## ¿Qué es este proyecto?

**MAGNUS** es un robot autónomo que juega ajedrez físicamente. Tres nodos
independientes (estilo ROS2) trabajan en cadena:

1. **Visión** — Detecta las piezas del tablero real con marcadores ArUco y produce una FEN
2. **Engine** — Recibe la FEN, calcula la mejor jugada con Stockfish, devuelve metadatos
3. **Brazo** — Recibe los metadatos y reproduce un movimiento **pregrabado** con un brazo articulado de 2 motores + 1 servo de garra

El flujo de datos es lineal y unidireccional:
`tablero físico → FEN string → jugada UCI + metadatos → secuencia de motor pregrabada`

---

## ⚠️ DECISIÓN DE ARQUITECTURA MÁS IMPORTANTE — léela antes de tocar `magnus/arm/`

**El brazo NO usa cinemática inversa (IK) calculada en tiempo real.**

El tablero tiene exactamente 64 casillas fijas. En vez de resolver la
trigonometría de un brazo de 2 eslabones (hombro + codo) en cada jugada, el
proyecto usa un enfoque de **"enseñar y reproducir" (teach & playback)**:

1. Los ángulos de hombro/codo para llegar a cada una de las 64 casillas se
   registran **una sola vez**, de antemano, y se guardan en una tabla
   (`magnus/arm/positions.json` o similar)
2. En tiempo de juego, el nodo del brazo **solo busca en la tabla** — nunca
   calcula geometría

**Si te piden implementar o modificar `magnus/arm/`, NO escribas código de
cinemática inversa (no hay que resolver ángulos con trigonometría/atan2/ley
de cosenos para mover el brazo durante el juego).** Esa complejidad se evitó
deliberadamente. Lo que sí hay que escribir es:
- Una estructura de datos para cargar/consultar la tabla de posiciones
- Un reproductor de secuencias de movimiento (orden de pasos: aproximar →
  bajar → agarrar/soltar → subir → mover → repetir en destino)
- Un backend que traduzca esas posiciones a comandos para CyberPi

La cinemática inversa **podría** ser útil en el futuro solo si se decide
generar la tabla de posiciones de forma calculada en lugar de medida a mano
— pero eso es una herramienta de calibración aparte (offline), no parte del
flujo de juego en vivo.

---

## Hardware del brazo — específico

| Actuador | Articulación | Tipo                          |
|----------|--------------|--------------------------------|
| Motor 1  | Hombro       | Motor Encoder (kit mBot2)       |
| Motor 2  | Codo         | Motor Encoder (kit mBot2)       |
| Servo 3  | Garra/agarre | Servomotor — agarra y suelta piezas |

⚠️ **Mecanismo exacto del servo de agarre: NO CONFIRMADO TODAVÍA.** No asumas
si es: acercar/alejar el imán N52 de la pieza, una pinza mecánica física, o
el eje vertical de bajar/subir. Si necesitas escribir código que dependa de
esto, pregunta antes de asumir — la lógica de la secuencia de movimiento
cambia según cuál sea.

- **Control:** CyberPi (placa del kit mBot2)
- **Comunicación Python ↔ CyberPi: NO DOCUMENTADA TODAVÍA.** No hay
  confirmación de si es serial USB, alguna librería específica de mBot2, o
  comandos crudos. No inventes una API — pregunta o deja un
  `# TODO(confirmar): protocolo de comunicación con CyberPi` explícito.
- **Imán de agarre:** N52, 12×3 mm — muy fuerte. Su radio de influencia puede
  desplazar piezas en casillas adyacentes si el brazo pasa muy cerca del
  tablero en movimientos laterales. Por eso la tabla de posiciones debería
  tener una sub-posición "segura" (`approach`) además de la de contacto
  (`engage`) — ver siguiente sección.

---

## Formato de `magnus/arm/positions.json`

> Formato **implementado** en `magnus/arm/positions_table.py`: dos
> sub-posiciones por casilla (`approach` + `engage`). Las **unidades** (grados
> vs. pasos de encoder) siguen abiertas a propósito: la tabla y el backend
> deben usar las mismas, pero el código no las interpreta.

```json
{
  "e4": {
    "approach": {"shoulder": 32.5, "elbow": 110.0},
    "engage":   {"shoulder": 35.0, "elbow": 118.0}
  }
}
```

- `approach`: el brazo está sobre la casilla, a altura segura (no toca piezas vecinas)
- `engage`: el brazo está bajado, en posición de agarrar/soltar

Una jugada simple (`e2`→`e4`, sin captura) se traduce en una secuencia como:
```
approach(e2) → engage(e2) → [activar garra] → approach(e2)
            → approach(e4) → engage(e4) → [soltar garra] → approach(e4)
```

Una captura, enroque, captura al paso o promoción necesitan secuencias
compuestas de varias de estas — usa los campos de `MoveResponse` (ver más
abajo) para decidir qué sub-secuencias encadenar.

**No tienes el archivo `positions.json` todavía.** No lo inventes con datos
ficticios salvo que sea explícitamente para un test (`FakeArmBackend`) — en
ese caso, usa valores claramente marcados como falsos (ej. `9999.0`) para que
no se confundan con datos reales.

---

## Estado actual de cada módulo

### ✅ `magnus/core/` — COMPLETO, no tocar la interfaz

`PositionRequest` y `MoveResponse` son el **contrato entre módulos**. Cualquier
cambio en sus campos rompe la compatibilidad entre nodos. Si necesitas agregar
un campo nuevo, usa `Optional` con valor por defecto.

```python
# CORRECTO — compatible hacia atrás
@dataclass
class MoveResponse:
    nuevo_campo: Optional[str] = None  # ✅

# INCORRECTO — rompe código existente
@dataclass
class MoveResponse:
    nuevo_campo: str  # ❌ sin default rompe instancias existentes
```

### ✅ `magnus/engine/` — COMPLETO y funcional

El engine está terminado. Usa Stockfish via protocolo UCI con `python-chess`.
Tests completos en `tests/test_chess_engine.py`. No requiere refactoring.

Si necesitas modificar algo aquí, los tests deben seguir pasando:
```bash
pytest tests/ -v
```

### ✅ `magnus/vision/` — IMPLEMENTADO (falta validar con cámara y tablero reales)

`BoardVisionNode.get_board_fen()` existe y funciona (validado con imágenes
sintéticas en `tests/test_vision_node.py`). Componentes:
- `aruco_detector.py` — detección + enclavamiento, los 3 roles separados
- `board_pose.py` — homografía mm↔px con las 4 esquinas (40=a8, 41=h8, 42=h1, 43=a1)
- `piece_map.py` — mapeo oficial ID→pieza (ver sección ArUco)
- `fen_builder.py` — placement → texto FEN (puro, sin dependencias)
- `game_state.py` — `GameTracker`: deduce la jugada del humano comparando el
  placement detectado contra las jugadas legales (resuelve turno/enroque/al paso)
- `calibration.py` — calibración de cámara (opcional en v1)
- `synthetic.py` — imágenes sintéticas del tablero para tests/demos
- `vision_node.py` — `BoardVisionNode` + backends de cámara (OpenCV/Fake)

`ArUco_Test.py` (raíz) es el prototipo original, superado por
`examples/run_vision_demo.py`. **Pendiente:** probar con la cámara y el tablero
físicos (los parámetros de detección pueden requerir ajuste con luz real).

### 🔶 `magnus/arm/` — IMPLEMENTADO EN SOFTWARE; bloqueado por hardware

Sigue el patrón de `magnus/engine/`:
- `backend.py` — `ArmBackend` (ABC) + `FakeArmBackend` (tests/demos) +
  `CyberPiBackend` (**stub**: lanza `NotImplementedError` hasta confirmar el
  protocolo con CyberPi y el mecanismo de la garra)
- `positions_table.py` — carga/consulta de `positions.json` (64 casillas +
  zonas `discard`/`exchange`); `make_fake_table()` con valores 9999.x para tests
- `arm_node.py` — `ArmNode.plan()` (secuencia testeable sin hardware) y
  `.execute()`; maneja captura, al paso, enroque y promoción

**Recuerda: NO calcula geometría. Solo busca en la tabla y reproduce.**

Pendiente (bloqueado por hardware):
- Implementar `CyberPiBackend` cuando se confirme el protocolo de comunicación
- Grabar `positions.json` real calibrando el brazo
  (`examples/generate_positions_template.py` genera la plantilla)

---

## ArUco — tres roles distintos, no los mezcles

Hay **tres tipos de marcadores ArUco** con propósitos completamente distintos.
Si escribes código de detección, sepáralos por rango de ID — no los proceses
con la misma lógica:

| Rol                       | Cantidad | Rango ID sugerido | Para qué sirve |
|-----------------------------|----------|----------------------|------------------|
| Piezas de ajedrez          | 12–32    | `0–31`               | Construir la FEN (tipo + color de cada pieza) |
| Esquinas del tablero        | 4        | `40–43`              | Homografía tablero↔cámara |
| Marcador del brazo          | 1        | `44`                 | Rastreo de posición real del extremo del brazo |

```python
ARUCO_DICT = aruco.DICT_4X4_50      # mismo diccionario para los tres roles
CONFIRM_N  = 5                       # frames consecutivos para confirmar detección
```

**¿Para qué sirven las esquinas y el marcador del brazo si los movimientos son
pregrabados?** Es la base para una **corrección automática futura (V2, NO
implementar ahora salvo que se pida explícitamente)**:

1. Calcular dónde *debería* estar el marcador del brazo (homografía de
   esquinas + casilla objetivo)
2. Detectar dónde *está realmente* (cámara)
3. Calcular el offset/error
4. Ajustar levemente los ángulos antes del siguiente movimiento

Si te piden trabajar en esto, créalo como módulo separado
(`magnus/vision/arm_tracker.py` + lógica de corrección en `magnus/arm/`), no
mezclado con la detección de piezas ni con el reproductor de secuencias
pregrabadas.

**Mapeo ID → pieza: DEFINIDO** en `magnus/vision/piece_map.py`:
- IDs **0–15 = blancas**, **16–31 = negras**
- Dentro de cada color, el orden es: K, Q, R, R, B, B, N, N, P×8
- Ej.: 0 = rey blanco (`"K"`), 1 = dama blanca (`"Q"`), 16 = rey negro (`"k"`)

El símbolo FEN sigue la convención de `python-chess`: mayúsculas = blancas,
minúsculas = negras. Los PNG imprimibles de los 37 marcadores se generan con
`examples/generate_aruco_markers.py`.

---

## FEN — formato que conecta visión y engine

La FEN es el único dato que viaja del módulo de visión al engine:
```
"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
  ^posición^                                   ^turno^ ^enroque^ ^al paso^ ^semi^ ^full^
```

El módulo de visión determina **turno** (w/b), **derechos de enroque** (KQkq)
y **casilla al paso** por **inferencia por software** (DECIDIDO — implementado
en `magnus/vision/game_state.py`): el `GameTracker` mantiene un `chess.Board`
interno y compara el placement detectado contra todas las jugadas legales; la
que coincide es la jugada del humano. Requiere empezar desde una posición
conocida (la inicial por defecto) y notificar las jugadas del robot con
`notify_robot_move(uci)`.

---

## Metadatos de MoveResponse que el brazo debe usar

El brazo NO debe re-calcular nada de ajedrez. Todo está en `MoveResponse`:

```python
resp.from_square    # "e2" — origen (notación algebraica) → buscar en positions.json
resp.to_square      # "e4" — destino → buscar en positions.json

# Casos especiales que el brazo DEBE manejar encadenando sub-secuencias:
resp.is_capture      # True si hay captura
resp.captured_square # donde está físicamente la pieza capturada
                     # ⚠️ en captura al paso ≠ resp.to_square
                     # secuencia: mover esa pieza a zona de descarte ANTES de mover la pieza que captura

resp.is_castling     # True si es enroque
resp.rook_from       # origen de la torre (también hay que moverla)
resp.rook_to         # destino de la torre
                     # secuencia: mover el rey, LUEGO mover la torre (o el orden que sea físicamente seguro)

resp.is_en_passant   # captura al paso: el peón capturado NO está en to_square
                     # está en resp.captured_square (misma columna que to_square,
                     # misma fila que from_square)

resp.promotion       # "q","r","b","n" o None — cambio de pieza física en zona de intercambio
```

---

## Convenciones de código

- **Comentarios y docstrings:** en español
- **Nombres de símbolos (vars, funciones, clases):** inglés (convención Python)
- **Type hints:** obligatorios en funciones públicas
- **Logging:** `logger = logging.getLogger("magnus.<modulo>.<submodulo>")`
  — nunca `print()` dentro de módulos (solo en scripts de demo/CLI)
- **Dataclasses** para todos los mensajes entre módulos
- **ABC** para todos los backends de hardware (permite Fake backend en tests)
- **Context managers** (`__enter__`/`__exit__`) en todos los nodos que tienen recursos

### Tests

- Cada módulo nuevo debe tener un `Fake*Backend` que no requiera hardware real
- Los tests de integración (con hardware real) se marcan con `@pytest.mark.skipif`
- Estructura: `tests/test_<modulo>.py`

---

## Estructura de directorios actual

```
magnus/
├── config.py      # ✅ constantes físicas + rangos de ID ArUco (única fuente de verdad)
├── core/          # ✅ contratos de datos — NO modificar interfaz
├── engine/        # ✅ nodo del engine — completo
├── vision/        # ✅ detecta tablero → FEN (falta validar con hardware real)
│   ├── __init__.py
│   ├── aruco_detector.py   # detección con enclavamiento (los 3 roles, separados)
│   ├── calibration.py      # corrección de distorsión de cámara (opcional v1)
│   ├── board_pose.py       # homografía a partir de las 4 esquinas ArUco
│   ├── piece_map.py        # mapeo oficial ID ArUco → símbolo FEN
│   ├── fen_builder.py      # placement → texto FEN (puro)
│   ├── game_state.py       # GameTracker: inferencia de la jugada del humano
│   ├── synthetic.py        # imágenes sintéticas del tablero (tests/demos)
│   └── vision_node.py      # BoardVisionNode + backends de cámara
│   (arm_tracker.py — V2 futura, NO creada a propósito)
└── arm/           # 🔶 software listo; CyberPiBackend y positions.json bloqueados por hardware
    ├── __init__.py
    ├── backend.py           # ArmBackend ABC + FakeArmBackend + CyberPiBackend (stub)
    ├── positions_table.py   # carga/consulta de positions.json + make_fake_table()
    └── arm_node.py          # MoveResponse → secuencia de sub-movimientos
    (positions.json — NO existe: se graba calibrando el brazo real)
examples/          # demos ejecutables (engine, brazo, visión, pipeline completo)
tests/             # 76+ tests; todos corren sin hardware
```

---

## Lo que NO debes hacer

- ❌ No escribir cinemática inversa (IK) como parte del flujo de juego en vivo del brazo — los movimientos son pregrabados, se buscan en una tabla
- ❌ No cambiar la firma de `PositionRequest` o `MoveResponse` sin valor por defecto en campos nuevos
- ❌ No hacer `import chess` en `magnus/core/` (debe ser independiente de python-chess)
- ❌ No usar `print()` dentro de `magnus/` (usar logging)
- ❌ No hardcodear constantes físicas (32mm, 22.5mm, rangos de ID ArUco, etc.) en los módulos — usar `config.py`
- ❌ No comunicarse directamente entre `magnus/vision/` y `magnus/arm/` — todo pasa por los mensajes tipados
- ❌ No asumir que Stockfish está instalado en los tests unitarios (usar FakeBackend)
- ❌ No inventar el protocolo de comunicación con CyberPi ni el mecanismo exacto del servo de garra — son decisiones pendientes de confirmar, no asunciones a hacer en silencio
- ❌ No mezclar la lógica de detección de piezas, esquinas del tablero y marcador del brazo en una sola función — son tres responsabilidades distintas

---

## Dependencias instaladas

```
chess>=1.10                    # python-chess
opencv-contrib-python>=4.7     # cv2 + cv2.aruco
numpy>=1.23
```

Motor externo (binario):
```bash
sudo apt install stockfish      # Linux / Raspberry Pi
brew install stockfish          # macOS
# o: export MAGNUS_ENGINE_PATH=/ruta/al/binario
```
