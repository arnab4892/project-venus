"""``search_documents`` hybrid retrieval (LLD-TOOL-04 / LLD-RET-03).

Sets up a few chunks + a tiny vector table inside the seeded (r2026.08.1) transaction via
``embed_chunks`` with a 3-d fake embedder, then drives the tool with a deterministic query
vector — no network, no model.
"""

from __future__ import annotations

from sqlalchemy import text

from agentkit.config import Settings
from agentkit.retrieval.chunk import Chunk
from agentkit.retrieval.embed import embed_chunks
from agentkit.tools.search_documents import search_documents

_SETTINGS = Settings(embed_dim=3, embed_batch=64, embed_model="bge-m3")
# One basis vector per chunk, in insertion order.
_SETUP_VECTORS = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _setup_embed(texts):
    return _SETUP_VECTORS[: len(texts)]


def _seed_doc_id(conn) -> str:
    return conn.execute(
        text("SELECT doc_id FROM facts.document WHERE release_id='r2026.08.1' LIMIT 1")
    ).scalar_one()


def _install_chunks(conn) -> list[Chunk]:
    doc_id = _seed_doc_id(conn)
    chunks = [
        Chunk("ch.t.0", doc_id, "§Hydrogen", ["Hydrogen"],
              "hydrogen process gas compressor for refinery service", 7,
              ["fam.process_recip"], "industrial"),
        Chunk("ch.t.1", doc_id, "§Oxygen", ["Oxygen"],
              "oxygen cylinder filling compressor", 5, ["fam.oxygen_recip"], "industrial"),
        Chunk("ch.t.2", doc_id, "§Diving", ["Diving"],
              "surface demand diving breathing apparatus", 5, ["fam.diving"], "diving"),
    ]
    embed_chunks(conn, chunks, "r2026.08.1", embed=_setup_embed, settings=_SETTINGS)
    return chunks


def test_hybrid_search_returns_locator_and_url(seeded_conn):
    _install_chunks(seeded_conn)
    result = search_documents(
        seeded_conn, "hydrogen", k=2, embed=lambda t: [[1.0, 0.0, 0.0]], settings=_SETTINGS
    )
    assert result["chunks"], "expected at least one hit"
    top = result["chunks"][0]
    assert top["chunk_id"] == "ch.t.0"  # vector + FTS both favour the hydrogen chunk
    assert top["locator"] == "§Hydrogen"
    assert "content_md" in top and top["url"] is not None


def test_family_prefilter_restricts_results(seeded_conn):
    _install_chunks(seeded_conn)
    # even with a query vector nearest the hydrogen chunk, a diving family filter excludes it
    result = search_documents(
        seeded_conn,
        "compressor",
        family_ids=["fam.diving"],
        embed=lambda t: [[1.0, 0.0, 0.0]],
        settings=_SETTINGS,
    )
    ids = [c["chunk_id"] for c in result["chunks"]]
    assert ids == ["ch.t.2"]  # only the diving-family chunk survives the pre-filter
