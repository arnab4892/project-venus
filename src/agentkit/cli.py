"""``agentkit`` command-line entry point.

Only database/migration plumbing exists at this milestone; feature commands
(ingest, extract, release, eval, …) are added as their modules land.
"""

from __future__ import annotations

from pathlib import Path

import typer
from alembic import command
from alembic.config import Config

from agentkit.config import get_settings

app = typer.Typer(help="Jyotech Agent / agentkit framework CLI.", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
app.add_typer(db_app, name="db")

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


if __name__ == "__main__":
    app()
