"""Tool layer (HLD-C-05 / LLD §6, LLD-TOOL-01..06).

Six read-only tools over the ``facts.active_*`` views (and, for search, the per-release
``vec.chunk_embedding_<release>``): :func:`~agentkit.tools.match_capability.match_capability`,
:func:`~agentkit.tools.list_products.list_products`,
:func:`~agentkit.tools.get_product.get_product`,
:func:`~agentkit.tools.search_documents.search_documents`,
:func:`~agentkit.tools.get_company_fact.get_company_fact` and
:func:`~agentkit.tools.get_office.get_office`. Each is a pure function returning JSON, reading
only the active release (never filtering ``release_id``).
"""
