"""Career-profile management (RFC-027): list, rm, resolve, amend, correct, clear."""

import json
from pathlib import Path

import pytest

from wingman.agents.profile_curator import ProposalParseError, parse_proposal
from wingman.application.evidence import locate_quote
from wingman.application.ingest import IngestError, ingest_resume
from wingman.application.interview import (
    AMENDMENT_SOURCE_TYPE,
    FORM_SOURCE_TYPE,
    capture_interview_reaction,
)
from wingman.application.profile_manage import (
    CORRECTION_SOURCE_TYPE,
    amend_item,
    clear_profile,
    correct_item,
    describe_amendment,
    describe_correction,
    find_item,
    preview_correction,
    rekind_item,
    remove_item,
    rename_item,
    render_profile_listing,
    resolve_item,
)
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind, SentimentIntensity
from wingman.domain.provenance import FORM_EXTRACTOR
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.recorded import RecordedProvider

RESUME = "# Jo\n\n- Shipped the search rewrite.\n\nSkills: BigQuery daily.\n"


def _response(bigquery_detail: str) -> str:
    return json.dumps(
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
                    "name": "BigQuery",
                    "detail": bigquery_detail,
                    "classification": "fact",
                    "confidence": 0.9,
                    "quotes": ["Skills: BigQuery daily."],
                },
            ]
        }
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _ingest_twice(config: Config, storage: Storage, tmp_path: Path) -> None:
    """Two DIFFERENT documents whose BigQuery detail differs -> active + conflict.

    Deliberately two filenames. Ingesting one file twice used to produce this
    same shape, but that was the bug in #336, not a conflict: a document does
    not disagree with itself, and the second read's differing detail was only
    the model wording the same claim differently. A genuine conflict needs two
    sources, which is what these tests are actually about.
    """
    first = tmp_path / "resume.md"
    first.write_text(RESUME, encoding="utf-8")
    ingest_resume(first, config, storage, RecordedProvider(_response("")))

    # Different CONTENT as well as a different name: an identical body hashes
    # to the same source record and gets reused, which would make these one
    # document again.
    second = tmp_path / "linkedin-profile.md"
    second.write_text(RESUME + "\nExported from LinkedIn.\n", encoding="utf-8")
    ingest_resume(second, config, storage, RecordedProvider(_response("Used daily.")))


def test_reingest_conflict_shape(workspace: Config, tmp_path: Path) -> None:
    """The situation that motivated RFC-027: re-ingest piles up conflict rows."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        items = storage.list_profile_items()
        bigquery = [item for item in items if item.name == "BigQuery"]
        assert len(bigquery) == 2
        assert {item.status for item in bigquery} == {ItemStatus.ACTIVE, ItemStatus.CONFLICT}
        listing = render_profile_listing(items)
        assert "Conflicts (resolve with" in listing
        assert "1 in conflict" in listing


def test_find_item_by_prefix(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        target = storage.list_profile_items()[0]
        assert find_item(target.item_id[:8], storage).item_id == target.item_id
        with pytest.raises(IngestError, match="no profile item"):
            find_item("zzzzzzzz", storage)
        with pytest.raises(IngestError, match="empty"):
            find_item("  ", storage)
        # ambiguity: two items sharing a crafted prefix
        seed = storage.list_profile_items()[0]
        for suffix in ("1", "2"):
            storage.add_profile_item(seed.model_copy(update={"item_id": f"shared-{suffix}"}))
        with pytest.raises(IngestError, match="ambiguous"):
            find_item("shared-", storage)


def test_rm_deletes_and_rerenders(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        conflict = next(
            item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
        )
        removed = remove_item(conflict.item_id[:8], workspace, storage)
        assert removed.item_id == conflict.item_id
        assert storage.get_profile_item(conflict.item_id) is None
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
    assert "Conflicts (need your resolution)" not in career_md


def test_resolve_keeps_challenger_and_drops_rivals(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        challenger = next(
            item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
        )
        winner, rivals = resolve_item(challenger.item_id[:8], workspace, storage)
        assert winner.item_id == challenger.item_id
        assert winner.status is ItemStatus.ACTIVE and winner.conflicts_with is None
        assert len(rivals) == 1
        bigquery = [item for item in storage.list_profile_items() if item.name == "BigQuery"]
        assert [item.item_id for item in bigquery] == [winner.item_id]
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
    assert "Used daily." in career_md  # the challenger's detail is now the active one
    assert "Conflicts (need your resolution)" not in career_md


def test_resolve_keeps_active_and_drops_conflicts(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        active = next(
            item
            for item in storage.list_profile_items()
            if item.name == "BigQuery" and item.status is ItemStatus.ACTIVE
        )
        winner, rivals = resolve_item(active.item_id[:8], workspace, storage)
        assert winner.item_id == active.item_id and len(rivals) == 1
        assert storage.count_profile_items() == 2  # search rewrite + BigQuery


def test_clear_empties_and_reingest_rebuilds(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        removed = clear_profile(workspace, storage)
        assert removed == 3 and storage.count_profile_items() == 0
        career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
        assert "_None yet._" in career_md
        # the same source re-ingests cleanly: source record reused, items fresh
        resume = tmp_path / "resume.md"
        report = ingest_resume(resume, workspace, storage, RecordedProvider(_response("")))
        assert report.source_reused is True
        assert report.accepted == 2 and report.conflicts == 0


def test_mcp_profile_manage_roundtrip(workspace: Config, tmp_path: Path) -> None:
    from wingman.mcp_server import profile_manage

    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        challenger = next(
            item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
        )
    listing = profile_manage("list")
    assert "BigQuery" in listing and "Conflicts" in listing
    assert "Kept skill 'BigQuery'" in profile_manage("resolve", challenger.item_id[:8])
    assert "Removed 2 profile items" in profile_manage("clear")
    assert "unknown action" in profile_manage("nope")
    assert "failed" in profile_manage("rm", "zzzzzzzz")


def _kind_column(storage: Storage, item_id: str) -> str:
    """The 'kind' COLUMN, not the payload — the two can disagree (#273)."""
    row = storage._conn.execute(  # noqa: SLF001 - asserting storage's own invariant
        "SELECT kind FROM profile_items WHERE item_id = ?", (item_id,)
    ).fetchone()
    return str(row[0])


def test_rekind_moves_the_item_and_keeps_its_lineage(workspace: Config, tmp_path: Path) -> None:
    """A misfiled item is a labelling mistake — the claim and its evidence
    are fine. Re-kinding must keep the id, the evidence and the source
    record, which is exactly what delete-and-recapture destroys."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        before = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )

        moved, was = rekind_item(before.item_id[:8], "role", workspace, storage)

        assert was is ProfileItemKind.ACHIEVEMENT
        assert moved.kind is ProfileItemKind.ROLE
        assert moved.item_id == before.item_id
        assert moved.evidence == before.evidence
        # The source record travels with the evidence span, which is where
        # the citation actually lives.
        assert moved.evidence[0].source_record_id == before.evidence[0].source_record_id
        assert moved.evidence[0].quote == before.evidence[0].quote
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
    assert "## Roles" in career_md


def test_rekind_writes_the_kind_column_not_just_the_payload(
    workspace: Config, tmp_path: Path
) -> None:
    """The half-apply this feature had to avoid.

    list_profile_items decodes the payload, while dedup/supersede queries
    'WHERE kind = ? AND name_key = ?' against the COLUMN. Writing only the
    payload leaves an item that looks re-kinded everywhere a human checks
    while dedup still matches it under its old kind — invisible until a
    later ingest collides with it (#273).
    """
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        target = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )
        assert _kind_column(storage, target.item_id) == "achievement"

        rekind_item(target.item_id[:8], "role", workspace, storage)

        assert _kind_column(storage, target.item_id) == "role"


def test_rekind_refuses_a_name_already_taken_in_the_target_kind(
    workspace: Config, tmp_path: Path
) -> None:
    """Two rows sharing (kind, name_key) is how the dedup index stops
    meaning anything — that is a conflict to resolve, not a move to make."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        achievement = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )
        twin = achievement.model_copy(
            update={"item_id": "twin-0001", "kind": ProfileItemKind.SKILL}
        )
        storage.add_profile_item(twin)

        with pytest.raises(IngestError, match="already exists"):
            rekind_item(achievement.item_id[:8], "skill", workspace, storage)
        # untouched
        assert storage.get_profile_item(achievement.item_id).kind is ProfileItemKind.ACHIEVEMENT


def test_rekind_rejects_unknown_kinds_interview_and_no_ops(
    workspace: Config, tmp_path: Path
) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        target = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )
        with pytest.raises(IngestError, match="unknown kind"):
            rekind_item(target.item_id[:8], "banana", workspace, storage)
        # INTERVIEW items carry a subtype and persona scope a re-kind can't invent
        with pytest.raises(IngestError, match="unknown kind"):
            rekind_item(target.item_id[:8], "interview", workspace, storage)
        with pytest.raises(IngestError, match="already a achievement"):
            rekind_item(target.item_id[:8], "achievement", workspace, storage)


ROLES_RESUME = (
    "# Jo\n\n"
    "## Experience\n\n"
    "Staff Data Engineer, Synctera — Jan 2022 to Jun 2024\n"
    "Led a team of six.\n\n"
    "Analytics Lead, Afresh — 2019 to Jan 2022\n\n"
    "Principal Engineer, Northwind — Mar 2024 to Present\n"
)


def _roles_response() -> str:
    return json.dumps(
        {
            "items": [
                {
                    "kind": "role",
                    "name": "Staff Data Engineer, Synctera",
                    "detail": "Led a team of six.",
                    "company": "Synctera",
                    "title": "Staff Data Engineer",
                    "started": "2022-01",
                    "ended": "2024-06",
                    "classification": "fact",
                    "confidence": 1.0,
                    "quotes": ["Staff Data Engineer, Synctera — Jan 2022 to Jun 2024"],
                },
                {
                    "kind": "role",
                    "name": "Principal Engineer, Northwind",
                    "company": "Northwind",
                    "title": "Principal Engineer",
                    "started": "2024-03",
                    "ended": "",
                    "classification": "fact",
                    "confidence": 1.0,
                    "quotes": ["Principal Engineer, Northwind — Mar 2024 to Present"],
                },
                {
                    "kind": "role",
                    "name": "Analytics Lead, Afresh",
                    "company": "Afresh",
                    "title": "Analytics Lead",
                    "started": "2019",
                    "ended": "2022-01",
                    "classification": "fact",
                    "confidence": 1.0,
                    "quotes": ["Analytics Lead, Afresh — 2019 to Jan 2022"],
                },
            ]
        }
    )


def test_curator_extracts_roles_with_structure(workspace: Config, tmp_path: Path) -> None:
    """The gap #269 named: v1 said 'extract achievements and skills', so a
    resume with a full employment history produced no roles at all."""
    resume = tmp_path / "resume.md"
    resume.write_text(ROLES_RESUME, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        ingest_resume(resume, workspace, storage, RecordedProvider(_roles_response()))
        roles = [i for i in storage.list_profile_items() if i.kind is ProfileItemKind.ROLE]

    assert len(roles) == 3
    synctera = next(r for r in roles if r.company == "Synctera")
    assert synctera.title == "Staff Data Engineer"
    assert synctera.started == "2022-01"
    assert synctera.ended == "2024-06"
    assert synctera.evidence[0].quote.startswith("Staff Data Engineer, Synctera")


def test_roles_render_reverse_chronologically_with_tenure(
    workspace: Config, tmp_path: Path
) -> None:
    resume = tmp_path / "resume.md"
    resume.write_text(ROLES_RESUME, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        ingest_resume(resume, workspace, storage, RecordedProvider(_roles_response()))
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")

    order = [
        career_md.index("Principal Engineer, Northwind"),
        career_md.index("Staff Data Engineer, Synctera"),
        career_md.index("Analytics Lead, Afresh"),
    ]
    assert order == sorted(order), "roles must render newest first"
    # A current role says so; it does not invent an end date.
    assert "(2024-03 – present)" in career_md
    assert "(2022-01 – 2024-06)" in career_md
    # A year-only date stays a year rather than being padded to a month.
    assert "(2019 – 2022-01)" in career_md


def test_an_undated_role_is_kept_and_sorted_last(workspace: Config, tmp_path: Path) -> None:
    """A position with no dates is still a fact about the career. It must
    survive, print without invented dates, and not sort as year zero."""
    resume = tmp_path / "resume.md"
    resume.write_text("Consultant, Tungsten\nStaff Engineer, Northwind — 2024\n", encoding="utf-8")
    response = json.dumps(
        {
            "items": [
                {
                    "kind": "role",
                    "name": "Consultant, Tungsten",
                    "company": "Tungsten",
                    "title": "Consultant",
                    "classification": "fact",
                    "confidence": 1.0,
                    "quotes": ["Consultant, Tungsten"],
                },
                {
                    "kind": "role",
                    "name": "Staff Engineer, Northwind",
                    "company": "Northwind",
                    "title": "Staff Engineer",
                    "started": "2024",
                    "classification": "fact",
                    "confidence": 1.0,
                    "quotes": ["Staff Engineer, Northwind — 2024"],
                },
            ]
        }
    )
    with Storage(workspace.db_path) as storage:
        ingest_resume(resume, workspace, storage, RecordedProvider(response))
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")

    assert career_md.index("Staff Engineer, Northwind") < career_md.index("Consultant, Tungsten")
    consultant_line = next(
        line for line in career_md.splitlines() if "Consultant, Tungsten" in line
    )
    assert "–" not in consultant_line and "present" not in consultant_line


def test_interview_is_not_an_extractable_kind() -> None:
    """INTERVIEW items carry a subtype and persona scope an extraction has
    no way to supply, so a model emitting one used to validate and persist
    an item every interview code path would then find malformed (#269)."""
    with pytest.raises(ProposalParseError):
        parse_proposal(
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "interview",
                            "name": "Something",
                            "classification": "fact",
                            "confidence": 1.0,
                            "quotes": ["Something"],
                        }
                    ]
                }
            )
        )


def test_the_four_real_kinds_still_parse() -> None:
    for kind in ("achievement", "skill", "role", "testimonial"):
        proposal = parse_proposal(
            json.dumps(
                {
                    "items": [
                        {
                            "kind": kind,
                            "name": "Something",
                            "classification": "fact",
                            "confidence": 1.0,
                            "quotes": ["Something"],
                        }
                    ]
                }
            )
        )
        assert proposal.items[0].item_kind is ProfileItemKind(kind)


def test_rename_keeps_lineage_and_moves_the_dedup_key(workspace: Config, tmp_path: Path) -> None:
    """qa_capture stores the QUESTION as the name, so a real achievement can
    be called 'Have you shipped an AI/LLM product?' — right kind, right
    evidence, prompt where its name should be (#282)."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        before = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )

        renamed, was = rename_item(
            before.item_id[:8], "  Shipped the search rewrite  ", workspace, storage
        )

        assert was == "Search rewrite"
        assert renamed.name == "Shipped the search rewrite"  # trimmed
        assert renamed.item_id == before.item_id
        assert renamed.kind is before.kind
        assert renamed.evidence == before.evidence
        # The dedup key follows the name, and lives in a column as well as
        # the payload — the invariant #273 had to establish.
        assert _kind_column(storage, before.item_id) == before.kind.value
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT name_key FROM profile_items WHERE item_id = ?", (before.item_id,)
        ).fetchone()
        assert row[0] == "shipped the search rewrite"


def test_rename_refuses_a_collision_an_empty_name_and_a_no_op(
    workspace: Config, tmp_path: Path
) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        target = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )
        twin = target.model_copy(update={"item_id": "twin-0002", "name": "Taken already"})
        storage.add_profile_item(twin)

        with pytest.raises(IngestError, match="conflict to resolve"):
            rename_item(target.item_id[:8], "Taken already", workspace, storage)
        with pytest.raises(IngestError, match="empty"):
            rename_item(target.item_id[:8], "   ", workspace, storage)
        # Case and spacing alone are not a rename: name_key is unchanged.
        with pytest.raises(IngestError, match="already named"):
            rename_item(target.item_id[:8], "search   REWRITE", workspace, storage)
        assert storage.get_profile_item(target.item_id).name == "Search rewrite"


# --- amend: the author revising their own answer (RFC-071, issue #381) ------


def _form_capture(config: Config, storage: Storage, why: str) -> ProfileItem:
    """One interview capture that arrived through the interview form (#287):
    the person's own words, no intensity, and the provenance the ingest
    stamps on it — the exact shape #381 says cannot currently be fixed."""
    capture_interview_reaction("values_pro", "Ada Lovelace", why, config, storage, via_form=True)
    return next(
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW and item.status is ItemStatus.ACTIVE
    )


def test_amend_keeps_the_id_the_source_record_and_the_form_provenance(workspace: Config) -> None:
    """What delete-and-recapture costs, and what amend must therefore keep:
    the item id anything citing it points at, the source record the answer
    arrived on, and #287's (answered in a form) provenance."""
    with Storage(workspace.db_path) as storage:
        before = _form_capture(workspace, storage, "She saw the machine could do more than sums.")
        original_record_id = before.evidence[0].source_record_id

        amended, was = amend_item(
            before.item_id[:8],
            workspace,
            storage,
            why="She saw that the machine could do more than arithmetic.",
        )

        assert was.detail == before.detail
        assert amended.item_id == before.item_id
        assert amended.name == before.name and amended.subtype == before.subtype
        # Provenance: still a form answer, on the form's own source record.
        assert amended.extracted_by == FORM_EXTRACTOR
        assert amended.classification is before.classification
        record = storage.get_source_record(original_record_id)
        assert record is not None and record.source_type == FORM_SOURCE_TYPE
        # The superseded answer keeps naming that record, so the item still
        # traces to where it came from rather than orphaning it.
        assert amended.revisions[-1].evidence[0].source_record_id == original_record_id
        assert amended.revisions[-1].detail == before.detail
        # And the item the storage layer holds is the amended one.
        stored = storage.get_profile_item(before.item_id)
        assert stored is not None
        assert stored.detail == "She saw that the machine could do more than arithmetic."


def test_an_amended_why_resolves_against_a_note_somebody_can_read(workspace: Config) -> None:
    """Evidence before assertion, on the one path that rewrites a quote.

    The original note is immutable — its content hash IS the source
    record's identity — so an amended sentence needs a note of its own, or
    the profile asserts a quote appearing in no document. The amendment
    record also joins the ORIGINAL's document lineage (RFC-028), so a later
    conversational re-capture of the same target supersedes it cleanly
    instead of landing as a cross-source conflict.
    """
    with Storage(workspace.db_path) as storage:
        before = _form_capture(workspace, storage, "She saw the machine could do more than sums.")
        original = storage.get_source_record(before.evidence[0].source_record_id)
        assert original is not None

        amended, _was = amend_item(
            before.item_id[:8], workspace, storage, why="Ada saw a general-purpose machine."
        )

        record = storage.get_source_record(amended.evidence[0].source_record_id)
        assert record is not None
        assert record.record_id != original.record_id
        assert record.source_type == AMENDMENT_SOURCE_TYPE
        assert record.document_key == original.document_key
        note = (workspace.data_dir / record.source_locator).read_text(encoding="utf-8")
        assert locate_quote("Ada saw a general-purpose machine.", note) is not None
        # The trail is on disk as well as in the row: the note names the
        # record it amends and the words it replaced.
        assert original.record_id in note
        assert "She saw the machine could do more than sums." in note
        # The original note is untouched — that is the point of not rewriting it.
        original_note = (workspace.data_dir / original.source_locator).read_text(encoding="utf-8")
        assert "She saw the machine could do more than sums." in original_note
        assert "Ada saw a general-purpose machine." not in original_note


def test_the_revision_is_not_a_second_evidence_span(workspace: Config) -> None:
    """A person restating themselves is not a second voucher (#336's rule).

    Keeping the superseded wording in `evidence` would make everything
    that counts spans — the profile page's 'one quote' flag, persist_items'
    merge arithmetic — read the person's own earlier sentence as
    corroboration by a second source.
    """
    with Storage(workspace.db_path) as storage:
        before = _form_capture(workspace, storage, "First wording.")
        amended, _was = amend_item(before.item_id[:8], workspace, storage, why="Second wording.")

        assert len(amended.evidence) == 1
        assert amended.evidence[0].quote == "Second wording."
        assert len(amended.revisions) == 1
        # Amending twice appends; the trail is oldest first.
        again, _was = amend_item(before.item_id[:8], workspace, storage, why="Third wording.")
        assert len(again.evidence) == 1
        assert [revision.detail for revision in again.revisions] == [
            "First wording.",
            "Second wording.",
        ]


def test_an_amended_form_capture_still_says_it_was_answered_in_a_form(workspace: Config) -> None:
    """Both facts are load-bearing: the sentence was reworded, and it still
    arrived in a form months earlier, offline, with no follow-up asked."""
    with Storage(workspace.db_path) as storage:
        before = _form_capture(workspace, storage, "Machines can do more than sums.")
        listing = render_profile_listing(storage.list_profile_items())
        assert "(answered in a form)" in listing and "(revised)" not in listing

        amend_item(before.item_id[:8], workspace, storage, why="Machines can do more than that.")

        listing = render_profile_listing(storage.list_profile_items())
        assert "(answered in a form) (revised)" in listing
        assert "Machines can do more than that." in listing


def test_amend_adds_the_intensity_a_form_never_collected(workspace: Config) -> None:
    """A form collects no intensity (RFC-069), and intensity is the
    magnitude the whole values score rests on — so a form capture is
    weightless until somebody can add one without destroying the capture."""
    with Storage(workspace.db_path) as storage:
        before = _form_capture(workspace, storage, "She saw further than the machine's builders.")
        assert before.intensity is None

        amended, _was = amend_item(
            before.item_id[:8],
            workspace,
            storage,
            intensity="strong",
            value_statement="I value people who see past the tool in front of them.",
        )

        assert amended.intensity is SentimentIntensity.STRONG
        assert amended.value_statement.startswith("I value people")
        # A field-only amendment leaves the answer and its evidence alone:
        # nothing was rewritten, so no new source record was needed.
        assert amended.detail == before.detail
        assert amended.evidence == before.evidence
        assert amended.revisions[-1].intensity is None
        assert amended.revisions[-1].value_statement == ""


def test_amend_refuses_a_document_sourced_item_and_says_what_to_do_instead(
    workspace: Config, tmp_path: Path
) -> None:
    """The line RFC-071 holds. Letting the author of an interview answer
    revise it does not weaken evidence discipline; letting anybody edit a
    sentence lifted out of a resume guts it."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        achievement = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )

        with pytest.raises(IngestError) as excinfo:
            amend_item(achievement.item_id[:8], workspace, storage, why="Something else.")

        message = str(excinfo.value)
        assert "not an interview answer of your own" in message
        assert "resume.md" in message  # names the document the quote came from
        assert "re-ingest" in message  # and the path that does work
        untouched = storage.get_profile_item(achievement.item_id)
        assert untouched is not None and untouched.revisions == []
        assert untouched.detail == achievement.detail


def test_amend_refuses_nothing_to_change_an_empty_why_and_a_garbled_scale(
    workspace: Config,
) -> None:
    with Storage(workspace.db_path) as storage:
        item = _form_capture(workspace, storage, "The original answer.")

        with pytest.raises(IngestError, match="nothing to amend"):
            amend_item(item.item_id[:8], workspace, storage)
        with pytest.raises(IngestError, match="has to say why"):
            amend_item(item.item_id[:8], workspace, storage, why="   ")
        with pytest.raises(IngestError, match="already reads exactly that"):
            amend_item(item.item_id[:8], workspace, storage, why="The original answer.")
        with pytest.raises(IngestError, match="unknown intensity"):
            amend_item(item.item_id[:8], workspace, storage, intensity="volcanic")
        with pytest.raises(IngestError, match="unknown company_reason"):
            amend_item(item.item_id[:8], workspace, storage, company_reason="vibes")
        # Nothing was written on any of those paths.
        stored = storage.get_profile_item(item.item_id)
        assert stored is not None and stored.detail == "The original answer."
        assert stored.revisions == []


def test_amend_echoes_exactly_what_it_stored(workspace: Config) -> None:
    """The CLI and the MCP tool both report the change through this, because
    'Amended.' cannot tell somebody whether the field they meant to change
    is the field that changed."""
    with Storage(workspace.db_path) as storage:
        before = _form_capture(workspace, storage, "First wording.")
        amended, was = amend_item(
            before.item_id[:8], workspace, storage, why="Second wording.", intensity="mild"
        )
    summary = describe_amendment(amended, was)
    assert 'why is now "Second wording." (was "First wording.")' in summary
    assert "intensity unset -> mild" in summary


def test_mcp_profile_manage_amend_roundtrip(workspace: Config) -> None:
    from wingman.mcp_server import profile_manage

    with Storage(workspace.db_path) as storage:
        item = _form_capture(workspace, storage, "The first answer.")

    response = profile_manage("amend", item.item_id[:8], why="The answer, said better.")
    assert "Amended" in response and "said better" in response
    assert "(revised)" in profile_manage("list")
    assert "failed" in profile_manage("amend", item.item_id[:8])
    assert "amend" in profile_manage("nope")


def test_the_amend_docstring_carries_the_echo_before_save_protocol() -> None:
    """BP-06, the same gate interview_react and qa_capture carry: the model
    shows the exact text it will store and saves only on confirmation. This
    tool can rewrite an evidence quote, so it is the last place a silent
    paraphrase should be possible."""
    from wingman.mcp_server import profile_manage as profile_manage_tool

    doc = (profile_manage_tool.__doc__ or "").lower()
    assert "echo verbatim" in doc
    assert "only after they confirm" in doc
    assert "never save your own tidied" in doc
    assert "these are the person's own" in doc


def test_cli_profile_amend(workspace: Config) -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app

    with Storage(workspace.db_path) as storage:
        item = _form_capture(workspace, storage, "The first answer.")

    result = CliRunner().invoke(
        app,
        [
            "profile",
            "amend",
            item.item_id[:8],
            "--why",
            "The answer, said better.",
            "--intensity",
            "strong",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "said better" in result.output
    assert "intensity unset -> strong" in result.output

    failed = CliRunner().invoke(app, ["profile", "amend", "zzzzzzzz", "--why", "Nope."])
    assert failed.exit_code == 1


def _active_skill(storage: Storage) -> ProfileItem:
    """The BigQuery skill's active row (single evidence span): _ingest_twice's
    two documents disagree on its *detail*, so it lands as a genuine
    RFC-027 conflict rather than an evidence-merge — unlike 'Search
    rewrite', whose identical detail on both documents merges into ONE
    item with TWO evidence spans (see test_correct_refuses_an_ambiguous_
    quote_across_multiple_spans below, which relies on exactly that)."""
    return next(
        item
        for item in storage.list_profile_items()
        if item.name == "BigQuery" and item.status is ItemStatus.ACTIVE
    )


def test_correct_updates_the_item_and_source_record_atomically(
    workspace: Config, tmp_path: Path
) -> None:
    """The core acceptance criterion from #487: fixing a transcription error
    in document-sourced evidence moves the item's quote AND its source
    record together, so the two never diverge."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)
        original_record_id = skill.evidence[0].source_record_id

        corrected, before = correct_item(
            skill.item_id[:8],
            "Skills: BigQuery daily.",
            "Skills: BigQuery weekly.",
            workspace,
            storage,
        )

        assert before.item_id == skill.item_id
        assert corrected.item_id == skill.item_id
        assert corrected.evidence[0].quote == "Skills: BigQuery weekly."
        assert corrected.evidence[0].source_record_id != original_record_id
        # detail didn't equal the old quote on this item, so it's untouched —
        # correct only rewrites what it was actually told to rewrite.
        assert corrected.detail == before.detail

        new_record = storage.get_source_record(corrected.evidence[0].source_record_id)
        assert new_record is not None
        assert new_record.source_type == CORRECTION_SOURCE_TYPE
        original_record = storage.get_source_record(original_record_id)
        assert original_record is not None
        # Same RFC-028 lineage as the claim it fixes.
        assert new_record.document_key == original_record.document_key

        # The original note is untouched — the point of not rewriting it.
        original_note = (workspace.data_dir / original_record.source_locator).read_text(
            encoding="utf-8"
        )
        assert "Skills: BigQuery daily." in original_note
        assert "Skills: BigQuery weekly." not in original_note
        # The new note names the record it corrects and the text it replaced.
        new_note = (workspace.data_dir / new_record.source_locator).read_text(encoding="utf-8")
        assert original_record_id in new_note
        assert "Skills: BigQuery daily." in new_note

        stored = storage.get_profile_item(skill.item_id)
        assert stored is not None
        assert stored.evidence[0].quote == corrected.evidence[0].quote
        assert stored.evidence[0].source_record_id == corrected.evidence[0].source_record_id


def test_correct_syncs_detail_when_it_held_the_same_text(workspace: Config) -> None:
    """A qa_capture/voice-dictated item stores its answer in BOTH `detail`
    and `evidence[0].quote` (the same string, by construction) — exactly
    the #487 concrete case (a Wispr mishearing in a voice-dictated
    capture). Correcting only the evidence span there would leave the
    listing, which renders `detail`, still showing the mistranscribed
    word."""
    from wingman.application.qa_capture import capture_qa

    with Storage(workspace.db_path) as storage:
        capture_qa(
            "Who led the Infinitus rollout?",
            "Erica Chen led the rollout end to end.",
            workspace,
            storage,
        )
        item = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Who led the Infinitus rollout?"
        )
        assert item.detail == item.evidence[0].quote == "Erica Chen led the rollout end to end."

        corrected, before = correct_item(
            item.item_id[:8],
            "Erica Chen led the rollout end to end.",
            "Arika Chen led the rollout end to end.",
            workspace,
            storage,
        )

        assert before.detail == "Erica Chen led the rollout end to end."
        assert corrected.detail == "Arika Chen led the rollout end to end."
        assert corrected.evidence[0].quote == "Arika Chen led the rollout end to end."


def test_correct_retains_the_old_wording_as_a_revision(workspace: Config, tmp_path: Path) -> None:
    """The prior wording is kept, not replaced — the same ItemRevision
    mechanism amend uses, never a parallel one."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)

        corrected, _before = correct_item(
            skill.item_id[:8],
            "Skills: BigQuery daily.",
            "Skills: BigQuery every day.",
            workspace,
            storage,
        )

        assert len(corrected.revisions) == 1
        revision = corrected.revisions[0]
        assert revision.detail == skill.detail
        assert revision.evidence[0].quote == "Skills: BigQuery daily."
        assert revision.evidence[0].source_record_id == skill.evidence[0].source_record_id
        # Not a second evidence span (#336's rule, exactly as amend's revisions).
        assert len(corrected.evidence) == 1


def test_correct_marks_the_item_corrected_in_listing(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)
        listing_before = render_profile_listing(storage.list_profile_items())
        assert "(corrected)" not in listing_before

        correct_item(
            skill.item_id[:8],
            "Skills: BigQuery daily.",
            "Skills: BigQuery constantly.",
            workspace,
            storage,
        )

        listing_after = render_profile_listing(storage.list_profile_items())
        assert "(corrected)" in listing_after
        # amend's marker, not correct's — the two never share an item's kind.
        assert "(revised)" not in listing_after


def test_preview_correction_does_not_mutate_anything(workspace: Config, tmp_path: Path) -> None:
    """The confirmation gate actually gates: an uncommitted preview call
    reports the diff but writes nothing."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)
        before_count = len(storage.list_profile_items())

        preview = preview_correction(
            skill.item_id[:8],
            "Skills: BigQuery daily.",
            "Skills: BigQuery frequently.",
            storage,
        )

        assert "Skills: BigQuery daily." in preview
        assert "Skills: BigQuery frequently." in preview
        assert len(storage.list_profile_items()) == before_count
        stored = storage.get_profile_item(skill.item_id)
        assert stored is not None
        assert stored.evidence[0].quote == "Skills: BigQuery daily."
        assert stored.revisions == []


def test_correct_refuses_an_interview_item_and_points_at_amend(workspace: Config) -> None:
    """correct is amend's mirror image: an interview capture has its own
    path, and letting correct also touch it would be a second, competing
    mutation mechanism for the same capture."""
    with Storage(workspace.db_path) as storage:
        item = _form_capture(workspace, storage, "Machines can do more than sums.")

        with pytest.raises(IngestError) as excinfo:
            correct_item(
                item.item_id[:8],
                "Machines can do more than sums.",
                "Machines can compute more than sums.",
                workspace,
                storage,
            )

        message = str(excinfo.value)
        assert "interview capture" in message
        assert "profile amend" in message
        untouched = storage.get_profile_item(item.item_id)
        assert untouched is not None and untouched.revisions == []


def test_correct_refuses_text_not_found_verbatim(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)

        with pytest.raises(IngestError, match="does not contain"):
            correct_item(skill.item_id[:8], "Something never said.", "Fixed.", workspace, storage)


def test_correct_refuses_an_ambiguous_quote_across_multiple_spans(
    workspace: Config, tmp_path: Path
) -> None:
    """'Search rewrite' is asserted identically by both ingested documents,
    so persist_items merges them into ONE item with TWO evidence spans
    citing the same quote (real corroboration, unlike a revision) — correct
    refuses rather than guess which span the caller meant."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        achievement = next(
            item
            for item in storage.list_profile_items()
            if item.name == "Search rewrite" and item.status is ItemStatus.ACTIVE
        )
        assert len(achievement.evidence) == 2

        with pytest.raises(IngestError, match="appears in 2 evidence spans"):
            correct_item(
                achievement.item_id[:8],
                "Shipped the search rewrite.",
                "Shipped the search-rewrite project.",
                workspace,
                storage,
            )


def test_correct_refuses_a_no_op(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)

        with pytest.raises(IngestError, match="already reads exactly that"):
            correct_item(
                skill.item_id[:8],
                "Skills: BigQuery daily.",
                "Skills: BigQuery daily.",
                workspace,
                storage,
            )


def test_correct_refuses_empty_old_or_new_text(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)

        with pytest.raises(IngestError, match="old_text is empty"):
            correct_item(skill.item_id[:8], "   ", "Fixed.", workspace, storage)
        with pytest.raises(IngestError, match="new_text is empty"):
            correct_item(
                skill.item_id[:8],
                "Skills: BigQuery daily.",
                "  ",
                workspace,
                storage,
            )


def test_correct_refuses_a_superseded_item(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)
        storage.update_profile_item(skill.model_copy(update={"status": ItemStatus.SUPERSEDED}))

        with pytest.raises(IngestError, match="superseded"):
            correct_item(
                skill.item_id[:8],
                "Skills: BigQuery daily.",
                "Skills: BigQuery constantly.",
                workspace,
                storage,
            )


def test_correct_echoes_exactly_what_it_changed(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        skill = _active_skill(storage)

        corrected, before = correct_item(
            skill.item_id[:8],
            "Skills: BigQuery daily.",
            "Skills: BigQuery every single day.",
            workspace,
            storage,
        )

    summary = describe_correction(corrected, before)
    assert (
        'evidence is now "Skills: BigQuery every single day." (was "Skills: BigQuery daily.")'
        in summary
    )
    # detail was untouched on this item, so it does not appear in the diff.
    assert "detail is now" not in summary


def test_mcp_profile_manage_correct_roundtrip(workspace: Config, tmp_path: Path) -> None:
    from wingman.mcp_server import profile_manage

    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        item_id = _active_skill(storage).item_id[:8]

    preview = profile_manage(
        "correct",
        item_id,
        old_text="Skills: BigQuery daily.",
        new_text="Skills: BigQuery weekly.",
    )
    assert "Not corrected" in preview
    assert "Skills: BigQuery weekly." in preview
    # confirmed defaults to false: nothing was written.
    assert "(corrected)" not in profile_manage("list")

    applied = profile_manage(
        "correct",
        item_id,
        old_text="Skills: BigQuery daily.",
        new_text="Skills: BigQuery weekly.",
        confirmed=True,
    )
    assert "Corrected" in applied
    assert "(corrected)" in profile_manage("list")
    assert "failed" in profile_manage(
        "correct", item_id, old_text="Skills: BigQuery daily.", confirmed=True
    )
    assert "correct" in profile_manage("nope")


def test_correct_docstring_carries_the_confirm_protocol() -> None:
    from wingman.mcp_server import profile_manage as profile_manage_tool

    doc = (profile_manage_tool.__doc__ or "").lower()
    assert "confirmed=false first" in doc
    assert "confirmed=true after they explicitly approve" in doc


def test_cli_profile_correct(workspace: Config, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app

    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        item_id = _active_skill(storage).item_id[:8]

    result = CliRunner().invoke(
        app,
        [
            "profile",
            "correct",
            item_id,
            "Skills: BigQuery daily.",
            "Skills: BigQuery routinely.",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Corrected" in result.output
    assert "BigQuery routinely" in result.output

    failed = CliRunner().invoke(app, ["profile", "correct", "zzzzzzzz", "Nope.", "Fixed.", "--yes"])
    assert failed.exit_code == 1


def test_cli_profile_correct_declined_prompt_does_not_mutate(
    workspace: Config, tmp_path: Path
) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        item_id = _active_skill(storage).item_id

    from typer.testing import CliRunner

    from wingman.cli.main import app

    result = CliRunner().invoke(
        app,
        [
            "profile",
            "correct",
            item_id[:8],
            "Skills: BigQuery daily.",
            "Skills: BigQuery routinely.",
        ],
        input="n\n",
    )
    assert result.exit_code != 0

    with Storage(workspace.db_path) as storage:
        stored = storage.get_profile_item(item_id)
        assert stored is not None and stored.revisions == []
