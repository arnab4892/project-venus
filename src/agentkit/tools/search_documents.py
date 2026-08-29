"""``search_documents`` — LLD-TOOL-04 / LLD-RET-03.

Hybrid retrieval over the active release: cosine top-20 (``vec.chunk_embedding_<release>``)
∪ FTS top-20 (``facts.chunk.tsv``) → reciprocal-rank fusion → top-k (default 5), with a SQL
pre-filter on ``division`` and ``family_ids``. Returns each chunk's text, locator and source
url. The query is embedded with the **same** self-hosted model as the chunks (PRD-N-002).

The ``embed`` seam lets tests pass a deterministic query vector. ``division`` is filtered
through the chunk's families (``facts.chunk`` carries ``family_ids`` but no division column):
a chunk matches a division if any of its families is in that division.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from agentkit.config import Settings, get_settings
from agentkit.retrieval.chunk import resolve_active_release
from agentkit.retrieval.embed import EmbedFn, client_from_settings, embedding_table

# Reciprocal-rank-fusion constant (the standard k=60 damping).
_RRF_K = 60
# Per-arm candidate depth before fusion (LLD-RET-03: cosine top-20 ∪ FTS top-20).
_ARM_DEPTH = 20


def _prefilter(division: str | None, family_ids: list[str] | None) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict[str, object] = {}
    if division is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM facts.active_product_family pf "
            "WHERE pf.family_id = ANY(c.family_ids) AND pf.division = :division)"
        )
        params["division"] = division
    if family_ids:
        clauses.append("c.family_ids && CAST(:families AS text[])")
        params["families"] = list(family_ids)
    return (" AND ".join(clauses) if clauses else "TRUE"), params


def search_documents(
    conn: Connection,
    query: str,
    *,
    division: str | None = None,
    family_ids: list[str] | None = None,
    k: int = 5,
    embed: EmbedFn | None = None,
    settings: Settings | None = None,
) -> dict:
    """Return ``{"chunks": [{chunk_id, doc_id, locator, url, content_md, family_ids, score}]}``.

    Fuses vector and full-text rankings (RRF) over the active release, honouring the
    ``division`` / ``family_ids`` pre-filter, and returns the top ``k`` chunks with their
    source locator + url for citation (LLD-RET-03 / PRD-F-008).
    """
    settings = settings or get_settings()
    release_id = resolve_active_release(conn)
    table = embedding_table(release_id)
    if embed is None:
        embed = client_from_settings(settings).raw_embed

    qvec = embed([query])[0]
    literal = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
    prefilter, params = _prefilter(division, family_ids)
    params.update({"rid": release_id, "qvec": literal, "q": query, "n": _ARM_DEPTH})

    vec_sql = (
        "SELECT c.chunk_id, row_number() OVER "
        "  (ORDER BY e.embedding <=> CAST(:qvec AS vector)) AS rank "
        f"FROM vec.{table} e JOIN facts.chunk c "
        "  ON c.chunk_id = e.chunk_id AND c.release_id = :rid "
        f"WHERE {prefilter} "
        "ORDER BY e.embedding <=> CAST(:qvec AS vector) LIMIT :n"
    )
    fts_sql = (
        "SELECT c.chunk_id, row_number() OVER "
        "  (ORDER BY ts_rank(c.tsv, plainto_tsquery('english', :q)) DESC) AS rank "
        "FROM facts.chunk c "
        "WHERE c.release_id = :rid AND c.tsv @@ plainto_tsquery('english', :q) "
        f"  AND {prefilter} "
        "ORDER BY ts_rank(c.tsv, plainto_tsquery('english', :q)) DESC LIMIT :n"
    )

    scores: dict[str, float] = {}
    for rows in (
        conn.execute(text(vec_sql), params).mappings().all(),
        conn.execute(text(fts_sql), params).mappings().all(),
    ):
        for row in rows:
            scores[row["chunk_id"]] = scores.get(row["chunk_id"], 0.0) + 1.0 / (
                _RRF_K + int(row["rank"])
            )

    top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    if not top:
        return {"chunks": []}

    detail_sql = (
        "SELECT c.chunk_id, c.doc_id, c.locator, c.content_md, c.family_ids, d.url "
        "FROM facts.chunk c JOIN facts.active_document d ON d.doc_id = c.doc_id "
        "WHERE c.release_id = :rid AND c.chunk_id = ANY(:ids)"
    )
    details = {
        r["chunk_id"]: r
        for r in conn.execute(
            text(detail_sql), {"rid": release_id, "ids": [cid for cid, _ in top]}
        ).mappings()
    }
    chunks = [
        {
            "chunk_id": cid,
            "doc_id": details[cid]["doc_id"],
            "locator": details[cid]["locator"],
            "url": details[cid]["url"],
            "content_md": details[cid]["content_md"],
            "family_ids": list(details[cid]["family_ids"] or []),
            "score": round(score, 6),
        }
        for cid, score in top
        if cid in details
    ]
    return {"chunks": chunks}
