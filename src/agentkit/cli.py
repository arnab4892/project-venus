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
chunk_app = typer.Typer(help="Chunk the active release for embedding.", no_args_is_help=True)
eval_app = typer.Typer(help="Golden-suite evaluation.", no_args_is_help=True)
prompt_app = typer.Typer(help="Prompt versioning in ops.prompt_version.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(seed_app, name="seed")
app.add_typer(tools_app, name="tools")
app.add_typer(ingest_app, name="ingest")
app.add_typer(extract_app, name="extract")
app.add_typer(release_app, name="release")
app.add_typer(chunk_app, name="chunk")
app.add_typer(eval_app, name="eval")
app.add_typer(prompt_app, name="prompt")

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
    release_id = result["release_id"]
    typer.echo(f"Promoted {rc} → release {release_id} (not yet active).")
    for table, n in result["counts"].items():
        typer.echo(f"  {table:16} {n}")

    # Trigger embedding (LLD-RET / LLD-REL-05). Best-effort in its own transaction: a
    # promote must not be lost because the self-hosted embedder is unreachable — the
    # facts are already committed and `agentkit release embed` can run later.
    from agentkit.retrieval.embed import release_embed

    try:
        with connect() as conn:
            with conn.begin():
                emb = release_embed(conn, client, release_id)
        typer.echo(f"  embedded {emb['embedded']}/{emb['chunks']} chunks → {emb['table']}")
    except Exception as exc:  # noqa: BLE001 - report, never abort the promote
        typer.echo(f"  embedding deferred: {type(exc).__name__}: {exc}")
        typer.echo(f"  run when the embedder is reachable:  agentkit release embed {release_id}")
    typer.echo(f"Activate with:  agentkit release activate {release_id}")


@release_app.command("embed")
def release_embed_cmd(
    release_id: str = typer.Argument(..., help="Release id, e.g. r2026.08.2."),
    client: Optional[str] = typer.Option(
        None, "--client", help="Client id (default: the single clients/* dir)."
    ),
) -> None:
    """Chunk + embed a release into facts.chunk and vec.chunk_embedding_<release>.

    Applies the approved chunking.yaml (LLD-RET-04), writes the chunk text/FTS to
    facts.chunk, and embeds every chunk with the self-hosted model into the per-release
    HNSW vector table (LLD-RET-02 / LLD-DB-03). Idempotent.
    """
    from agentkit.db.engine import connect
    from agentkit.ingest.sources import autodetect_client
    from agentkit.retrieval.embed import release_embed

    client = client or autodetect_client()
    with connect() as conn:
        with conn.begin():
            result = release_embed(conn, client, release_id)
    typer.echo(
        f"Embedded {result['embedded']}/{result['chunks']} chunks "
        f"→ {result['table']} ({result['dim']}-d)"
    )


@eval_app.command("run")
def eval_run_cmd(
    client: str = typer.Argument(..., help="Client id, e.g. jyotech."),
    layers: str = typer.Option(
        "fact,retrieval,e2e", "--layers",
        help="Comma-separated layers to run. Drop 'e2e' for a fast dev run (no runtime LLM).",
    ),
) -> None:
    """Run the golden suite against the active release (LLD-EVAL-02/03).

    Runs all three layers by default: fact, retrieval and **e2e** (each question through the
    real orchestrator + self-hosted runtime LLM). The full e2e pass can be slow — that's
    acceptable for an activation gate; use `--layers fact,retrieval` to skip it explicitly.
    Exits non-zero if any executed check fails.
    """
    from agentkit.db.engine import connect
    from agentkit.eval.runner import format_report, run_eval

    layer_tuple = tuple(s.strip() for s in layers.split(",") if s.strip())
    complete = None
    if "e2e" in layer_tuple:
        from agentkit.runtime.llm import build_runtime_client

        complete = build_runtime_client().complete_fn()

    with connect() as conn:
        report = run_eval(conn, client, complete=complete, layers=layer_tuple)
    typer.echo(format_report(report))
    if not report.ok:
        raise typer.Exit(code=1)


@chunk_app.command("run")
def chunk_run_cmd(
    client: str = typer.Argument(..., help="Client id, e.g. jyotech."),
) -> None:
    """Seed/refresh chunking.yaml + a disposition report for the active release (Gate 1).

    Writes ``clients/<client>/seeds/chunking.yaml`` and prints the per-document
    disposition, chunk counts, dedup list and largest-chunk stats, then STOPS. Review /
    edit the yaml, then run ``agentkit release embed <release>``. Re-running applies the
    approved yaml.
    """
    from agentkit.db.engine import connect
    from agentkit.retrieval.chunk import (
        chunk_corpus,
        format_report,
        resolve_active_release,
        write_dispositions_config,
    )
    from agentkit.retrieval.tokenizer import bge_m3_counter

    settings = get_settings()
    counter = bge_m3_counter(settings)
    with connect() as conn:
        release_id = resolve_active_release(conn)
        _chunks, report = chunk_corpus(
            conn, client, release_id, count_tokens=counter, embed_limit=settings.embed_limit
        )
    path = write_dispositions_config(client, report.dispositions)
    typer.echo(format_report(report, embed_limit=settings.embed_limit))
    typer.echo("")
    typer.echo(f"Wrote {path}")
    typer.echo("── GATE 1 ── review dispositions / edit chunking.yaml, then embed:")
    typer.echo(f"  agentkit release embed {release_id}")


@release_app.command("activate")
def release_activate_cmd(
    release_id: str = typer.Argument(..., help="Release id, e.g. r2026.08.2."),
) -> None:
    """Make a release the single active one, gated by the golden suite (LLD-REL-05/EVAL-03).

    Activation and the golden run share one transaction: the release is flipped active, all
    three layers (fact + retrieval + **e2e**, the last through the real orchestrator + runtime
    LLM) run against it, and any failure rolls the activation back (LLD-EVAL-03). An unreachable
    embedder degrades the retrieval layer to 'not evaluated', never a failure.
    """
    from agentkit.db.engine import connect
    from agentkit.eval.runner import format_report, run_eval
    from agentkit.ingest.sources import autodetect_client
    from agentkit.release.promote import activate
    from agentkit.runtime.llm import build_runtime_client

    client = autodetect_client()
    complete = build_runtime_client().complete_fn()
    with connect() as conn:
        trans = conn.begin()
        try:
            activate(conn, release_id)
            report = run_eval(conn, client, complete=complete)
        except Exception:
            trans.rollback()
            raise
        if not report.ok:
            trans.rollback()
            typer.echo(format_report(report))
            typer.echo(f"\nGolden suite FAILED — {release_id} NOT activated (LLD-EVAL-03).")
            raise typer.Exit(code=1)
        trans.commit()
    typer.echo(f"Activated {release_id}. Golden suite passed (fact + retrieval + e2e).")


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


@prompt_app.command("load")
def prompt_load_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: the single clients/* dir)."
    ),
) -> None:
    """Load the runtime prompt manifest into ops.prompt_version (new inactive versions).

    Also upserts ops.client from config.yaml + the active release. Activate a loaded version
    with `agentkit prompt activate <id>` (golden-gated).
    """
    from agentkit.db.engine import connect
    from agentkit.ingest.sources import autodetect_client
    from agentkit.runtime.prompts import load_prompts

    client = client or autodetect_client()
    with connect() as conn:
        with conn.begin():
            written = load_prompts(conn, client)
    typer.echo(f"Loaded {len(written)} prompt version(s) into ops.prompt_version (inactive):")
    for w in written:
        typer.echo(f"  {w['prompt_id']:36} agent={w['agent']} v{w['version']}")
    typer.echo("Activate each with:  agentkit prompt activate <prompt_id>")


@prompt_app.command("activate")
def prompt_activate_cmd(
    prompt_id: str = typer.Argument(..., help="Prompt version id, e.g. pv.jyotech.triage.1."),
    layers: str = typer.Option(
        "fact,retrieval,e2e", "--layers",
        help="Golden layers to gate on. Drop 'e2e' for a fast bring-up (no runtime LLM per turn).",
    ),
) -> None:
    """Activate a prompt version, gated by the golden suite (LLD-EVAL-03).

    The activation and the golden run share one transaction: any fact/retrieval/e2e failure
    rolls the activation back and the prompt stays inactive, exactly like `release activate`.
    The full e2e pass is slow (one live-LLM turn per question) — `--layers fact,retrieval`
    gates without it for a fast bring-up, after which `agentkit eval run` proves all three.
    """
    from sqlalchemy import text as _text

    from agentkit.db.engine import connect
    from agentkit.eval.runner import format_report
    from agentkit.runtime.prompts import activate_prompt_gated

    layer_tuple = tuple(s.strip() for s in layers.split(",") if s.strip())
    complete = None
    if "e2e" in layer_tuple:
        from agentkit.runtime.llm import build_runtime_client

        complete = build_runtime_client().complete_fn()
    with connect() as conn:
        trans = conn.begin()
        try:
            client = conn.execute(
                _text("SELECT client_id FROM ops.prompt_version WHERE prompt_id = :p"),
                {"p": prompt_id},
            ).scalar_one_or_none()
            if client is None:
                raise typer.BadParameter(f"unknown prompt_id {prompt_id!r}")
            activated, report = activate_prompt_gated(
                conn, prompt_id, client, complete=complete, layers=layer_tuple
            )
        except Exception:
            trans.rollback()
            raise
        if not activated:
            trans.rollback()
            typer.echo(format_report(report))
            typer.echo(f"\nGolden suite FAILED — {prompt_id} NOT activated (LLD-EVAL-03).")
            raise typer.Exit(code=1)
        trans.commit()
    typer.echo(f"Activated {prompt_id}. Golden suite passed ({'+'.join(layer_tuple)}).")


@app.command("chat")
def chat_cmd(
    client: Optional[str] = typer.Argument(
        None, help="Client id (default: the single clients/* dir)."
    ),
    session: Optional[str] = typer.Option(None, "--session", help="Resume/label a session uuid."),
    show_trace: bool = typer.Option(
        False, "--show-trace", help="After each turn print the ops rows written (data-model §4 style)."
    ),
) -> None:
    """Interactive chat against the runtime LLM (LLD-RT). Type /quit to end.

    Uses the self-hosted runtime LLM (LLM_BASE_URL) and the active release. Each turn is
    persisted to ops.*; --session resumes a prior conversation from those rows.
    """
    import uuid as _uuid

    from agentkit.client_config import gas_alias_map
    from agentkit.db.engine import connect
    from agentkit.ingest.sources import autodetect_client
    from agentkit.retrieval.chunk import resolve_active_release
    from agentkit.runtime.llm import build_runtime_client
    from agentkit.runtime.ops import create_session, rebuild_context, session_exists
    from agentkit.runtime.orchestrator import Ctx, run_turn

    client = client or autodetect_client()
    runtime_client = build_runtime_client()
    ctx_complete = runtime_client.complete_fn()

    with connect() as conn:
        # session lifecycle (all reads/writes inside a transaction so no autobegin lingers
        # before the next explicit begin).
        with conn.begin():
            release_id = resolve_active_release(conn)
            if session:
                sid = _uuid.UUID(session)
                if not session_exists(conn, sid):
                    create_session(conn, client, release_id, session_id=sid)
            else:
                sid = create_session(conn, client, release_id)

        ctx = Ctx(
            conn=conn,
            client=client,
            complete=ctx_complete,
            embed=None,  # live self-hosted embedder for search_documents
            gas_aliases=gas_alias_map(client),
        )
        typer.echo(f"session {sid}  release {release_id}  (type /quit to end)")
        while True:
            try:
                user = input("you › ").strip()
            except (EOFError, KeyboardInterrupt):
                typer.echo("")
                break
            if not user:
                continue
            if user.lower() in ("/quit", "/exit", "quit", "exit"):
                break
            with conn.begin():
                history = rebuild_context(conn, sid)
                result = run_turn(ctx, sid, user, history=history)
            for m in result.messages:
                if m.get("kind") == "document_card":
                    pl = m.get("payload") or {}
                    typer.echo(
                        f"bot › 📄 {pl.get('title')}  {pl.get('locator') or ''}  {pl.get('url') or ''}".rstrip()
                    )
                else:
                    typer.echo(f"bot › {m['text']}")
            for c in result.citations:
                loc = c.get("locator") or ""
                url = c.get("url") or ""
                typer.echo(f"     ↳ [{c['kind']}] {c['ref_id']} {loc} {url}".rstrip())
            if show_trace:
                _print_trace(result)


def _print_trace(result) -> None:
    """Print the ops rows written this turn, data-model §4 style."""
    typer.echo("  ── trace ──────────────────────────────────────────")
    tri = result.triage or {}
    typer.echo(
        f"  turn {result.turn_id}  outcome={result.outcome}  routed={result.route}"
    )
    typer.echo(
        f"  triage {{division:{tri.get('division')} intent:{tri.get('intent')} "
        f"language:{tri.get('language')} in_scope:{tri.get('in_scope')} "
        f"confidence:{tri.get('confidence')}}}"
    )
    for inv in result.invocations:
        typer.echo(f"  agent_invocation {inv['agent']}  prompt={inv['prompt_id']}")
        for tc in inv["tool_calls"]:
            typer.echo(f"    tool_call {tc['tool']} args={json.dumps(tc['args'], default=str)} rows={tc['rows_returned']}")
    if result.grounding:
        typer.echo(f"  grounding {json.dumps(result.grounding, default=str)}")
    for c in result.citations:
        typer.echo(f"  citation {c['kind']} {c['ref_id']} {c.get('locator') or ''}")
    typer.echo("  ───────────────────────────────────────────────────")


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
    client: Optional[str] = typer.Option(None, "--client", help="Client id (for gas aliases)."),
) -> None:
    """Match a gas duty against the published capability envelope (LLD-TOOL-01)."""
    from agentkit.client_config import gas_alias_map
    from agentkit.db.engine import connect
    from agentkit.ingest.sources import autodetect_client
    from agentkit.tools.match_capability import match_capability

    client = client or autodetect_client()
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
            gas_aliases=gas_alias_map(client),
        )
    typer.echo(json.dumps(result, indent=2))


@tools_app.command("search-documents")
def search_documents_cmd(
    query: str = typer.Argument(..., help="Free-text search query."),
    division: Optional[str] = typer.Option(None, "--division", help="Filter: industrial/fire_rescue/diving."),
    family: Optional[list[str]] = typer.Option(None, "--family", help="Filter: family id (repeatable)."),
    k: int = typer.Option(5, "--k", help="Number of chunks to return."),
) -> None:
    """Hybrid (vector ∪ FTS) chunk search over the active release (LLD-TOOL-04)."""
    from agentkit.db.engine import connect
    from agentkit.tools.search_documents import search_documents

    with connect() as conn:
        result = search_documents(
            conn, query, division=division, family_ids=list(family) if family else None, k=k
        )
    typer.echo(json.dumps(result, indent=2))


@tools_app.command("list-products")
def list_products_cmd(
    division: str = typer.Argument(..., help="Division: industrial/fire_rescue/diving."),
    category: Optional[str] = typer.Option(None, "--category", help="Optional category filter."),
) -> None:
    """List families + products in a division (LLD-TOOL-02)."""
    from agentkit.db.engine import connect
    from agentkit.tools.list_products import list_products

    with connect() as conn:
        result = list_products(conn, division, category=category)
    typer.echo(json.dumps(result, indent=2))


@tools_app.command("get-product")
def get_product_cmd(
    model_or_family: str = typer.Argument(..., help="Model number, family id or family name."),
) -> None:
    """Look up a product by model or family, with alias normalisation (LLD-TOOL-03)."""
    from agentkit.db.engine import connect
    from agentkit.tools.get_product import get_product

    with connect() as conn:
        result = get_product(conn, model_or_family)
    typer.echo(json.dumps(result, indent=2))


@tools_app.command("get-company-fact")
def get_company_fact_cmd(
    kind: str = typer.Argument(..., help="Fact kind, e.g. certification, founded, coverage."),
) -> None:
    """Return company facts of a kind (LLD-TOOL-05)."""
    from agentkit.db.engine import connect
    from agentkit.tools.get_company_fact import get_company_fact

    with connect() as conn:
        result = get_company_fact(conn, kind)
    typer.echo(json.dumps(result, indent=2))


@tools_app.command("get-office")
def get_office_cmd(
    city: Optional[str] = typer.Option(None, "--city", help="City name."),
    state: Optional[str] = typer.Option(None, "--state", help="State name."),
    region: Optional[str] = typer.Option(None, "--region", help="Region: North/South/East/West/Intl."),
) -> None:
    """Resolve an office by city/state/region, head-office fallback (LLD-TOOL-06)."""
    from agentkit.db.engine import connect
    from agentkit.tools.get_office import get_office

    with connect() as conn:
        result = get_office(conn, city=city, state=state, region=region)
    typer.echo(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    app()
