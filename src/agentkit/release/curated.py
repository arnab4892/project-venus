"""Curated release inputs: the frozen family catalogue and the region_state seed.

These are the two ``facts.*`` sources that do NOT come from staging review:
``product_family`` is built from ``families.yaml`` at promote (LLD-REL-05), and
``region_state`` is curated configuration (LLD-DB-07). Both live under
``clients/<client>/`` and are loaded here for ``release check`` and ``release
promote``.
"""

from __future__ import annotations

import yaml

from agentkit.extract.registry import client_dir


def parse_source(source: str | None) -> tuple[str | None, str | None]:
    """Parse a ``families.yaml`` ``source`` into ``(source_doc_id, source_locator)``.

    A source may list several ``;``-separated ``doc.<id> §<locator>`` tokens; the
    first wins. The ``§`` locator is optional (a doc may be cited whole).
    """
    if not source:
        return None, None
    first = source.split(";")[0].strip()
    if not first:
        return None, None
    if " §" in first:
        doc, loc = first.split(" §", 1)
        return doc.strip(), "§" + loc.strip()
    parts = first.split(None, 1)
    doc = parts[0].strip()
    loc = parts[1].strip() if len(parts) > 1 else None
    return doc, loc


def load_region_states(client: str) -> list[dict]:
    """The curated ``region_state`` rows (``state, region, office_id`` + aux ``cities``)."""
    path = client_dir(client) / "seeds" / "region_state.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("region_states", []) or [])


def _norm_city(city: str | None) -> str:
    return (city or "").strip().upper()


def city_to_state(client: str) -> dict[str, dict]:
    """Map a normalised (upper) city name to its curated ``{state, region, office_id}``."""
    out: dict[str, dict] = {}
    for entry in load_region_states(client):
        meta = {k: entry.get(k) for k in ("state", "region", "office_id")}
        for city in entry.get("cities", []) or []:
            out[_norm_city(city)] = meta
    return out
