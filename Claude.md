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

## Formato recomendado de `magnus/arm/positions.json`

> Esto es una **recomendación de diseño**, no un hecho confirmado. El formato
> exacto (grados vs. pasos de encoder, una posición vs. dos por casilla) está
> pendiente de decidir con el usuario.

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

### 🔶 `ArUco_Test.py` — PROTOTIPO, necesita convertirse en módulo

Este archivo es el origen del futuro `magnus/vision/`. Funciona pero:
- No está encapsulado como clase/módulo
- No produce FEN (solo detecta IDs)
- No tiene calibración de cámara ni homografía
- El mapeo ID ArUco → pieza de ajedrez no está definido
- No distingue entre los 3 roles de marcadores (ver siguiente sección)

**Cuando trabajes en visión**, el objetivo final es una clase que exponga:
```python
class BoardVisionNode:
    def get_board_fen(self) -> str:
        """Captura un frame y devuelve la FEN actual del tablero."""
```

### 🔴 `magnus/arm/` — NO EXISTE AÚN

El módulo del brazo es la tarea principal pendiente. Debe seguir el mismo patrón
que `magnus/engine/`:
- `ArmBackend` (clase abstracta) + `CyberPiBackend` (implementación concreta)
- `ArmNode` que recibe `MoveResponse`, consulta `positions.json` y orquesta la secuencia
- `FakeArmBackend` para tests sin hardware (registra qué comandos se "enviarían", sin hardware real)

**Recuerda: NO calcula geometría. Solo busca en la tabla y reproduce.**

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

**Mapeo ID → pieza de ajedrez: NO DEFINIDO AÚN.** Cuando lo implementes,
crear `magnus/vision/piece_map.py` con un dict como:
```python
ARUCO_TO_PIECE: dict[int, tuple[str, str]] = {
    # id: (tipo, color)   tipo="K","Q","R","B","N","P"  color="w","b"
    0:  ("K", "w"),  # rey blanco
    1:  ("Q", "w"),  # dama blanca
    # etc.
}
```

El carácter FEN de cada pieza sigue la convención de `python-chess`:
mayúsculas = blancas, minúsculas = negras. Ej: `"K"` = rey blanco, `"k"` = rey negro.

---

## FEN — formato que conecta visión y engine

La FEN es el único dato que viaja del módulo de visión al engine:
```
"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
  ^posición^                                   ^turno^ ^enroque^ ^al paso^ ^semi^ ^full^
```

El módulo de visión debe determinar **turno** (w/b) y **derechos de enroque**
(KQkq). Opciones para el turno (sin decidir):
- Marcador ArUco externo al tablero que indica de quién es el turno
- Botón físico que el jugador humano presiona cuando termina su jugada
- Inferencia por comparación de FEN anterior vs actual

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

## Estructura de directorios objetivo

```
magnus/
├── core/          # ✅ contratos de datos — NO modificar interfaz
├── engine/        # ✅ nodo del engine — completo
├── vision/        # 🔴 por crear — detecta tablero → FEN
│   ├── __init__.py
│   ├── aruco_detector.py   # lógica de detección con enclavamiento (los 3 roles, separados)
│   ├── calibration.py      # corrección de distorsión de cámara
│   ├── board_pose.py       # homografía a partir de las 4 esquinas ArUco
│   ├── arm_tracker.py      # rastreo del marcador del brazo (V2, no v1)
│   ├── piece_map.py        # ID ArUco → (tipo, color) de pieza
│   └── vision_node.py      # nodo principal (equivalente a chess_engine_node.py)
└── arm/           # 🔴 por crear — recibe MoveResponse → reproduce movimiento
    ├── __init__.py
    ├── backend.py           # ArmBackend ABC + CyberPiBackend
    ├── positions.json       # tabla de posiciones pregrabadas (64 casillas) — NO existe, no inventar datos
    ├── positions_table.py   # carga/consulta de positions.json
    └── arm_node.py          # MoveResponse → secuencia de sub-movimientos
config.py          # 🔴 por crear — todas las constantes físicas + rangos de ID ArUco
examples/
tests/
docs/
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
