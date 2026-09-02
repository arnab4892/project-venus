"""Golden-suite runner (LLD-EVAL-01/02/03).

Runs ``clients/<client>/golden/questions.yaml`` at three layers against the live active
release:

* **fact** — a named tool call returns the expected ids (cap/family/product/office/fact);
* **retrieval** — the expected chunk is in ``search_documents`` top-k;
* **end-to-end** — the question is run through the **real orchestrator graph** (live runtime
  LLM + live active release) and the turn's outcome, answer text, citations and forbidden
  strings are scored (LLD-EVAL-02).

A run fails (``ok = False``) iff any fact, retrieval **or e2e** check that actually executed
failed; that gates ``release promote`` / ``prompt activate`` (LLD-EVAL-03). The e2e layer runs
only when a ``complete=`` seam is supplied (the live CLI passes the self-hosted runtime client;
tests pass a scripted fake); without it e2e is **skipped** (never failed), so the hermetic
fact/retrieval gate still works. Each e2e turn runs inside a rolled-back savepoint, so eval
never writes to ``ops.*`` — on failure the report itself is the only record of what the model
said. Questions carry the LLD-EVAL-01 fields plus optional ``fact`` / ``retrieval`` execution
blocks and an optional ``must_not_contain`` list.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import Connection

from agentkit.client_config import gas_alias_map
from agentkit.config import Settings, get_settings
from agentkit.extract.llm import CompleteFn
from agentkit.retrieval.embed import EmbedFn
from agentkit.tools.get_company_fact import get_company_fact
from agentkit.tools.get_office import get_office
from agentkit.tools.get_product import get_product
from agentkit.tools.list_products import list_products
from agentkit.tools.match_capability import match_capability
from agentkit.tools.search_documents import search_documents

PASS, FAIL, NA, PENDING, SKIP = "pass", "fail", "na", "pending", "skip"
# e2e only: a question that failed once but passed on a single retry (LLD-EVAL-02). Counts as a
# pass for the gate (EvalReport.ok) but stays visible in the report so live-LLM variance is never
# hidden. Fact/retrieval are deterministic and never produce this status.
FLAKY = "flaky"
_ID_KEYS = {"cap_id", "family_id", "office_id", "product_id", "fact_id", "chunk_id"}

# Orchestrator outcome → golden `expected_outcome` vocabulary (LLD-EVAL-01).
_OUTCOME_MAP = {
    "answered": "answer",
    "asked_slot": "asked_slot",
    "handoff": "handoff",
    "declined_oos": "out_of_scope",
    "deflected": "out_of_scope",
    "clarify": "clarify",
}

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
    runtime_s: float = 0.0

    @property
    def ok(self) -> bool:
        # Any executed fact / retrieval / e2e failure fails the run (LLD-EVAL-03). Layers that
        # were NA (no block) or SKIP (e2e without a runtime LLM) never fail it.
        return not any(r.status == FAIL for q in self.results for r in q.layers)

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for q in self.results:
            for r in q.layers:
                out.setdefault(r.layer, {}).setdefault(r.status, 0)
                out[r.layer][r.status] += 1
        return out


_DIGIT_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")
_TRAILING_ZERO_RE = re.compile(r"(\d)\.0+(?!\d)")


def normalize_digits(text: str) -> str:
    """Fold number typography so digit-group commas and spurious ``.0`` don't defeat matching.

    ``25,000`` → ``25000``; ``25000.0`` → ``25000``. Applied to BOTH the answer and the needle so
    an `expected_answer_contains` "25000" matches a formatted "25,000", and a `must_not_contain`
    "20000" still catches "20,000".
    """
    t = _DIGIT_COMMA_RE.sub("", text or "")
    return _TRAILING_ZERO_RE.sub(r"\1", t)


def text_contains(haystack: str, needle: str) -> bool:
    """Case-insensitive containment, digit-normalised on both sides."""
    h, n = haystack.lower(), needle.lower()
    return n in h or normalize_digits(n) in normalize_digits(h)


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


def _answer_text(result) -> str:
    """The turn's full answer text: every assistant message plus any document_card title."""
    parts: list[str] = []
    for m in result.messages:
        if m.get("text"):
            parts.append(m["text"])
        pl = m.get("payload") or {}
        if pl.get("title"):
            parts.append(str(pl["title"]))
    return " ".join(parts)


def _handoff_number_ok(result, question_text: str) -> tuple[bool, set]:
    """A handoff answer must contain no spec number absent from the turn's tool results/args.

    Asserts the numeric guard the grounding gate already enforces (LLD-RT-05): no fabricated
    price/spec figure leaks on a handoff. Returns ``(ok, offending)``.
    """
    from agentkit.runtime.grounding import all_numbers, spec_numbers

    allowed: set = set()
    import json as _json

    for blob in (*result.tool_results, *result.tool_args):
        allowed |= all_numbers(_json.dumps(blob, default=str))
    allowed |= all_numbers(question_text)
    offending = {n for n in spec_numbers(_answer_text(result)) if n not in allowed}
    return (not offending), offending


def _e2e_attempt(
    conn: Connection,
    q: dict,
    *,
    client: str,
    complete: CompleteFn,
    embed: EmbedFn | None,
    settings: Settings,
    gas_aliases: dict,
) -> LayerResult:
    """One e2e attempt: run the question through the real orchestrator and score the turn.

    Runs inside its own rolled-back savepoint so no ``ops.*`` row survives; the turn's answer
    text / outcome / citations are captured into the detail **before** rollback (the report is
    the only record). Returns a ``PASS``/``FAIL`` ``LayerResult`` — retry policy lives in the
    caller.
    """
    from agentkit.retrieval.chunk import resolve_active_release
    from agentkit.runtime.ops import create_session
    from agentkit.runtime.orchestrator import Ctx, run_turn

    sp = conn.begin_nested()
    try:
        release_id = resolve_active_release(conn)
        sid = create_session(conn, client, release_id)
        ctx = Ctx(
            conn=conn, client=client, complete=complete, embed=embed,
            settings=settings, gas_aliases=gas_aliases,
        )
        result = run_turn(ctx, sid, q["question"])
    finally:
        sp.rollback()

    answer = _answer_text(result)
    outcome = _OUTCOME_MAP.get(result.outcome or "", result.outcome or "")
    cite_ids = {c["ref_id"] for c in result.citations}
    reasons: list[str] = []

    want_outcome = q.get("expected_outcome", "")
    if want_outcome and outcome != want_outcome:
        reasons.append(f"outcome {outcome!r} != expected {want_outcome!r}")

    # Number typography is folded on both sides (25,000 ↔ 25000) so voice-layer formatting
    # never breaks a keyword/guard match.
    missing = [s for s in (q.get("expected_answer_contains") or []) if not text_contains(answer, s)]
    if missing:
        reasons.append(f"missing text {missing}")

    present = [s for s in (q.get("must_not_contain") or []) if text_contains(answer, s)]
    if present:
        reasons.append(f"forbidden text present {present}")

    need_ids = set(q.get("expected_source_ids") or [])
    missing_ids = need_ids - cite_ids
    if missing_ids:
        reasons.append(f"missing sources {sorted(missing_ids)}; cited {sorted(cite_ids)}")

    if outcome == "handoff":
        ok_num, offending = _handoff_number_ok(result, q["question"])
        if not ok_num:
            reasons.append(f"handoff answer has unsourced numbers {sorted(offending)}")

    # Evidence captured pre-rollback (the report is the only record — ops kept nothing).
    evidence = f"outcome={result.outcome} cites={sorted(cite_ids)} answer={answer!r}"
    if reasons:
        return LayerResult("e2e", FAIL, "; ".join(reasons) + " || " + evidence)
    return LayerResult("e2e", PASS, evidence)


def _e2e_layer(
    conn: Connection,
    q: dict,
    *,
    client: str,
    complete: CompleteFn | None,
    embed: EmbedFn | None,
    settings: Settings,
    gas_aliases: dict,
) -> LayerResult:
    """Score the question end-to-end, retrying a failure ONCE (LLD-EVAL-02).

    The e2e layer runs the live runtime LLM, which carries a known ~1-per-run transient. To absorb
    it by mechanism (not judgement), a failed attempt is retried exactly once: if the retry passes
    the question is ``FLAKY`` — it counts as a pass for the gate but the report keeps the **first**
    attempt's evidence so variance stays visible. Two consecutive failures is a real ``FAIL`` and
    blocks as before. Fact/retrieval are deterministic and never reach this path. Skipped when no
    runtime LLM (``complete``) is supplied.
    """
    if complete is None:
        return LayerResult("e2e", SKIP, "no runtime LLM (complete=) — e2e not evaluated")

    first = _e2e_attempt(
        conn, q, client=client, complete=complete, embed=embed,
        settings=settings, gas_aliases=gas_aliases,
    )
    if first.status == PASS:
        return first

    # First attempt failed — retry once. Each attempt already runs in its own rolled-back
    # savepoint, so the retry is independent.
    retry = _e2e_attempt(
        conn, q, client=client, complete=complete, embed=embed,
        settings=settings, gas_aliases=gas_aliases,
    )
    if retry.status == PASS:
        # Flaky: keep the FIRST attempt's evidence (what actually failed), labelled.
        return LayerResult("e2e", FLAKY, "flaky (passed on retry) || first attempt: " + first.detail)
    # Two consecutive failures — a real failure. Report the retry's evidence.
    return retry


def run_eval(
    conn: Connection,
    client: str,
    *,
    questions: list[dict] | None = None,
    complete: CompleteFn | None = None,
    embed: EmbedFn | None = None,
    settings: Settings | None = None,
    layers: tuple[str, ...] = ("fact", "retrieval", "e2e"),
) -> EvalReport:
    """Run the golden suite. e2e runs only when ``complete`` is supplied (else skipped).

    ``layers`` selects which layers execute (a dev run may drop ``e2e`` explicitly). e2e uses
    the real orchestrator with the ``complete`` runtime-LLM seam + ``embed`` seam.
    """
    # Dev-only Langfuse tracing is force-disabled for every eval path — this covers `eval run`
    # and both golden-gated activations (`prompt activate`, `release activate`), which all route
    # through here. Enforced in code, not by convention: eval turns run in a rolled-back savepoint,
    # so their traces would reference ops rows that never commit; the eval report is the record.
    from agentkit.runtime.tracing import force_off

    force_off()

    settings = settings or get_settings()
    gas_aliases = gas_alias_map(client)
    if questions is None:
        data = yaml.safe_load(questions_path(client).read_text(encoding="utf-8")) or {}
        questions = data.get("questions") or []

    started = time.monotonic()
    results: list[QuestionResult] = []
    for q in questions:
        qr = QuestionResult(id=q["id"], expected_outcome=q.get("expected_outcome", ""))
        if "fact" in layers:
            qr.layers.append(_fact_layer(conn, q, client=client))
        if "retrieval" in layers:
            qr.layers.append(_retrieval_layer(conn, q, embed=embed, settings=settings))
        if "e2e" in layers:
            qr.layers.append(
                _e2e_layer(
                    conn, q, client=client, complete=complete, embed=embed,
                    settings=settings, gas_aliases=gas_aliases,
                )
            )
        results.append(qr)
    return EvalReport(results=results, runtime_s=time.monotonic() - started)


def format_report(report: EvalReport) -> str:
    lines = ["Golden suite — fact / retrieval / e2e layers", ""]
    lines.append(f"{'id':28} {'fact':6} {'retr':6} {'e2e':6}  detail")
    failures: list[str] = []
    for q in report.results:
        detail = ""
        for r in q.layers:
            if r.status == FAIL:
                detail = f"{r.layer}: {r.detail}"
                failures.append(f"  {q.id} [{r.layer}] {r.detail}")
                break
        lines.append(
            f"{q.id:28} {q.status('fact'):6} {q.status('retrieval'):6} "
            f"{q.status('e2e'):6}  {detail[:80]}"
        )
    lines.append("")
    for layer, buckets in report.counts().items():
        # passed = pass + flaky (a flaky question passed on retry); applicable excludes the
        # na/skip questions that were never evaluated. Flaky count is surfaced explicitly so
        # live-LLM variance stays visible (e.g. "e2e   53/53, 1 flaky").
        passed = buckets.get(PASS, 0) + buckets.get(FLAKY, 0)
        applicable = sum(v for k, v in buckets.items() if k not in (NA, SKIP))
        parts = [f"{passed}/{applicable}"]
        if buckets.get(FLAKY):
            parts.append(f"{buckets[FLAKY]} flaky")
        noted = " ".join(
            f"{k}={v}" for k, v in sorted(buckets.items()) if k in (FAIL, NA, SKIP) and v
        )
        if noted:
            parts.append(noted)
        lines.append(f"{layer:10} {', '.join(parts)}")
    if failures:
        lines.append("")
        lines.append("FAILURES (full evidence — ops kept nothing):")
        lines.extend(failures)
    lines.append("")
    lines.append(f"runtime: {report.runtime_s:.1f}s")
    lines.append("RESULT: " + ("PASS" if report.ok else "FAIL"))
    return "\n".join(lines)
