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
