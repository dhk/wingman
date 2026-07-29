"""Profile Bootstrap (docs/PROFILE-BOOTSTRAP-DESIGN.md): interview
reactions (fetch a target for provenance) and nominations (name a target,
no fetch) as evidence — either way, 'why' is the only evidence stored."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.interview import (
    INTERVIEW_SOURCE_TYPE,
    capture_interview_reaction,
    interview_document_key,
    render_interview_reaction,
)
from wingman.domain.profile import ItemStatus, ProfileItemKind
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

PAGE = b"<html><head><title>An Essay</title></head><body><p>Some argument.</p></body></html>"
WHY_AGREE = "This matches how I think about it — I've seen the same pattern firsthand."
WHY_DISAGREE = "I don't buy the premise; the incentives cut the other way in practice."


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def test_capture_url_stimulus_creates_source_record_and_item(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        report = capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            WHY_AGREE,
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        assert report.outcome == "saved"
        assert report.title == "An Essay"
        items = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        assert len(items) == 1
        item = items[0]
        assert item.kind is ProfileItemKind.INTERVIEW
        assert item.subtype == "alignment_of_perspective_agree"
        assert item.detail == WHY_AGREE
        assert item.extracted_by == "user" and item.confidence == 1.0
        # the stimulus's own words never become the evidence quote — only 'why' does
        assert item.evidence[0].quote == WHY_AGREE
        record = storage.get_source_record(item.evidence[0].source_record_id)
        assert record is not None
        assert record.source_type == INTERVIEW_SOURCE_TYPE
        # a reaction's target IS a real, dereferenceable location — used as-is
        assert record.source_locator == "https://example.com/essay"
        assert record.document_key == interview_document_key(
            "alignment_of_perspective_agree", "https://example.com/essay"
        )


def test_capture_local_file_stimulus(workspace: Config, tmp_path: Path) -> None:
    stimulus = tmp_path / "post.md"
    stimulus.write_text("# A post\n\nSome argument worth reacting to.\n", encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        report = capture_interview_reaction(
            "alignment_of_perspective_disagree", str(stimulus), WHY_DISAGREE, workspace, storage
        )
        assert report.outcome == "saved"
        assert report.title == "post.md"
        items = storage.list_profile_items()
        assert items[0].subtype == "alignment_of_perspective_disagree"
        assert items[0].detail == WHY_DISAGREE


def test_reacting_to_same_stimulus_and_subtype_supersedes(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            WHY_AGREE,
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        report = capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            "A revised, better reason.",
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        assert "superseded" in report.outcome
        active = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        superseded = [i for i in storage.list_profile_items() if i.status is ItemStatus.SUPERSEDED]
        assert [i.detail for i in active] == ["A revised, better reason."]
        assert [i.detail for i in superseded] == [WHY_AGREE]
        # identical re-capture changes nothing
        again = capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            "A revised, better reason.",
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        assert "already captured" in again.outcome


def test_different_subtype_same_stimulus_is_a_separate_item(workspace: Config) -> None:
    """Agreeing and disagreeing with the SAME piece under different subtypes
    (e.g. across two different interview passes) must not collide — only a
    same-subtype re-reaction supersedes."""
    with Storage(workspace.db_path) as storage:
        capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            WHY_AGREE,
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        capture_interview_reaction(
            "alignment_of_perspective_disagree",
            "https://example.com/essay",
            WHY_DISAGREE,
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        active = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        assert {i.subtype for i in active} == {
            "alignment_of_perspective_agree",
            "alignment_of_perspective_disagree",
        }


def test_capture_validates_inputs(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="unknown subtype"):
            capture_interview_reaction(
                "vibe", "https://example.com/x", WHY_AGREE, workspace, storage
            )
        with pytest.raises(IngestError, match="required"):
            capture_interview_reaction(
                "alignment_of_perspective_agree", "https://example.com/x", "  ", workspace, storage
            )
        with pytest.raises(IngestError, match="neither an https"):
            capture_interview_reaction(
                "alignment_of_perspective_agree", "/no/such/file.md", WHY_AGREE, workspace, storage
            )


WHY_PRO = "She's spent decades arguing for something and never flinched on it."
WHY_CON = "He built his career on a premise I think is actively harmful."
WHY_WALMART_CON = "They destroy local communities on their way to scale."
WHY_WALMART_PRO = "Their supply-chain logistics are genuinely a marvel of operations."


def test_values_pro_and_con_nominations_are_captured_with_no_fetch(workspace: Config) -> None:
    """Nominations never fetch anything — 'target' is just a name, unlike
    the reaction subtypes above."""
    with Storage(workspace.db_path) as storage:
        pro = capture_interview_reaction("values_pro", "Jane Goodall", WHY_PRO, workspace, storage)
        con = capture_interview_reaction(
            "values_con", "A. Public Figure", WHY_CON, workspace, storage
        )
        assert pro.outcome == "saved" and pro.title is None
        assert con.outcome == "saved" and con.title is None
        items = {
            i.subtype: i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE
        }
        assert items["values_pro"].name == "values_pro: Jane Goodall"
        assert items["values_pro"].detail == WHY_PRO
        assert items["values_con"].detail == WHY_CON
        # a nomination's target is a bare name, not a real location — the
        # note is archived to the inbox instead, unlike a reaction
        record = storage.get_source_record(items["values_pro"].evidence[0].source_record_id)
        assert record is not None
        note = workspace.data_dir / record.source_locator
        assert note.exists() and WHY_PRO in note.read_text(encoding="utf-8")


def test_values_con_excludes_hitler(workspace: Config) -> None:
    """Too easy a nomination to discriminate anything about actual values —
    docs/PROFILE-BOOTSTRAP-DESIGN.md's explicit exclusion."""
    with Storage(workspace.db_path) as storage:
        for name in ("Hitler", "hitler", "  HITLER  ", "Adolf Hitler", "adolf   hitler"):
            with pytest.raises(IngestError, match="excluded from values_con"):
                capture_interview_reaction("values_con", name, WHY_CON, workspace, storage)
        # the exclusion is specific to values_con — the identical literal
        # string is accepted under values_pro or the company fallback (no
        # exclusion list there at all, per the design's resolution)
        assert (
            capture_interview_reaction("values_pro", "Hitler", WHY_PRO, workspace, storage).outcome
            == "saved"
        )
        assert (
            capture_interview_reaction(
                "values_fallback_con", "Hitler", WHY_WALMART_CON, workspace, storage
            ).outcome
            == "saved"
        )


def test_values_fallback_nominates_a_company_instead_of_a_person(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        con = capture_interview_reaction(
            "values_fallback_con", "Walmart", WHY_WALMART_CON, workspace, storage
        )
        pro = capture_interview_reaction(
            "values_fallback_pro", "A Local Co-op", WHY_WALMART_PRO, workspace, storage
        )
        assert con.outcome == "saved" and pro.outcome == "saved"
        items = {
            i.subtype: i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE
        }
        assert items["values_fallback_con"].detail == WHY_WALMART_CON


def test_renominating_the_same_person_supersedes(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        capture_interview_reaction("values_pro", "Jane Goodall", WHY_PRO, workspace, storage)
        report = capture_interview_reaction(
            "values_pro", "Jane Goodall", "A different, better reason.", workspace, storage
        )
        assert "superseded" in report.outcome
        active = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        assert [i.detail for i in active] == ["A different, better reason."]


WHY_MISSION_PRO = "Their approach to firefighting logistics matches how I think teams should run."
WHY_MISSION_CON = "I think their core business model is extractive by design."


def test_mission_alignment_requires_primary_purpose(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="primary_purpose is required"):
            capture_interview_reaction(
                "mission_alignment_pro", "The Fire Department", WHY_MISSION_PRO, workspace, storage
            )
        with pytest.raises(IngestError, match="primary_purpose is required"):
            capture_interview_reaction(
                "mission_alignment_pro",
                "The Fire Department",
                WHY_MISSION_PRO,
                workspace,
                storage,
                primary_purpose="   ",
            )
        # not required for any other subtype, reaction or nomination
        assert (
            capture_interview_reaction(
                "values_pro", "Jane Goodall", WHY_PRO, workspace, storage
            ).outcome
            == "saved"
        )


def test_mission_alignment_captures_purpose_as_context_not_evidence(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        report = capture_interview_reaction(
            "mission_alignment_pro",
            "The Fire Department",
            WHY_MISSION_PRO,
            workspace,
            storage,
            primary_purpose="Puts out fires",
        )
        assert report.outcome == "saved" and report.title is None
        items = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        item = items[0]
        # the evidence quote is ONLY the reasoning — the purpose answer
        # never becomes the (or part of the) evidence itself
        assert item.evidence[0].quote == WHY_MISSION_PRO
        assert item.detail == WHY_MISSION_PRO
        assert "Puts out fires" not in item.detail
        assert "Puts out fires" not in item.evidence[0].quote
        # it IS retained as retrievable context in the inbox note (unlike
        # v1 slice 1, which had no primary-purpose concept to lose)
        record = storage.get_source_record(item.evidence[0].source_record_id)
        assert record is not None
        note = workspace.data_dir / record.source_locator
        note_text = note.read_text(encoding="utf-8")
        assert "Puts out fires" in note_text
        assert WHY_MISSION_PRO in note_text
        assert record.document_key == interview_document_key(
            "mission_alignment_pro", "The Fire Department"
        )


def test_mission_alignment_con_has_no_exclusion_list(workspace: Config) -> None:
    """Unlike values_con, there's no equivalent to 'no Hitler' here — see
    docs/PROFILE-BOOTSTRAP-DESIGN.md's resolution of why."""
    with Storage(workspace.db_path) as storage:
        report = capture_interview_reaction(
            "mission_alignment_con",
            "Hitler Youth Reenactment Society",
            WHY_MISSION_CON,
            workspace,
            storage,
            primary_purpose="Historical reenactment",
        )
        assert report.outcome == "saved"


def test_render_interview_reaction() -> None:
    from wingman.application.interview import InterviewReactionReport
    from wingman.application.profile_store import ItemCounts

    report = InterviewReactionReport(
        subtype="alignment_of_perspective_agree",
        target="https://example.com/essay",
        title="An Essay",
        outcome="saved",
        counts=ItemCounts(accepted=1),
    )
    rendered = render_interview_reaction(report)
    assert "alignment_of_perspective_agree" in rendered
    assert "An Essay" in rendered and "saved" in rendered


def test_captured_reactions_are_reviewable_via_profile_list(workspace: Config) -> None:
    """v0's own requirement: 'reactions are stored and reviewable' — no
    bespoke listing command needed, 'wingman profile list' already groups
    by kind dynamically."""
    from wingman.application.profile_manage import render_profile_listing

    with Storage(workspace.db_path) as storage:
        capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            WHY_AGREE,
            workspace,
            storage,
            fetcher=lambda url: PAGE,
        )
        listing = render_profile_listing(storage.list_profile_items())
    assert "Interviews:" in listing
    assert WHY_AGREE in listing


def test_mcp_interview_react(workspace: Config) -> None:
    from wingman.mcp_server import interview_react as interview_react_tool

    result = interview_react_tool(
        "alignment_of_perspective_agree", "https://example.com/essay", WHY_AGREE
    )
    # the MCP tool uses the real fetch_url, unreachable in tests — assert the
    # failure path is honest rather than silently swallowed
    assert "interview capture failed" in result
    assert "echo verbatim" in (interview_react_tool.__doc__ or "").lower()


def test_mcp_interview_react_reports_position_only_past_half_the_cap(
    workspace: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-0001 BP-05/Q3: position surfaces from the halfway point, never
    from zero, and never only as an error at the cap."""
    from wingman.mcp_server import interview_react as interview_react_tool

    monkeypatch.setenv("WINGMAN_INTERVIEW_MAX_PER_SUBTYPE", "4")
    first = interview_react_tool("values_pro", "Jane Goodall", WHY_PRO)
    assert "Position:" not in first  # 1 of 4 — below half, stay quiet

    interview_react_tool("values_pro", "A. Nother Name", WHY_PRO)
    third = interview_react_tool("values_pro", "A Third Name", WHY_PRO)
    assert "Position: 3 of 4 captured for values_pro so far." in third


def test_mcp_perspectives_start_returns_branching_guidance(workspace: Config) -> None:
    from wingman.mcp_server import perspectives_start

    result = perspectives_start()
    assert "writing to add" in result
    assert "interview_react" in result
    assert "Pick up where I left off" not in result  # nothing captured yet


def test_mcp_perspectives_start_promotes_resume_when_captures_exist(
    workspace: Config,
) -> None:
    from wingman.mcp_server import perspectives_start

    with Storage(workspace.db_path) as storage:
        capture_interview_reaction("values_pro", "Jane Goodall", WHY_PRO, workspace, storage)
        capture_interview_reaction("values_con", "A. Public Figure", WHY_CON, workspace, storage)

    result = perspectives_start()
    assert result.startswith(
        "Perspectives — profile onboarding entry point."
    )  # explainer framing still leads
    assert "Pick up where I left off" in result
    assert "2 Values nominations" in result


def test_url_submission_over_size_limit_is_rejected(workspace: Config) -> None:
    from wingman.application.interview import INTERVIEW_MAX_SUBMISSION_BYTES

    oversized = b"x" * (INTERVIEW_MAX_SUBMISSION_BYTES + 1)
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="interview submission limit"):
            capture_interview_reaction(
                "alignment_of_perspective_agree",
                "https://example.com/huge",
                WHY_AGREE,
                workspace,
                storage,
                fetcher=lambda url: oversized,
            )


def test_local_file_submission_over_size_limit_is_rejected(
    workspace: Config, tmp_path: Path
) -> None:
    from wingman.application.interview import INTERVIEW_MAX_SUBMISSION_BYTES

    stimulus = tmp_path / "huge.md"
    stimulus.write_bytes(b"x" * (INTERVIEW_MAX_SUBMISSION_BYTES + 1))
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="interview submission limit"):
            capture_interview_reaction(
                "alignment_of_perspective_agree", str(stimulus), WHY_AGREE, workspace, storage
            )


def test_new_targets_beyond_the_per_subtype_cap_are_rejected(
    workspace: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINGMAN_INTERVIEW_MAX_PER_SUBTYPE", "2")
    with Storage(workspace.db_path) as storage:
        capture_interview_reaction("values_pro", "Jane Goodall", WHY_PRO, workspace, storage)
        capture_interview_reaction("values_pro", "A. Nother Name", WHY_PRO, workspace, storage)
        with pytest.raises(IngestError, match="already has 2 captures"):
            capture_interview_reaction("values_pro", "A Third Name", WHY_PRO, workspace, storage)
        # a different subtype has its own, independent cap
        capture_interview_reaction("values_con", "A. Public Figure", WHY_CON, workspace, storage)


def test_recapturing_an_existing_target_is_exempt_from_the_cap(
    workspace: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINGMAN_INTERVIEW_MAX_PER_SUBTYPE", "1")
    with Storage(workspace.db_path) as storage:
        capture_interview_reaction("values_pro", "Jane Goodall", WHY_PRO, workspace, storage)
        # updating the SAME target stays allowed even once the subtype is at its cap
        updated = capture_interview_reaction(
            "values_pro", "Jane Goodall", WHY_PRO + " Still true.", workspace, storage
        )
        assert "superseded" in updated.outcome
        # but a genuinely new target is still refused
        with pytest.raises(IngestError, match="already has 1 captures"):
            capture_interview_reaction("values_pro", "Someone Else", WHY_PRO, workspace, storage)
