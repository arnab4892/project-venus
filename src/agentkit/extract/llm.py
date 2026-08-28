"""Extractor LLM client (LLD-EXT-02/03/04, CR-0002).

The extractor's LLM calls (section classification, family discovery, typed
extraction) go to ``EXTRACT_LLM_BASE_URL`` — which **may be an external API** —
never the runtime chat endpoint. Inputs are the converted public Markdown only.

The client is OpenAI-compatible, runs at ``temperature=0`` with a JSON-schema
constrained response, and **retries on invalid JSON/schema up to 3 times, then
logs and skips** the section (:class:`ExtractionSkipped`). Every call's model,
token counts and latency are appended to an ``llm-calls.jsonl`` log so the run
report and milestone note can summarise token/cost totals.

This module is framework-generic (no client strings). Downstream callers accept
an injectable ``complete=`` seam so tests pass a canned callable and never touch
the network — mirroring the ``render=`` seam used by the PDF converter.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentkit.config import Settings, get_settings

# A ``complete`` seam: (messages, json_schema, schema_name) -> raw model text.
CompleteFn = Callable[[list[dict[str, str]], dict[str, Any], str], str]

MAX_RETRIES = 3


class ExtractionSkipped(Exception):
    """Raised when a section's LLM call never returns schema-valid JSON.

    Carries the section id and the last raw output so the caller can log the
    drop (never silently, never "fixed") and move on to the next section.
    """

    def __init__(self, section_id: str, raw: str, reason: str) -> None:
        super().__init__(f"section {section_id}: {reason}")
        self.section_id = section_id
        self.raw = raw
        self.reason = reason


@dataclass
class CallRecord:
    """One extractor LLM call, appended to the run's ``llm-calls.jsonl``."""

    model: str
    kind: str  # classify | family_discovery | extract:<type>
    section_id: str | None
    tokens_in: int
    tokens_out: int
    tokens_cached: int  # cached input tokens (prompt-cache hits), subset of tokens_in
    latency_ms: int
    attempts: int
    ok: bool

    def as_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


@dataclass
class CallLog:
    """Accumulates :class:`CallRecord`s and (optionally) tees them to a file."""

    path: Path | None = None
    records: list[CallRecord] = field(default_factory=list)

    def append(self, record: CallRecord) -> None:
        self.records.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(record.as_json() + "\n")

    def totals(self, *, settings: Settings | None = None) -> dict[str, Any]:
        """Aggregate token totals + cache hit rate and (if priced) cost.

        Prompt-cache hits are cheaper: ``tokens_cached`` (a subset of ``tokens_in``)
        is priced at ``extract_llm_price_cached_in_per_1k`` when set, the remaining
        cache-miss input at ``extract_llm_price_in_per_1k``. If the cached price is
        unset, cached tokens fall back to the miss price (no discount assumed).
        """
        settings = settings or get_settings()
        tokens_in = sum(r.tokens_in for r in self.records)
        tokens_out = sum(r.tokens_out for r in self.records)
        tokens_cached = sum(r.tokens_cached for r in self.records)
        tokens_miss = max(tokens_in - tokens_cached, 0)
        calls = len(self.records)
        skipped = sum(1 for r in self.records if not r.ok)
        hit_rate = (tokens_cached / tokens_in) if tokens_in else 0.0

        pin = settings.extract_llm_price_in_per_1k
        pout = settings.extract_llm_price_out_per_1k
        pcached = settings.extract_llm_price_cached_in_per_1k
        cost: float | None = None
        if pin is not None or pout is not None or pcached is not None:
            cached_rate = pcached if pcached is not None else (pin or 0.0)
            cost = (
                tokens_miss / 1000 * (pin or 0.0)
                + tokens_cached / 1000 * cached_rate
                + tokens_out / 1000 * (pout or 0.0)
            )
        return {
            "calls": calls,
            "skipped": skipped,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_cached": tokens_cached,
            "cache_hit_rate": hit_rate,
            "tokens_total": tokens_in + tokens_out,
            "cost_usd": cost,  # None → reported as "n/a"
        }


@dataclass
class ExtractorClient:
    """Thin OpenAI-compatible wrapper exposing a ``complete`` seam.

    Construction records the resolved ``base_url`` / ``model`` (from
    ``EXTRACT_LLM_*`` with fallback to the runtime chat endpoint) without opening
    a connection, so config can be asserted in tests offline. The underlying
    OpenAI client is created lazily on first real call.
    """

    base_url: str
    model: str
    api_key: str = ""
    # Provider sampling knobs — each sent only when set (see build_request_kwargs).
    temperature: float | None = None
    reasoning_effort: str | None = None
    max_completion_tokens: int | None = None
    _client: Any = None

    def _openai(self) -> Any:
        if self._client is None:  # pragma: no cover - exercised only with a live endpoint
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key or "not-needed")
        return self._client

    def raw_complete(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any], schema_name: str
    ) -> tuple[str, int, int, int]:
        """One OpenAI-compatible chat completion → (text, in, out, cached_in)."""
        kwargs = build_request_kwargs(
            self.model, messages, json_schema, schema_name,
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            max_completion_tokens=self.max_completion_tokens,
        )
        resp = self._openai().chat.completions.create(**kwargs)  # pragma: no cover
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        return resp.choices[0].message.content or "", tin, tout, cached_input_tokens(usage)


def cached_input_tokens(usage: Any) -> int:
    """Best-effort prompt-cache-hit token count from a chat ``usage`` object.

    Providers name this differently; check the common shapes so cost reporting
    works regardless: OpenAI-style ``prompt_tokens_details.cached_tokens``, a flat
    ``cached_tokens``, or DeepSeek/Moonshot-style ``prompt_cache_hit_tokens``.
    Returns 0 when none is present. (Confirm the exact field against a live k3
    response; unknown shapes degrade safely to 0 — cost then assumes no cache.)
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    candidates = (
        getattr(details, "cached_tokens", None) if details is not None else None,
        getattr(usage, "cached_tokens", None),
        getattr(usage, "prompt_cache_hit_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),  # Anthropic-style mirror (kimi-k3)
    )
    for value in candidates:
        if value:
            return int(value)
    return 0


def build_request_kwargs(
    model: str,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    schema_name: str,
    *,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    """Assemble ``chat.completions.create`` kwargs, omitting any unset sampling param.

    Keeps the request minimal and provider-safe: an endpoint that rejects a param
    (kimi-k3 forbids ``temperature``, for instance) never receives it. The
    non-standard ``reasoning_effort`` value (kimi-k3 allows ``max``) is sent via
    ``extra_body`` so it lands verbatim in the request body regardless of the SDK's
    typed enum. ``response_format`` is the strict ``json_schema`` shape unchanged.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
        },
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_completion_tokens is not None:
        kwargs["max_completion_tokens"] = max_completion_tokens
    if reasoning_effort is not None:
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
    return kwargs


def build_extractor_client(settings: Settings | None = None) -> ExtractorClient:
    """Build the extractor client from ``EXTRACT_LLM_*`` (CR-0002 fallback rules)."""
    settings = settings or get_settings()
    base_url, model, api_key = settings.extractor_endpoint()
    return ExtractorClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=settings.extract_llm_temperature,
        reasoning_effort=settings.extract_llm_reasoning_effort,
        max_completion_tokens=settings.extract_llm_max_completion_tokens,
    )


def call_json(
    *,
    client: ExtractorClient | None,
    complete: CompleteFn | None = None,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    schema_name: str,
    kind: str,
    section_id: str | None,
    log: CallLog | None = None,
) -> dict[str, Any]:
    """Run one schema-constrained call with retry-on-invalid, logging each attempt.

    ``complete`` (a test seam) takes precedence over ``client.raw_complete``. On
    3 consecutive invalid responses raises :class:`ExtractionSkipped` so the
    caller logs the section and skips it — never silently, never patched.
    """
    if complete is None:
        if client is None:  # pragma: no cover - guarded by callers
            raise ValueError("call_json needs either a client or a complete= seam")

        def complete(msgs: list[dict[str, str]], schema: dict[str, Any], name: str) -> str:
            text, tin, tout, cached = client.raw_complete(msgs, schema, name)
            complete.last_tokens = (tin, tout, cached)  # type: ignore[attr-defined]
            return text

        complete.last_tokens = (0, 0, 0)  # type: ignore[attr-defined]

    model = client.model if client is not None else "seam"
    last_raw = ""
    reason = "no attempts"
    started = time.monotonic()
    for attempt in range(1, MAX_RETRIES + 1):
        last_raw = complete(messages, json_schema, schema_name)
        tin, tout, cached = getattr(complete, "last_tokens", (0, 0, 0))
        try:
            parsed = json.loads(last_raw)
        except json.JSONDecodeError as exc:
            reason = f"invalid JSON: {exc}"
            continue
        if not isinstance(parsed, dict):
            reason = "top-level JSON is not an object"
            continue
        latency_ms = int((time.monotonic() - started) * 1000)
        if log is not None:
            log.append(
                CallRecord(model, kind, section_id, tin, tout, cached, latency_ms, attempt, ok=True)
            )
        return parsed

    latency_ms = int((time.monotonic() - started) * 1000)
    if log is not None:
        tin, tout, cached = getattr(complete, "last_tokens", (0, 0, 0))
        log.append(
            CallRecord(model, kind, section_id, tin, tout, cached, latency_ms, MAX_RETRIES, ok=False)
        )
    raise ExtractionSkipped(section_id or "?", last_raw, reason)
