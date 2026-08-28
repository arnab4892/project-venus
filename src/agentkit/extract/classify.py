"""Section classifier (LLD-EXT-02).

Each section is routed to one of five types; ``other`` is skipped for facts (it is
still chunked later, in the retrieval milestone). The classification is persisted
per section (``classification.json``) so a re-run is inspectable.

Framework-generic: the prompt *body* is passed in (a client file); the type
taxonomy and JSON schema are framework-level (they are the LLD-EXT-02 enum).
"""

from __future__ import annotations

from dataclasses import dataclass

from agentkit.extract.llm import CallLog, CompleteFn, ExtractionSkipped, ExtractorClient, call_json
from agentkit.extract.markdown import Section

SECTION_TYPES = (
    "capability_spec", "product_list", "company_fact", "office_contact", "other",
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": list(SECTION_TYPES)},
        "confidence": {"type": "number"},
    },
    "required": ["type", "confidence"],
}


@dataclass
class Classification:
    section_id: str
    type: str
    confidence: float

    def as_record(self) -> dict:
        return {"section_id": self.section_id, "type": self.type, "confidence": self.confidence}


def _messages(prompt_body: str, section: Section) -> list[dict[str, str]]:
    heading = " › ".join(section.heading_path) if section.heading_path else "(preamble)"
    user = f"DOC: {section.doc_id}\nHEADING: {heading}\nLOCATOR: {section.locator}\n\n{section.text}"
    return [
        {"role": "system", "content": prompt_body},
        {"role": "user", "content": user},
    ]


def classify_section(
    section: Section,
    prompt_body: str,
    *,
    client: ExtractorClient | None = None,
    complete: CompleteFn | None = None,
    log: CallLog | None = None,
) -> Classification:
    """Classify one section. On a schema-invalid response after retries → ``other``."""
    try:
        result = call_json(
            client=client,
            complete=complete,
            messages=_messages(prompt_body, section),
            json_schema=CLASSIFY_SCHEMA,
            schema_name="section_classification",
            kind="classify",
            section_id=section.section_id,
            log=log,
        )
    except ExtractionSkipped:
        return Classification(section.section_id, "other", 0.0)
    type_ = result.get("type")
    if type_ not in SECTION_TYPES:
        type_ = "other"
    conf = result.get("confidence")
    return Classification(section.section_id, type_, float(conf) if conf is not None else 0.0)


def classify_sections(
    sections: list[Section],
    prompt_body: str,
    *,
    client: ExtractorClient | None = None,
    complete: CompleteFn | None = None,
    log: CallLog | None = None,
) -> list[Classification]:
    return [
        classify_section(s, prompt_body, client=client, complete=complete, log=log)
        for s in sections
    ]
