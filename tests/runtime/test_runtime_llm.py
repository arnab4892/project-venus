"""PRD-N-002 / LLD-RT: the runtime chat LLM is the self-hosted endpoint, never the extractor.

The customer-facing runtime uses ``LLM_BASE_URL`` (self-hosted, qwen3 via Ollama). Even when
the offline extractor points at an external API, the runtime client must not borrow it. Also
checks the shared ``call_json`` seam works through an injected ``complete=`` (offline). Pure
Python — no DB, no network.
"""

from __future__ import annotations

from agentkit.config import Settings
from agentkit.extract.llm import call_json
from agentkit.runtime.llm import build_runtime_client


def _settings(**overrides: object) -> Settings:
    base = dict(
        llm_base_url="http://llm.internal:8000/v1",
        llm_model="qwen3",
        extract_llm_base_url="https://api.external.example/v1",
        extract_llm_model="big-extractor",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_runtime_client_uses_self_hosted_chat_endpoint() -> None:
    client = build_runtime_client(_settings())
    assert client.base_url == "http://llm.internal:8000/v1"
    assert client.model == "qwen3"


def test_runtime_client_never_borrows_the_extractor_endpoint() -> None:
    settings = _settings()
    client = build_runtime_client(settings)
    # The extractor may be external; the runtime must stay on the self-hosted chat endpoint.
    assert client.base_url != settings.extract_llm_base_url
    assert client.model != settings.extract_llm_model


def test_runtime_auth_key_sent_when_auth_on_and_omitted_when_off() -> None:
    # LLM_AUTH=true → the bearer key is wired into the client; LLM_AUTH=false force-disables auth
    # even with a key present, leaving the "not-needed" placeholder path in _openai().
    on = build_runtime_client(_settings(llm_auth=True, llm_api_key="sk-vllm-123"))
    assert on.api_key == "sk-vllm-123"

    off = build_runtime_client(_settings(llm_auth=False, llm_api_key="sk-vllm-123"))
    assert off.api_key == ""

    # backward-compatible default: no key set → nothing to send (placeholder used downstream)
    assert build_runtime_client(_settings()).api_key == ""


def test_runtime_sampling_knobs_flow_from_settings() -> None:
    client = build_runtime_client(_settings(llm_temperature=0.0, llm_reasoning_effort="low"))
    assert client.temperature == 0.0
    assert client.reasoning_effort == "low"
    assert client.max_completion_tokens is None


def test_call_json_through_injected_complete_seam() -> None:
    calls: list[tuple] = []

    def fake_complete(messages, schema, name):
        calls.append((name, tuple(m["role"] for m in messages)))
        return '{"intent": "faq", "confidence": 0.9}'

    out = call_json(
        client=None,
        complete=fake_complete,
        messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}],
        json_schema={"type": "object"},
        schema_name="triage",
        kind="triage",
        section_id=None,
    )
    assert out == {"intent": "faq", "confidence": 0.9}
    assert calls == [("triage", ("system", "user"))]
