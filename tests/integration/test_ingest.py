import hashlib
import json
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError, ingest_resume
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.recorded import RecordedProvider

RESUME = "# Jo\n\n- Shipped the search rewrite.\n\nSkills: Python.\n"
RESPONSE = json.dumps(
    {
        "items": [
            {
                "kind": "achievement",
                "name": "Search rewrite",
                "detail": "Shipped the search rewrite.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": ["Shipped the search rewrite."],
            },
            {
                "kind": "skill",
                "name": "Python",
                "detail": "",
                "classification": "fact",
                "confidence": 0.95,
                "quotes": ["Skills: Python."],
            },
        ]
    }
)


@pytest.fixture
def workspace(tmp_path: Path) -> Config:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "ws")})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _resume_file(tmp_path: Path, text: str = RESUME) -> Path:
    path = tmp_path / "resume.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_full_slice_produces_cited_artifacts(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        report = ingest_resume(
            _resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE)
        )
        assert report.accepted == 2
        assert report.rejected == []
        assert storage.count_source_records() == 1
        assert storage.count_profile_items() == 2

    payload = json.loads(report.career_json_path.read_text(encoding="utf-8"))
    assert len(payload["achievements"]) == 1
    assert payload["achievements"][0]["evidence"][0]["quote"] == "Shipped the search rewrite."

    markdown = report.career_md_path.read_text(encoding="utf-8")
    assert "Search rewrite" in markdown
    assert "> Shipped the search rewrite." in markdown
    assert "[^1]" in markdown


def test_reingest_same_resume_is_idempotent(workspace: Config, tmp_path: Path) -> None:
    resume = _resume_file(tmp_path)
    provider = RecordedProvider(RESPONSE)
    with Storage(workspace.db_path) as storage:
        first = ingest_resume(resume, workspace, storage, provider)
        second = ingest_resume(resume, workspace, storage, provider)
        assert second.source_record_id == first.source_record_id
        assert second.source_reused is True
        assert second.accepted == 0
        assert second.skipped_duplicates == 2
        assert storage.count_profile_items() == 2


def test_conflicting_value_across_sources_is_preserved_and_surfaced(
    workspace: Config, tmp_path: Path
) -> None:
    """A DIFFERENT document disagreeing is a real conflict, kept side by side.

    (A newer version of the SAME document replacing its own claim is not —
    that's the RFC-028 supersede path, covered in test_supersede.py.)
    """
    conflicting = json.loads(RESPONSE)
    conflicting["items"][1]["detail"] = "10 years of experience"
    other = tmp_path / "recruiter-notes.md"
    other.write_text(RESUME + "\nMore Python.\n", encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        ingest_resume(_resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE))
        report = ingest_resume(other, workspace, storage, RecordedProvider(json.dumps(conflicting)))
        # identical achievement from a new source merges evidence; conflicting
        # skill is stored side by side
        assert report.conflicts == 1
        assert report.evidence_merged == 1
        assert report.updated == 0 and report.retired == 0
        assert storage.count_profile_items() == 3
    markdown = report.career_md_path.read_text(encoding="utf-8")
    assert "Conflicts (need your resolution)" in markdown


def test_same_value_new_evidence_is_merged_not_dropped(workspace: Config, tmp_path: Path) -> None:
    second_resume = "# Jo again\n\n- Shipped the search rewrite.\n\nExpert in Python daily.\n"
    second_response = json.loads(RESPONSE)
    second_response["items"] = [
        {
            "kind": "skill",
            "name": "Python",
            "detail": "",
            "classification": "fact",
            "confidence": 0.95,
            "quotes": ["Expert in Python daily."],
        }
    ]
    v2_dir = tmp_path / "v2"
    v2_dir.mkdir()
    with Storage(workspace.db_path) as storage:
        ingest_resume(_resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE))
        report = ingest_resume(
            _resume_file(v2_dir, text=second_resume),
            workspace,
            storage,
            RecordedProvider(json.dumps(second_response)),
        )
        assert report.evidence_merged == 1
        assert report.accepted == 0
        assert storage.count_profile_items() == 2  # no duplicate item created
        python_items = [i for i in storage.list_profile_items() if i.name == "Python"]
        assert len(python_items) == 1
        assert {span.quote for span in python_items[0].evidence} == {
            "Skills: Python.",
            "Expert in Python daily.",
        }


def test_empty_resume_fails_visibly(workspace: Config, tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            ingest_resume(empty, workspace, storage, RecordedProvider(RESPONSE))
        assert storage.count_source_records() == 0


def test_resume_copied_into_inbox(workspace: Config, tmp_path: Path) -> None:
    from wingman.application.resume_formats import extract_resume_text

    resume = _resume_file(tmp_path)
    # The hash covers the extracted text — for .md, the normalized form (RFC-026).
    extracted = extract_resume_text(resume)
    with Storage(workspace.db_path) as storage:
        ingest_resume(resume, workspace, storage, RecordedProvider(RESPONSE))
        record = storage.get_source_record_by_hash(hashlib.sha256(extracted.encode()).hexdigest())
    assert record is not None
    assert record.source_locator.startswith("inbox/")
    assert (workspace.data_dir / record.source_locator).read_text(encoding="utf-8") == RESUME


def test_ingest_from_google_url_archives_then_extracts(workspace: Config) -> None:
    from wingman.application.ingest import ingest_resume_from_url

    def fetcher(url: str) -> bytes:
        assert url == "https://docs.google.com/document/d/DOC1/export?format=txt"
        return RESUME.encode()

    with Storage(workspace.db_path) as storage:
        report = ingest_resume_from_url(
            "https://docs.google.com/document/d/DOC1/edit",
            workspace,
            storage,
            RecordedProvider(RESPONSE),
            fetcher=fetcher,
        )
        assert report.accepted == 2
    # the fetched artifact was archived in the inbox before extraction
    archived = list(workspace.inbox_dir.glob("*google-DOC1*"))
    assert len(archived) == 1 and archived[0].suffix == ".txt"
    assert archived[0].read_text(encoding="utf-8") == RESUME


def test_repeat_url_ingest_never_overwrites_the_archive(workspace: Config) -> None:
    from wingman.application.ingest import ingest_resume_from_url

    def fetcher(url: str) -> bytes:
        return RESUME.encode()

    with Storage(workspace.db_path) as storage:
        for _ in range(2):
            ingest_resume_from_url(
                "https://docs.google.com/document/d/DOC1/edit",
                workspace,
                storage,
                RecordedProvider(RESPONSE),
                fetcher=fetcher,
            )
    # both fetches were archived as distinct artifacts (microsecond stamps)
    assert len(list(workspace.inbox_dir.glob("*google-DOC1*"))) == 2


# --- #317: a designed resume's section headings are not skills ---------------

_HEADING_RESUME = (
    "# Jo\n\n"
    "Roadmap\n\n"
    "Owned the 2026 roadmap end to end.\n\n"
    "Analytics\n\n"
    "Built the attribution pipeline in Python.\n"
)


def _heading_response(*items: tuple[str, str]) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "kind": "skill",
                    "name": name,
                    "detail": "",
                    "classification": "fact",
                    "confidence": 0.9,
                    "quotes": [quote],
                }
                for name, quote in items
            ]
        }
    )


def test_a_section_heading_is_not_stored_as_a_skill(workspace: Config, tmp_path: Path) -> None:
    """Layout-mode extraction (#278) keeps a designed resume's structure, which
    is the point of it — and makes a heading look exactly like a short skill.
    A heading can only ever cite itself; a real skill is claimed by a sentence
    that uses it."""
    response = _heading_response(("Roadmap", "Roadmap"), ("Analytics", "Analytics"))

    with Storage(workspace.db_path) as storage:
        report = ingest_resume(
            _resume_file(tmp_path, _HEADING_RESUME),
            workspace,
            storage,
            RecordedProvider(response),
        )
        stored = {item.name for item in storage.list_profile_items()}

    assert stored == set()
    assert {r.name for r in report.rejected} == {"Roadmap", "Analytics"}
    assert all("its own name" in r.reason for r in report.rejected)


def test_a_one_word_skill_a_sentence_actually_uses_still_survives(
    workspace: Config, tmp_path: Path
) -> None:
    """The acceptance criterion that stops this becoming 'ban short names'."""
    response = _heading_response(("Python", "Built the attribution pipeline in Python."))

    with Storage(workspace.db_path) as storage:
        ingest_resume(
            _resume_file(tmp_path, _HEADING_RESUME),
            workspace,
            storage,
            RecordedProvider(response),
        )
        stored = {item.name for item in storage.list_profile_items()}

    assert stored == {"Python"}


def test_the_heading_rule_ignores_case_and_typographic_punctuation(
    workspace: Config, tmp_path: Path
) -> None:
    """The quote is the SOURCE's span, so it can differ from the proposed name
    in case or punctuation and still be the very same label."""
    resume = "# Jo\n\nROADMAP\n\nOwned the 2026 roadmap end to end.\n"
    response = _heading_response(("Roadmap", "ROADMAP"))

    with Storage(workspace.db_path) as storage:
        report = ingest_resume(
            _resume_file(tmp_path, resume), workspace, storage, RecordedProvider(response)
        )
        assert storage.list_profile_items() == []

    assert [r.name for r in report.rejected] == ["Roadmap"]


def test_only_skills_are_held_to_this_rule(workspace: Config, tmp_path: Path) -> None:
    """A role or achievement carries structure of its own and does not fail
    this way; widening the rule to every kind would reject real items."""
    response = json.dumps(
        {
            "items": [
                {
                    "kind": "achievement",
                    "name": "Roadmap",
                    "detail": "Owned it.",
                    "classification": "fact",
                    "confidence": 0.9,
                    "quotes": ["Roadmap"],
                }
            ]
        }
    )

    with Storage(workspace.db_path) as storage:
        ingest_resume(
            _resume_file(tmp_path, _HEADING_RESUME),
            workspace,
            storage,
            RecordedProvider(response),
        )
        assert [i.name for i in storage.list_profile_items()] == ["Roadmap"]
