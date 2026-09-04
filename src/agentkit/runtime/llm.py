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
__all__ = ["RuntimeClient", "build_runtime_client", "CompleteFn", "compose_thinking_off"]


def compose_thinking_off(knob: bool, schema_name: str, language: str | None) -> bool:
    """Whether to send Qwen3/vLLM thinking-off for THIS call, given the ``LLM_DISABLE_THINKING``
    knob, the schema name, and the reply language.

    Thinking-off is scoped to the **compose** call (schema ``"answer"``) — triage and the per-agent
    parse calls use other schema names and are never affected. It is further gated **per language**:
    the Devanagari (``hi``) register needs compose reasoning at this model size, so ``hi`` **always
    keeps thinking on** regardless of the knob; the knob only ever silences ``en`` / ``hinglish``
    compose. A ``None`` language (an "answer" call that did not thread one) is treated as non-Hindi,
    preserving the pre-per-language behaviour. Knob unset/false ⇒ thinking on everywhere.
    """
    return bool(knob) and schema_name == "answer" and language != "hi"


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
    # Apply Qwen3/vLLM thinking-off to the COMPOSE call only (schema_name == "answer"); triage and
    # the per-agent parse calls use other schema names and are never affected. Default off.
    disable_thinking: bool = False
    _client: Any = None

    def _openai(self) -> Any:
        if self._client is None:  # pragma: no cover - exercised only with a live endpoint
            # Dev-only: when tracing is enabled, use langfuse's drop-in OpenAI wrapper so each
            # runtime chat call carries tokens/latency as a generation span under the turn's trace.
            # Fail-open: any import problem falls back to the plain SDK, so a turn never breaks.
            OpenAI = None
            try:
                from agentkit.runtime.tracing import ensure_configured, tracing_enabled

                if tracing_enabled():
                    ensure_configured()  # bridge LANGFUSE_* from .env into os.environ for the SDK
                    from langfuse.openai import OpenAI
            except Exception:  # noqa: BLE001 - never let tracing wiring break the runtime client
                OpenAI = None
            if OpenAI is None:
                from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key or "not-needed")
        return self._client

    def raw_complete(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        schema_name: str,
        language: str | None = None,
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
            # Thinking-off is scoped to the compose call ("answer" schema) AND to non-Hindi replies:
            # the Devanagari register needs compose reasoning at this model size (compose_thinking_off).
            disable_thinking=compose_thinking_off(self.disable_thinking, schema_name, language),
        )
        resp = self._openai().chat.completions.create(**kwargs)  # pragma: no cover
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        return resp.choices[0].message.content or "", tin, tout, cached_input_tokens(usage)

    def complete_fn(self) -> CompleteFn:
        """Return a ``CompleteFn`` (text-only) seam over :meth:`raw_complete` for the runtime.

        The compose site (``compose_grounded_answer``) sets a ``compose_language`` attribute on this
        seam before its "answer" call, so the per-language thinking gate can see the reply language
        without changing the shared 3-arg ``CompleteFn`` signature (same attribute idiom as
        ``last_tokens``). Non-compose calls leave it unread (their schema is never "answer").
        """

        def _complete(messages, json_schema, schema_name):  # pragma: no cover - live endpoint
            language = getattr(_complete, "compose_language", None)
            text, *_ = self.raw_complete(messages, json_schema, schema_name, language=language)
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
        disable_thinking=settings.llm_disable_thinking,
    )
