"""Prompt versioning in ``ops.*`` (LLD-RT / LLD-AG, data-model §3.1).

The runtime reads each agent's system prompt from ``ops.prompt_version`` (the ACTIVE row
for that ``(client, agent)``), falling back to the file under ``clients/<client>/prompts/``
only when no active DB row exists — a dev convenience that is **logged loudly** so a
misconfigured deployment is obvious.

* ``agentkit prompt load <client>`` upserts ``ops.client`` from ``config.yaml`` + the active
  release, and loads the fixed **runtime prompt manifest** (:data:`RUNTIME_AGENTS`) into
  ``ops.prompt_version`` as new, inactive versions. The manifest is explicit so the offline
  extraction ``classifier.md`` is never mistaken for a runtime prompt.
* ``agentkit prompt activate <prompt_id>`` flips a version active (deactivating the previous
  active one for that agent). Activation is **gated by the golden suite** in the CLI
  (LLD-EVAL-03), mirroring ``release activate``.

``ops.client`` / ``ops.prompt_version`` are *config* tables managed by this tooling; the
append-only rule constrains the runtime's *conversation* writes, not these loaders. Activation
flips the designed ``is_active`` status column.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, text

from agentkit.client_config import load_client_config
from agentkit.extract.prompts import load_prompt as load_prompt_file

log = logging.getLogger(__name__)

# The runtime agents that own a DB-versioned prompt → the prompt file basename under
# clients/<client>/prompts/. (5a ships triage + these three agents; 5b adds the rest.)
RUNTIME_AGENTS: dict[str, str] = {
    "triage": "triage",
    "application_discovery": "application_discovery",
    "faq_company": "faq_company",
    "deflect": "deflect",
}


def _active_release(conn: Connection) -> str | None:
    return conn.execute(
        text("SELECT release_id FROM facts.release WHERE is_active")
    ).scalar_one_or_none()


def upsert_client(conn: Connection, client: str) -> None:
    """Upsert the ``ops.client`` config row from ``config.yaml`` + the active release."""
    cfg = load_client_config(client)
    conn.execute(
        text(
            "INSERT INTO ops.client (client_id, name, domain, theme, active_release, "
            "handoff_to, created_at) VALUES (:cid, :name, :domain, CAST(:theme AS jsonb), "
            ":rel, CAST(:handoff AS jsonb), now()) "
            "ON CONFLICT (client_id) DO UPDATE SET "
            "name = EXCLUDED.name, domain = EXCLUDED.domain, theme = EXCLUDED.theme, "
            "active_release = EXCLUDED.active_release, handoff_to = EXCLUDED.handoff_to"
        ),
        {
            "cid": cfg.get("client_id", client),
            "name": cfg.get("name"),
            "domain": cfg.get("domain"),
            "theme": _json_or_none(cfg.get("theme")),
            "rel": _active_release(conn),
            "handoff": _json_or_none(cfg.get("handoff_to")),
        },
    )


def _json_or_none(value) -> str | None:
    import json

    return None if value is None else json.dumps(value)


def _next_version(conn: Connection, client: str, agent: str) -> int:
    current = conn.execute(
        text(
            "SELECT max(version) FROM ops.prompt_version "
            "WHERE client_id = :c AND agent = :a"
        ),
        {"c": client, "a": agent},
    ).scalar_one_or_none()
    return (current or 0) + 1


def load_prompts(conn: Connection, client: str) -> list[dict]:
    """Load the runtime prompt manifest into ``ops.prompt_version`` (new inactive versions).

    Upserts ``ops.client`` first. Each agent's file body is inserted as the next version for
    that ``(client, agent)``; it is **not** activated here — ``prompt activate`` (golden-gated)
    does that. Returns ``[{prompt_id, agent, version}]`` for the rows written.
    """
    upsert_client(conn, client)
    written: list[dict] = []
    for agent, filename in RUNTIME_AGENTS.items():
        body = load_prompt_file(client, filename)
        version = _next_version(conn, client, agent)
        prompt_id = f"pv.{client}.{agent}.{version}"
        conn.execute(
            text(
                "INSERT INTO ops.prompt_version "
                "(prompt_id, client_id, agent, version, is_active, body, created_at) "
                "VALUES (:pid, :cid, :agent, :ver, false, :body, now())"
            ),
            {"pid": prompt_id, "cid": client, "agent": agent, "ver": version, "body": body},
        )
        written.append({"prompt_id": prompt_id, "agent": agent, "version": version})
    return written


def activate_prompt(conn: Connection, prompt_id: str) -> dict:
    """Flip ``prompt_id`` active, deactivating the current active row for its agent.

    Returns ``{prompt_id, client_id, agent, version}``. Raises ``ValueError`` if the id is
    unknown. The caller (CLI) wraps this in a transaction with the golden-suite gate so a
    failing suite rolls the activation back (LLD-EVAL-03).
    """
    row = conn.execute(
        text(
            "SELECT client_id, agent, version FROM ops.prompt_version WHERE prompt_id = :p"
        ),
        {"p": prompt_id},
    ).mappings().first()
    if row is None:
        raise ValueError(f"unknown prompt_id {prompt_id!r}")
    conn.execute(
        text(
            "UPDATE ops.prompt_version SET is_active = false "
            "WHERE client_id = :c AND agent = :a AND is_active"
        ),
        {"c": row["client_id"], "a": row["agent"]},
    )
    conn.execute(
        text("UPDATE ops.prompt_version SET is_active = true WHERE prompt_id = :p"),
        {"p": prompt_id},
    )
    return {"prompt_id": prompt_id, **dict(row)}


def activate_prompt_gated(
    conn: Connection,
    prompt_id: str,
    client: str,
    *,
    questions: list[dict] | None = None,
    embed=None,
    settings=None,
):
    """Activate ``prompt_id`` only if the golden suite passes (LLD-EVAL-03).

    Runs inside a SAVEPOINT: the activation is applied, the fact + retrieval layers of the
    golden suite run against the active release, and **any fact/retrieval failure rolls the
    activation back**. Returns ``(activated: bool, report)``. The tools read the
    ``facts.active_*`` views, so this gates prompt activation exactly as ``release activate``
    gates a release. ``questions`` is an injectable override for the gate test.
    """
    from agentkit.eval.runner import run_eval

    sp = conn.begin_nested()
    activate_prompt(conn, prompt_id)
    report = run_eval(conn, client, questions=questions, embed=embed, settings=settings)
    if report.ok:
        sp.commit()
        return True, report
    sp.rollback()
    return False, report


def active_prompt(conn: Connection, client: str, agent: str) -> tuple[str, str | None]:
    """Return ``(body, prompt_id)`` for an agent's active prompt, else the file (prompt_id None).

    Falls back to ``clients/<client>/prompts/<file>.md`` when no active DB row exists, logging
    loudly (a real deployment should have activated its prompts).
    """
    row = conn.execute(
        text(
            "SELECT prompt_id, body FROM ops.prompt_version "
            "WHERE client_id = :c AND agent = :a AND is_active"
        ),
        {"c": client, "a": agent},
    ).mappings().first()
    if row is not None:
        return row["body"], row["prompt_id"]

    filename = RUNTIME_AGENTS.get(agent, agent)
    log.warning(
        "ops.prompt_version has no ACTIVE prompt for (%s, %s) — falling back to the file "
        "clients/%s/prompts/%s.md. Run `agentkit prompt load %s` + `prompt activate` to use "
        "the DB-versioned prompt.",
        client, agent, client, filename, client,
    )
    return load_prompt_file(client, filename), None
