"""PRD-N-002 / CR-0002: the extractor LLM endpoint is separate from the runtime.

The offline extractor may use an external API (``EXTRACT_LLM_BASE_URL``); the
customer-facing chat (``LLM_BASE_URL``) and the embedding model
(``EMBED_BASE_URL``) stay self-hosted. When the extractor endpoint is unset it
falls back to the runtime chat endpoint/model. Pure-Python — no DB, no network.
"""

from __future__ import annotations

from agentkit.config import Settings
from agentkit.extract.llm import build_extractor_client


def _settings(**overrides: object) -> Settings:
    # _env_file=None → ignore any local .env so the assertions are deterministic.
    base = dict(
        llm_base_url="http://llm.internal:8000/v1",
        llm_model="chat-model",
        embed_base_url="http://embed.internal:8001/v1",
        embed_model="bge-m3",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_extractor_uses_its_own_endpoint_when_set() -> None:
    settings = _settings(
        extract_llm_base_url="https://api.external.example/v1",
        extract_llm_model="big-extractor",
        extract_llm_api_key="sk-secret",
    )
    client = build_extractor_client(settings)
    assert client.base_url == "https://api.external.example/v1"
    assert client.model == "big-extractor"
    assert client.api_key == "sk-secret"


def test_extractor_falls_back_to_runtime_chat_when_unset() -> None:
    settings = _settings()  # no extract_* set
    base_url, model, api_key = settings.extractor_endpoint()
    assert base_url == "http://llm.internal:8000/v1"
    assert model == "chat-model"
    assert api_key == ""

    client = build_extractor_client(settings)
    assert client.base_url == "http://llm.internal:8000/v1"
    assert client.model == "chat-model"


def test_extractor_never_borrows_the_embedding_endpoint() -> None:
    settings = _settings(extract_llm_base_url="https://api.external.example/v1")
    client = build_extractor_client(settings)
    # Embeddings stay self-hosted and are untouched by the extractor resolution.
    assert client.base_url != settings.embed_base_url
    assert settings.embed_base_url == "http://embed.internal:8001/v1"
    assert settings.embed_model == "bge-m3"


def test_runtime_chat_endpoint_is_independent_of_extractor() -> None:
    # Setting the extractor endpoint must not change the runtime chat endpoint.
    settings = _settings(extract_llm_base_url="https://api.external.example/v1")
    assert settings.llm_base_url == "http://llm.internal:8000/v1"
    assert settings.llm_model == "chat-model"


def test_sampling_knobs_default_to_unset() -> None:
    client = build_extractor_client(_settings())
    assert client.temperature is None          # omitted → kimi-k3 safe
    assert client.reasoning_effort is None
    assert client.max_completion_tokens is None


def test_sampling_knobs_flow_from_settings_to_client() -> None:
    settings = _settings(
        extract_llm_reasoning_effort="high",
        extract_llm_max_completion_tokens=8192,
    )
    client = build_extractor_client(settings)
    assert client.reasoning_effort == "high"
    assert client.max_completion_tokens == 8192
    assert client.temperature is None
