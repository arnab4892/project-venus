"""Write one ``staging.document`` row per source (LLD-ING-05).

Stage 1 records provenance in ``staging.document`` only — never ``facts.document``
(facts are written solely by release promotion). ``division`` and ``title`` are
left for the extraction milestone. Runs on the caller's ``Connection`` and never
commits, mirroring :func:`agentkit.release.seed.seed_demo`.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

_UPSERT = text(
    """
    INSERT INTO staging.document
        (release_candidate_id, doc_id, kind, url, sha256, page_count)
    VALUES
        (:rc, :doc_id, :kind, :url, :sha256, :page_count)
    ON CONFLICT (release_candidate_id, doc_id) DO UPDATE SET
        kind        = EXCLUDED.kind,
        url         = EXCLUDED.url,
        sha256      = EXCLUDED.sha256,
        page_count  = EXCLUDED.page_count
    """
)


def upsert_document(
    conn: Connection,
    *,
    rc_id: str,
    doc_id: str,
    kind: str,
    url: str,
    sha256: str,
    page_count: int | None,
) -> None:
    """Idempotently upsert a ``staging.document`` row keyed by ``(rc_id, doc_id)``."""
    conn.execute(
        _UPSERT,
        {
            "rc": rc_id,
            "doc_id": doc_id,
            "kind": kind,
            "url": url,
            "sha256": sha256,
            "page_count": page_count,
        },
    )
