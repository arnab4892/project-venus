"""Embedder tests (LLD-RET-02, LLD-DB-03) — written before the embedder is wired live.

Pure units (table naming, endpoint separation, batching) run anywhere; the DB-layer
parity tests use ``seeded_conn`` and a deterministic fake embedder (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from agentkit.config import Settings
from agentkit.retrieval.chunk import Chunk
from agentkit.retrieval.embed import (
    client_from_settings,
    embed_chunks,
    embedding_table,
    _embed_all,
)


class _FakeEmbedder:
    """Records batch sizes; returns ``dim``-wide vectors, one per input text."""

    def __init__(self, dim: int, *, wrong_dim: bool = False) -> None:
        self.dim = dim
        self.wrong_dim = wrong_dim
        self.batches: list[int] = []

    def __call__(self, texts):
        self.batches.append(len(texts))
        width = self.dim - 1 if self.wrong_dim else self.dim
        return [[float(i)] * width for i, _ in enumerate(texts)]


def test_embedding_table_name_sanitises_release_id():
    assert embedding_table("r2026.08.2") == "chunk_embedding_r2026_08_2"
    assert embedding_table("r2026.08.1") == "chunk_embedding_r2026_08_1"


def test_embed_auth_key_sent_when_auth_on_and_omitted_when_off():
    # EMBED_AUTH=true → the bearer key is wired into the embedding client; EMBED_AUTH=false force-
    # disables auth even with a key present, leaving the "not-needed" placeholder path in _client().
    on = client_from_settings(Settings(_env_file=None, embed_auth=True, embed_api_key="sk-e"))
    assert on.api_key == "sk-e"

    off = client_from_settings(Settings(_env_file=None, embed_auth=False, embed_api_key="sk-e"))
    assert off.api_key == ""

    # backward-compatible default: no key set → nothing to send (placeholder used in _client())
    assert client_from_settings(Settings(_env_file=None)).api_key == ""


def test_client_uses_embed_endpoint_not_llm():
    """PRD-N-002: chunk + query vectors come from the self-hosted EMBED endpoint only."""
    settings = Settings(
        embed_base_url="http://embed.internal:8001/v1",
        embed_model="bge-m3",
        llm_base_url="http://llm.internal:8000/v1",
        llm_model="some-chat-model",
    )
    client = client_from_settings(settings)
    assert client.base_url == settings.embed_base_url
    assert client.model == settings.embed_model
    assert client.base_url != settings.llm_base_url
    assert client.model != settings.llm_model


def test_embed_all_batches_by_size():
    fake = _FakeEmbedder(dim=4)
    vectors = _embed_all([f"t{i}" for i in range(5)], fake, batch=2)
    assert fake.batches == [2, 2, 1]
    assert len(vectors) == 5


def _chunks(doc_id: str, n: int) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"ch.test.{i:04d}",
            doc_id=doc_id,
            locator=f"§Test {i}",
            heading_path=["Test", f"Section {i}"],
            content_md=f"hydrogen compressor specification number {i}",
            token_count=7,
            family_ids=["fam.process_recip"],
            division="industrial",
        )
        for i in range(n)
    ]


def _seed_doc_id(conn) -> str:
    return conn.execute(
        text("SELECT doc_id FROM facts.document WHERE release_id = 'r2026.08.1' LIMIT 1")
    ).scalar_one()


def test_embed_chunks_writes_vectors_with_parity(seeded_conn):
    settings = Settings(embed_dim=8, embed_batch=2, embed_model="bge-m3")
    fake = _FakeEmbedder(dim=8)
    chunks = _chunks(_seed_doc_id(seeded_conn), 3)

    result = embed_chunks(seeded_conn, chunks, "r2026.08.1", embed=fake, settings=settings)

    assert result["chunks"] == 3
    assert result["embedded"] == 3
    assert result["table"] == "vec.chunk_embedding_r2026_08_1"
    assert fake.batches == [2, 1]  # batched by embed_batch=2

    # facts.chunk populated with an FTS tsv
    n_chunk, n_tsv = seeded_conn.execute(
        text(
            "SELECT count(*), count(tsv) FROM facts.chunk WHERE release_id = 'r2026.08.1'"
        )
    ).one()
    assert n_chunk == 3 and n_tsv == 3
    n_vec = seeded_conn.execute(
        text("SELECT count(*) FROM vec.chunk_embedding_r2026_08_1")
    ).scalar_one()
    assert n_vec == 3


def test_embed_chunks_rejects_wrong_dimension(seeded_conn):
    settings = Settings(embed_dim=8, embed_batch=64, embed_model="bge-m3")
    fake = _FakeEmbedder(dim=8, wrong_dim=True)  # returns 7-wide vectors
    chunks = _chunks(_seed_doc_id(seeded_conn), 2)
    with pytest.raises(RuntimeError, match="dimension"):
        embed_chunks(seeded_conn, chunks, "r2026.08.1", embed=fake, settings=settings)
