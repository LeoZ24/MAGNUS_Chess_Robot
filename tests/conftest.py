"""Configuración compartida de pytest.

Garantiza que el paquete ``magnus`` sea importable sin instalar el proyecto,
se invoque pytest como se invoque: ``pytest tests/`` (el binario) NO añade la
raíz del repo a ``sys.path`` — solo ``python -m pytest`` lo hace — y sin esto
la importación de los módulos de test falla según el orden de colección.
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
