"""Retained research pages (RFC-060): an approved page kept as citable prose.

RFC-015 keeps a hash of the page and throws the words away, which answers
"did this change" and makes a values page permanently unquotable. These
tests pin per-source retention that keeps the prose too — and, just as importantly,
pin WHICH extraction is stored, because the snapshot pipeline's
hash-oriented text does not survive the verbatim-quote gate.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from wingman.application.evidence import fold_whitespace
from wingman.application.pov import build_company_pov, company_card_id
from wingman.application.research import (
    add_company_source,
    extract_page,
    remove_company_source,
    rename_company,
    render_research_report,
    research_company,
)
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "research_retention" / "values_page.html"
)
VALUES_PAGE = FIXTURE.read_bytes()
VALUES_URL = "https://northwind.example/about/values"

# A second version of the same page: one value restated, the rest identical.
VALUES_PAGE_V2 = VALUES_PAGE.replace(
    b"<h2>Ship small, ship often</h2>",
    b"<h2>Ship small, ship often, ship reversibly</h2>",
)

CAREERS_PAGE = b"""<html><head><title>Northwind Careers</title></head>
<body><h1>Open roles</h1><p>We are hiring engineers who like small changes.</p>
<a href="/jobs/platform">Platform Engineer</a></body></html>"""


class ScriptedProvider:
    """Returns a fixed proposal; the point of the test is the validation gate."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_prompt: str | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.last_prompt = request.prompt
        return ModelResponse(
            text=json.dumps(self._payload), provider="scripted", model="scripted-1", latency_ms=0
        )


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    resolved = load_config()
    for directory in (resolved.data_dir, resolved.inbox_dir, resolved.reports_dir):
        directory.mkdir(parents=True)
    return resolved


@pytest.fixture
def storage(config: Config) -> Storage:
    with Storage(config.db_path) as handle:
        yield handle


def _approve(storage: Storage, *, retain: bool, url: str = VALUES_URL) -> None:
    add_company_source("Northwind Labs", url, storage, label="values", retain=retain)


def test_retained_source_becomes_a_company_attributed_document(
    config: Config, storage: Storage
) -> None:
    """The gap #349 names: the page's words survive, attributed to the company."""
    _approve(storage, retain=True)
    report = research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)

    assert report.results[0].retained == "stored"
    assert "kept as a document" in render_research_report(report)

    documents = storage.list_external_documents()
    assert len(documents) == 1
    document = documents[0]
    # Org-attributed through the company anchor, exactly as a company feed
    # post is (RFC-029) — no invented human byline.
    assert document.person_id == company_card_id("northwind labs")
    assert document.organization == "Northwind Labs"
    assert document.url == VALUES_URL
    assert document.title == "Our values — Northwind Labs"
    # A fetch says when we looked, never when the page was written.
    assert document.published_at is None

    body = storage.get_external_body(document.doc_id) or ""
    assert "We optimise for the reader, not the writer" in body
    # Chrome the snapshot hash happily includes is not evidence: nav and
    # footer boilerplate stay out of a document a stance may quote.
    assert "equal opportunity employer" not in body
    assert "Privacy" not in body

    # Provenance is intact: an archived page on disk behind an immutable record.
    record = storage.get_source_record(document.source_record_id)
    assert record is not None and record.source_type == "research_page"
    assert record.document_key.startswith("research-northwind-example-about-values-")
    assert (config.data_dir / record.source_locator).read_text(encoding="utf-8")


def test_retained_body_survives_the_verbatim_quote_gate(config: Config, storage: Storage) -> None:
    """Extraction quality, checked rather than assumed (#349).

    Every sentence below is one a stance would plausibly quote. They must
    match the STORED body under the same whitespace folding `_validate_proposal`
    uses — and the snapshot pipeline's own text is shown failing the same
    check, which is why it is not what gets stored.
    """
    _approve(storage, retain=True)
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    body = storage.get_external_body(storage.list_external_documents()[0].doc_id) or ""
    folded_body = fold_whitespace(body)

    quotable = [
        (
            "A document that takes an hour to write and saves ten people ten minutes each"
            " is a good trade."
        ),
        "We hold written communication to a high bar precisely because it scales",
        "Every change should be small enough that one person can hold it in their head",
        "We would rather have the argument in the room than the retrospective.",
        "Being distributed is not a perk we offer; it is the operating model we chose",
        "it only works if the written record is actually the source of truth",
        "We are Northwind—forty people in nine countries.",
        "Our founder’s rule: hire people who finish things",
    ]
    for quote in quotable:
        assert fold_whitespace(quote) in folded_body, quote

    # The hashing extraction inserts a space at every tag boundary, so any
    # sentence carrying inline markup mid-word or before punctuation comes
    # out as 'North wind', 'rule :' — verbatim-unmatchable, and the reason
    # the retained body is extracted the way feed posts are.
    snapshot_text, _ = extract_page(VALUES_PAGE, VALUES_URL)
    folded_snapshot = fold_whitespace(snapshot_text)
    assert fold_whitespace("We are Northwind—forty people") not in folded_snapshot
    assert (
        fold_whitespace("Our founder’s rule: hire people who finish things") not in folded_snapshot
    )


def test_company_pov_can_quote_a_retained_research_page(config: Config, storage: Storage) -> None:
    """The user-visible payoff: a company with no feed gets a real stance."""
    add_company_source("Northwind Labs", VALUES_URL, storage, label="about")
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    doc_id = storage.list_external_documents()[0].doc_id
    provider = ScriptedProvider(
        {
            "stances": [
                {
                    "statement": "Northwind holds written communication to a deliberately high bar.",
                    "quote": "We hold written communication to a high bar precisely because it"
                    " scales",
                    "doc_id": doc_id,
                    "dimension": "craft",
                },
                {
                    "statement": "Northwind claims to make changes small and reversible.",
                    "quote": "Every change should be small enough that one person can hold it in"
                    " their head",
                    "doc_id": doc_id,
                    "dimension": "craft",
                },
            ],
            "topics": ["written culture", "small changes"],
        }
    )

    report = build_company_pov("Northwind Labs", storage, provider)

    assert [stance.statement for stance in report.card.stances] == [
        "Northwind holds written communication to a deliberately high bar.",
        "Northwind claims to make changes small and reversible.",
    ]
    assert report.rejected == []
    assert all(stance.organization == "Northwind Labs" for stance in report.card.stances)
    assert "Our values — Northwind Labs" in (provider.last_prompt or "")


def test_a_non_retained_source_keeps_nothing(config: Config, storage: Storage) -> None:
    """An explicit opt-out keeps only the snapshot."""
    _approve(storage, retain=False)
    report = research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)

    assert report.results[0].retained == ""
    assert "retained text" not in render_research_report(report)
    assert storage.list_external_documents() == []
    assert storage.count_source_records() == 0
    assert storage.get_person(company_card_id("northwind labs")) is None
    # …and the snapshot the whole of RFC-015 rests on is still there.
    snapshot = storage.get_research_snapshot("northwind labs", VALUES_URL)
    assert snapshot is not None and snapshot.text_hash


def test_two_sources_one_retained_leaves_the_other_alone(config: Config, storage: Storage) -> None:
    """Retention is per source, not per company: the careers page is not stored."""
    _approve(storage, retain=True)
    add_company_source(
        "Northwind Labs", "https://northwind.example/careers", storage, label="careers"
    )
    pages = {VALUES_URL: VALUES_PAGE, "https://northwind.example/careers": CAREERS_PAGE}
    report = research_company("Northwind Labs", config, storage, fetcher=lambda url: pages[url])

    retained = {result.url: result.retained for result in report.results}
    assert retained == {VALUES_URL: "stored", "https://northwind.example/careers": ""}
    documents = storage.list_external_documents()
    assert [document.url for document in documents] == [VALUES_URL]


def test_refetch_supersedes_rather_than_duplicating(config: Config, storage: Storage) -> None:
    """RFC-028 lineage: a newer version replaces its own predecessor."""
    _approve(storage, retain=True)
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    first = storage.list_external_documents()[0]

    report = research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE_V2)

    assert report.results[0].retained == "replaced"
    documents = storage.list_external_documents()
    assert len(documents) == 1
    assert documents[0].doc_id != first.doc_id
    body = storage.get_external_body(documents[0].doc_id) or ""
    assert "Ship small, ship often, ship reversibly" in body
    # The superseded document is gone from the pool AND its full-text index.
    assert storage.get_external_body(first.doc_id) is None
    # Both versions remain as immutable records under one document identity.
    records = [storage.get_source_record(document.source_record_id) for document in documents]
    assert records[0] is not None
    assert len(storage.record_ids_for_document(records[0].document_key)) == 2


def test_unchanged_page_is_not_restored(config: Config, storage: Storage) -> None:
    """No churn: an unchanged page keeps the document it already produced."""
    _approve(storage, retain=True)
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    first = storage.list_external_documents()[0]

    report = research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)

    assert report.results[0].retained == "unchanged"
    assert [document.doc_id for document in storage.list_external_documents()] == [first.doc_id]
    assert storage.count_source_records() == 1


def test_retention_can_be_turned_on_for_an_already_approved_source(
    config: Config, storage: Storage
) -> None:
    """Changing your mind must not cost the snapshot the diff depends on."""
    _approve(storage, retain=False)
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    assert storage.list_external_documents() == []

    _approve(storage, retain=True)
    snapshot = storage.get_research_snapshot("northwind labs", VALUES_URL)
    assert snapshot is not None  # not a remove-and-re-add

    # Text is unchanged since that snapshot, yet the page has never been kept:
    # retention must still store it rather than report "unchanged".
    report = research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    assert report.results[0].retained == "stored"
    assert len(storage.list_external_documents()) == 1

    # And a bare re-add never silently switches retention back off.
    add_company_source("Northwind Labs", VALUES_URL, storage)
    assert storage.list_company_sources("northwind labs")[0].retain is True


def test_withdrawing_a_source_withdraws_its_retained_text(config: Config, storage: Storage) -> None:
    _approve(storage, retain=True)
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    assert storage.list_external_documents()

    assert remove_company_source("Northwind Labs", VALUES_URL, storage)

    assert storage.list_external_documents() == []


def test_rename_keeps_lineage_so_a_refetch_still_supersedes(
    config: Config, storage: Storage
) -> None:
    """Document identity comes from the URL, so renaming cannot orphan it."""
    _approve(storage, retain=True)
    research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
    rename_company("Northwind Labs", "Northwind Labs Inc.", storage)

    report = research_company(
        "Northwind Labs Inc.", config, storage, fetcher=lambda url: VALUES_PAGE_V2
    )

    assert report.results[0].retained == "replaced"
    assert len(storage.list_external_documents()) == 1


def test_missing_company_prose_points_to_retention(storage: Storage) -> None:
    from wingman.application.ingest import IngestError

    _approve(storage, retain=False)
    with pytest.raises(IngestError, match="--retain"):
        build_company_pov("Northwind Labs", storage, ScriptedProvider({}))


@pytest.mark.parametrize("label", ["about", " About ", "ABOUT"])
def test_about_retains_by_default(storage: Storage, label: str) -> None:
    source, _ = add_company_source("Northwind Labs", VALUES_URL, storage, label=label)
    assert source.retain is True


def test_explicit_about_opt_out_survives_repeat_add(storage: Storage) -> None:
    add_company_source("Northwind Labs", VALUES_URL, storage, label="about", retain=False)
    source, created = add_company_source("Northwind Labs", VALUES_URL, storage, label="about")
    assert created is False
    assert source.retain is False


def test_legacy_about_migrates_once_and_next_fetch_keeps_prose(config: Config) -> None:
    with Storage(config.db_path) as storage:
        add_company_source("Northwind Labs", VALUES_URL, storage, label=" About ", retain=False)
        add_company_source("Northwind Labs", "https://northwind.example/careers", storage)
        research_company("Northwind Labs", config, storage, fetcher=lambda url: VALUES_PAGE)
        snapshot = storage.get_research_snapshot("northwind labs", VALUES_URL)
    # Simulate a pre-#463 workspace, including a field from a newer release.
    with sqlite3.connect(config.db_path) as db:
        db.execute("DROP TABLE IF EXISTS schema_migrations")
        payload = json.loads(
            db.execute(
                "SELECT payload FROM company_sources WHERE url = ?", (VALUES_URL,)
            ).fetchone()[0]
        )
        payload["future_field"] = "preserve me"
        db.execute(
            "UPDATE company_sources SET payload = ? WHERE url = ?",
            (json.dumps(payload), VALUES_URL),
        )
    with Storage(config.db_path) as storage:
        sources = {source.url: source for source in storage.list_company_sources("northwind labs")}
        assert sources[VALUES_URL].retain is True
        assert sources["https://northwind.example/careers"].retain is False
        assert storage.get_research_snapshot("northwind labs", VALUES_URL) == snapshot
        calls = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return VALUES_PAGE

        report = research_company("Northwind Labs", config, storage, fetcher=fetch)
        assert (
            next(result for result in report.results if result.url == VALUES_URL).retained
            == "stored"
        )
        assert calls.count(VALUES_URL) == 1
        assert len(storage.list_external_documents()) == 1
        with sqlite3.connect(config.db_path) as db:
            payload = json.loads(
                db.execute(
                    "SELECT payload FROM company_sources WHERE url = ?", (VALUES_URL,)
                ).fetchone()[0]
            )
            assert payload["future_field"] == "preserve me"
        storage.set_company_source_retention("northwind labs", VALUES_URL, False)
    with Storage(config.db_path) as storage:
        assert (
            next(
                source
                for source in storage.list_company_sources("northwind labs")
                if source.url == VALUES_URL
            ).retain
            is False
        )
