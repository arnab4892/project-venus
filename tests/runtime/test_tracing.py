"""Dev-only Langfuse tracing: the structural gate, the eval/pytest force-off, and fail-open.

The suite's autouse fixture force-disables tracing globally; these tests locally clear
``_forced_off`` (via monkeypatch, auto-restored) to exercise the gate itself, and stub
``tracing.get_settings`` so the assertions never depend on the developer's real ``.env``.
"""

from __future__ import annotations

import sys
import types

from agentkit.config import Settings
from agentkit.runtime import tracing


def _with_keys() -> Settings:
    return Settings(langfuse_host="http://lf.internal", langfuse_public_key="pk",
                    langfuse_secret_key="sk")


def _no_keys() -> Settings:
    return Settings(langfuse_host="", langfuse_public_key="", langfuse_secret_key="")


def test_off_without_dev_flag_even_with_keys(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_forced_off", False)
    monkeypatch.setattr(tracing, "get_settings", _with_keys)
    monkeypatch.delenv("JYOTECH_DEV_UI", raising=False)
    assert tracing.tracing_enabled() is False


def test_off_without_keys_even_with_dev_flag(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_forced_off", False)
    monkeypatch.setattr(tracing, "get_settings", _no_keys)
    monkeypatch.setenv("JYOTECH_DEV_UI", "1")
    assert tracing.tracing_enabled() is False


def test_enabled_only_with_both_gate_and_keys(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_forced_off", False)
    monkeypatch.setattr(tracing, "get_settings", _with_keys)
    monkeypatch.setenv("JYOTECH_DEV_UI", "1")
    assert tracing.tracing_enabled() is True


def test_eval_run_forces_tracing_off(monkeypatch) -> None:
    from agentkit.eval.runner import run_eval

    monkeypatch.setattr(tracing, "_forced_off", False)
    monkeypatch.setattr(tracing, "get_settings", _with_keys)
    monkeypatch.setenv("JYOTECH_DEV_UI", "1")
    # Gate would be ON if not for the eval force-off.
    assert tracing.tracing_enabled() is True

    # An eval run with the tracing env fully set must still emit nothing (no layers → no DB use).
    run_eval(conn=None, client="jyotech", layers=())

    assert tracing.tracing_enabled() is False


def test_ensure_configured_bridges_settings_into_os_environ(monkeypatch) -> None:
    # The langfuse SDK reads credentials from os.environ, not from our pydantic .env-backed
    # Settings — ensure_configured() bridges them so the client does not init "without public_key".
    import os

    monkeypatch.setattr(tracing, "_forced_off", False)
    monkeypatch.setattr(tracing, "_env_bridged", False)
    monkeypatch.setattr(tracing, "get_settings", _with_keys)
    monkeypatch.setenv("JYOTECH_DEV_UI", "1")
    # Seed the three via monkeypatch so teardown restores them regardless of the bridge's writes.
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(k, "seed")

    tracing.ensure_configured()

    assert os.environ["LANGFUSE_HOST"] == "http://lf.internal"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk"


def test_ensure_configured_noop_when_disabled(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tracing, "_forced_off", True)  # disabled → must not touch os.environ
    monkeypatch.setattr(tracing, "_env_bridged", False)
    monkeypatch.setattr(tracing, "get_settings", _with_keys)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    tracing.ensure_configured()

    assert "LANGFUSE_PUBLIC_KEY" not in os.environ


def test_observe_inert_when_disabled(monkeypatch) -> None:
    # Disabled: the decorated function runs as-is and langfuse is never touched.
    monkeypatch.setattr(tracing, "_forced_off", True)

    @tracing.observe(name="node")
    def double(x):
        return x * 2

    assert double(21) == 42


def test_observe_fails_open_when_langfuse_broken(monkeypatch) -> None:
    # Enabled, but the langfuse layer is broken/unreachable → run untraced, never raise.
    monkeypatch.setattr(tracing, "_forced_off", False)
    monkeypatch.setattr(tracing, "get_settings", _with_keys)
    monkeypatch.setenv("JYOTECH_DEV_UI", "1")

    broken = types.ModuleType("langfuse")

    def _raises(*_a, **_k):
        raise RuntimeError("langfuse unreachable")

    broken.observe = _raises  # `from langfuse import observe` picks this up, then it raises
    monkeypatch.setitem(sys.modules, "langfuse", broken)

    @tracing.observe(name="tool", as_type="tool")
    def add(a, b):
        return a + b

    assert add(20, 22) == 42  # fail-open: the value still comes through


def test_annotate_and_session_are_noops_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_forced_off", True)
    # Neither should raise nor require a live client when tracing is off.
    tracing.annotate_trace(turn_id="t", session_id="s", prompt_ids=["p"])
    with tracing.trace_session("s"):
        pass
