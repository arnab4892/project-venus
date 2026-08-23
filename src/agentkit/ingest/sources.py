"""Ingestion seed loading + deterministic doc-id derivation (LLD-ING-01/05).

``clients/<client>/seeds/sources.yaml`` is the single source of truth for what
the crawler fetches: the discovered page/PDF URLs, crawl settings, an explicit
``exclude`` list, and the CSS ``strip_selectors``. This module is client-agnostic
— it auto-detects the single ``clients/*`` dir the same way as
:mod:`agentkit.release.seed`, and holds no Jyotech-specific strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENTS_DIR = _REPO_ROOT / "clients"

# Release-candidate id used for stage-1 staging writes when no --rc is given.
# Real RC allocation (create/list/status) arrives with the release milestone;
# this bootstrap default lets ingestion record staging.document rows now.
BOOTSTRAP_RC_ID_TEMPLATE = "rc.{client}.bootstrap"


def bootstrap_rc_id(client: str) -> str:
    """The per-client bootstrap release-candidate id, e.g. ``rc.jyotech.bootstrap``."""
    return BOOTSTRAP_RC_ID_TEMPLATE.format(client=client)


@dataclass
class Sources:
    """Parsed ``sources.yaml`` for one client."""

    client: str
    start_url: str
    crawl: dict
    pages: list[str]
    pdfs: list[str]
    exclude: list[str] = field(default_factory=list)
    strip_selectors: list[str] = field(default_factory=list)
    # generic-cleaner value lists (per-client): image-alt placeholders and
    # teaser link stubs to drop during HTML conversion (LLD-ING-02).
    drop_alt_text: list[str] = field(default_factory=list)
    drop_link_text: list[str] = field(default_factory=list)

    def active_pages(self) -> list[str]:
        """Pages minus anything listed under ``exclude``."""
        drop = set(self.exclude)
        return [u for u in self.pages if u not in drop]

    def active_pdfs(self) -> list[str]:
        drop = set(self.exclude)
        return [u for u in self.pdfs if u not in drop]


def autodetect_client() -> str:
    """Return the sole ``clients/*`` id, or raise if there is not exactly one."""
    clients = sorted(p.name for p in _CLIENTS_DIR.iterdir() if p.is_dir())
    if len(clients) == 1:
        return clients[0]
    raise ValueError(f"Cannot auto-detect client from {clients!r}; pass an explicit client id.")


def load_sources(client: str | None = None, *, clients_dir: Path | None = None) -> Sources:
    """Load ``clients/<client>/seeds/sources.yaml`` into a :class:`Sources`."""
    client = client or autodetect_client()
    base = clients_dir or _CLIENTS_DIR
    data = yaml.safe_load((base / client / "seeds" / "sources.yaml").read_text())
    return Sources(
        client=data.get("client", client),
        start_url=data["start_url"],
        crawl=data.get("crawl", {}) or {},
        pages=list(data.get("pages", []) or []),
        pdfs=list(data.get("pdfs", []) or []),
        exclude=list(data.get("exclude", []) or []),
        strip_selectors=list(data.get("strip_selectors", []) or []),
        drop_alt_text=list(data.get("drop_alt_text", []) or []),
        drop_link_text=list(data.get("drop_link_text", []) or []),
    )


def doc_id_for(url: str) -> str:
    """Deterministic ``doc.<slug>`` id from a URL's last path segment.

    Stable across runs and across the raw-space / ``%20`` spellings of the same
    resource, so re-ingesting upserts the same ``staging.document`` row.
    """
    path = unquote(urlsplit(url).path)
    stem = Path(path).stem or "index"
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return f"doc.{slug}"


def kind_for(url: str) -> str:
    """``pdf`` for a ``.pdf`` path, else ``html`` (covers .html/.php)."""
    return "pdf" if urlsplit(url).path.lower().endswith(".pdf") else "html"
