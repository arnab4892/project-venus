"""PRD-N-002 / LLD-RT: the runtime chat LLM is the self-hosted endpoint, never the extractor.

The customer-facing runtime uses ``LLM_BASE_URL`` (self-hosted, qwen3 via Ollama). Even when
the offline extractor points at an external API, the runtime client must not borrow it. Also
checks the shared ``call_json`` seam works through an injected ``complete=`` (offline). Pure
Python — no DB, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

from agentkit.config import Settings
from agentkit.extract.llm import build_request_kwargs, call_json
from agentkit.runtime.llm import RuntimeClient, build_runtime_client


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


# --- Qwen thinking-off knob (compose-only), LLD-RT / PRD-N-002 -----------------

def test_disable_thinking_flag_flows_from_settings() -> None:
    assert build_runtime_client(_settings()).disable_thinking is False
    assert build_runtime_client(_settings(llm_disable_thinking=True)).disable_thinking is True


def test_build_request_kwargs_disable_thinking_merges_with_reasoning_effort() -> None:
    # thinking-off must be added to extra_body WITHOUT clobbering a coexisting reasoning_effort.
    kwargs = build_request_kwargs(
        "qwen3", [{"role": "user", "content": "hi"}], {"type": "object"}, "answer",
        reasoning_effort="low", disable_thinking=True,
    )
    assert kwargs["extra_body"] == {
        "reasoning_effort": "low",
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_build_request_kwargs_no_extra_body_when_nothing_set() -> None:
    kwargs = build_request_kwargs(
        "qwen3", [{"role": "user", "content": "hi"}], {"type": "object"}, "answer",
    )
    assert "extra_body" not in kwargs


def _capturing_openai(capture: list) -> SimpleNamespace:
    """A stand-in OpenAI client that records create() kwargs and returns a minimal response."""
    def create(**kwargs):
        capture.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_thinking_off_applies_to_compose_only() -> None:
    # With the knob on, only the compose call (schema_name == "answer") carries thinking-off;
    # triage and parse calls (other schema names) are untouched.
    capture: list = []
    client = RuntimeClient(base_url="x", model="qwen3", disable_thinking=True)
    client._client = _capturing_openai(capture)

    client.raw_complete([{"role": "user", "content": "hi"}], {"type": "object"}, "answer")
    assert capture[-1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    client.raw_complete([{"role": "user", "content": "hi"}], {"type": "object"}, "triage")
    assert "extra_body" not in capture[-1]

    client.raw_complete([{"role": "user", "content": "hi"}], {"type": "object"}, "product_query")
    assert "extra_body" not in capture[-1]


def test_thinking_off_knob_off_leaves_compose_untouched() -> None:
    capture: list = []
    client = RuntimeClient(base_url="x", model="qwen3", disable_thinking=False)
    client._client = _capturing_openai(capture)
    client.raw_complete([{"role": "user", "content": "hi"}], {"type": "object"}, "answer")
    assert "extra_body" not in capture[-1]


# --- per-language thinking gate (hi keeps thinking on), LLD-RT-07 --------------

def test_compose_thinking_off_matrix() -> None:
    from agentkit.runtime.llm import compose_thinking_off

    # knob off ⇒ never thinking-off, whatever the schema/language
    for lang in ("en", "hinglish", "hi", None):
        assert compose_thinking_off(False, "answer", lang) is False
    # knob on, compose call: en/hinglish are silenced; hi keeps thinking on; None ⇒ non-Hindi
    assert compose_thinking_off(True, "answer", "en") is True
    assert compose_thinking_off(True, "answer", "hinglish") is True
    assert compose_thinking_off(True, "answer", "hi") is False
    assert compose_thinking_off(True, "answer", None) is True
    # knob on, non-compose calls (triage/parse) are never thinking-off, whatever the language
    for name in ("triage", "product_query"):
        for lang in ("en", "hinglish", "hi", None):
            assert compose_thinking_off(True, name, lang) is False


def test_raw_complete_keeps_thinking_on_for_hindi_compose() -> None:
    capture: list = []
    client = RuntimeClient(base_url="x", model="qwen3", disable_thinking=True)
    client._client = _capturing_openai(capture)
    msg = [{"role": "user", "content": "hi"}]

    client.raw_complete(msg, {"type": "object"}, "answer", language="hi")
    assert "extra_body" not in capture[-1]  # Devanagari compose thinks

    client.raw_complete(msg, {"type": "object"}, "answer", language="en")
    assert capture[-1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    client.raw_complete(msg, {"type": "object"}, "answer", language="hinglish")
    assert capture[-1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_complete_fn_seam_reads_compose_language_attribute() -> None:
    capture: list = []
    client = RuntimeClient(base_url="x", model="qwen3", disable_thinking=True)
    client._client = _capturing_openai(capture)
    seam = client.complete_fn()
    msg = [{"role": "user", "content": "hi"}]

    seam.compose_language = "hi"
    seam(msg, {"type": "object"}, "answer")
    assert "extra_body" not in capture[-1]  # hi ⇒ thinking on

    seam.compose_language = "en"
    seam(msg, {"type": "object"}, "answer")
    assert capture[-1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_compose_grounded_answer_sets_reply_language_on_the_seam() -> None:
    # compose_grounded_answer must stamp the turn's reply language onto the complete seam so the
    # per-language thinking gate can see it (base.py); asserted for a hi turn.
    from agentkit.runtime.agents.base import compose_grounded_answer

    class _Spy:
        seen = "UNSET"

        def __call__(self, messages, json_schema, schema_name):
            self.seen = getattr(self, "compose_language", "UNSET")
            return '{"message": "ठीक है", "citations": []}'

    spy = _Spy()
    compose_grounded_answer(
        complete=spy, prompt_body="p", history=[], latest_user="q", records=[], language="hi",
    )
    assert spy.seen == "hi"


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
