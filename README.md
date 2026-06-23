# MAGNUS: the DIY chess robot made by a 16 year old

this will be improved later on

---

## Arquitectura modular (estilo ROS2)

MAGNUS está organizado en **módulos/nodos** independientes que se comunican
mediante **mensajes tipados**, igual que en ROS2. Cada nodo hace una sola cosa y
no conoce los detalles internos de los demás, así que se pueden desarrollar,
probar y reemplazar por separado.

```
[ Visión / ArUco ]  --PositionRequest(FEN)-->  [ Engine ]  --MoveResponse-->  [ Brazo robótico ]
   (tablero físico)                          (Stockfish/UCI)                    (motores/servos)
```

| Módulo            | Carpeta            | Estado            | Función |
|-------------------|--------------------|-------------------|---------|
| Visión            | `ArUco_Test.py`    | prototipo         | Detecta las piezas en el tablero físico (ArUco). |
| **Engine**        | `magnus/engine/`   | ✅ funcional       | Recibe una posición (FEN) y devuelve la mejor jugada según la dificultad. |
| Brazo robótico    | _(pendiente)_      | por hacer         | Ejecuta físicamente la jugada de `MoveResponse`. |

Los **contratos de datos** compartidos entre nodos viven en `magnus/core/`
(`PositionRequest`, `MoveResponse`) y son estructuras puras (sin dependencias),
de modo que cualquier nodo pueda construirlas/leerlas e incluso serializarlas a
JSON para enviarlas por la red.

```
magnus/
├── core/
│   └── messages.py        # PositionRequest, MoveResponse (los "mensajes")
└── engine/
    ├── difficulty.py      # niveles de dificultad -> EngineConfig
    ├── backend.py         # backend de motor UCI intercambiable (Stockfish, Lc0...)
    └── chess_engine_node.py  # el nodo: FEN -> jugada
```

## El nodo del engine

Recibe la **posición** del tablero como una **FEN** (lo que producirá el módulo
de visión a partir del tablero real) y responde con la jugada elegida, con
metadatos pensados para el brazo (captura, enroque, captura al paso, promoción,
jaque/mate...).

### Instalación

```bash
pip install -r requirements.txt      # python-chess (+ visión)
sudo apt install stockfish           # el motor (binario externo)
# alternativamente: export MAGNUS_ENGINE_PATH=/ruta/a/tu/motor_uci
```

### Uso desde código

```python
from magnus.engine import ChessEngineNode

with ChessEngineNode(default_difficulty="MEDIUM") as node:
    # "position X" llega como FEN (más adelante, desde el nodo de visión)
    resp = node.compute_move_from_fen(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    )
    print(resp.uci)   # 'd2d4'   -> útil para mover el brazo (origen->destino)
    print(resp.san)   # 'd4'
    if resp.is_capture:
        print("retirar pieza de", resp.captured_square)

    # Cambiar la dificultad en caliente
    node.set_difficulty("MAXIMUM")
```

### Niveles de dificultad

Configurables por nodo o por petición. Cada nivel ajusta la fuerza del motor
(Skill Level / Elo objetivo) y cuánto piensa:

| Nivel       | Elo aprox. | Pensado para |
|-------------|-----------|--------------|
| `BEGINNER`  | ~1350     | principiantes / juega casi al azar |
| `EASY`      | ~1500     | fácil |
| `MEDIUM`    | ~1800     | intermedio (por defecto) |
| `HARD`      | ~2200     | difícil |
| `EXPERT`    | ~2600     | experto |
| `MAXIMUM`   | máximo    | fuerza completa del motor |

### Demo por línea de comandos

```bash
# Jugada desde la posición inicial
python examples/run_engine_node.py --difficulty MEDIUM

# Una posición concreta, máxima dificultad, salida JSON
python examples/run_engine_node.py --fen "<FEN>" --difficulty MAXIMUM --json

# El engine jugando contra sí mismo (6 medias-jugadas)
python examples/run_engine_node.py --selfplay 6 --difficulty EASY
```

### Tests

```bash
pip install pytest
pytest tests/      # los tests de integración se omiten si no hay motor instalado
```
