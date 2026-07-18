"""The MCP tools are thin wrappers over the application layer — call them directly."""

import asyncio
from pathlib import Path

import pytest

from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.mcp_server import (
    assess_job,
    career_profile,
    evidence,
    ingest_resume_text,
    server,
    status,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    from wingman.infrastructure.storage import Storage

    Storage(config.db_path).close()
    return data_dir


def test_all_tools_are_registered() -> None:
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    assert tools == {
        "status",
        "evidence",
        "career_profile",
        "assess_job",
        "ingest_resume_text",
        "people_add",
        "people_list",
        "people_fetch",
        "sync",
        "embed",
        "people_evidence",
        "people_similar",
        "people_like",
        "people_discover",
        "feed_discover",
        "feed_attach",
        "people_import_connections",
        "ingest_resume_url",
        "people_pov",
        "people_brief",
        "company_similar",
        "company_like",
        "company_dossier",
    }


def test_tools_report_uninitialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "nowhere"))
    assert "not initialized" in status()
    assert "not initialized" in evidence("anything")
    assert "not initialized" in career_profile()


def test_status_and_evidence_roundtrip(workspace: Path, tmp_path: Path) -> None:
    from wingman.application.corpus import add_to_corpus
    from wingman.infrastructure.storage import Storage

    essay = tmp_path / "essay.md"
    essay.write_text("# Streaming\n\nWe moved billing to Apache Kafka.\n", encoding="utf-8")
    config = load_config()
    with Storage(config.db_path) as storage:
        add_to_corpus(essay, "writing", config, storage)

    assert "Corpus documents: 1" in status()
    found = evidence("kafka")
    assert "Streaming" in found
    assert "source: inbox/" in found
    assert "No corpus evidence found" in evidence("flamingo")
    assert "Search failed" in evidence('"unbalanced')


def test_ingest_and_profile_via_recorded_provider(workspace: Path) -> None:
    config = load_config()
    response = FIXTURES / "profile_extraction" / "case_001_basic" / "response.json"
    config.models_config_path.write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{response}"\n',
        encoding="utf-8",
    )
    resume = (FIXTURES / "profile_extraction" / "case_001_basic" / "resume.md").read_text(
        encoding="utf-8"
    )
    result = ingest_resume_text(resume)
    assert "Accepted: 4" in result

    profile = career_profile()
    assert "# Career Profile" in profile
    assert "Apache Kafka" in profile


def test_assess_requires_profile_and_fails_visibly(workspace: Path) -> None:
    config = load_config()
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider = "recorded"\npath = "/nonexistent"\n',
        encoding="utf-8",
    )
    result = assess_job("# Some Role\n\n- Requirement one.\n")
    assert result.startswith("Assessment failed:")


def test_empty_inputs_do_nothing(workspace: Path) -> None:
    assert "nothing was assessed" in assess_job("   ")
    assert "nothing was ingested" in ingest_resume_text("   ")


def test_filename_traversal_is_neutralized(workspace: Path) -> None:
    config = load_config()
    response = FIXTURES / "profile_extraction" / "case_001_basic" / "response.json"
    config.models_config_path.write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{response}"\n',
        encoding="utf-8",
    )
    resume = (FIXTURES / "profile_extraction" / "case_001_basic" / "resume.md").read_text(
        encoding="utf-8"
    )
    ingest_resume_text(resume, filename="../../../escape.md")
    # nothing escaped the inbox: the write landed inside it, basename only
    assert not (config.data_dir.parent / "escape.md").exists()
    inbox_names = [p.name for p in config.inbox_dir.iterdir()]
    assert any(name.endswith("-escape.md") for name in inbox_names)
    assert all("/" not in name for name in inbox_names)


RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>On Kafka</title>
    <link>https://jane.substack.com/p/on-kafka</link>
    <content:encoded><![CDATA[<p>Kafka streaming pipelines everywhere.</p>]]></content:encoded>
  </item></channel></rss>
"""


def test_people_watchlist_flow_via_mcp(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parity flow: add → fetch → evidence → sync → similar, all through MCP tools."""
    import wingman.application.people as people_module
    from wingman.mcp_server import (
        people_add,
        people_evidence,
        people_fetch,
        people_list,
        people_similar,
        sync,
    )

    config = load_config()
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)

    assert "Added Jane Author" in people_add(
        "Jane Author", substack_url="https://jane.substack.com"
    )
    assert "Jane Author" in people_list(watched_only=True)
    fetched = people_fetch("Jane Author")
    assert "added: 1" in fetched
    found = people_evidence("kafka")
    assert "Jane Author — On Kafka" in found
    synced = sync()
    assert "Embedded" in synced and "hashed" in synced
    # only one person has writing, so similar-to-them yields nobody else
    assert "No other people" in people_similar("Jane Author")


def test_feed_attach_is_a_two_step_confirmation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.people as people_module
    from wingman.mcp_server import feed_attach, feed_discover, people_add, people_list

    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    people_add("Marko Klopets")
    discovery = feed_discover("Marko Klopets", "https://medium.com/feed/@marko")
    # discovery reports but never attaches, and tells the model to get consent
    assert "Found feed" in discovery
    assert "explicit yes" in discovery or "confirm" in discovery
    assert "medium.com" not in people_list()

    # a trailing slash is normalized away, and the message echoes what was stored
    attached = feed_attach("Marko Klopets", "https://medium.com/feed/@marko/")
    assert "Attached rss source" in attached
    assert "https://medium.com/feed/@marko" in attached
    assert "@marko/" not in attached
    assert "medium.com" in people_list()
    # invalid kind is rejected
    assert "kind must be" in feed_attach("Marko Klopets", "https://x.example.com", kind="weird")


def test_people_pov_via_mcp_with_recorded_provider(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    import wingman.application.people as people_module
    from wingman.infrastructure.storage import Storage
    from wingman.mcp_server import people_add, people_fetch, people_pov

    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    people_add("Jane Author", substack_url="https://jane.substack.com")
    people_fetch("Jane Author")

    config = load_config()
    with Storage(config.db_path) as storage:
        person = storage.find_person_by_name_key("jane author")
        assert person is not None
        doc_id = storage.list_external_documents(person.person_id)[0].doc_id
    response_path = tmp_path / "pov-response.json"
    response_path.write_text(
        json.dumps(
            {
                "stances": [
                    {
                        "statement": "Believes streaming infrastructure is foundational.",
                        "quote": "Kafka streaming pipelines everywhere",
                        "doc_id": doc_id,
                    }
                ],
                "topics": ["streaming"],
            }
        ),
        encoding="utf-8",
    )
    config.models_config_path.write_text(
        f'[models.synthesize_balanced]\nprovider = "recorded"\npath = "{response_path}"\n',
        encoding="utf-8",
    )
    card = people_pov("Jane Author")
    assert "POV card: Jane Author" in card
    assert "streaming pipelines everywhere" in card
    # second call serves the stored card without a model call
    stored = people_pov("Jane Author")
    assert "stored card" in stored


def test_new_tools_report_uninitialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import people_add, people_discover, sync

    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "nowhere"))
    assert "not initialized" in people_add("Anyone")
    assert "not initialized" in sync()
    assert "not initialized" in people_discover()


def test_evidence_limit_is_clamped(workspace: Path, tmp_path: Path) -> None:
    from wingman.application.corpus import add_to_corpus
    from wingman.infrastructure.storage import Storage

    essay = tmp_path / "essay.md"
    essay.write_text("# Streaming\n\nApache Kafka everywhere.\n", encoding="utf-8")
    config = load_config()
    with Storage(config.db_path) as storage:
        add_to_corpus(essay, "writing", config, storage)
    # a negative limit must not mean "unlimited" — it clamps to a sane floor
    assert "Streaming" in evidence("kafka", limit=-1)
