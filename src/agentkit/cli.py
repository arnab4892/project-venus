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
ingest_app = typer.Typer(help="Crawl + convert client sources (stage 1).", no_args_is_help=True)
extract_app = typer.Typer(help="Extract facts to staging (LLM).", no_args_is_help=True)
release_app = typer.Typer(help="Release candidates + review export.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(seed_app, name="seed")
app.add_typer(tools_app, name="tools")
app.add_typer(ingest_app, name="ingest")
app.add_typer(extract_app, name="extract")
app.add_typer(release_app, name="release")

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


@ingest_app.command("run")
def ingest_run_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: the single clients/* dir)."
    ),
    rc: Optional[str] = typer.Option(
        None, "--rc", help="Release-candidate id (default: rc.<client>.bootstrap)."
    ),
) -> None:
    """Crawl + convert the client's sources to data/<client>/md/ and record staging rows.

    Stage 1 only: no extraction, no LLM. Writes one staging.document row per
    source (LLD-ING-05) and prints a conversion report.
    """
    from agentkit.db.engine import connect
    from agentkit.ingest.pipeline import run_ingest
    from agentkit.ingest.sources import autodetect_client

    client = client or autodetect_client()
    with connect() as conn:
        with conn.begin():
            report = run_ingest(client, rc_id=rc, conn=conn)
    for line in report.summary_lines():
        typer.echo(line)


@ingest_app.command("verify")
def ingest_verify_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: the single clients/* dir)."
    ),
) -> None:
    """Verify converted Markdown against an independent witness (pdftotext / junk scan).

    PDFs: assert the pdftotext token multiset appears in the Markdown. HTML:
    assert no banned junk patterns remain. Exits non-zero on any failure.
    """
    from agentkit.ingest.sources import autodetect_client
    from agentkit.ingest.verify import run_verify

    client = client or autodetect_client()
    report = run_verify(client)
    for line in report.lines():
        typer.echo(line)
    if not report.ok:
        raise typer.Exit(code=1)


@extract_app.command("run")
def extract_run_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: the single clients/* dir)."
    ),
    rc: str = typer.Option(..., "--rc", help="Release-candidate id (from `release create`)."),
    redo_families: bool = typer.Option(
        False, "--redo-families", help="Re-run pass-1 family discovery, overwriting families.yaml."
    ),
) -> None:
    """Extract facts to staging under RC (LLD-EXT). Pauses at families.yaml (pass 1).

    First run (no families.yaml) proposes the family list and STOPS for review.
    Re-run after approving families.yaml completes pass-2 typed extraction into
    staging.*. Never writes to facts.*.
    """
    from openai import APIConnectionError

    from agentkit.config import get_settings
    from agentkit.db.engine import connect
    from agentkit.extract.run import run_extract
    from agentkit.ingest.sources import autodetect_client

    client = client or autodetect_client()
    try:
        with connect() as conn:
            with conn.begin():
                report = run_extract(client, rc_id=rc, conn=conn, redo_families=redo_families)
    except APIConnectionError:
        base, _, _ = get_settings().extractor_endpoint()
        typer.echo(
            f"Cannot reach the extractor LLM at {base}. Set EXTRACT_LLM_BASE_URL "
            "(and EXTRACT_LLM_MODEL / EXTRACT_LLM_API_KEY) to a reachable "
            "OpenAI-compatible endpoint, then re-run. No staging rows were written."
        )
        raise typer.Exit(code=1) from None
    for line in report.summary_lines():
        typer.echo(line)


@release_app.command("create")
def release_create_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: the single clients/* dir)."
    ),
) -> None:
    """Allocate the next release candidate rc.<client>.NNNN (status open)."""
    from agentkit.db.engine import connect
    from agentkit.ingest.sources import autodetect_client
    from agentkit.release.candidate import create_rc

    client = client or autodetect_client()
    with connect() as conn:
        with conn.begin():
            rc_id = create_rc(conn, client)
    typer.echo(f"Created {rc_id} (status open).")


@release_app.command("list")
def release_list_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: all clients)."
    ),
) -> None:
    """List release candidates (newest first)."""
    from agentkit.db.engine import connect
    from agentkit.release.candidate import list_rc

    with connect() as conn:
        rows = list_rc(conn, client)
    if not rows:
        typer.echo("No release candidates.")
        return
    for r in rows:
        typer.echo(f"{r['id']:24} {r['status']:10} {r['created_at']:%Y-%m-%d %H:%M}")


@release_app.command("export")
def release_export_cmd(
    rc: str = typer.Argument(..., help="Release-candidate id, e.g. rc.<client>.0001."),
    out: Optional[str] = typer.Option(
        None, "--out", help="Output xlsx path (default: data/<client>/extract/<rc>/review-<rc>.xlsx)."
    ),
) -> None:
    """Export the RC's staging rows to a review workbook (LLD-REL-01)."""
    import re

    from agentkit.db.engine import connect
    from agentkit.release.export import export_rc

    m = re.match(r"^rc\.(?P<client>.+)\.\w+$", rc)
    if not m:
        raise typer.BadParameter(f"unrecognised RC id {rc!r} (expected rc.<client>.NNNN)")
    client = m.group("client")
    out_path = Path(out) if out else (
        _REPO_ROOT / "data" / client / "extract" / rc / f"review-{rc}.xlsx"
    )
    with connect() as conn:
        with conn.begin():
            counts = export_rc(conn, rc, out_path)
    total = sum(counts.values())
    typer.echo(f"Exported {total} rows to {out_path}")
    for table, n in counts.items():
        typer.echo(f"  {table:16} {n}")


def _client_of(rc: str) -> str:
    import re

    m = re.match(r"^rc\.(?P<client>.+)\.\w+$", rc)
    if not m:
        raise typer.BadParameter(f"unrecognised RC id {rc!r} (expected rc.<client>.NNNN)")
    return m.group("client")


def _default_review_path(client: str, rc: str) -> Path:
    return _REPO_ROOT / "data" / client / "extract" / rc / f"review-{rc}.xlsx"


@release_app.command("import")
def release_import_cmd(
    rc: str = typer.Argument(..., help="Release-candidate id, e.g. rc.<client>.0001."),
    xlsx: Optional[str] = typer.Argument(None, help="Reviewed workbook (default: the exported path)."),
) -> None:
    """Apply reviewer decisions from the reviewed workbook into staging (LLD-REL-02)."""
    from agentkit.db.engine import connect
    from agentkit.release.import_review import import_review

    client = _client_of(rc)
    path = Path(xlsx) if xlsx else _default_review_path(client, rc)
    if not path.exists():
        raise typer.BadParameter(f"workbook not found: {path}")
    with connect() as conn:
        with conn.begin():
            counts = import_review(conn, rc, path, client=client)
    typer.echo(f"Imported decisions from {path}; RC {rc} → imported.")
    for table, buckets in counts.items():
        total = sum(buckets.values())
        if total:
            summary = " ".join(f"{k}={v}" for k, v in buckets.items() if v)
            typer.echo(f"  {table:16} {summary}")


@release_app.command("check")
def release_check_cmd(
    rc: str = typer.Argument(..., help="Release-candidate id, e.g. rc.<client>.0001."),
) -> None:
    """Integrity-check the approved+edited set of an RC (LLD-REL-03)."""
    from agentkit.db.engine import connect
    from agentkit.release.check import check_rc, format_report

    client = _client_of(rc)
    with connect() as conn:
        report = check_rc(conn, rc, client=client)
    typer.echo(format_report(report))
    if not report.ok:
        raise typer.Exit(code=1)


@release_app.command("diff")
def release_diff_cmd(
    rc: str = typer.Argument(..., help="Release-candidate id, e.g. rc.<client>.0001."),
) -> None:
    """Diff an RC's surviving set against the active release (LLD-REL-04)."""
    from agentkit.db.engine import connect
    from agentkit.release.diff import diff_rc, format_diff

    with connect() as conn:
        report = diff_rc(conn, rc)
    typer.echo(format_diff(report))


@release_app.command("promote")
def release_promote_cmd(
    rc: str = typer.Argument(..., help="Release-candidate id, e.g. rc.<client>.0001."),
) -> None:
    """Promote approved+edited rows into facts.* under a new release (LLD-REL-05)."""
    from agentkit.db.engine import connect
    from agentkit.release.promote import promote

    client = _client_of(rc)
    with connect() as conn:
        with conn.begin():
            result = promote(conn, rc, client=client)
    typer.echo(f"Promoted {rc} → release {result['release_id']} (not yet active).")
    for table, n in result["counts"].items():
        typer.echo(f"  {table:16} {n}")
    typer.echo("  embedding (LLD-RET) deferred to the next milestone.")
    typer.echo(f"Activate with:  agentkit release activate {result['release_id']}")


@release_app.command("activate")
def release_activate_cmd(
    release_id: str = typer.Argument(..., help="Release id, e.g. r2026.08.2."),
) -> None:
    """Make a release the single active one (LLD-REL-05)."""
    from agentkit.db.engine import connect
    from agentkit.release.promote import activate

    with connect() as conn:
        with conn.begin():
            activate(conn, release_id)
    typer.echo(f"Activated {release_id}.")


@release_app.command("rollback")
def release_rollback_cmd(
    release_id: str = typer.Argument(..., help="Release id to activate instead (roll back to)."),
) -> None:
    """Roll back to a prior release by making it active (LLD-REL-05)."""
    from agentkit.db.engine import connect
    from agentkit.release.promote import activate

    with connect() as conn:
        with conn.begin():
            activate(conn, release_id)
    typer.echo(f"Rolled back to {release_id}.")


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
