"""Regression tests for the external code review's findings (#62–#71)."""

import json
import logging
from pathlib import Path

import pytest

from wingman.application.people import add_person
from wingman.application.pov import company_card_id
from wingman.application.research import delete_company, rename_company
from wingman.domain.person import PersonOrigin
from wingman.domain.pov import PovCard, Stance, StanceDimension
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError, _require_public_host
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.telemetry import scrub_secrets, set_enabled


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    return tmp_path


def _company_card(key: str, name: str) -> PovCard:
    return PovCard(
        person_id=company_card_id(key),
        person_name=f"{name} (company)",
        stances=[
            Stance(
                statement=f"{name} ships small.",
                quote="We ship small.",
                doc_id="d1",
                doc_title="Ship Small",
                source_record_id="r1",
                dimension=StanceDimension.ATTITUDE,
            )
        ],
        topics=[],
        documents_used=1,
        provider="scripted",
        model="scripted-1",
        prompt_version="v2",
    )


def test_62_rename_with_pov_card_does_not_collide(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        storage.save_pov_card(_company_card("acme corp", "Acme Corp"))
        rename_company("Acme Corp", "Acme Inc", storage)  # crashed before the fix
        assert storage.get_pov_card(company_card_id("acme corp")) is None
        moved = storage.get_pov_card(company_card_id("acme inc"))
        assert moved is not None and moved.stances[0].statement == "Acme Corp ships small."
        # target already occupied: old card dropped, existing card kept
        storage.save_pov_card(_company_card("beta llc", "Beta LLC"))
        storage.save_pov_card(_company_card("gamma co", "Gamma Co"))
        assert storage.move_pov_card(company_card_id("beta llc"), company_card_id("gamma co"))
        kept = storage.get_pov_card(company_card_id("gamma co"))
        assert kept is not None and kept.person_name == "Gamma Co (company)"


def test_63_rename_moves_people_attribution(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Ana", storage, company="Acme Corp")
        add_person("Bo", storage, company="Elsewhere")
        _, people_moved = rename_company("Acme Corp", "Acme Inc", storage)
        assert people_moved == 1
        companies = {person.name: person.company for person in storage.list_people()}
        assert companies == {"Ana": "Acme Inc", "Bo": "Elsewhere"}
        # attribution follows: the dossier now works under the NEW name
        from wingman.application.dossier import build_company_dossier

        assert "Ana" in build_company_dossier("Acme Inc", config, storage).markdown


def test_64_delete_clears_people_and_reports_them(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Ana", storage, company="Acme Inc")
        removed, cleared = delete_company("Acme Inc", storage)
        assert removed and cleared == ["Ana"]
        person = storage.find_person_by_name_key("ana")
        assert person is not None and person.company is None


def test_65_merge_keeps_linkedin_connection_signal(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        manual, _ = add_person("Edmund Wong", storage, company="Acme")
        linked = manual.model_copy(
            update={
                "person_id": "li-1",
                "name": "Ed Wong",
                "name_key": "ed wong",
                "origin": PersonOrigin.LINKEDIN_CONNECTIONS,
                "connected_on": "2019-03-02",
                "company": None,
            }
        )
        storage.add_person(linked)
        merged = storage.merge_person(manual.person_id, "li-1")
        assert merged.origin is PersonOrigin.LINKEDIN_CONNECTIONS
        assert merged.connected_on == "2019-03-02"
        assert merged.company == "Acme"  # keep's own fields untouched


def test_66_provider_crash_is_failed_not_skipped(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.pipeline as pipeline_module
    from wingman.application.pipeline import make_it_so

    config = load_config()
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n'
        '[models.synthesize_balanced]\nprovider = "recorded"\npath = "/nonexistent"\n',
        encoding="utf-8",
    )

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(pipeline_module, "build_pov_card", boom)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage)
        report = make_it_so("Jane Author", config, storage, kind="person")
    statuses = {step.name: step.status for step in report.steps}
    assert statuses["pov"] == "failed"  # was "skipped" before the fix


def test_68_fts_reserved_terms_do_not_abort_search(workspace: Path) -> None:
    from wingman.application.corpus import add_to_corpus
    from wingman.application.search import search_workspace

    config = load_config()
    essay = workspace / "essay.md"
    essay.write_text("# Roles\n\nFront-end and C++ work, AND analytics.\n", encoding="utf-8")
    with Storage(config.db_path) as storage:
        add_to_corpus(essay, "writing", config, storage)
        for query in ("front-end", "C++", "AND", '"unbalanced', "co-founder OR ceo"):
            report = search_workspace(query, storage, config, limit=10)
            assert report.searched  # swept the stores instead of aborting
        hits = search_workspace("front-end", storage, config, limit=10).hits
        assert any(hit.kind == "corpus" for hit in hits)


def test_69_harvest_scrubs_secrets(workspace: Path, tmp_path: Path) -> None:
    from wingman.application.telemetry_harvest import harvest_transcript
    from wingman.infrastructure.telemetry import list_events

    config = load_config()
    set_enabled(config, True)
    transcript = tmp_path / "session.jsonl"
    lines = [
        {
            "type": "assistant",
            "timestamp": "2026-07-19T00:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {
                            "command": "wingman keys set voyage --value pa-live-abcdef1234567890"
                        },
                    },
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {
                            "command": "export ANTHROPIC_API_KEY=sk-ant-verysecret123 && wingman embed"
                        },
                    },
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-07-19T00:00:01Z",
            "message": {"role": "user", "content": "my key is sk-ant-api03-alsosecret"},
        },
    ]
    with transcript.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    harvest_transcript(transcript, config)
    dumped = json.dumps([event["payload"] for event in list_events(config)])
    assert "pa-live-abcdef1234567890" not in dumped
    assert "verysecret123" not in dumped
    assert "alsosecret" not in dumped
    assert "[redacted]" in dumped


def test_69_scrub_patterns_directly() -> None:
    assert scrub_secrets("--value sk-live-x") == "--value [redacted]"
    assert "sk-ant-abc123" not in scrub_secrets("token sk-ant-abc123 here")
    assert scrub_secrets("plain text stays") == "plain text stays"
    scrubbed = scrub_secrets('security add-generic-password -s x -a y -w "topsecret"')
    assert "topsecret" not in scrubbed


def test_70_http_transport_disables_access_log(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import main, server

    monkeypatch.setattr(server, "run", lambda *a, **k: None)
    logging.getLogger("uvicorn.access").disabled = False
    main(["--http", "--port", "9944"])
    assert logging.getLogger("uvicorn.access").disabled  # token never hits the access log


def test_71_private_hosts_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.infrastructure import fetch as fetch_module

    def fake_resolve(host: str, port: object) -> list:
        addresses = {
            "internal.corp": "10.1.2.3",
            "metadata.host": "169.254.169.254",
            "localhost": "127.0.0.1",
            "public.example.com": "93.184.216.34",
        }
        return [(2, 1, 6, "", (addresses[host], 0))]

    monkeypatch.setattr(fetch_module, "_resolve", fake_resolve)
    for bad in ("https://internal.corp/feed", "https://metadata.host/x", "https://localhost/f"):
        with pytest.raises(FetchError, match="non-public"):
            _require_public_host(bad)
    _require_public_host("https://public.example.com/feed")  # public passes

    def unresolvable(host: str, port: object) -> list:
        raise OSError("no such host")

    monkeypatch.setattr(fetch_module, "_resolve", unresolvable)
    with pytest.raises(FetchError, match="could not resolve"):
        _require_public_host("https://gone.example.com/feed")
