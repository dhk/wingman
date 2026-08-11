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
        "completeness",
        "artifacts",
        "setup_guide",
        "profile_html",
        "profile_manage",
        "company_feed",
        "answer_bank",
        "action_triage",
        "job_criteria",
        "qa_capture",
        "interview_react",
        "interview_status",
        "perspectives_start",
        "relationship_objective",
        "relationship_log",
        "heap",
        "commentary",
        "resolve_requirement",
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
        "people_deep_dive",
        "people_deep_dive_save",
        "people_dossier",
        "people_brief",
        "company_similar",
        "company_like",
        "company_dossier",
        "company_deep_dive",
        "company_deep_dive_save",
        "export_pdf",
        "my_pov",
        "my_values",
        "values_chart",
        "people_docs",
        "people_news",
        "make_it_so",
        "watchlist",
        "backup",
        "company_source",
        "company_research",
        "company_pov",
        "company_follow",
        "overnight",
        "people_manage",
        "company_manage",
        "search",
        "telemetry",
        "digest",
        "assess_job_url",
        "pack",
        "opportunities_list",
        "feature_request",
        "changelog",
        "woven_warm_path",
        "coach_persona",
        "carve_off_persona",
        "tenant_url",
        "tenant_urls",
        "drive_auth",
    }


def test_tools_report_uninitialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "nowhere"))
    assert "not initialized" in status()
    assert "not initialized" in evidence("anything")
    assert "not initialized" in career_profile()


def test_changelog_reports_curated_titles_without_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """changelog describes wingman itself, not the user's workspace (issue
    #145) — it should answer even before 'wingman init' has run."""
    from datetime import UTC, datetime

    import wingman.changelog_data as data_module
    from wingman.mcp_server import changelog

    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "nowhere"))
    today = datetime.now(UTC).date().isoformat()
    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (
            (today, 200, "Add a brand new feature"),
            (today, 199, "docs: session snapshot for resume"),
        ),
    )
    result = changelog()
    assert "1 new today, 1 in the last 7 days" in result
    assert "Add a brand new feature (https://github.com/dhk/wingman/pull/200)" in result
    assert "session snapshot" not in result


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
    # issue #175: building the card also refreshes a web-viewable export —
    # no separate export_pdf call needed to browse it in the web UI.
    export_dir = config.reports_dir / "pdf"
    exported = list(export_dir.glob("jane-author-*.html"))
    assert len(exported) == 1
    assert "Jane Author" in exported[0].read_text(encoding="utf-8")
    written_at = exported[0].stat().st_mtime
    # second call serves the stored card without a model call, and does NOT
    # re-trigger the export (nothing new was built)
    stored = people_pov("Jane Author")
    assert "stored card" in stored
    assert exported[0].stat().st_mtime == written_at


def test_people_brief_via_mcp_auto_exports_person(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """issue #175: building a brief refreshes the person's web-viewable export
    too, so it shows up in the web UI without a separate export_pdf call."""
    import json

    import wingman.application.people as people_module
    from wingman.application.corpus import add_to_corpus
    from wingman.infrastructure.storage import Storage
    from wingman.mcp_server import people_add, people_brief, people_fetch, people_pov

    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    people_add("Jane Author", substack_url="https://jane.substack.com")
    people_fetch("Jane Author")

    config = load_config()
    with Storage(config.db_path) as storage:
        person = storage.find_person_by_name_key("jane author")
        assert person is not None
        doc_id = storage.list_external_documents(person.person_id)[0].doc_id

    pov_response = tmp_path / "pov-response.json"
    pov_response.write_text(
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
        f'[models.synthesize_balanced]\nprovider = "recorded"\npath = "{pov_response}"\n',
        encoding="utf-8",
    )
    people_pov("Jane Author")

    essay = tmp_path / "essay.md"
    essay.write_text("Streaming pipelines beat batch jobs.", encoding="utf-8")
    with Storage(config.db_path) as storage:
        add_to_corpus(essay, "writing", config, storage)
        corpus_id = storage.list_corpus_documents()[0].doc_id

    brief_response = tmp_path / "brief-response.json"
    brief_response.write_text(
        json.dumps(
            {
                "talking_points": [
                    {
                        "point": "You both bet on streaming.",
                        "their_stance": "Believes streaming infrastructure is foundational.",
                        "corpus_doc_id": corpus_id,
                        "your_quote": "Streaming pipelines beat batch jobs.",
                    }
                ],
                "intro_points": ["Ask about their Kafka rollout"],
            }
        ),
        encoding="utf-8",
    )
    config.models_config_path.write_text(
        f'[models.synthesize_balanced]\nprovider = "recorded"\npath = "{brief_response}"\n',
        encoding="utf-8",
    )
    brief = people_brief("Jane Author")
    assert "You both bet on streaming." in brief

    export_dir = config.reports_dir / "pdf"
    exported = list(export_dir.glob("jane-author-*.html"))
    assert len(exported) == 1  # same day's export refreshed in place, not duplicated
    text = exported[0].read_text(encoding="utf-8")
    assert "they argue" in text and "you wrote" in text


def test_people_deep_dive_via_mcp_with_recorded_provider(workspace: Path, tmp_path: Path) -> None:
    from wingman.mcp_server import people_deep_dive, people_deep_dive_save, people_dossier

    # confirmed=False: no provider is configured at all, so a network call
    # here would raise — proving the preview path never reaches the provider.
    preview = people_deep_dive("Scott Brady")
    assert "Ask the user to confirm" in preview
    assert "confirmed=true" in preview

    response_path = tmp_path / "dossier-response.txt"
    response_path.write_text(
        "Scott Brady is a founding partner at Innovation Endeavors.\n\n"
        "Sources:\n- [Bio](https://example.com/bio)",
        encoding="utf-8",
    )
    config = load_config()
    config.models_config_path.write_text(
        f'[models.research_websearch]\nprovider = "recorded"\npath = "{response_path}"\n',
        encoding="utf-8",
    )

    found = people_deep_dive("Scott Brady", confirmed=True)
    assert "Scott Brady is a founding partner" in found
    assert "https://example.com/bio" in found
    assert "Not stored" in found
    # people_deep_dive never touches storage — no one named Scott Brady exists yet.
    assert "No person named" in people_dossier("Scott Brady")

    saved = people_deep_dive_save(
        "Scott Brady",
        "Scott Brady is a founding partner at Innovation Endeavors.\n\n"
        "Sources:\n- [Bio](https://example.com/bio)",
    )
    assert "Stored deep-dive for Scott Brady" in saved
    stored = people_dossier("Scott Brady")
    assert "https://example.com/bio" in stored


def test_company_deep_dive_via_mcp_with_recorded_provider(workspace: Path, tmp_path: Path) -> None:
    """#350's contract, tool by tool: the preview costs nothing, the paid
    call stores nothing, and only the separate save tool writes — and it
    writes only findings whose source the search actually returned."""
    from wingman.mcp_server import company_deep_dive, company_deep_dive_save, company_dossier

    # confirmed=False: no provider is configured at all, so a network call
    # here would raise — proving the preview path never reaches the provider.
    preview = company_deep_dive("Acme Corp")
    assert "Ask the user to confirm" in preview
    assert "confirmed=true" in preview
    assert "$0.04" in preview

    response_path = tmp_path / "company-response.json"
    response_path.write_text(
        '{"findings": [\n'
        '  {"dimension": "values", "claim": "Acme says safety comes before speed.",\n'
        '   "quote": "we will delay a launch rather than ship an unsafe product",\n'
        '   "source_url": "https://acme.example/values", "source_title": "Our values"},\n'
        '  {"dimension": "culture", "claim": "Acme has a four-day week.",\n'
        '   "source_url": "https://invented.example/four-day-week"}\n'
        "]}\n\nSources:\n- [Our values](https://acme.example/values)",
        encoding="utf-8",
    )
    config = load_config()
    config.models_config_path.write_text(
        f'[models.research_websearch]\nprovider = "recorded"\npath = "{response_path}"\n',
        encoding="utf-8",
    )

    found = company_deep_dive("Acme Corp", confirmed=True)
    assert "Acme says safety comes before speed." in found
    assert "https://acme.example/values" in found
    assert "not among the pages the search returned" in found  # the invented one
    assert "Not stored" in found
    # Nothing was written: the dossier still reports the deep-dive gap.
    assert "no open-web deep dive" in company_dossier("Acme Corp")

    saved = company_deep_dive_save("Acme Corp", found)
    assert "Stored deep-dive for Acme Corp (1 sourced findings)." in saved
    stored = company_dossier("Acme Corp")
    assert "## Open-web deep dive" in stored
    assert "https://acme.example/values" in stored
    assert "four-day week" not in stored


def test_company_deep_dive_save_refuses_unsourced_content(workspace: Path) -> None:
    """The storage tool is reachable directly, so it re-checks rather than
    trusting that a preview produced its input."""
    from wingman.mcp_server import company_deep_dive_save, company_dossier

    refused = company_deep_dive_save(
        "Acme Corp",
        "## Stated values\n\n- claim: Acme is the best company in the world.\n",
    )
    assert "verifiable source" in refused
    assert "no open-web deep dive" in company_dossier("Acme Corp")


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


def test_partial_names_resolve_with_did_you_mean(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.people as people_module
    from wingman.mcp_server import people_add, people_fetch

    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    people_add("Marko Klopets", substack_url="https://m.substack.com")
    people_add("Mark Otero", substack_url="https://o.substack.com")

    # unique partial resolves silently
    assert "Marko Klopets:" in people_fetch("klopets")
    # ambiguity lists candidates instead of guessing
    ambiguous = people_fetch("mark")
    assert "matches several people" in ambiguous
    assert "Mark Otero" in ambiguous and "Marko Klopets" in ambiguous


def test_profile_html_measures_its_cap_in_bytes_not_characters(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit is denominated in bytes and bytes are what cross the wire.

    A page of accented names, curly quotes or CJK runs well past its own
    character count once encoded, so a character-based test waves through
    exactly the documents most likely to be oversized. Here the page is
    comfortably under the cap by characters and over it by bytes.
    """
    from wingman import mcp_server, webui

    page = "é" * 100  # 100 characters, 200 bytes
    monkeypatch.setattr(webui, "render_profile_html", lambda config: page)
    monkeypatch.setattr(mcp_server, "_PROFILE_HTML_MAX_BYTES", 150)

    result = mcp_server.profile_html()

    assert result != page
    assert "too large" in result
    assert "browser" in result
