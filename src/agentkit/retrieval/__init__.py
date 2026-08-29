"""Chunker & embedder (HLD-C-04 / LLD §5, LLD-RET-01..04).

:mod:`agentkit.retrieval.chunk` turns the active release's converted Markdown into
:class:`~agentkit.retrieval.chunk.Chunk` records (heading-aware, table-safe, family-tagged,
with per-document dispositions and corpus dedup). :mod:`agentkit.retrieval.embed` writes
those chunks to ``facts.chunk`` and their vectors to ``vec.chunk_embedding_<release>`` via
the self-hosted embedding model. :mod:`agentkit.retrieval.tokenizer` owns token counting and
the loud :class:`~agentkit.retrieval.tokenizer.ChunkTooLargeError`.
"""
