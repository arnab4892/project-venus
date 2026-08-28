"""Load extraction prompt bodies from ``clients/<client>/prompts/`` (LLD-EXT §3).

Prompts are file-based for now. NOTE (milestone): in a later milestone these
bodies are loaded into ``ops.prompt_version`` and versioned there; keeping them
as files under the client dir keeps the framework free of prompt text and lets
this milestone run without the ``ops.*`` tables (which do not exist yet).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENTS_DIR = _REPO_ROOT / "clients"


def prompts_dir(client: str) -> Path:
    return _CLIENTS_DIR / client / "prompts"


def load_prompt(client: str, name: str) -> str:
    """Read ``clients/<client>/prompts/<name>.md`` (``.txt`` fallback)."""
    base = prompts_dir(client)
    for ext in (".md", ".txt"):
        path = base / f"{name}{ext}"
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no prompt {name!r} under {base}")
