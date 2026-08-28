"""Extraction orchestrator (LLD-EXT-01…10).

Ties the pieces together and enforces the pass-1 human gate:

* ``families.yaml`` absent (or ``--redo-families``) → split + classify + propose
  families, write ``families.yaml``, **STOP** (return a :class:`FamiliesReport`).
* ``families.yaml`` present → load the frozen family list, run pass-2 typed
  extraction, and upsert the result into ``staging.*`` under the RC.

Section splits, classifications and per-call LLM logs are persisted under
``data/<client>/extract/<rc>/`` so a re-run is inspectable; token/cost totals are
surfaced in the returned report (not only in the gitignored log).

Runs on the caller's ``Connection`` and never commits.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from sqlalchemy import Connection

from agentkit.extract import families as fam
from agentkit.extract.classify import Classification, classify_sections
from agentkit.extract.extractors import extract_sections
from agentkit.extract.llm import CallLog, CompleteFn, ExtractorClient, build_extractor_client
from agentkit.extract.markdown import Section, corpus_sections, document_records
from agentkit.extract.prompts import load_prompt
from agentkit.extract.registry import load_client_schemas
from agentkit.extract.report import ExtractReport, FamiliesReport
from agentkit.extract.staging_write import write_rows
from agentkit.ingest.finish import output_path
from agentkit.release.candidate import get_rc

_DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


def _extract_dir(client: str, rc_id: str, data_root: Path | None) -> Path:
    root = data_root or _DEFAULT_DATA_ROOT
    return Path(root) / client / "extract" / rc_id


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _family_catalog(client: str) -> str:
    path = fam.families_path(client)
    if not path.exists():
        return ""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    lines = []
    for f in data.get("families", []) or []:
        lines.append(f"- {f.get('id')} [{f.get('division')}] {f.get('name')} — {f.get('summary')}")
    return "\n".join(lines)


def _load_classifications(path: Path) -> list[Classification] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Classification(d["section_id"], d["type"], d["confidence"]) for d in data]


def run_extract(
    client: str,
    *,
    rc_id: str,
    conn: Connection,
    data_root: Path | None = None,
    complete: CompleteFn | None = None,
    redo_families: bool = False,
) -> FamiliesReport | ExtractReport:
    """Run extraction for ``client`` under ``rc_id``. Returns the pass it reached."""
    if get_rc(conn, rc_id) is None:
        raise ValueError(
            f"unknown release candidate {rc_id!r}. Run `agentkit release create {client}` first."
        )

    schemas = load_client_schemas(client)
    ext_dir = _extract_dir(client, rc_id, data_root)
    log = CallLog(path=ext_dir / "llm-calls.jsonl")
    client_llm: ExtractorClient | None = None if complete else build_extractor_client()

    # 1. Section split (deterministic; persisted every run).
    sections: list[Section] = corpus_sections(client, data_root=data_root)
    _write_json(ext_dir / "sections.json", [s.as_record() for s in sections])
    by_id = {s.section_id: s for s in sections}

    # 2. Classification (cached under the RC unless a redo is forced).
    class_path = ext_dir / "classification.json"
    classifications = None if redo_families else _load_classifications(class_path)
    if classifications is None:
        classifications = classify_sections(
            sections, load_prompt(client, "classifier"),
            client=client_llm, complete=complete, log=log,
        )
        _write_json(class_path, [c.as_record() for c in classifications])
    kept = [(by_id[c.section_id], c) for c in classifications
            if c.type != "other" and c.section_id in by_id]

    # 3. Pass-1 gate: propose families and STOP if the list isn't approved yet.
    families_exist = fam.families_path(client).exists()
    if redo_families or not families_exist:
        proposed = fam.discover_families(
            [s for s, _ in kept], load_prompt(client, "family_discovery"),
            client=client_llm, complete=complete, log=log,
        )
        path = fam.write_families_yaml(client, proposed)
        return FamiliesReport(proposed, log.totals(), str(path))

    # 4. Pass 2: typed extraction under the frozen family list → staging.*.
    family_ids = fam.load_family_ids(client)
    prompt_bodies = {t: load_prompt(client, f"extract_{t}") for t in schemas.SCHEMAS}
    result = extract_sections(
        kept,
        schemas=schemas,
        prompt_bodies=prompt_bodies,
        family_ids=family_ids,
        family_catalog=_family_catalog(client),
        client=client_llm,
        complete=complete,
        log=log,
    )
    _write_json(ext_dir / "drops.json", [d.as_record() for d in result.drops])
    _write_json(ext_dir / "raw-extractions.json", result.raw_outputs)

    doc_rows = document_records(client, data_root=data_root)
    counts = write_rows(
        conn, rc_id=rc_id, document_rows=doc_rows, content_rows=result.rows
    )
    return ExtractReport(
        rc_id=rc_id,
        counts=counts,
        drops=result.drops,
        skipped_sections=result.skipped_sections,
        rows=result.rows,
        totals=log.totals(),
        family_count=len(family_ids),
    )


def md_dir_for(client: str, *, data_root: Path | None = None) -> Path:
    """The converted-Markdown directory for a client (guard used by the CLI)."""
    return output_path(client, "x", data_root=data_root).parent
