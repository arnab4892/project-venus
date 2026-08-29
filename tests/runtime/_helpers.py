"""Importable helpers for the runtime turn tests (kept out of conftest so tests can import them).

``FakeLLM`` is the injectable ``complete=`` seam: it maps a schema name (``triage``,
``application_slots``, ``answer``, ``deflect``) to canned response(s), so a whole turn runs
offline and deterministically — no Ollama, no network.
"""

from __future__ import annotations

import json

from agentkit.runtime.prompts import load_prompts
from sqlalchemy import text


class FakeLLM:
    """A ``complete=`` seam returning canned JSON keyed by schema name.

    Each value is a single dict (returned every call) or a list used as a FIFO queue.
    Records the schema names seen in ``.seen`` for assertions.
    """

    def __init__(self, responses: dict[str, object]):
        self._responses = {k: (list(v) if isinstance(v, list) else v) for k, v in responses.items()}
        self.seen: list[str] = []

    def __call__(self, messages, json_schema, schema_name):
        self.seen.append(schema_name)
        val = self._responses.get(schema_name)
        if val is None:
            raise AssertionError(f"FakeLLM got no canned response for schema {schema_name!r}")
        if isinstance(val, list):
            if not val:
                raise AssertionError(f"FakeLLM queue for {schema_name!r} is empty")
            val = val.pop(0)
        return json.dumps(val)


def activate_all_prompts(conn, client: str = "jyotech") -> None:
    """Load the runtime prompt manifest and activate the freshly-loaded version per agent.

    Deactivates any other version for that agent first (there may be committed rows in the dev
    DB from a real ``prompt load``), so exactly one version is active per (client, agent) —
    satisfying the partial unique index. No golden gate (that path is covered by
    ``test_prompt_activate_gate``).
    """
    written = load_prompts(conn, client)
    for w in written:
        conn.execute(
            text(
                "UPDATE ops.prompt_version SET is_active = false "
                "WHERE client_id = :c AND agent = :a AND is_active"
            ),
            {"c": client, "a": w["agent"]},
        )
        conn.execute(
            text("UPDATE ops.prompt_version SET is_active = true WHERE prompt_id = :p"),
            {"p": w["prompt_id"]},
        )
