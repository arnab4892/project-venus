"""Pass 2 — typed extraction (LLD-EXT-04…08).

For each classified section (``other`` already dropped) the extractor calls the
type's schema, applies the **verbatim evidence gate** (dropping and logging any
ungrounded field), runs unit/range normalisation in Python, and hands the result
to the client's ``build_rows`` — which enforces the frozen family list and emits
``staging.*`` row tuples. A corpus-wide pass then links unresolved same-family
capacity conflicts (20000 vs 25000) via ``conflict_group``.

The typed schemas and row mapping are client-supplied (``clients/<client>/
schemas.py``); this module is the framework machinery around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType

from agentkit.extract.classify import Classification
from agentkit.extract.evidence import Drop, apply_gate
from agentkit.extract.llm import (
    CallLog,
    CompleteFn,
    ExtractionSkipped,
    ExtractorClient,
    call_json,
)
from agentkit.extract.markdown import Section


@dataclass
class ExtractionResult:
    rows: list[tuple[str, dict]] = field(default_factory=list)
    drops: list[Drop] = field(default_factory=list)
    skipped_sections: list[str] = field(default_factory=list)
    # Per-section raw LLM outputs, so a future gate change can be re-applied offline
    # without re-calling the model: {section_id: {"type": ..., "raw": {...}}}.
    raw_outputs: dict[str, dict] = field(default_factory=dict)


# Types that assign a frozen family id and so get the family catalogue injected.
_FAMILY_BEARING = {"capability_spec", "product_list"}


def _messages(
    prompt_body: str, section: Section, *, family_catalog: str | None, type_: str
) -> list[dict[str, str]]:
    heading = " › ".join(section.heading_path) if section.heading_path else "(preamble)"
    user = (
        f"DOC: {section.doc_id}\nHEADING: {heading}\nLOCATOR: {section.locator}\n\n"
        f"{section.text}"
    )
    messages = [{"role": "system", "content": prompt_body}]
    if family_catalog and type_ in _FAMILY_BEARING:
        messages.append({
            "role": "system",
            "content": (
                "FROZEN family list — assign `family.value` to exactly one of these "
                "ids, or omit `family` if none fits (do NOT invent an id):\n"
                + family_catalog
            ),
        })
    messages.append({"role": "user", "content": user})
    return messages


def extract_sections(
    pairs: list[tuple[Section, Classification]],
    *,
    schemas: ModuleType,
    prompt_bodies: dict[str, str],
    family_ids: set[str],
    family_catalog: str | None = None,
    client: ExtractorClient | None = None,
    complete: CompleteFn | None = None,
    log: CallLog | None = None,
) -> ExtractionResult:
    """Run pass-2 extraction over classified sections. ``other`` is ignored."""
    result = ExtractionResult()
    for section, classification in pairs:
        type_ = classification.type
        if type_ not in schemas.SCHEMAS:
            continue  # 'other' and any unknown type carry no facts
        try:
            raw = call_json(
                client=client,
                complete=complete,
                messages=_messages(
                    prompt_bodies[type_], section,
                    family_catalog=family_catalog, type_=type_,
                ),
                json_schema=schemas.json_schema_for(type_),
                schema_name=f"extract_{type_}",
                kind=f"extract:{type_}",
                section_id=section.section_id,
                log=log,
            )
        except ExtractionSkipped:
            result.skipped_sections.append(section.section_id)
            continue

        result.raw_outputs[section.section_id] = {"type": type_, "raw": raw}
        # Gate against heading + body: the heading is verbatim source text (LLD-EXT-04).
        gated, drops = apply_gate(raw, section.evidence_text, section_id=section.section_id)
        result.drops.extend(drops)
        result.rows.extend(
            schemas.build_rows(
                section, type_, gated,
                family_ids=family_ids, confidence=classification.confidence,
            )
        )

    # Corpus-wide: link unresolved same-family capacity conflicts, don't resolve.
    cap_rows = [row for table, row in result.rows if table == "capability_row"]
    schemas.mark_conflicts(cap_rows)
    return result
