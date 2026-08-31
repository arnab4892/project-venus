"""Adapter-layer tests for the Gradio dev harness (apps/harness.py).

Pure-unit: the turn function and session creation are injected mocks — no gradio, no DB,
no live LLM. Covers session-id handling, the streamed update ordering (status → token →
final), Sources dedup, and the background-thread error path.
"""

from __future__ import annotations

from types import SimpleNamespace

from apps.harness import (
    ERROR,
    FINAL,
    STATUS,
    TOKEN,
    build_sources,
    get_or_create_session,
    stream_turn,
)

FINAL_TEXT = "That duty sits within our range [tr1]. Contact sales to proceed."


def _fake_result() -> SimpleNamespace:
    """A RunResult-shaped stand-in with two citations sharing one url (to test dedup)."""
    return SimpleNamespace(
        turn_id="turn-123",
        triage={
            "division": "industrial",
            "intent": "application_enquiry",
            "language": "en",
            "in_scope": True,
            "confidence": 0.95,
        },
        outcome="answered",
        route="application_discovery",
        grounding={"status": "full", "grounded": True},
        messages=[
            {"role": "assistant", "kind": "text", "text": FINAL_TEXT, "payload": None},
            {
                "role": "assistant",
                "kind": "document_card",
                "text": "",
                "payload": {
                    "title": "Process Compressors",
                    "url": "https://jyotech.com/process.pdf",
                    "locator": "PROCESS.pdf §Process Compressors",
                },
            },
        ],
        citations=[
            {"kind": "capability", "ref_id": "cap.002", "locator": None, "url": None},
            {
                "kind": "document",
                "ref_id": "doc.7",
                "locator": "PROCESS.pdf §Process Compressors",
                "url": "https://jyotech.com/process.pdf",
            },
            {
                "kind": "chunk",
                "ref_id": "chunk.9",
                "locator": "PROCESS.pdf §Process Compressors",
                "url": "https://jyotech.com/process.pdf",  # same url → deduped away
            },
        ],
        invocations=[
            {
                "agent": "application_discovery",
                "prompt_id": "prompt-42",
                "tool_calls": [
                    {"tool": "match_capability", "args": {"gas": "hydrogen"}, "rows_returned": 1}
                ],
            }
        ],
    )


def _counting_provider(ids=("sess-1", "sess-2")):
    """A session_provider that hands out ids in order and records how often it was called."""
    calls = {"n": 0}
    seq = list(ids)

    def provider():
        calls["n"] += 1
        return seq[calls["n"] - 1]

    return provider, calls


# --- session-id handling ---------------------------------------------------


def test_session_created_once_then_reused():
    provider, calls = _counting_provider()
    state: dict = {}

    first = get_or_create_session(state, provider)
    second = get_or_create_session(state, provider)

    assert first == "sess-1"
    assert second == "sess-1"  # reused, not regenerated
    assert calls["n"] == 1
    assert state["session_id"] == "sess-1"


def test_stream_turn_creates_session_once_across_turns():
    provider, calls = _counting_provider()
    state: dict = {}
    seen_sids: list = []

    def turn_runner(session_id, message):
        seen_sids.append(session_id)
        return _fake_result()

    for _ in stream_turn(
        "q1", state, turn_runner=turn_runner, session_provider=provider, poll=0.01
    ):
        pass
    for _ in stream_turn(
        "q2", state, turn_runner=turn_runner, session_provider=provider, poll=0.01
    ):
        pass

    assert calls["n"] == 1  # session created on the first message only
    assert seen_sids == ["sess-1", "sess-1"]  # same session id threaded into both turns


# --- streamed update ordering + content ------------------------------------


def test_stream_turn_yields_status_then_tokens_then_final():
    state: dict = {}
    provider, _ = _counting_provider()

    def turn_runner(session_id, message):
        return _fake_result()

    updates = list(
        stream_turn(
            "hello", state, turn_runner=turn_runner, session_provider=provider, poll=0.01
        )
    )

    kinds = [u.kind for u in updates]
    # at least one status, then tokens, then exactly one terminal final — in that order
    assert kinds[0] == STATUS
    assert kinds[-1] == FINAL
    assert kinds.count(FINAL) == 1
    assert ERROR not in kinds

    first_token = kinds.index(TOKEN)
    last_status = max(i for i, k in enumerate(kinds) if k == STATUS)
    assert last_status < first_token  # every status precedes every token
    assert all(k in (TOKEN, FINAL) for k in kinds[first_token:])

    # typewriter reproduces the gated final text exactly
    streamed = "".join(u.delta for u in updates if u.kind == TOKEN)
    final = updates[-1]
    assert streamed == final.text
    assert FINAL_TEXT in final.text

    # trace mirrors --show-trace content
    assert "turn-123" in final.trace
    assert "application_discovery" in final.trace
    assert "prompt-42" in final.trace
    assert "match_capability" in final.trace
    assert "rows=`1`" in final.trace

    # sources present and deduped on the terminal update
    assert final.sources
    assert any(s["ref_id"] == "cap.002" for s in final.sources)


# --- sources dedup ---------------------------------------------------------


def test_build_sources_dedupes_url_and_uses_card_title():
    rows = build_sources(_fake_result())

    # the two citations sharing the process.pdf url collapse to a single row
    pdf_rows = [r for r in rows if r["url"] == "https://jyotech.com/process.pdf"]
    assert len(pdf_rows) == 1
    # the friendly name comes from the document_card payload title
    assert pdf_rows[0]["name"] == "Process Compressors"
    # the url-less capability citation is kept as its own row
    assert any(r["ref_id"] == "cap.002" and r["url"] is None for r in rows)


# --- error path ------------------------------------------------------------


def test_stream_turn_surfaces_turn_runner_exception_as_error_update():
    state: dict = {}
    provider, _ = _counting_provider()

    def turn_runner(session_id, message):
        raise RuntimeError("boom in the graph")

    updates = list(
        stream_turn(
            "hello", state, turn_runner=turn_runner, session_provider=provider, poll=0.01
        )
    )

    kinds = [u.kind for u in updates]
    assert kinds[0] == STATUS
    assert kinds[-1] == ERROR
    assert kinds.count(ERROR) == 1  # exactly one terminal error, generator then ends
    assert TOKEN not in kinds
    assert FINAL not in kinds

    err = updates[-1]
    assert "RuntimeError" in err.text  # message shown to the user
    assert "boom in the graph" in err.trace  # full traceback into the trace panel


# --- real stage events: queue iterator + stage_source branch (Part A) ------


def test_queue_stages_iterator_is_reusable_across_empty_reads():
    """The queue-backed iterator yields pending labels, StopIterations on empty, then resumes."""
    import queue

    from apps.dev_ui import _QueueStages

    q: "queue.Queue[str]" = queue.Queue()
    it = iter(_QueueStages(q))

    q.put("one")
    q.put("two")
    assert next(it) == "one"
    assert next(it) == "two"

    # momentarily empty → StopIteration (harness falls back to its generic line)…
    try:
        next(it)
        raise AssertionError("expected StopIteration on empty queue")
    except StopIteration:
        pass

    # …and a later put is still delivered — unlike a spent generator.
    q.put("three")
    assert next(it) == "three"


def test_stream_turn_status_updates_come_from_stage_source():
    """When a stage_source is supplied, STATUS updates carry its labels in order."""
    import threading

    labels = ["Understanding your question", "Finding the right specialist", "Looking into it"]
    provider, _ = _counting_provider()
    state: dict = {}
    release = threading.Event()

    def turn_runner(session_id, message):
        # Keep the worker alive long enough for the poller to drain several labels.
        release.wait(timeout=2)
        return _fake_result()

    statuses: list[str] = []
    for up in stream_turn(
        "hello",
        state,
        turn_runner=turn_runner,
        session_provider=provider,
        stage_source=iter(labels),
        poll=0.01,
    ):
        if up.kind == STATUS:
            statuses.append(up.status)
            if len(statuses) >= len(labels):
                release.set()

    # the status stream begins with the fed labels, in node order (extras, if any, are the
    # generic fallback line emitted once the source is exhausted — never a reordered label)
    assert statuses[: len(labels)] == labels
