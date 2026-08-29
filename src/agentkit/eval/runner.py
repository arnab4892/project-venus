"""Golden-suite runner (LLD-EVAL-01/02/03).

Runs ``clients/<client>/golden/questions.yaml`` at two of its three layers against the live
active release:

* **fact** — a named tool call returns the expected ids (cap/family/product/office/fact);
* **retrieval** — the expected chunk is in ``search_documents`` top-k.

The **end-to-end** layer (agent answer contains/omits) needs the agents (milestone 5), so it
is reported ``pending`` per question — never failed. A run fails (``ok = False``) iff any
fact or retrieval check that actually executed failed; that gates ``release promote`` /
``prompt activate`` (LLD-EVAL-03). Questions carry the LLD-EVAL-01 fields plus optional
``fact`` / ``retrieval`` execution blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import Connection

from agentkit.client_config import gas_alias_map
from agentkit.config import Settings, get_settings
from agentkit.retrieval.embed import EmbedFn
from agentkit.tools.get_company_fact import get_company_fact
from agentkit.tools.get_office import get_office
from agentkit.tools.get_product import get_product
from agentkit.tools.list_products import list_products
from agentkit.tools.match_capability import match_capability
from agentkit.tools.search_documents import search_documents

PASS, FAIL, NA, PENDING = "pass", "fail", "na", "pending"
_ID_KEYS = {"cap_id", "family_id", "office_id", "product_id", "fact_id", "chunk_id"}

_REPO_ROOT = Path(__file__).resolve().parents[3]


def questions_path(client: str) -> Path:
    return _REPO_ROOT / "clients" / client / "golden" / "questions.yaml"


@dataclass
class LayerResult:
    layer: str
    status: str
    detail: str = ""


@dataclass
class QuestionResult:
    id: str
    expected_outcome: str
    layers: list[LayerResult] = field(default_factory=list)

    def status(self, layer: str) -> str:
        for r in self.layers:
            if r.layer == layer:
                return r.status
        return NA


@dataclass
class EvalReport:
    results: list[QuestionResult]

    @property
    def ok(self) -> bool:
        return not any(
            r.status in (FAIL,) for q in self.results for r in q.layers if r.layer != "e2e"
        )

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for q in self.results:
            for r in q.layers:
                out.setdefault(r.layer, {}).setdefault(r.status, 0)
                out[r.layer][r.status] += 1
        return out


def _collect_ids(obj) -> set[str]:
    """Recursively gather id-like string values from a tool result."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _ID_KEYS and isinstance(value, str):
                found.add(value)
            else:
                found |= _collect_ids(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _collect_ids(item)
    return found


def _call_tool(conn: Connection, spec: dict, *, client: str) -> dict:
    tool = spec["tool"]
    args = dict(spec.get("args") or {})
    if tool == "match_capability":
        return match_capability(conn, gas_aliases=gas_alias_map(client), **args)
    if tool == "get_product":
        return get_product(conn, args["model_or_family"])
    if tool == "list_products":
        return list_products(conn, args["division"], category=args.get("category"))
    if tool == "get_company_fact":
        return get_company_fact(conn, args["kind"])
    if tool == "get_office":
        return get_office(conn, **args)
    raise ValueError(f"unknown tool in golden question: {tool!r}")


def _fact_layer(conn: Connection, q: dict, *, client: str) -> LayerResult:
    spec = q.get("fact")
    if not spec:
        return LayerResult("fact", NA, "no fact block")
    result = _call_tool(conn, spec, client=client)
    ids = _collect_ids(result)
    need = set(spec.get("expect_ids") or [])
    missing = need - ids
    if missing:
        return LayerResult("fact", FAIL, f"missing {sorted(missing)}; got {sorted(ids)}")
    return LayerResult("fact", PASS, f"{spec['tool']} returned {sorted(need)}")


def _retrieval_layer(
    conn: Connection, q: dict, *, embed: EmbedFn | None, settings: Settings
) -> LayerResult:
    spec = q.get("retrieval")
    if not spec:
        return LayerResult("retrieval", NA, "no retrieval block")
    try:
        res = search_documents(
            conn,
            spec.get("query") or q["question"],
            division=spec.get("division"),
            family_ids=spec.get("family_ids"),
            k=spec.get("k", 5),
            embed=embed,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - infra (embedder/vec) missing ≠ content failure
        return LayerResult("retrieval", NA, f"not evaluated: {type(exc).__name__}: {exc}")
    hits = res["chunks"]
    if "expect_chunk_ids" in spec:
        got = {c["chunk_id"] for c in hits}
        ok = bool(set(spec["expect_chunk_ids"]) & got)
        return LayerResult("retrieval", PASS if ok else FAIL, f"top-k={sorted(got)}")
    if "expect_locator" in spec:
        want = spec["expect_locator"].lower()
        ok = any(want in (c["locator"] or "").lower() for c in hits)
        locs = [c["locator"] for c in hits]
        return LayerResult("retrieval", PASS if ok else FAIL, f"top-k locators={locs}")
    if "expect_doc" in spec:
        ok = any(c["doc_id"] == spec["expect_doc"] for c in hits)
        docs = [c["doc_id"] for c in hits]
        return LayerResult("retrieval", PASS if ok else FAIL, f"top-k docs={docs}")
    return LayerResult("retrieval", NA, "retrieval block has no expectation")


def run_eval(
    conn: Connection,
    client: str,
    *,
    questions: list[dict] | None = None,
    embed: EmbedFn | None = None,
    settings: Settings | None = None,
) -> EvalReport:
    """Run the fact + retrieval layers of the golden suite; mark e2e pending."""
    settings = settings or get_settings()
    if questions is None:
        data = yaml.safe_load(questions_path(client).read_text(encoding="utf-8")) or {}
        questions = data.get("questions") or []

    results: list[QuestionResult] = []
    for q in questions:
        qr = QuestionResult(id=q["id"], expected_outcome=q.get("expected_outcome", ""))
        qr.layers.append(_fact_layer(conn, q, client=client))
        qr.layers.append(_retrieval_layer(conn, q, embed=embed, settings=settings))
        qr.layers.append(LayerResult("e2e", PENDING, "needs agents (milestone 5)"))
        results.append(qr)
    return EvalReport(results=results)


def format_report(report: EvalReport) -> str:
    lines = ["Golden suite — fact + retrieval layers (e2e pending agents)", ""]
    lines.append(f"{'id':28} {'fact':6} {'retr':6} {'e2e':8}  detail")
    for q in report.results:
        fact = q.status("fact")
        retr = q.status("retrieval")
        detail = ""
        for r in q.layers:
            if r.status == FAIL:
                detail = f"{r.layer}: {r.detail}"
                break
        lines.append(f"{q.id:28} {fact:6} {retr:6} {'pending':8}  {detail}")
    lines.append("")
    for layer, buckets in report.counts().items():
        summary = " ".join(f"{k}={v}" for k, v in sorted(buckets.items()))
        lines.append(f"{layer:10} {summary}")
    lines.append("")
    lines.append("RESULT: " + ("PASS" if report.ok else "FAIL"))
    return "\n".join(lines)
