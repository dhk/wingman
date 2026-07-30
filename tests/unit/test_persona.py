"""Coaching mode (docs/COACHING-MODE-DESIGN.md): the Persona domain type,
its storage CRUD, and the persisted active-persona pointer file."""

from pathlib import Path

import pytest

from wingman.domain.persona import Persona
from wingman.infrastructure.coach_state import (
    STATE_FILENAME,
    clear_active_persona,
    read_active_persona_id,
    write_active_persona_id,
)
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import DuplicateRecordError, Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def test_persona_name_key_is_case_and_whitespace_insensitive() -> None:
    assert Persona(name="Mike").name_key == "mike"
    assert Persona(name="  mike  Chen ").name_key == "mike chen"
    assert Persona(name="Mike").name_key == Persona(name="  MIKE ").name_key


def test_add_get_find_list_persona(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        persona = Persona(name="Mike Chen", notes="Directs eng at a startup.")
        storage.add_persona(persona)

        assert storage.get_persona(persona.persona_id) == persona
        assert storage.get_persona("no-such-id") is None

        found = storage.find_persona_by_name("  mike CHEN ")
        assert found == persona
        assert storage.find_persona_by_name("Someone Else") is None

        second = Persona(name="Priya Nair")
        storage.add_persona(second)
        listed = storage.list_personas()
        assert [p.name for p in listed] == ["Mike Chen", "Priya Nair"]


def test_add_persona_rejects_duplicate_name(workspace) -> None:
    with Storage(workspace.db_path) as storage:
        storage.add_persona(Persona(name="Mike Chen"))
        with pytest.raises(DuplicateRecordError):
            storage.add_persona(Persona(name="  MIKE   chen  "))


def test_coach_state_round_trips_and_defaults_to_none(workspace) -> None:
    assert read_active_persona_id(workspace) is None
    write_active_persona_id(workspace, "persona-123")
    assert read_active_persona_id(workspace) == "persona-123"
    # overwriting replaces, not appends
    write_active_persona_id(workspace, "persona-456")
    assert read_active_persona_id(workspace) == "persona-456"
    clear_active_persona(workspace)
    assert read_active_persona_id(workspace) is None
    # clearing when nothing is set is a no-op, not an error
    clear_active_persona(workspace)
    assert read_active_persona_id(workspace) is None


def test_coach_state_survives_garbage_file(workspace) -> None:
    path = workspace.data_dir / STATE_FILENAME
    path.write_text("not json at all", encoding="utf-8")
    assert read_active_persona_id(workspace) is None
