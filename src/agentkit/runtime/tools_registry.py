"""Runtime tool registry + call logging (LLD-TOOL preamble, LLD-RT).

Wraps the pure read-only tools (``agentkit.tools.*``) so the orchestrator can call them by
name, time each call, and collect a :class:`~agentkit.runtime.ops.ToolCallRecord` per call.
**``ops.tool_call`` logging begins this milestone** — the records are flushed to the DB with
the turn (``runtime.ops.persist_turn``), keeping the whole turn append-only.

Each call gets a turn-local ``tr_id`` (``tr1``, ``tr2`` …) that agents cite; grounding resolves
those ids and the citation rows are derived from the cited results. Per-client dependencies
(``gas_aliases`` for ``match_capability``, the ``embed`` seam + ``settings`` for
``search_documents``) are injected once at construction so agents call tools plainly.
"""

from __future__ import annotations

import time

from sqlalchemy import Connection

from agentkit.config import Settings, get_settings
from agentkit.runtime.ops import ToolCallRecord
from agentkit.tools.get_company_fact import get_company_fact
from agentkit.tools.get_office import get_office
from agentkit.tools.get_product import get_product
from agentkit.tools.list_products import list_products
from agentkit.tools.match_capability import match_capability
from agentkit.tools.search_documents import search_documents


def _rows_returned(tool: str, result: dict) -> int:
    if tool == "match_capability":
        return len(result.get("matches", []))
    if tool == "search_documents":
        return len(result.get("chunks", []))
    if tool == "get_company_fact":
        return len(result.get("facts", []))
    if tool in ("get_product", "list_products"):
        return len(result.get("products", result.get("families", [])) or [])
    if tool == "get_office":
        return 1 if result.get("office") else 0
    return 0


class ToolRunner:
    """Runs tools for one agent invocation, collecting timed records (no DB writes here)."""

    def __init__(
        self,
        conn: Connection,
        client: str,
        *,
        gas_aliases: dict[str, str] | None = None,
        embed=None,
        settings: Settings | None = None,
        session_id=None,
    ) -> None:
        self.conn = conn
        self.client = client
        self.gas_aliases = gas_aliases or {}
        self.embed = embed
        self.settings = settings or get_settings()
        # The current session id (used by application_discovery to read its own prior duty from
        # recorded tool calls for follow-up carry-forward); None outside a persisted turn.
        self.session_id = session_id
        self.records: list[ToolCallRecord] = []

    def _run(self, tool: str, args: dict, fn) -> ToolCallRecord:
        started = time.monotonic()
        # Wrap each call in a SAVEPOINT: a tool that errors (e.g. search_documents when the
        # vector table/embedder is absent) must not poison the turn's transaction — the caller
        # catches the re-raised error and answers from the tools that did succeed.
        sp = self.conn.begin_nested()
        try:
            result = fn()
            sp.commit()
        except Exception:
            sp.rollback()
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        rec = ToolCallRecord(
            tr_id=f"tr{len(self.records) + 1}",
            tool=tool,
            args=args,
            result=result,
            rows_returned=_rows_returned(tool, result),
            latency_ms=latency_ms,
        )
        self.records.append(rec)
        return rec

    # --- typed convenience wrappers (agents call these) ---

    def match_capability(self, **args) -> ToolCallRecord:
        return self._run(
            "match_capability",
            args,
            lambda: match_capability(self.conn, gas_aliases=self.gas_aliases, **args),
        )

    def search_documents(self, query: str, **kw) -> ToolCallRecord:
        args = {"query": query, **kw}
        return self._run(
            "search_documents",
            args,
            lambda: search_documents(
                self.conn, query, embed=self.embed, settings=self.settings, **kw
            ),
        )

    def get_company_fact(self, kind: str) -> ToolCallRecord:
        return self._run(
            "get_company_fact", {"kind": kind}, lambda: get_company_fact(self.conn, kind)
        )

    def get_product(self, model_or_family: str) -> ToolCallRecord:
        return self._run(
            "get_product",
            {"model_or_family": model_or_family},
            lambda: get_product(self.conn, model_or_family),
        )

    def list_products(self, division: str, category: str | None = None) -> ToolCallRecord:
        return self._run(
            "list_products",
            {"division": division, "category": category},
            lambda: list_products(self.conn, division, category=category),
        )

    def get_office(self, **kw) -> ToolCallRecord:
        return self._run("get_office", kw, lambda: get_office(self.conn, **kw))
