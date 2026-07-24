"""The people commands drive the watchlist end to end through the CLI."""

import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import wingman.application.people as people_module
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

runner = CliRunner()

RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <item>
      <title>On Kafka Migrations</title>
      <link>https://example.substack.com/p/on-kafka-migrations</link>
      <pubDate>Tue, 14 Jul 2026 12:00:00 GMT</pubDate>
      <content:encoded><![CDATA[<p>We moved billing to Apache Kafka.</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    return data_dir


def test_people_add_list_and_status(workspace: Path) -> None:
    result = runner.invoke(
        app, ["people", "add", "Jane Author", "--substack", "https://example.substack.com"]
    )
    assert result.exit_code == 0
    assert "Added Jane Author" in result.stdout

    listed = runner.invoke(app, ["people", "list"])
    assert listed.exit_code == 0
    assert "Jane Author" in listed.stdout
    assert "https://example.substack.com" in listed.stdout

    status = runner.invoke(app, ["status"])
    assert "People: 1" in status.stdout


def test_people_list_surfaces_linkedin_url(workspace: Path) -> None:
    """#80: a stored LinkedIn URL is a one-click-open link in the listing,
    on both the CLI and MCP surfaces."""
    result = runner.invoke(
        app,
        ["people", "add", "Jane Author", "--linkedin", "https://www.linkedin.com/in/janeauthor"],
    )
    assert result.exit_code == 0

    listed = runner.invoke(app, ["people", "list"])
    assert "https://www.linkedin.com/in/janeauthor" in listed.stdout

    from wingman.mcp_server import people_list as people_list_tool

    assert "https://www.linkedin.com/in/janeauthor" in people_list_tool()


def test_warm_path_commands_report_not_configured_without_woven_url(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#81: with WINGMAN_WOVEN_URL unset, both warm-path commands fail
    clearly rather than raising — the feature is inert, not broken."""
    from wingman.infrastructure.woven_client import ENV_WOVEN_URL

    monkeypatch.delenv(ENV_WOVEN_URL, raising=False)
    person = runner.invoke(app, ["people", "warm-path", "Target Person"])
    assert person.exit_code == 1
    assert "WINGMAN_WOVEN_URL" in person.output

    company = runner.invoke(app, ["company", "warm-path", "Acme"])
    assert company.exit_code == 1
    assert "WINGMAN_WOVEN_URL" in company.output


def test_import_connections_and_watched_filter(workspace: Path, tmp_path: Path) -> None:
    export = tmp_path / "linkedin.zip"
    raw = (
        "Notes:\n"
        '"Emails may be missing."\n'
        "\n"
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Mario,Rossi,https://linkedin.com/in/mario,m@x.com,GoSimple,CEO,01 Jan 2020\n"
    )
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("Connections.csv", raw)

    result = runner.invoke(app, ["people", "import-connections", str(export)])
    assert result.exit_code == 0
    assert "Created: 1" in result.stdout

    watched = runner.invoke(app, ["people", "list", "--watched"])
    assert "Mario Rossi" not in watched.stdout  # no feed configured yet


def test_people_fetch_and_evidence(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(
        app, ["people", "add", "Jane Author", "--substack", "https://example.substack.com"]
    )
    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)

    fetched = runner.invoke(app, ["people", "fetch", "Jane Author"])
    assert fetched.exit_code == 0
    assert "added: 1" in fetched.stdout
    assert "On Kafka Migrations" in fetched.stdout

    evidence = runner.invoke(app, ["people", "evidence", "kafka"])
    assert evidence.exit_code == 0
    assert "Jane Author — On Kafka Migrations" in evidence.stdout
    assert "https://example.substack.com/p/on-kafka-migrations" in evidence.stdout

    status = runner.invoke(app, ["status"])
    assert "External documents: 1" in status.stdout


def test_people_fetch_unknown_person_fails(workspace: Path) -> None:
    result = runner.invoke(app, ["people", "fetch", "Nobody Here"])
    assert result.exit_code == 1
    assert "No person named" in result.output


def test_people_fetch_all_without_feeds(workspace: Path) -> None:
    result = runner.invoke(app, ["people", "fetch", "--all"])
    assert result.exit_code == 0
    assert "Nothing was fetched" in result.stdout


def test_partial_names_resolve_and_ambiguity_gets_a_picker(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    runner.invoke(app, ["people", "add", "Marko Klopets", "--substack", "https://m.substack.com"])
    runner.invoke(app, ["people", "add", "Mark Otero", "--substack", "https://o.substack.com"])

    # unique partial resolves with a visible note
    result = runner.invoke(app, ["people", "fetch", "klopets"])
    assert result.exit_code == 0
    assert "→ Marko Klopets" in result.stdout
    assert "Marko Klopets:" in result.stdout

    # ambiguous partial becomes a numbered picker; choosing 2 fetches Marko
    result = runner.invoke(app, ["people", "fetch", "mark"], input="2\n")
    assert result.exit_code == 0
    assert "matches 2 people" in result.stdout
    assert "1. Mark Otero" in result.stdout and "2. Marko Klopets" in result.stdout
    assert "Marko Klopets:" in result.stdout

    # cancelling the picker (0) does nothing
    result = runner.invoke(app, ["people", "fetch", "mark"], input="0\n")
    assert result.exit_code == 1

    # no match still fails with guidance
    result = runner.invoke(app, ["people", "fetch", "nobody"])
    assert result.exit_code == 1
    assert "No person named" in result.output


def test_bare_fetch_offers_the_most_recent_person(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    runner.invoke(app, ["people", "add", "Jane Author", "--substack", "https://j.substack.com"])
    runner.invoke(app, ["people", "add", "No Sources Person"])

    # Jane is the most recent person WITH sources; declining keeps the old error
    result = runner.invoke(app, ["people", "fetch"], input="n\n")
    assert result.exit_code == 1
    assert "Fetch Jane Author" in result.output
    assert "Name a person or pass --all" in result.output

    # accepting fetches her
    result = runner.invoke(app, ["people", "fetch"], input="y\n")
    assert result.exit_code == 0
    assert "Jane Author:" in result.stdout


def test_people_docs_lists_stored_documents(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS_FEED)
    runner.invoke(app, ["people", "add", "Jane Author", "--substack", "https://j.substack.com"])
    result = runner.invoke(app, ["people", "docs", "Jane Author"])
    assert result.exit_code == 0
    assert "no stored documents" in result.stdout

    runner.invoke(app, ["people", "fetch", "Jane Author"])
    result = runner.invoke(app, ["people", "docs", "jane"])
    assert result.exit_code == 0
    assert "On Kafka Migrations" in result.stdout
    assert "2026-07-14" in result.stdout
    assert "1 documents." in result.stdout
