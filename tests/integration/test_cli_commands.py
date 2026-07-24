from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    return data_dir


def test_init_creates_workspace(workspace: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert str(workspace) in result.stdout
    assert (workspace / "inbox").is_dir()
    assert (workspace / "reports").is_dir()
    assert (workspace / "wingman.db").is_file()


def test_init_is_idempotent(workspace: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["init"]).exit_code == 0


def test_status_before_init(workspace: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "not initialized" in result.stdout
    assert "wingman init" in result.stdout


def test_status_after_init(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert str(workspace) in result.stdout
    assert "Source records: 0" in result.stdout


def test_doctor_before_init_fails_with_guidance(workspace: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "wingman init" in result.stdout


def test_doctor_after_init_passes(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "All checks passed." in result.stdout
    assert "[ok] database" in result.stdout


def test_doctor_deep_walks_one_step_at_a_time(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#137: each invocation of 'doctor --deep' shows exactly one step and
    advances the cursor once that step passes, rather than dumping the
    whole ladder."""
    from wingman.infrastructure import doctor_deep, portcheck

    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: 4242)
    monkeypatch.setattr(
        portcheck,
        "find_port_owner",
        lambda port, run=None: portcheck.PortOwner(
            pid=4242, command="wingman-mcp --http", user="dave"
        ),
    )

    result = runner.invoke(app, ["doctor", "--deep"])
    assert result.exit_code == 0
    assert "[1/5]" in result.stdout
    assert "[2/5]" not in result.stdout  # only one step per invocation
    state = (workspace / doctor_deep.STATE_FILENAME).read_text(encoding="utf-8")
    assert '"step": 2' in state


def test_doctor_deep_stops_and_does_not_advance_on_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #138 cross-account-squatting case: a failing step prints its
    diagnosis and a concrete next action, and does not silently move on."""
    from wingman.infrastructure import doctor_deep, portcheck

    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: None)
    monkeypatch.setattr(
        portcheck,
        "find_port_owner",
        lambda port, run=None: portcheck.PortOwner(pid=999, command=None, user=None),
    )

    result = runner.invoke(app, ["doctor", "--deep"])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout
    assert "next:" in result.stdout
    from wingman.infrastructure.config import load_config

    assert doctor_deep.read_cursor(load_config()) == 1


def test_doctor_deep_reset_returns_to_step_one(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import doctor_deep, portcheck

    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: 4242)
    monkeypatch.setattr(
        portcheck,
        "find_port_owner",
        lambda port, run=None: portcheck.PortOwner(
            pid=4242, command="wingman-mcp --http", user="dave"
        ),
    )
    runner.invoke(app, ["doctor", "--deep"])  # advances to step 2
    result = runner.invoke(app, ["doctor", "--deep", "--reset"])
    assert "[1/5]" in result.stdout


def test_init_writes_models_config(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    models = (workspace / "models.toml").read_text(encoding="utf-8")
    assert "[models.extract_fast]" in models


def test_ingest_before_init_fails_with_guidance(workspace: Path, tmp_path: Path) -> None:
    resume = tmp_path / "resume.md"
    resume.write_text("Skills: Python.\n", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(resume)])
    assert result.exit_code == 1
    assert "wingman init" in result.output


def test_ingest_with_recorded_provider(workspace: Path, tmp_path: Path) -> None:
    runner.invoke(app, ["init"])
    response = tmp_path / "response.json"
    response.write_text(
        '{"items": [{"kind": "skill", "name": "Python", "detail": "",'
        ' "classification": "fact", "confidence": 0.9, "quotes": ["Skills: Python."]}]}',
        encoding="utf-8",
    )
    (workspace / "models.toml").write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{response}"\n',
        encoding="utf-8",
    )
    resume = tmp_path / "resume.md"
    resume.write_text("# Jo\n\nSkills: Python.\n", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(resume)])
    assert result.exit_code == 0, result.output
    assert "Accepted: 1" in result.output
    assert (workspace / "reports" / "career.md").exists()
    status = runner.invoke(app, ["status"])
    assert "Profile items: 1" in status.stdout


def test_corpus_add_and_evidence_cli(workspace: Path, tmp_path: Path) -> None:
    runner.invoke(app, ["init"])
    essay = tmp_path / "essay.md"
    essay.write_text(
        "# Streaming migration\n\nWe moved billing to Apache Kafka streaming.\n",
        encoding="utf-8",
    )
    added = runner.invoke(app, ["corpus", "add", str(essay)])
    assert added.exit_code == 0, added.output
    assert "Added: 1" in added.output

    listing = runner.invoke(app, ["corpus", "list"])
    assert "Streaming migration" in listing.output

    found = runner.invoke(app, ["evidence", "kafka"])
    assert found.exit_code == 0, found.output
    assert "Streaming migration" in found.output
    assert "source: inbox/" in found.output

    status = runner.invoke(app, ["status"])
    assert "Corpus documents: 1" in status.stdout


def test_evidence_no_results(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["evidence", "flamingo"])
    assert result.exit_code == 0
    assert "No corpus evidence found" in result.output
