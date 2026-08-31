"""Runtime configuration (PRD-N-002 / LLD §0 endpoints).

Reads the self-hosted LLM/embedding endpoints, the database URL and the SMTP
relay from the environment or a local ``.env`` file. Secrets never live in code
(see ``.env.example`` for the shape).

Endpoints are scoped by stage (CR-0002 / LLD §0). The customer-facing **runtime**
chat (``llm_base_url``) and the **embedding** model (``embed_base_url``) stay
self-hosted. The offline **extractor** LLM (``extract_llm_base_url``) may be an
external API — it runs over public website/catalogue content only — and falls
back to the runtime chat endpoint/model when unset.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Framework-wide settings, populated from env / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # A blank env var (e.g. EXTRACT_LLM_TEMPERATURE=) means "unset": fall back to
        # the field default (None) rather than trying to coerce "" into float/int.
        env_ignore_empty=True,
    )

    # Database
    database_url: str = "postgresql+psycopg://agent:change-me@localhost:5433/jyotech_v1"

    # LLM (chat) endpoint — OpenAI-compatible, self-hosted (runtime chat only, PRD-N-002)
    llm_base_url: str = "http://llm.internal:8000/v1"
    llm_model: str = ""

    # Auth for the runtime chat endpoint. `llm_api_key` is the bearer key; `llm_auth` toggles
    # whether it is sent. Default True is backward-compatible — an empty key already resolves to
    # the "not-needed" placeholder — so a no-auth endpoint is unaffected; set LLM_AUTH=false to
    # force auth off even when a key is present.
    llm_api_key: str = ""
    llm_auth: bool = True

    # Runtime chat sampling knobs — each sent ONLY when set (same discipline as the
    # extractor knobs). qwen3 via Ollama accepts `temperature` (0 for determinism);
    # `reasoning_effort` is a free-string passthrough via extra_body; `max_completion_tokens`
    # None → provider default. A blank env var means "unset" (env_ignore_empty).
    llm_temperature: float | None = None
    llm_reasoning_effort: str | None = None
    llm_max_completion_tokens: int | None = None

    # Embedding endpoint — OpenAI-compatible, self-hosted
    embed_base_url: str = "http://embed.internal:8001/v1"
    embed_model: str = "bge-m3"

    # Auth for the embedding endpoint (independent of the chat LLM auth). `embed_api_key` is the
    # bearer key; `embed_auth` toggles whether it is sent. Default True is backward-compatible — an
    # empty key resolves to the "not-needed" placeholder — so a no-auth endpoint is unaffected; set
    # EMBED_AUTH=false to force auth off even when a key is present.
    embed_api_key: str = ""
    embed_auth: bool = True

    # Embedding tokenizer + limits (LLD-RET-02). `embed_tokenizer` is a HF repo id
    # (resolved from the local cache) or `embed_tokenizer_path` a local tokenizer.json
    # / dir for air-gapped installs. `embed_limit` is the hard token ceiling: a chunk at
    # or over it raises ChunkTooLargeError (never silent truncation). `embed_num_ctx` is
    # the provider context window sent per request (≥ embed_limit + margin). `embed_batch`
    # is the embedding batch size (LLD-RET-02: 64). bge-m3's max sequence length is 8192.
    embed_tokenizer: str = "BAAI/bge-m3"
    embed_tokenizer_path: str = ""
    embed_dim: int = 1024
    embed_limit: int = 8192
    embed_num_ctx: int = 8448  # embed_limit + 256 margin
    embed_batch: int = 64

    # Offline extractor LLM (CR-0002) — OpenAI-compatible, MAY be an external API
    # (public content only). Empty means "fall back to the runtime chat endpoint":
    # resolve via ``extractor_endpoint()`` rather than reading these fields raw.
    extract_llm_base_url: str = ""
    extract_llm_model: str = ""
    extract_llm_api_key: str = ""

    # Provider-specific sampling knobs — each is sent ONLY when set, so an endpoint
    # that rejects a param (e.g. kimi-k3 forbids `temperature`) never sees it.
    # `temperature` None → omitted (leave unset for kimi-k3; a self-hosted model may
    # set 0 for determinism). `reasoning_effort` is a free-string passthrough
    # (kimi-k3: low|high|max; sent via extra_body). `max_completion_tokens` None →
    # provider default.
    extract_llm_temperature: float | None = None
    extract_llm_reasoning_effort: str | None = None
    extract_llm_max_completion_tokens: int | None = None

    # Optional extractor price per 1k tokens (external API cost reporting). Unset
    # (None) → cost is reported as "n/a"; token totals are always reported.
    # `cached_in` is the discounted price for prompt-cache-hit input tokens (kimi-k3
    # caches repeated prompt prefixes); unset → cached tokens billed at the miss price.
    extract_llm_price_in_per_1k: float | None = None
    extract_llm_price_out_per_1k: float | None = None
    extract_llm_price_cached_in_per_1k: float | None = None

    # Outbound email relay (handoff dispatch, LLD-HO-04)
    smtp_url: str = "smtp://user:pass@smtp.internal:587"

    def extractor_endpoint(self) -> tuple[str, str, str]:
        """Resolve the extractor ``(base_url, model, api_key)``.

        ``extract_llm_base_url`` / ``extract_llm_model`` fall back to the
        runtime ``llm_base_url`` / ``llm_model`` when unset (CR-0002). The API
        key defaults to empty (self-hosted endpoints need none).
        """
        base_url = self.extract_llm_base_url or self.llm_base_url
        model = self.extract_llm_model or self.llm_model
        return base_url, model, self.extract_llm_api_key


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
