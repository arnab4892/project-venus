"""Dev-only Langfuse tracing (dev tooling — LLD-free).

Turn-level observability against an **existing external self-hosted Langfuse** (v4, OTel-based
SDK). Nothing here is a PRD/HLD/LLD item; ``ops.*`` remains the system of record.

Three properties, all enforced in code (not by convention):

* **Structural gate.** :func:`tracing_enabled` is ``True`` only when ``JYOTECH_DEV_UI=1`` **and**
  all three ``LANGFUSE_*`` settings are set. Production tracing is a later, deliberate decision.
* **Force-off.** :func:`force_off` hard-disables tracing for a process regardless of env — called
  by the eval runner, the ``prompt``/``release`` activate gates and an autouse pytest fixture.
  Rationale: those paths run turns inside a rolled-back savepoint, so their traces would reference
  ``ops`` rows that never commit; the eval report is already the complete record.
* **Fail-open.** Every langfuse touch point is lazy and wrapped: langfuse is imported only inside
  functions (so the ``dev-obs`` extra is optional), and an unreachable / unconfigured / missing
  Langfuse never delays or breaks a turn — the wrapped code simply runs untraced.

The decorators are **inert until enabled at call time**: :func:`observe` decorates at import but
each call re-checks :func:`tracing_enabled`, so a decorated node/tool runs as the plain function
whenever tracing is off (including after :func:`force_off`).
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable

from agentkit.config import get_settings

logger = logging.getLogger(__name__)

# The dev-UI gate (mirrors apps/dev_ui.py). Tracing is dev-only for now.
_ENV_GATE = "JYOTECH_DEV_UI"

# Process-wide hard override set by eval / gate / pytest. Once on, tracing stays off.
_forced_off = False

# Whether the .env-sourced LANGFUSE_* settings have been exported into os.environ yet.
_env_bridged = False


def force_off() -> None:
    """Structurally disable tracing for the rest of this process (eval / gate / pytest)."""
    global _forced_off
    _forced_off = True


def ensure_configured() -> None:
    """Export the ``LANGFUSE_*`` settings into ``os.environ`` for the langfuse SDK.

    pydantic loads them from ``.env`` into :class:`Settings`, but the langfuse SDK reads its
    credentials from ``os.environ`` **directly** — a value that lives only in ``.env`` never
    reaches the SDK, so its client initialises ``without public_key`` and disables itself. We
    bridge the two here: idempotent, and only when tracing is actually enabled (so the three
    values are known non-empty). Must run before the first langfuse client is constructed —
    every langfuse touch point below calls it first.
    """
    global _env_bridged
    if _env_bridged or not tracing_enabled():
        return
    s = get_settings()
    os.environ["LANGFUSE_HOST"] = s.langfuse_host
    os.environ["LANGFUSE_PUBLIC_KEY"] = s.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = s.langfuse_secret_key
    _env_bridged = True


def tracing_enabled() -> bool:
    """True only when the dev-UI gate is set AND all three ``LANGFUSE_*`` settings are present.

    Evaluated at call time so :func:`force_off` (and env changes in tests) always take effect.
    """
    if _forced_off:
        return False
    if os.environ.get(_ENV_GATE) != "1":
        return False
    s = get_settings()
    return bool(s.langfuse_host and s.langfuse_public_key and s.langfuse_secret_key)


def observe(*, name: str | None = None, as_type: str | None = None) -> Callable:
    """Inert-by-default ``@observe``: wraps ``langfuse.observe`` only when tracing is enabled.

    The langfuse wrapper is built lazily on the first traced call and cached. If tracing is off
    (or langfuse cannot be imported), the decorated function runs untouched — no span, no import
    cost, no behavioural change.
    """

    def deco(fn: Callable) -> Callable:
        holder: dict[str, Callable] = {}

        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any):
            if not tracing_enabled():
                return fn(*args, **kwargs)
            wrapped = holder.get("fn")
            if wrapped is None:
                try:
                    ensure_configured()
                    from langfuse import observe as lf_observe

                    kw: dict[str, Any] = {"name": name}
                    if as_type is not None:
                        kw["as_type"] = as_type
                    wrapped = lf_observe(**kw)(fn)
                except Exception:  # noqa: BLE001 - fail-open: run untraced if langfuse is absent
                    logger.debug("langfuse.observe unavailable; running untraced", exc_info=True)
                    wrapped = fn
                holder["fn"] = wrapped
            # langfuse's wrapper only starts/ends a local span and exports in the background, so a
            # dead / unconfigured host cannot raise or block here; fn's own exceptions propagate.
            return wrapped(*args, **kwargs)

        return inner

    return deco


def trace_session(session_id) -> Any:
    """Context manager that stamps ``session_id`` on the current trace (no-op when tracing off).

    Uses langfuse's ``propagate_attributes`` so the id rides on the root span and all children.
    """
    if not tracing_enabled():
        return _nullcontext()
    try:
        ensure_configured()
        from langfuse import propagate_attributes

        return propagate_attributes(session_id=str(session_id))
    except Exception:  # noqa: BLE001 - fail-open
        logger.debug("langfuse propagate_attributes unavailable", exc_info=True)
        return _nullcontext()


def annotate_trace(*, turn_id=None, session_id=None, prompt_ids=None) -> None:
    """Attach turn_id / session_id / active prompt_version ids as metadata on the current trace.

    Called from inside the root ``@observe`` span (``run_turn``), so it updates that span — which
    is the trace root in langfuse v4. No-op / fail-open when tracing is off or langfuse errors.
    ``ops.*`` stays the system of record; this is diagnostic metadata only.
    """
    if not tracing_enabled():
        return
    try:
        ensure_configured()
        from langfuse import get_client

        metadata: dict[str, Any] = {}
        if turn_id is not None:
            metadata["turn_id"] = str(turn_id)
        if session_id is not None:
            metadata["session_id"] = str(session_id)
        if prompt_ids:
            metadata["prompt_ids"] = list(prompt_ids)
        get_client().update_current_span(metadata=metadata)
    except Exception:  # noqa: BLE001 - fail-open: metadata is diagnostic, never turn-critical
        logger.debug("langfuse trace annotation failed open", exc_info=True)


class _nullcontext:
    """A tiny no-op context manager (contextlib.nullcontext is fine too; kept local + explicit)."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
