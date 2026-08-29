"""make-it-so: orchestration with honest per-step results; watchlists cycle it."""

from pathlib import Path

import pytest

import wingman.application.people as people_module
from wingman.application.ingest import IngestError
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.pipeline import make_it_so
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>On Kafka</title>
    <link>https://jane.substack.com/p/on-kafka</link>
    <content:encoded><![CDATA[<p>Kafka streaming pipelines everywhere.</p>]]></content:encoded>
  </item></channel></rss>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    # add_person verifies a passed substack_url's feed before storing it
    # (#483) -- a default valid feed so that verification never hits the
    # real network; tests that care about specific feed content patch
    # fetch_url again afterward.
    monkeypatch.setattr(
        people_module,
        "fetch_url",
        lambda url: (
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        ),
    )
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    # hashed embeddings: the pipeline's embed step needs no key in tests
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    return tmp_path


def test_person_pipeline_degrades_step_by_step(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        report = make_it_so("jane", config, storage)

    status = {step.name: step.status for step in report.steps}
    details = {step.name: step.detail for step in report.steps}
    assert report.kind == "person" and report.target == "Jane Author"
    assert status["fetch"] == "ok" and "1 added" in details["fetch"]
    assert status["news"] == "ok"
    assert status["embed"] == "ok" and "hashed" in details["embed"]
    # no synthesize model configured in tests: POV and brief skip visibly
    assert status["pov"] == "skipped"
    assert status["brief"] == "skipped"
    # the export still lands, with the skip hints inside it
    assert status["export"] == "ok" and status["export-html"] == "ok"
    assert report.export_path is not None and Path(report.export_path).exists()
    assert report.html_path is not None and Path(report.html_path).exists()


def test_pipeline_without_sources_and_ambiguity(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module

    config = load_config()
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    with Storage(config.db_path) as storage:
        add_person("Mark One", storage)
        add_person("Mark Two", storage)
        report = make_it_so("Mark One", config, storage)
        status = {step.name: step.status for step in report.steps}
        assert status["fetch"] == "skipped"
        with pytest.raises(IngestError, match="matches several"):
            make_it_so("mark", config, storage)
        # unknown name falls through to the company pipeline, which now degrades
        # gracefully (issue #310) rather than raising: a Gaps-only dossier still
        # exports.
        report = make_it_so("NoSuchTarget", config, storage)
        assert report.kind == "company"
        status = {step.name: step.status for step in report.steps}
        assert status["dossier"] == "ok"
        assert report.export_path is not None and Path(report.export_path).exists()


def test_company_kind_runs_dossier_export(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import wingman.application.people as people_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    with Storage(config.db_path) as storage:
        person, _ = add_person(
            "Jane Author", storage, substack_url="https://jane.substack.com", company="DataCo"
        )
        fetch_person_feed(person, config, storage)
        report = make_it_so("DataCo", config, storage, kind="company")
    assert report.kind == "company"
    assert report.export_path is not None and Path(report.export_path).exists()
    assert "dataco" in report.export_path


def test_watchlists_store_and_cycle(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        assert storage.watchlist_add("Investors", "person", "Jane Author")
        assert not storage.watchlist_add("investors", "person", "Jane Author")  # key-normalized
        assert storage.watchlist_add("Investors", "company", "DataCo")
        assert storage.watchlists() == [("Investors", 2)]
        assert storage.watchlist_members("INVESTORS") == [
            ("person", "Jane Author"),
            ("company", "DataCo"),
        ]
        assert storage.watchlist_remove("Investors", "company", "DataCo")
        assert not storage.watchlist_remove("Investors", "company", "DataCo")
        assert storage.watchlists() == [("Investors", 1)]
