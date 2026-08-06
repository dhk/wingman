"""Career-profile management (RFC-027): list, rm, resolve, clear."""

import json
from pathlib import Path

import pytest

from wingman.agents.profile_curator import ProposalParseError, parse_proposal
from wingman.application.ingest import IngestError, ingest_resume
from wingman.application.profile_manage import (
    clear_profile,
    find_item,
    rekind_item,
    remove_item,
    rename_item,
    render_profile_listing,
    resolve_item,
)
from wingman.domain.profile import ItemStatus, ProfileItemKind
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
    """Two ingests whose BigQuery detail differs -> one active + one conflict."""
    resume = tmp_path / "resume.md"
    resume.write_text(RESUME, encoding="utf-8")
    ingest_resume(resume, config, storage, RecordedProvider(_response("")))
    ingest_resume(resume, config, storage, RecordedProvider(_response("Used daily.")))


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
