"""Embed a release's chunks into ``facts.chunk`` + ``vec.chunk_embedding_<release>``.

``release_embed`` chunks a release's documents (LLD-RET-01/04), writes the chunk text +
metadata to ``facts.chunk``, creates the per-release ``vec.chunk_embedding_<release>`` table
(HNSW, ``vector_cosine_ops``, ``m=16``/``ef_construction=128`` — LLD-DB-03), and embeds every
chunk with the **self-hosted** model at ``EMBED_BASE_URL`` (bge-m3, 1024-d — PRD-N-002: the
same model serves chunk and runtime query vectors, and never leaves Nyalazone infra). It is
idempotent (a re-run replaces the release's chunks + vector table) and verifies the embedded
count equals the chunk count.

Embedding is a seam — ``EmbedFn`` — so tests inject a deterministic fake (no network, no
model). The real client is :class:`EmbeddingClient`; its OpenAI SDK construction is lazy and
uncovered. Framework-generic — no client strings.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from sqlalchemy import Connection, text

from agentkit.config import Settings, get_settings
from agentkit.retrieval.chunk import Chunk, chunk_corpus
from agentkit.retrieval.tokenizer import TokenCounter, bge_m3_counter

log = logging.getLogger(__name__)

# An embedding seam: a batch of texts -> one 1024-d vector each, in order.
EmbedFn = Callable[[Sequence[str]], list[list[float]]]

_TABLE_SLUG_RE = re.compile(r"[^a-z0-9]+")


def embedding_table(release_id: str) -> str:
    """Per-release vector table name: ``r2026.08.2`` → ``chunk_embedding_r2026_08_2``."""
    slug = _TABLE_SLUG_RE.sub("_", release_id.lower()).strip("_")
    if not slug:
        raise ValueError(f"cannot derive a table name from release id {release_id!r}")
    return f"chunk_embedding_{slug}"


@dataclass
class EmbeddingClient:
    """OpenAI-compatible embedding client for the self-hosted model (LLD-RET-02)."""

    base_url: str
    model: str
    dim: int
    num_ctx: int
    api_key: str = ""

    def raw_embed(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        """Embed a batch, sending the provider context window explicitly (num_ctx)."""
        resp = self._client().embeddings.create(
            model=self.model,
            input=list(texts),
            extra_body={"options": {"num_ctx": self.num_ctx}},
        )
        return [row.embedding for row in resp.data]

    def _client(self):  # pragma: no cover - constructs the real SDK client lazily
        from openai import OpenAI

        return OpenAI(base_url=self.base_url, api_key=self.api_key or "not-needed")


def client_from_settings(settings: Settings | None = None) -> EmbeddingClient:
    """Build the embedding client from the **embedding** endpoint (never the chat one)."""
    settings = settings or get_settings()
    return EmbeddingClient(
        base_url=settings.embed_base_url,
        model=settings.embed_model,
        dim=settings.embed_dim,
        num_ctx=settings.embed_num_ctx,
        # Send the bearer key only when auth is on; off (or no key) keeps the "not-needed"
        # placeholder in _client(), so a no-auth endpoint still works.
        api_key=settings.embed_api_key if settings.embed_auth else "",
    )


def _embed_all(texts: list[str], embed: EmbedFn, batch: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch):
        vectors.extend(embed(texts[start : start + batch]))
    return vectors


def _write_chunks(conn: Connection, chunks: list[Chunk], release_id: str) -> None:
    """Replace the release's ``facts.chunk`` rows (idempotent), with an FTS ``tsv``."""
    conn.execute(
        text("DELETE FROM facts.chunk WHERE release_id = :rid"), {"rid": release_id}
    )
    insert = text(
        "INSERT INTO facts.chunk "
        "(chunk_id, release_id, doc_id, locator, heading_path, content_md, "
        " token_count, family_ids, tsv) "
        "VALUES (:chunk_id, :rid, :doc_id, :locator, CAST(:heading_path AS text[]), "
        " :content_md, :token_count, CAST(:family_ids AS text[]), "
        " to_tsvector('english', :content_md))"
    )
    for c in chunks:
        conn.execute(
            insert,
            {
                "chunk_id": c.chunk_id,
                "rid": release_id,
                "doc_id": c.doc_id,
                "locator": c.locator,
                "heading_path": c.heading_path,
                "content_md": c.content_md,
                "token_count": c.token_count,
                "family_ids": c.family_ids,
            },
        )


def _create_vec_table(conn: Connection, table: str, dim: int) -> None:
    """(Re)create ``vec.<table>`` with an HNSW cosine index (LLD-DB-03)."""
    conn.execute(text(f"DROP TABLE IF EXISTS vec.{table}"))
    conn.execute(
        text(
            f"CREATE TABLE vec.{table} ("
            "  chunk_id text PRIMARY KEY,"
            f"  embedding vector({int(dim)}),"
            "  model text NOT NULL,"
            "  created_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX ON vec.{table} USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 128)"
        )
    )


def _write_vectors(
    conn: Connection, table: str, chunks: list[Chunk], vectors: list[list[float]], model: str
) -> None:
    insert = text(
        f"INSERT INTO vec.{table} (chunk_id, embedding, model) "
        "VALUES (:cid, CAST(:emb AS vector), :model)"
    )
    for chunk, vec in zip(chunks, vectors):
        literal = "[" + ",".join(repr(float(x)) for x in vec) + "]"
        conn.execute(insert, {"cid": chunk.chunk_id, "emb": literal, "model": model})


def embed_chunks(
    conn: Connection,
    chunks: list[Chunk],
    release_id: str,
    *,
    embed: EmbedFn,
    settings: Settings,
) -> dict:
    """Persist chunks + their vectors for a release, verifying dimension and count parity.

    Writes ``facts.chunk``, (re)creates ``vec.chunk_embedding_<release>``, embeds in batches
    of ``embed_batch``, and asserts every vector is ``embed_dim``-wide and the embedded count
    equals the chunk count. Idempotent.
    """
    dim = int(settings.embed_dim)
    table = embedding_table(release_id)
    _write_chunks(conn, chunks, release_id)
    _create_vec_table(conn, table, dim)

    vectors = _embed_all([c.embedding_text() for c in chunks], embed, settings.embed_batch)
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
        )
    for vec in vectors:
        if len(vec) != dim:
            raise RuntimeError(
                f"embedding dimension {len(vec)} != expected {dim} (wrong model?)"
            )
    _write_vectors(conn, table, chunks, vectors, settings.embed_model)

    embedded = conn.execute(text(f"SELECT count(*) FROM vec.{table}")).scalar_one()
    if embedded != len(chunks):
        raise RuntimeError(
            f"embedded count {embedded} != chunk count {len(chunks)} for {release_id}"
        )
    return {
        "release_id": release_id,
        "chunks": len(chunks),
        "embedded": embedded,
        "table": f"vec.{table}",
        "dim": dim,
    }


def release_embed(
    conn: Connection,
    client: str,
    release_id: str,
    *,
    count_tokens: TokenCounter | None = None,
    embed: EmbedFn | None = None,
    settings: Settings | None = None,
    data_root: Path | None = None,
) -> dict:
    """Chunk + embed one release into ``facts.chunk`` and ``vec.chunk_embedding_<release>``.

    ``embed`` overrides the real client (tests pass a fake). Idempotent: a re-run replaces
    both the chunk rows and the vector table.
    """
    settings = settings or get_settings()
    count_tokens = count_tokens or bge_m3_counter(settings)
    if embed is None:
        embed = client_from_settings(settings).raw_embed
    log.info(
        "embedding release %s: model=%s limit=%d tokens num_ctx=%d batch=%d dim=%d",
        release_id, settings.embed_model, settings.embed_limit,
        settings.embed_num_ctx, settings.embed_batch, int(settings.embed_dim),
    )

    chunks, _report = chunk_corpus(
        conn,
        client,
        release_id,
        count_tokens=count_tokens,
        embed_limit=settings.embed_limit,
        data_root=data_root,
    )
    return embed_chunks(conn, chunks, release_id, embed=embed, settings=settings)
