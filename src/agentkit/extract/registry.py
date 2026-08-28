"""Load a client's extraction module by path (keeps the framework client-agnostic).

The typed schemas and their row-mapping live in ``clients/<client>/schemas.py``
(no Jyotech strings in ``src/agentkit``). The extractor loads that module at
runtime through this thin registry, the same way ingestion auto-detects the
single ``clients/*`` directory.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENTS_DIR = _REPO_ROOT / "clients"


def client_dir(client: str) -> Path:
    return _CLIENTS_DIR / client


def load_client_schemas(client: str) -> ModuleType:
    """Import ``clients/<client>/schemas.py`` as a module object."""
    path = _CLIENTS_DIR / client / "schemas.py"
    spec = importlib.util.spec_from_file_location(f"_client_{client}_schemas", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable if file exists
        raise ImportError(f"cannot load client schemas at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
