"""Smoke test for the agentkit CLI wiring (scaffolding, not a feature test)."""

from typer.testing import CliRunner

from agentkit.cli import app

runner = CliRunner()


def test_root_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "db" in result.output


def test_db_help_lists_commands() -> None:
    result = runner.invoke(app, ["db", "--help"])
    assert result.exit_code == 0
    for cmd in ("upgrade", "downgrade", "current"):
        assert cmd in result.output
