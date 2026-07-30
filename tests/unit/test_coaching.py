"""Coaching mode (docs/COACHING-MODE-DESIGN.md): find-or-create/resolve/
render for the active persona, plus the coach_persona MCP tool and
'wingman coach-persona' CLI command that drive it."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.coaching import (
    clear_active_persona_and_report,
    find_or_create_persona,
    get_active_persona,
    render_acting_as,
    resolve_persona,
    set_active_persona,
)
from wingman.application.ingest import IngestError
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def test_find_or_create_persona_matches_case_and_whitespace_insensitively(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        first = find_or_create_persona("Mike Chen", storage)
        again = find_or_create_persona("  mike   chen ", storage)
        assert first.persona_id == again.persona_id
        assert len(storage.list_personas()) == 1


def test_find_or_create_persona_rejects_blank_name(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="needs a name"):
            find_or_create_persona("   ", storage)


def test_set_get_clear_active_persona(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        assert get_active_persona(storage, workspace) is None
        persona = set_active_persona("Mike Chen", storage, workspace)
        assert get_active_persona(storage, workspace) == persona
        clear_active_persona_and_report(workspace)
        assert get_active_persona(storage, workspace) is None


def test_get_active_persona_treats_dangling_pointer_as_unset(workspace) -> None:
    from wingman.infrastructure.coach_state import write_active_persona_id

    with Storage(workspace.db_path) as storage:
        write_active_persona_id(workspace, "no-such-persona-id")
        assert get_active_persona(storage, workspace) is None


def test_resolve_persona_prefers_override_without_disturbing_active(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        set_active_persona("Mike Chen", storage, workspace)
        resolved = resolve_persona("Priya Nair", storage, workspace)
        assert resolved is not None and resolved.name == "Priya Nair"
        # the one-off override never became the active pointer
        assert get_active_persona(storage, workspace).name == "Mike Chen"


def test_resolve_persona_falls_back_to_active_then_none(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        assert resolve_persona("", storage, workspace) is None
        set_active_persona("Mike Chen", storage, workspace)
        assert resolve_persona("", storage, workspace).name == "Mike Chen"


def test_render_acting_as() -> None:
    from wingman.domain.persona import Persona

    assert render_acting_as(None) == "Acting as: yourself."
    assert render_acting_as(Persona(name="Mike Chen")) == "Acting as: coach for Mike Chen."


def test_mcp_coach_persona_set_who_list_clear(workspace) -> None:
    from wingman.mcp_server import coach_persona

    assert coach_persona("set", "") == "coach_persona 'set' needs a name — nothing changed."

    result = coach_persona("set", "Mike Chen")
    assert "Acting as: coach for Mike Chen." in result
    assert "Everything from here scopes to them." in result

    assert coach_persona("who") == "Acting as: coach for Mike Chen."

    listing = coach_persona("list")
    assert "Mike Chen" in listing

    assert coach_persona("clear") == "Acting as: yourself."
    assert coach_persona("who") == "Acting as: yourself."


def test_mcp_coach_persona_list_when_empty(workspace) -> None:
    from wingman.mcp_server import coach_persona

    assert "No personas yet" in coach_persona("list")


def test_mcp_coach_persona_unknown_action(workspace) -> None:
    from wingman.mcp_server import coach_persona

    result = coach_persona("dance")
    assert "unknown action" in result


def test_cli_coach_persona_set_who_list_clear(workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(workspace.data_dir))

    result = runner.invoke(app, ["coach-persona", "set", "Mike Chen"])
    assert result.exit_code == 0
    assert "Acting as: coach for Mike Chen." in result.stdout

    result = runner.invoke(app, ["coach-persona", "who"])
    assert result.exit_code == 0
    assert "Acting as: coach for Mike Chen." in result.stdout

    result = runner.invoke(app, ["coach-persona", "list"])
    assert result.exit_code == 0
    assert "Mike Chen" in result.stdout

    result = runner.invoke(app, ["coach-persona", "clear"])
    assert result.exit_code == 0
    assert "Acting as: yourself." in result.stdout

    result = runner.invoke(app, ["coach-persona", "who"])
    assert "Acting as: yourself." in result.stdout


def test_cli_coach_persona_set_without_name_fails(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(workspace.data_dir))
    result = runner.invoke(app, ["coach-persona", "set"])
    assert result.exit_code != 0
    assert "needs a name" in result.output


def test_cli_coach_persona_unknown_action_fails(workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(workspace.data_dir))
    result = runner.invoke(app, ["coach-persona", "dance"])
    assert result.exit_code == 1
    assert "unknown action" in result.output


def test_cli_coach_persona_requires_initialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "uninitialized"))
    result = runner.invoke(app, ["coach-persona", "who"])
    assert result.exit_code == 1
    assert "not initialized" in result.output
