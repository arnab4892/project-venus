"""Provider-safe request assembly: sampling params are sent only when set.

kimi-k3 rejects `temperature` (must be omitted) and takes `reasoning_effort ∈
{low,high,max}`; a self-hosted model may want `temperature=0`. build_request_kwargs
must include each knob only when configured. Pure-Python, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentkit.config import Settings
from agentkit.extract.llm import (
    CallLog,
    CallRecord,
    build_request_kwargs,
    cached_input_tokens,
)

_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_MSGS = [{"role": "user", "content": "hi"}]


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_omits_all_sampling_params_by_default() -> None:
    kwargs = build_request_kwargs("m", _MSGS, _SCHEMA, "s")
    assert set(kwargs) == {"model", "messages", "response_format"}
    assert "temperature" not in kwargs          # kimi-k3 would 400 on temperature
    assert "extra_body" not in kwargs
    assert "max_completion_tokens" not in kwargs


def test_response_format_is_strict_json_schema() -> None:
    rf = build_request_kwargs("m", _MSGS, _SCHEMA, "my_schema")["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "my_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] is _SCHEMA


def test_reasoning_effort_goes_via_extra_body() -> None:
    kwargs = build_request_kwargs("m", _MSGS, _SCHEMA, "s", reasoning_effort="max")
    assert kwargs["extra_body"] == {"reasoning_effort": "max"}
    # Never leaks as a top-level kwarg (avoids the SDK's typed literal).
    assert "reasoning_effort" not in kwargs


def test_temperature_and_tokens_when_set() -> None:
    kwargs = build_request_kwargs(
        "m", _MSGS, _SCHEMA, "s", temperature=0, max_completion_tokens=4096
    )
    assert kwargs["temperature"] == 0
    assert kwargs["max_completion_tokens"] == 4096


# --- prompt-cache accounting ------------------------------------------------ #

def test_cached_input_tokens_reads_common_usage_shapes() -> None:
    openai_style = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=800))
    flat = SimpleNamespace(cached_tokens=640, prompt_tokens_details=None)
    deepseek_style = SimpleNamespace(prompt_cache_hit_tokens=512, prompt_tokens_details=None)
    anthropic_style = SimpleNamespace(cache_read_input_tokens=384, prompt_tokens_details=None)
    assert cached_input_tokens(openai_style) == 800
    assert cached_input_tokens(flat) == 640
    assert cached_input_tokens(deepseek_style) == 512
    assert cached_input_tokens(anthropic_style) == 384
    assert cached_input_tokens(SimpleNamespace(prompt_tokens_details=None)) == 0
    assert cached_input_tokens(None) == 0


def _rec(tin: int, tout: int, cached: int) -> CallRecord:
    return CallRecord("kimi-k3", "classify", "s1", tin, tout, cached, 10, 1, ok=True)


def test_totals_reports_cache_hit_rate() -> None:
    log = CallLog()
    log.append(_rec(1000, 200, 800))
    log.append(_rec(500, 100, 0))
    totals = log.totals(settings=_settings())
    assert totals["tokens_in"] == 1500
    assert totals["tokens_cached"] == 800
    assert totals["cache_hit_rate"] == 800 / 1500
    assert totals["cost_usd"] is None  # no prices configured


def test_totals_prices_cached_tokens_at_the_discount() -> None:
    log = CallLog()
    log.append(_rec(1000, 200, 800))
    log.append(_rec(500, 100, 0))  # tokens_in=1500, cached=800, miss=700, out=300
    priced = _settings(
        extract_llm_price_in_per_1k=0.003,
        extract_llm_price_out_per_1k=0.015,
        extract_llm_price_cached_in_per_1k=0.0003,
    )
    cost = log.totals(settings=priced)["cost_usd"]
    # miss 700*0.003/1k + cached 800*0.0003/1k + out 300*0.015/1k
    assert cost == pytest.approx(0.0021 + 0.00024 + 0.0045)


def test_totals_without_cached_price_falls_back_to_miss_rate() -> None:
    log = CallLog()
    log.append(_rec(1000, 200, 800))
    log.append(_rec(500, 100, 0))
    priced = _settings(extract_llm_price_in_per_1k=0.003, extract_llm_price_out_per_1k=0.015)
    cost = log.totals(settings=priced)["cost_usd"]
    # cached billed at the miss rate: all 1500 in * 0.003/1k + 300 out * 0.015/1k
    assert cost == pytest.approx(1500 / 1000 * 0.003 + 300 / 1000 * 0.015)
