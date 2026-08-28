"""The verbatim evidence gate (LLD-EXT-04) — code, not prompt.

Every extracted field is an ``Evidenced`` value ``{"value": …, "evidence": …}``.
The rule the design insists on: ``evidence`` must be an **exact substring of the
section text after whitespace normalisation**. A field whose evidence fails is
**dropped and logged** — with its section id and raw output — never silently kept
and never "fixed" to look grounded.

This gate is schema-agnostic: it walks the parsed extraction dict, recognises
``Evidenced``-shaped nodes wherever they appear (scalars and inside lists), and
returns a cleaned copy plus the list of drops. Keeping it in the framework (no
client strings) means the same guarantee holds for every client's schemas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WS_RE = re.compile(r"\s+")


def normalise_ws(text: str) -> str:
    """Collapse all runs of whitespace to single spaces and strip the ends."""
    return _WS_RE.sub(" ", text).strip()


def is_verbatim(evidence: str, section_text: str) -> bool:
    """True iff ``evidence`` is a non-empty substring of ``section_text`` (ws-normalised)."""
    ev = normalise_ws(evidence or "")
    if not ev:
        return False
    return ev in normalise_ws(section_text)


def _is_evidenced(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node and "evidence" in node


@dataclass
class Drop:
    """One dropped field: enough to log the loss without guessing."""

    section_id: str
    path: str
    value: Any
    evidence: Any
    reason: str

    def as_record(self) -> dict:
        return {
            "section_id": self.section_id,
            "path": self.path,
            "value": self.value,
            "evidence": self.evidence,
            "reason": self.reason,
        }


def apply_gate(obj: Any, section_text: str, *, section_id: str) -> tuple[Any, list[Drop]]:
    """Return ``(cleaned, drops)`` — every ungrounded Evidenced node removed.

    * A failing scalar Evidenced field becomes ``None`` (dropped, logged).
    * A failing Evidenced element inside a list is removed from the list.
    * Non-Evidenced structure is preserved and recursed into.
    """
    drops: list[Drop] = []

    def recurse(node: Any, path: str) -> Any:
        if _is_evidenced(node):
            if is_verbatim(str(node.get("evidence", "")), section_text):
                return node
            drops.append(
                Drop(
                    section_id=section_id,
                    path=path,
                    value=node.get("value"),
                    evidence=node.get("evidence"),
                    reason="evidence not a verbatim substring of the section text",
                )
            )
            return None
        if isinstance(node, dict):
            return {k: recurse(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            kept: list[Any] = []
            for i, item in enumerate(node):
                cleaned = recurse(item, f"{path}[{i}]")
                # Drop failing Evidenced list elements entirely; keep other Nones
                # only if they were not themselves a dropped Evidenced node.
                if _is_evidenced(item) and cleaned is None:
                    continue
                kept.append(cleaned)
            return kept
        return node

    return recurse(obj, ""), drops


def unwrap(node: Any) -> Any:
    """Return the ``value`` of an Evidenced node, or the node itself if plain/None."""
    if _is_evidenced(node):
        return node.get("value")
    return node
