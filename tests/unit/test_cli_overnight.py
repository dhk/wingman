"""'wingman overnight' exit semantics.

A per-target step failure — a feed that 404s, a news query that comes back
empty, an export that cannot write — is data the run is designed to collect
and report, not a reason for the run itself to have failed. Conflating the
two is what put the systemd unit into 'failed' on 2026-08-05: nine targets
processed, a digest written, every export on disk, and
'status=1/FAILURE' in the journal because seven targets had a bad step.
'wingman tenant overnight' already draws this line ("one tenant's failure
... never blocks the rest"); the single-workspace command did not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.focus import OvernightReport, OvernightTarget
from wingman.cli import main as cli_main
from wingman.cli.main import app
from wingman.infrastructure.storage import Storage

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = cli_main.load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    config.models_config_path.write_text('[models.embed_semantic]\nprovider = "hashed"\n')
    return tmp_path


def _partly_failed_report(digest: Path) -> OvernightReport:
    """The 2026-08-05 shape, shrunk: some targets clean, most with a bad step."""
    return OvernightReport(
        targets=[
            OvernightTarget(name="Supersimple", kind="company", status="ok"),
            OvernightTarget(name="OpenAI", kind="company", status="failed"),
            OvernightTarget(name="Peter Hazlehurst", kind="person", status="failed"),
        ],
        processed=3,
        failed=2,
        actions=[],
        digest_path=str(digest),
    )


def test_overnight_completes_despite_failed_targets(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run produced a digest, so it succeeded. Failed targets are reported,
    not promoted into a process-level failure."""
    digest = workspace / "digest-2026-08-05.md"
    digest.write_text("# digest", encoding="utf-8")
    monkeypatch.setattr(
        cli_main,
        "overnight_run",
        lambda config, storage, out_dir=None: _partly_failed_report(digest),
    )

    result = runner.invoke(app, ["overnight", "--no-drive"])

    assert result.exit_code == 0, result.output
    # Succeeding quietly would be the opposite error: the failures still have
    # to be legible to whoever reads the journal in the morning.
    assert "✗ OpenAI (company)" in result.output
    assert "3 targets, 2 failed" in result.output


def test_overnight_strict_still_exits_nonzero(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--strict keeps the old behaviour for anyone who wants a target failure to
    fail the whole invocation."""
    digest = workspace / "digest-2026-08-05.md"
    digest.write_text("# digest", encoding="utf-8")
    monkeypatch.setattr(
        cli_main,
        "overnight_run",
        lambda config, storage, out_dir=None: _partly_failed_report(digest),
    )

    result = runner.invoke(app, ["overnight", "--no-drive", "--strict"])

    assert result.exit_code == 1, result.output
    assert "3 targets, 2 failed" in result.output


def test_overnight_still_fails_when_the_run_itself_cannot_complete(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinction being drawn: no digest means the run failed, and that is
    still a non-zero exit even without --strict."""

    def explode(config: object, storage: object, out_dir: object = None) -> OvernightReport:
        raise cli_main.IngestError("workspace database is locked")

    monkeypatch.setattr(cli_main, "overnight_run", explode)

    result = runner.invoke(app, ["overnight", "--no-drive"])

    assert result.exit_code == 1
    assert "overnight failed" in result.output
