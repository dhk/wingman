from typer.testing import CliRunner

from wingman.cli.main import app

runner = CliRunner()


def test_status_command() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Phase 0 scaffold" in result.stdout
