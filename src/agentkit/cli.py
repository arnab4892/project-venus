"""``agentkit`` command-line entry point.

Database/migration plumbing plus the first data + tool commands (``seed``,
``tools``); ingest/extract/release/eval land as their modules do.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from alembic import command
from alembic.config import Config

from agentkit.config import get_settings

app = typer.Typer(help="agentkit framework CLI.", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
seed_app = typer.Typer(help="Seed data into facts.*.", no_args_is_help=True)
tools_app = typer.Typer(help="Run a runtime tool from the shell.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(seed_app, name="seed")
app.add_typer(tools_app, name="tools")

# Repo root = two levels up from this file (src/agentkit/cli.py -> src -> root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def _alembic_config() -> Config:
    """Build an Alembic ``Config`` anchored to the repo, with the URL from settings.

    Anchoring by path (not CWD) lets ``agentkit db …`` run from anywhere. The URL
    is injected here so no secret is committed in ``alembic.ini``.
    """
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


@db_app.command()
def upgrade(revision: str = typer.Option("head", help="Target revision.")) -> None:
    """Apply migrations up to REVISION (default: head)."""
    command.upgrade(_alembic_config(), revision)
    typer.echo(f"Upgraded to {revision}.")


@db_app.command()
def downgrade(revision: str = typer.Argument(..., help="Target revision (e.g. base).")) -> None:
    """Revert migrations down to REVISION."""
    command.downgrade(_alembic_config(), revision)
    typer.echo(f"Downgraded to {revision}.")


@db_app.command()
def current() -> None:
    """Show the currently-applied migration revision."""
    command.current(_alembic_config(), verbose=True)


@seed_app.command("demo")
def seed_demo_cmd(
    client: Optional[str] = typer.Option(
        None, "--client", help="Client id (default: the single clients/* dir)."
    ),
) -> None:
    """Load the demo fact rows into facts.* (idempotent), marking its release active."""
    from agentkit.db.engine import connect
    from agentkit.release.seed import seed_demo

    with connect() as conn:
        with conn.begin():
            counts = seed_demo(conn, client=client)
    total = sum(counts.values())
    typer.echo(f"Seeded {total} fact rows: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


@tools_app.command("match-capability")
def match_capability_cmd(
    gas: str = typer.Option(..., "--gas", help="Gas to compress, e.g. hydrogen."),
    capacity: float = typer.Option(..., "--capacity", help="Flow capacity."),
    unit: str = typer.Option("Nm3/hr", "--unit", help="Capacity unit: Nm3/hr or SCMD."),
    discharge_p: float = typer.Option(..., "--discharge-p", help="Discharge pressure (barg)."),
    oil_free: Optional[bool] = typer.Option(
        None, "--oil-free/--lubricated", help="Filter to oil-free / lubricated rows."
    ),
    standard: Optional[str] = typer.Option(None, "--standard", help="Required standard."),
) -> None:
    """Match a gas duty against the published capability envelope (LLD-TOOL-01)."""
    from agentkit.db.engine import connect
    from agentkit.tools.match_capability import match_capability

    lubricated = None if oil_free is None else (not oil_free)
    with connect() as conn:
        result = match_capability(
            conn,
            gas=gas,
            capacity=capacity,
            capacity_unit=unit,
            discharge_p=discharge_p,
            lubricated=lubricated,
            standard=standard,
        )
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
