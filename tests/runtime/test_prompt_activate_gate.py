"""LLD-EVAL-03 / scope-2: `prompt activate` is gated by the golden suite.

Loading prompts writes inactive ``ops.prompt_version`` rows and upserts ``ops.client``.
Activation runs the golden suite (fact + retrieval) against the active release and **refuses
(rolls back) on any fact/retrieval failure**, exactly like ``release activate``. We drive the
gate with an injected ``questions=`` set so the test is deterministic and offline: a passing
question activates; a failing one leaves the prompt inactive.

Runs on ``seeded_conn`` (demo release active). The retrieval layer needs no embedder here — the
injected questions carry only ``fact`` blocks.
"""

from __future__ import annotations

from agentkit.runtime.prompts import (
    RUNTIME_AGENTS,
    activate_prompt_gated,
    load_prompts,
)
from sqlalchemy import text

# A fact-layer question that PASSES on the demo seed (cap.002 serves hydrogen up to 20000/1000).
_PASS_Q = [
    {
        "id": "gate-pass",
        "expected_outcome": "answer",
        "fact": {
            "tool": "match_capability",
            "args": {
                "gas": "hydrogen",
                "capacity": 3000,
                "capacity_unit": "Nm3/hr",
                "discharge_p": 350,
                "lubricated": False,
            },
            "expect_ids": ["fam.process_recip"],
        },
    }
]

# A fact-layer question that FAILS: demands an id the tool will never return.
_FAIL_Q = [
    {
        "id": "gate-fail",
        "expected_outcome": "answer",
        "fact": {
            "tool": "match_capability",
            "args": {
                "gas": "hydrogen",
                "capacity": 3000,
                "capacity_unit": "Nm3/hr",
                "discharge_p": 350,
            },
            "expect_ids": ["fam.does_not_exist"],
        },
    }
]


def _load(conn, client="jyotech"):
    written = load_prompts(conn, client)
    by_agent = {w["agent"]: w["prompt_id"] for w in written}
    return by_agent


def _is_active(conn, prompt_id) -> bool:
    return bool(
        conn.execute(
            text("SELECT is_active FROM ops.prompt_version WHERE prompt_id = :p"),
            {"p": prompt_id},
        ).scalar_one()
    )


def test_load_writes_inactive_versions_and_upserts_client(seeded_conn):
    by_agent = _load(seeded_conn)
    # every runtime agent got a version, none active yet
    assert set(by_agent) == set(RUNTIME_AGENTS)
    for pid in by_agent.values():
        assert not _is_active(seeded_conn, pid)
    # ops.client upserted
    assert seeded_conn.execute(
        text("SELECT count(*) FROM ops.client WHERE client_id = 'jyotech'")
    ).scalar_one() == 1


def test_activate_succeeds_when_golden_suite_passes(seeded_conn):
    by_agent = _load(seeded_conn)
    pid = by_agent["triage"]
    activated, report = activate_prompt_gated(
        seeded_conn, pid, "jyotech", questions=_PASS_Q
    )
    assert activated is True
    assert report.ok
    assert _is_active(seeded_conn, pid)


def test_activate_refused_when_golden_suite_fails(seeded_conn):
    by_agent = _load(seeded_conn)
    pid = by_agent["triage"]
    activated, report = activate_prompt_gated(
        seeded_conn, pid, "jyotech", questions=_FAIL_Q
    )
    assert activated is False
    assert not report.ok
    # activation rolled back — the prompt stays inactive
    assert not _is_active(seeded_conn, pid)
