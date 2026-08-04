"""Person deep-dive: open-web research call + gated storage (#222)."""

from pathlib import Path

import pytest

from wingman.application.dossier_research import (
    build_dossier_prompt,
    research_person_dossier,
    save_person_dossier,
)
from wingman.application.ingest import IngestError
from wingman.domain.person import PersonOrigin
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse


class ScriptedProvider:
    """Returns canned free text; records the request it was given."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_request: ModelRequest | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.last_request = request
        return ModelResponse(
            text=self._text, provider="openrouter", model="scripted-1", latency_ms=0
        )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def test_research_calls_provider_with_name_in_prompt() -> None:
    provider = ScriptedProvider("Scott Brady is a founding partner at Innovation Endeavors.")
    response = research_person_dossier("Scott Brady", provider)
    assert response.text.startswith("Scott Brady is a founding partner")
    assert provider.last_request is not None
    assert "Scott Brady" in provider.last_request.prompt
    assert "Current Role" in provider.last_request.system


def test_research_rejects_blank_name() -> None:
    with pytest.raises(IngestError, match="name"):
        research_person_dossier("   ", ScriptedProvider("irrelevant"))


def test_build_dossier_prompt_asks_for_citations() -> None:
    prompt = build_dossier_prompt("Scott Brady")
    assert "Scott Brady" in prompt
    assert "Cite sources" in prompt


def test_save_creates_a_new_person_and_stores_the_dossier(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person = save_person_dossier(
            "Scott Brady",
            "Findings text.\n\nSources:\n- [Bio](https://example.com/bio)",
            storage,
            provider="openrouter",
            model="anthropic/claude-sonnet-5",
        )
        assert person.name == "Scott Brady"
        assert person.origin is PersonOrigin.MANUAL
        dossier = storage.get_person_dossier(person.person_id)
        assert dossier is not None
        assert "Findings text." in dossier.content
        assert "https://example.com/bio" in dossier.content
        assert dossier.provider == "openrouter"
        assert dossier.model == "anthropic/claude-sonnet-5"


def test_save_reuses_an_existing_person_by_name(workspace: Path) -> None:
    from wingman.application.people import add_person

    config = load_config()
    with Storage(config.db_path) as storage:
        existing, _ = add_person("Scott Brady", storage, company="Innovation Endeavors")
        person = save_person_dossier("Scott Brady", "Findings.", storage)
        assert person.person_id == existing.person_id
        assert person.company == "Innovation Endeavors"


def test_save_overwrites_a_prior_dossier_rather_than_versioning(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        save_person_dossier("Scott Brady", "First pass.", storage)
        person = save_person_dossier("Scott Brady", "Second, better pass.", storage)
        dossier = storage.get_person_dossier(person.person_id)
        assert dossier is not None
        assert dossier.content == "Second, better pass."


def test_save_rejects_blank_content(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="no dossier content"):
            save_person_dossier("Scott Brady", "   ", storage)


def test_delete_person_cascades_the_dossier(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person = save_person_dossier("Scott Brady", "Findings.", storage)
        assert storage.delete_person(person.person_id) is True
        assert storage.get_person_dossier(person.person_id) is None


def test_merge_person_moves_the_dossier_when_keeper_has_none(workspace: Path) -> None:
    from wingman.application.people import add_person

    config = load_config()
    with Storage(config.db_path) as storage:
        keep, _ = add_person("Scott B", storage)
        absorb = save_person_dossier("Scott Brady", "Findings.", storage)
        storage.merge_person(keep.person_id, absorb.person_id)
        moved = storage.get_person_dossier(keep.person_id)
        assert moved is not None
        assert moved.content == "Findings."


def test_merge_person_keeps_keepers_dossier_when_both_have_one(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        keep = save_person_dossier("Scott B", "Keeper's findings.", storage)
        absorb = save_person_dossier("Scott Brady", "Absorbed findings.", storage)
        storage.merge_person(keep.person_id, absorb.person_id)
        kept = storage.get_person_dossier(keep.person_id)
        assert kept is not None
        assert kept.content == "Keeper's findings."
