"""Documents & Compliance agent (LLD-AG-03 / PRD-F-005).

A download/catalogue ask serves a ``document_card`` message (kind + payload title/url/locator)
persisted to ``ops.message``; the card title falls back to a URL-derived label when
``facts.document.title`` is null. Chunks + a tiny vector table are installed inside the seeded
transaction (as in tests/tools/test_search_documents.py) so ``search_documents`` returns a hit.
"""

from __future__ import annotations

import json

from tests.runtime._helpers import FakeLLM

from agentkit.config import Settings
from agentkit.retrieval.chunk import Chunk
from agentkit.retrieval.embed import embed_chunks
from agentkit.runtime.agents.documents_compliance import _document_card, title_from_url
from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

_SETTINGS = Settings(embed_dim=3, embed_batch=64, embed_model="bge-m3")


def _install_chunks(conn):
    doc_id = conn.execute(
        text("SELECT doc_id FROM facts.document WHERE release_id='r2026.08.1' AND doc_id='doc.fs'")
    ).scalar_one()
    chunks = [
        Chunk("ch.fs.0", doc_id, "§Fire & Rescue", ["Fire"],
              "fire rescue and diving equipment catalogue breathing air compressors", 8,
              ["fam.mch_bac"], "fire_rescue"),
    ]
    embed_chunks(conn, chunks, "r2026.08.1", embed=lambda t: [[1.0, 0.0, 0.0]], settings=_SETTINGS)


def _fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "fire_rescue", "intent": "documents", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "answer": {
                "message": "Yes — here's our Fire, Rescue & Diving Equipment catalogue [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_serves_document_card(seeded_conn, make_ctx, new_session):
    _install_chunks(seeded_conn)
    ctx = make_ctx(_fake(), embed=lambda t: [[1.0, 0.0, 0.0]], settings=_SETTINGS)
    sid = new_session()
    result = run_turn(ctx, sid, "Do you have a fire equipment catalogue I can download?")

    assert result.route == "documents_compliance"
    assert result.outcome == "answered"

    # a document_card message was persisted with the right kind + payload shape (LLD-DB-04)
    rows = seeded_conn.execute(
        text(
            "SELECT kind, payload FROM ops.message m JOIN ops.turn t ON t.turn_id = m.turn_id "
            "WHERE t.session_id = :s AND m.kind = 'document_card'"
        ),
        {"s": sid},
    ).mappings().all()
    assert len(rows) == 1
    payload = rows[0]["payload"]
    payload = payload if isinstance(payload, dict) else json.loads(payload)
    assert payload["title"] and payload["url"] and payload["locator"]


def test_card_title_falls_back_to_url_when_null():
    # Live PDF catalogues carry a null facts.document.title → a readable URL-derived label.
    assert title_from_url(
        "https://www.jyotech.com/pdf/Jyotech%20Catalog%20-%20F%26S.pdf"
    ) == "Jyotech Catalog - F&S"
    card = _document_card(
        {"title": None, "url": "https://www.jyotech.com/pdf/Jyotech%20Catalog%20-%20PROCESS.pdf",
         "locator": "p1 §PROCESS"}
    )
    assert card.kind == "document_card"
    assert card.payload["title"] == "Jyotech Catalog - PROCESS"
    assert card.payload["locator"] == "p1 §PROCESS"
