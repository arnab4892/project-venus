"""Per-client runtime config (``clients/<client>/config.yaml``).

Framework code stays client-agnostic (CLAUDE.md): anything Jyotech-specific — the gas
alias map, handoff routing, theme — lives in the client's ``config.yaml`` and is read
through here. Loading is cached; values are plain data.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _config_path(client: str) -> Path:
    return _REPO_ROOT / "clients" / client / "config.yaml"


@lru_cache
def load_client_config(client: str) -> dict:
    """Read ``clients/<client>/config.yaml`` (empty dict if absent)."""
    path = _config_path(client)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def gas_alias_map(client: str) -> dict[str, str]:
    """Query-time gas aliases (``hydrogen`` → ``H2`` …), keys lower-cased (LLD-TOOL-01)."""
    raw = load_client_config(client).get("gas_aliases") or {}
    return {str(k).strip().lower(): str(v) for k, v in raw.items()}
