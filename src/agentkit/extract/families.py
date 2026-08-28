"""Pass 1 — family discovery (LLD-EXT-03).

One whole-corpus prompt proposes the ``product_family`` list. The result is
written to ``clients/<client>/families.yaml`` and the run **stops** for human
confirmation. Pass 2 treats that file as frozen: extractors assign only ids
present in it (anything else → ``needs_family``), never a new id.

Only the *classified* sections (capability/product/company/office — not
``other``) are fed in, and only their heading + text, so the proposal is grounded
in the same public content the extractors will see.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentkit.extract.llm import CallLog, CompleteFn, ExtractorClient, call_json
from agentkit.extract.markdown import Section
from agentkit.extract.registry import client_dir

FAMILY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "division": {"type": "string"},
                    "category": {"type": "string"},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["id", "division", "category", "name", "summary", "source"],
            },
        }
    },
    "required": ["families"],
}


def families_path(client: str) -> Path:
    return client_dir(client) / "families.yaml"


def _corpus_digest(sections: list[Section], *, max_chars: int = 60_000) -> str:
    """A compact, grounded digest of the classified sections for the whole-corpus prompt."""
    parts: list[str] = []
    for s in sections:
        heading = " › ".join(s.heading_path) if s.heading_path else "(preamble)"
        parts.append(f"## [{s.doc_id}] {heading} ({s.locator})\n{s.text}")
    digest = "\n\n".join(parts)
    return digest[:max_chars]


def discover_families(
    sections: list[Section],
    prompt_body: str,
    *,
    client: ExtractorClient | None = None,
    complete: CompleteFn | None = None,
    log: CallLog | None = None,
) -> list[dict]:
    """Propose the family list from the classified corpus (LLD-EXT-03)."""
    messages = [
        {"role": "system", "content": prompt_body},
        {"role": "user", "content": _corpus_digest(sections)},
    ]
    result = call_json(
        client=client,
        complete=complete,
        messages=messages,
        json_schema=FAMILY_SCHEMA,
        schema_name="family_discovery",
        kind="family_discovery",
        section_id=None,
        log=log,
    )
    return list(result.get("families", []) or [])


def write_families_yaml(client: str, families: list[dict]) -> Path:
    """Write the proposed families to ``clients/<client>/families.yaml`` (pass-1 gate)."""
    path = families_path(client)
    header = (
        "# Pass-1 family discovery (LLD-EXT-03) — REVIEW AND EDIT, then re-run extract.\n"
        "# Pass 2 treats this list as FROZEN: extractors assign only ids present here;\n"
        "# anything unassignable is flagged needs_family, never given a new id.\n"
    )
    body = yaml.safe_dump({"families": families}, sort_keys=False, allow_unicode=True)
    path.write_text(header + body, encoding="utf-8")
    return path


def load_family_ids(client: str) -> set[str]:
    """The frozen set of family ids from ``families.yaml`` (empty if absent)."""
    path = families_path(client)
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {f["id"] for f in data.get("families", []) if f.get("id")}
