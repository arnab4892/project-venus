"""Runtime chat LLM client (LLD-RT, PRD-N-002).

The **customer-facing** runtime LLM. It talks to ``LLM_BASE_URL`` (self-hosted, qwen3
via Ollama) — **never** the extractor endpoint (``EXTRACT_LLM_BASE_URL``, which may be
external) and never an external API. Extraction runs offline over public content;
the runtime answers real visitors, so it stays self-hosted.

The schema-constrained call machinery is shared with the extractor
(:func:`agentkit.extract.llm.build_request_kwargs` / :func:`~agentkit.extract.llm.call_json`):
strict ``json_schema`` ``response_format``, retry-on-invalid, sampling knobs sent only
when set. This module only differs in *which endpoint/model* it targets and exposes the
same injectable ``complete=`` seam so triage/agents are tested offline with canned JSON —
mirroring the extractor's ``complete=`` seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentkit.config import Settings, get_settings
from agentkit.extract.llm import CompleteFn, build_request_kwargs

# Re-exported so runtime callers import the seam type + the schema-call driver from one place.
__all__ = ["RuntimeClient", "build_runtime_client", "CompleteFn"]


@dataclass
class RuntimeClient:
    """Thin OpenAI-compatible wrapper for the self-hosted runtime chat model.

    Construction records the resolved ``base_url`` / ``model`` from the runtime
    ``LLM_*`` settings without opening a connection (config is assertable offline);
    the underlying OpenAI client is created lazily on first real call. Sampling knobs
    are sent only when set (``build_request_kwargs``).
    """

    base_url: str
    model: str
    api_key: str = ""
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
        """One schema-constrained chat completion → (text, tokens_in, tokens_out, cached_in)."""
        from agentkit.extract.llm import cached_input_tokens

        kwargs = build_request_kwargs(
            self.model,
            messages,
            json_schema,
            schema_name,
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            max_completion_tokens=self.max_completion_tokens,
        )
        resp = self._openai().chat.completions.create(**kwargs)  # pragma: no cover
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        return resp.choices[0].message.content or "", tin, tout, cached_input_tokens(usage)

    def complete_fn(self) -> CompleteFn:
        """Return a ``CompleteFn`` (text-only) seam over :meth:`raw_complete` for the runtime."""

        def _complete(messages, json_schema, schema_name):  # pragma: no cover - live endpoint
            text, *_ = self.raw_complete(messages, json_schema, schema_name)
            return text

        return _complete


def build_runtime_client(settings: Settings | None = None) -> RuntimeClient:
    """Build the runtime chat client from the self-hosted ``LLM_*`` settings (PRD-N-002).

    Uses ``llm_base_url`` / ``llm_model`` directly — it never falls back to the extractor
    endpoint (that fallback runs the other direction, extractor → runtime, in ``config.py``).
    """
    settings = settings or get_settings()
    return RuntimeClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        # Send the bearer key only when auth is on; off (or no key) keeps the "not-needed"
        # placeholder in RuntimeClient._openai(), so a no-auth endpoint still works.
        api_key=settings.llm_api_key if settings.llm_auth else "",
        temperature=settings.llm_temperature,
        reasoning_effort=settings.llm_reasoning_effort,
        max_completion_tokens=settings.llm_max_completion_tokens,
    )
