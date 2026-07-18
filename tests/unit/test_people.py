"""People: watchlist, connections seeding, and feed ingestion are deterministic."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.people import (
    add_person,
    fetch_person_feed,
    find_people_evidence,
    seed_from_connections,
)
from wingman.domain.person import PersonOrigin
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage

RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example Letters</title>
    <item>
      <title>On Kafka Migrations</title>
      <link>https://example.substack.com/p/on-kafka-migrations</link>
      <pubDate>Tue, 14 Jul 2026 12:00:00 GMT</pubDate>
      <content:encoded><![CDATA[<p>We moved billing to Apache Kafka and lived.</p>]]></content:encoded>
    </item>
    <item>
      <title>Short Note</title>
      <link>https://example.substack.com/p/short-note</link>
      <pubDate>Wed, 15 Jul 2026 12:00:00 GMT</pubDate>
      <description>&lt;p&gt;A note about developer tools.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Empty Post</title>
      <link>https://example.substack.com/p/empty</link>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def connections_zip(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / "linkedin-export.zip"
    raw = (
        "Notes:\n"
        '"When exporting your connection data, you may be missing emails."\n'
        "\n"
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n" + rows
    )
    with zipfile.ZipFile(path, "w") as archive:
        # nested entry with a BOM, the way real exports arrive
        archive.writestr("Export/Connections.csv", b"\xef\xbb\xbf" + raw.encode("utf-8"))
    return path


def test_add_person_then_update(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, created = add_person("Jane Author", storage)
        assert created and person.origin == PersonOrigin.MANUAL
        updated, created_again = add_person(
            "jane  author", storage, substack_url="https://example.substack.com/"
        )
        assert not created_again
        assert updated.person_id == person.person_id
        assert updated.substack_url == "https://example.substack.com"
        assert storage.count_people() == 1


def test_add_person_rejects_non_https_feed(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="https://"):
            add_person("Jane", storage, substack_url="http://example.substack.com")


def test_seed_from_connections_keeps_names_never_emails(workspace: Path, tmp_path: Path) -> None:
    config = load_config()
    export = connections_zip(
        tmp_path,
        "Mario,Rossi,https://www.linkedin.com/in/mario,mario@example.com,GoSimple,CEO,01 Jan 2020\n"
        "Brian,Chen,https://www.linkedin.com/in/brian,,Hex,Founder,02 Feb 2021\n"
        ",,,,Ghost Co,Nobody,03 Mar 2022\n",
    )
    with Storage(config.db_path) as storage:
        report = seed_from_connections(export, storage)
        assert report.created == 2
        assert report.skipped_incomplete == 1
        mario = storage.find_person_by_name_key("mario rossi")
        assert mario is not None
        assert mario.origin == PersonOrigin.LINKEDIN_CONNECTIONS
        assert mario.company == "GoSimple"
        assert mario.source_record_id is not None
        # PII minimization: the email never lands anywhere in the workspace
        assert "mario@example.com" not in mario.model_dump_json()
        record = storage.get_source_record(mario.source_record_id)
        assert record is not None and record.source_type == "linkedin_connections"
        # the locator names the actual (nested) entry that was consumed, and
        # the hash is of the exact bytes in the archive, BOM included
        assert record.source_locator.endswith("!Export/Connections.csv")
        with zipfile.ZipFile(export) as archive:
            raw_bytes = archive.read("Export/Connections.csv")
        assert record.content_hash == hashlib.sha256(raw_bytes).hexdigest()
        # the CSV bytes stay in the export zip, not the inbox
        assert list(config.inbox_dir.iterdir()) == []

        again = seed_from_connections(export, storage)
        assert again.created == 0
        assert again.skipped_existing == 2


def test_seed_without_connections_csv_fails_visibly(workspace: Path, tmp_path: Path) -> None:
    config = load_config()
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Positions.csv", "Company Name,Title\n")
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="Connections.csv"):
            seed_from_connections(path, storage)


def test_fetch_person_feed_indexes_new_posts(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Jane Author", storage, substack_url="https://example.substack.com")
        fetched: list[str] = []

        def fake_fetch(url: str) -> bytes:
            fetched.append(url)
            return RSS_FEED

        report = fetch_person_feed(person, config, storage, fetcher=fake_fetch)
        assert fetched == ["https://example.substack.com/feed"]
        assert report.items == 3
        assert report.added == 2  # the empty post is skipped, not stored
        assert report.skipped_empty == 1

        documents = storage.list_external_documents(person.person_id)
        assert len(documents) == 2
        kafka = next(d for d in documents if "Kafka" in d.title)
        assert kafka.url == "https://example.substack.com/p/on-kafka-migrations"
        assert kafka.published_at is not None and kafka.published_at.year == 2026
        record = storage.get_source_record(kafka.source_record_id)
        assert record is not None and record.source_type == "substack_feed"
        # the raw post HTML is archived in the inbox
        archived = config.data_dir / record.source_locator
        assert "Apache Kafka" in archived.read_text(encoding="utf-8")

        again = fetch_person_feed(person, config, storage, fetcher=fake_fetch)
        assert again.added == 0
        assert again.skipped_duplicates == 2


def test_fetch_requires_feed_url_and_valid_xml(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("No Feed", storage)
        with pytest.raises(IngestError, match="no sources"):
            fetch_person_feed(person, config, storage, fetcher=lambda url: b"")
        watched, _ = add_person("Bad Feed", storage, substack_url="https://bad.substack.com")
        with pytest.raises(IngestError, match="not parseable"):
            fetch_person_feed(watched, config, storage, fetcher=lambda url: b"<html>nope")


def test_people_evidence_attributes_authors(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Jane Author", storage, substack_url="https://example.substack.com")
        fetch_person_feed(person, config, storage, fetcher=lambda url: RSS_FEED)
        hits = find_people_evidence("kafka", storage)
        assert hits and hits[0].person_name == "Jane Author"
        assert "Kafka" in hits[0].document.title
        assert find_people_evidence("flamingo", storage) == []


def test_match_people_resolves_partial_names(workspace: Path) -> None:
    from wingman.application.people import match_people

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Marko Klopets", storage)
        add_person("Mark Otero", storage)
        add_person("Scott Brady", storage)

        # exact normalized match wins outright, even when a substring of others
        assert [p.name for p in match_people(storage, "  MARKO   KLOPETS ")] == ["Marko Klopets"]
        # unique partial resolves
        assert [p.name for p in match_people(storage, "klopets")] == ["Marko Klopets"]
        # token order does not matter
        assert [p.name for p in match_people(storage, "klopets marko")] == ["Marko Klopets"]
        # ambiguous prefix returns all candidates, sorted
        assert [p.name for p in match_people(storage, "mark")] == ["Mark Otero", "Marko Klopets"]
        # no match and blank input
        assert match_people(storage, "nobody") == []
        assert match_people(storage, "   ") == []


def test_add_person_email_and_linkedin_are_validated_and_upserted(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person(
            "Jo Contact",
            storage,
            linkedin_url="https://linkedin.com/in/jo/",
            email=" jo@example.com ",
        )
        assert person.linkedin_url == "https://linkedin.com/in/jo"  # trailing slash normalized
        assert person.email == "jo@example.com"  # whitespace stripped
        # None on update preserves the stored values
        person, created = add_person("Jo Contact", storage, company="NewCo")
        assert not created
        assert person.email == "jo@example.com" and person.linkedin_url is not None

        with pytest.raises(IngestError, match="does not look like an email"):
            add_person("Bad Email", storage, email="not-an-email")
        with pytest.raises(IngestError, match="https"):
            add_person("Bad Link", storage, linkedin_url="http://linkedin.com/in/x")
