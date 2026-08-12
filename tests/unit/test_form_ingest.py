"""Ingesting a completed interview form into one named tenant's workspace (#287).

The generator half is tested in test_interview_form.py — this is the other
half: the export comes back keyed by question title, and each answer has to
land where that question's answer belongs, in the workspace the OPERATOR
named, with nothing written before it has been shown.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.form_ingest import (
    FormResponse,
    IngestOutcome,
    IngestPlan,
    apply_plan,
    compose_criteria_document,
    parse_responses,
    plan_ingest,
    questions_from_manifest,
    render_plan,
)
from wingman.application.interview_form import FormQuestion, form_questions, render_manifest
from wingman.application.job_scoring import INTERVIEW_AREAS, criteria_path
from wingman.cli.main import app
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

cli = CliRunner()

# A form built for every destination that exists, so routing can be
# asserted per destination rather than only for the questions today's
# generator happens to emit: no shipped question routes to the answer bank
# yet, the manifest supports one, and so must the ingest.
QUESTIONS = [
    FormQuestion(
        key="criteria_hard_filters",
        title="Job criteria — Hard filters",
        destination="criteria",
    ),
    FormQuestion(
        key="criteria_tie_breaker",
        title="Job criteria — Tie-breaker",
        destination="criteria",
    ),
    FormQuestion(
        key="values_pro",
        title="Name three people, living or dead, you would have dinner with — and why.",
        destination="profile",
        subtype="values_pro",
    ),
    FormQuestion(
        key="mission_alignment_pro",
        title="Name an organization you would be proud to be associated with.",
        destination="profile",
        subtype="mission_alignment_pro",
    ),
    FormQuestion(
        key="notice_period",
        title="What notice do you have to give?",
        destination="profile",
    ),
    FormQuestion(
        key="screen_llm",
        title="Have you shipped an AI/LLM product?",
        destination="answers",
    ),
]

HARD_FILTERS = "Remote-first, nothing below Staff, £120k floor. No gambling, no defence."
TIE_BREAKER = "People. I took less money twice for a better manager and never regretted it."
DINNER = (
    "Ada Lovelace — she saw what a machine could be a century before anyone built one\n"
    "- Terry Pratchett — he was angry about the right things and still funny\n"
)
ORG = (
    "Ordnance Survey — they map the country and publish it — "
    "I like infrastructure nobody notices until it is gone"
)
NOTICE = "Three months, but my current lead would let me go at two."
LLM_ANSWER = "Yes — I shipped the retrieval layer behind our support assistant in 2024."


def _csv(rows: list[dict[str, str]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return buffer.getvalue()


def _full_row(**overrides: str) -> dict[str, str]:
    row = {
        "Timestamp": "2026-08-11 14:02:31",
        QUESTIONS[0].title: HARD_FILTERS,
        QUESTIONS[1].title: TIE_BREAKER,
        QUESTIONS[2].title: DINNER,
        QUESTIONS[3].title: ORG,
        QUESTIONS[4].title: NOTICE,
        QUESTIONS[5].title: LLM_ANSWER,
    }
    row.update(overrides)
    return row


def _write_responses(directory: Path, *rows: dict[str, str], name: str = "responses.csv") -> Path:
    columns = ["Timestamp"] + [question.title for question in QUESTIONS]
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    path = directory / name
    path.write_text(_csv(list(rows), columns), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def _ingest(
    config: Config,
    path: Path,
    submission: int | None = None,
    replace_criteria: bool = False,
) -> tuple[IngestPlan, IngestOutcome]:
    """plan then apply, exactly as the CLI does it."""
    plan = plan_ingest(
        "jason",
        parse_responses(path),
        source=str(path),
        questions=QUESTIONS,
        submission=submission,
    )
    with Storage(config.db_path) as storage:
        outcome = apply_plan(plan, config, storage, replace_criteria=replace_criteria)
    return plan, outcome


def _items(config: Config) -> list[ProfileItem]:
    with Storage(config.db_path) as storage:
        return [item for item in storage.list_profile_items() if item.status is ItemStatus.ACTIVE]


def _reported(plan: IngestPlan) -> str:
    return " ".join(f"{item.what} {item.reason}" for item in plan.skipped)


# --------------------------------------------------------------------------
# Routing: every destination, by title
# --------------------------------------------------------------------------


def test_each_destination_is_routed_by_its_question_title(workspace: Config) -> None:
    """The export carries titles and nothing else, so the title is the whole
    routing key. A criteria answer, a nomination, a profile Q&A and an
    answer-bank question have to land in four different places off one file
    — mis-routing one is #283's 'Willing to be in-office 25%+? — Yes' filed
    as a skill, again."""
    path = _write_responses(workspace.data_dir, _full_row())
    _ingest(workspace, path)

    criteria = criteria_path(workspace).read_text(encoding="utf-8")
    assert HARD_FILTERS in criteria and TIE_BREAKER in criteria

    items = _items(workspace)
    nominations = [item for item in items if item.kind is ProfileItemKind.INTERVIEW]
    assert {item.subtype for item in nominations} == {"values_pro", "mission_alignment_pro"}
    assert {item.name for item in nominations if item.subtype == "values_pro"} == {
        "values_pro: Ada Lovelace",
        "values_pro: Terry Pratchett",
    }
    qa = [item for item in items if item.kind is not ProfileItemKind.INTERVIEW]
    assert [item.name for item in qa] == ["What notice do you have to give?"]
    assert qa[0].detail == NOTICE

    with Storage(workspace.db_path) as storage:
        answers = storage.list_answers()
    assert [record.question for record in answers] == ["Have you shipped an AI/LLM product?"]
    assert answers[0].answer == LLM_ANSWER


def test_a_nomination_line_becomes_a_target_and_a_why(workspace: Config) -> None:
    """'why' is the only thing an interview capture stores as evidence, so
    the split has to be right: the nominee's name is never the quote."""
    path = _write_responses(workspace.data_dir, _full_row())
    _ingest(workspace, path)
    ada = next(item for item in _items(workspace) if item.name == "values_pro: Ada Lovelace")
    assert ada.detail == "she saw what a machine could be a century before anyone built one"
    assert ada.evidence[0].quote == ada.detail


def test_a_bulleted_line_and_a_url_survive_the_split() -> None:
    """People type bullets, and network_admired asks for LinkedIn URLs.
    Splitting on the earliest punctuation of any kind would cut
    'https://...' at its colon and nominate 'https'."""
    question = FormQuestion(
        key="network_admired",
        title="Which people you actually know do you admire, and why?",
        destination="profile",
        subtype="network_admired",
    )
    answer = "* https://www.linkedin.com/in/rk — she told me the unwelcome thing, in private"
    plan = plan_ingest(
        "jason",
        [FormResponse(answers={question.title: answer})],
        source="responses.csv",
        questions=[question],
    )
    assert [capture.target for capture in plan.captures] == ["https://www.linkedin.com/in/rk"]
    assert plan.captures[0].why == "she told me the unwelcome thing, in private"


ADMIRED = FormQuestion(
    key="network_admired",
    title="Which people you actually know do you admire, and why?",
    destination="profile",
    subtype="network_admired",
)


def _admired(answer: str) -> IngestPlan:
    return plan_ingest(
        "jason",
        [FormResponse(answers={ADMIRED.title: answer})],
        source="responses.csv",
        questions=[ADMIRED],
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Karla Martin: https://www.linkedin.com/in/km/ - she told me the unwelcome thing",
        "Karla Martin — https://www.linkedin.com/in/km/ — she told me the unwelcome thing",
        "https://www.linkedin.com/in/km/ Karla Martin — she told me the unwelcome thing",
    ],
)
def test_a_name_and_a_url_are_separated_not_glued_together(answer: str) -> None:
    """The form offers "Name (or their LinkedIn URL)" and the natural answer
    gives BOTH (#385). A nomination's target becomes the item's own name, so
    a composite is neither a followable URL nor a displayable person."""
    capture = _admired(answer).captures[0]
    assert capture.target == "Karla Martin"
    assert capture.identifier_url == "https://www.linkedin.com/in/km/"
    assert capture.why == "she told me the unwelcome thing"


def test_the_identifier_never_ends_up_inside_the_evidence_quote() -> None:
    """'why' is stored verbatim as the evidence span. A URL glued to its
    front is a quote the person never wrote."""
    for answer in (
        "Karla Martin: https://www.linkedin.com/in/km/ - she is honest",
        "Karla Martin — https://www.linkedin.com/in/km/ — she is honest",
    ):
        assert "http" not in _admired(answer).captures[0].why


def test_a_url_only_nomination_keeps_the_url_as_its_target() -> None:
    """The form offers a URL INSTEAD of a name. Lifting it out of that answer
    would leave the nomination with no name at all."""
    capture = _admired("https://www.linkedin.com/in/rk — she told me in private").captures[0]
    assert capture.target == "https://www.linkedin.com/in/rk"
    assert capture.identifier_url == ""


def test_an_identifier_is_shown_in_the_preview_before_anything_is_written() -> None:
    """The operator applies what the preview showed. A split they cannot see
    is one they cannot check."""
    plan = _admired("Karla Martin: https://www.linkedin.com/in/km/ - she is honest")
    rendered = render_plan(plan, Path("/tmp/ws"), criteria_exists=False)
    assert "identifier: https://www.linkedin.com/in/km/" in rendered


def test_an_identifier_survives_into_the_stored_capture(workspace: Config) -> None:
    """Kept out of the target and out of the evidence — so it has to be kept
    SOMEWHERE, or the answer was silently dropped."""
    plan = _admired("Karla Martin: https://www.linkedin.com/in/km/ - she is honest")
    with Storage(workspace.db_path) as storage:
        apply_plan(plan, workspace, storage)
    karla = next(item for item in _items(workspace) if item.name == "network_admired: Karla Martin")
    assert karla.detail == "she is honest"
    with Storage(workspace.db_path) as storage:
        record = storage.get_source_record(karla.evidence[0].source_record_id)
    assert record is not None
    note = (workspace.data_dir / record.source_locator).read_text(encoding="utf-8")
    assert "https://www.linkedin.com/in/km/" in note


def test_the_mission_alignment_purpose_is_kept_out_of_the_evidence(workspace: Config) -> None:
    """The organization's purpose is context, never the quote — interview.py's
    own rule. The 'why' is the part that says something about the person."""
    path = _write_responses(workspace.data_dir, _full_row())
    _ingest(workspace, path)
    org = next(item for item in _items(workspace) if item.subtype == "mission_alignment_pro")
    assert org.name == "mission_alignment_pro: Ordnance Survey"
    assert org.detail == "I like infrastructure nobody notices until it is gone"
    assert "map the country" not in org.evidence[0].quote
    notes = [
        path.read_text(encoding="utf-8")
        for path in workspace.inbox_dir.glob("*interview-form-note.md")
    ]
    assert any("they map the country and publish it" in note for note in notes)
    assert all("Answered in: " in note for note in notes)


def test_an_organization_without_a_reason_is_reported_never_invented(workspace: Config) -> None:
    """A line naming the org and its purpose but no reason has nothing that
    can become evidence. Using the purpose as the reason would file a claim
    about the ORGANIZATION as a statement about the PERSON."""
    row = _full_row(**{QUESTIONS[3].title: "A Payday Lender — they lend at 1000% APR"})
    path = _write_responses(workspace.data_dir, row)
    plan, _outcome = _ingest(workspace, path)
    assert not [item for item in _items(workspace) if item.subtype == "mission_alignment_pro"]
    assert "Payday Lender" in _reported(plan) and "no reason" in _reported(plan)


# --------------------------------------------------------------------------
# The preview gate, and whose workspace this is
# --------------------------------------------------------------------------


def _tenant(tmp_path: Path, slug: str) -> Path:
    data_dir = tmp_path / slug
    data_dir.mkdir()
    Storage(data_dir / "wingman.db").close()
    return data_dir


def _registry(tmp_path: Path, *entries: tuple[str, Path]) -> Path:
    registry = tmp_path / "tenants.toml"
    lines: list[str] = []
    for slug, data_dir in entries:
        lines += ["[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""]
    registry.write_text("\n".join(lines), encoding="utf-8")
    return registry


def _shipped_form_csv(tmp_path: Path) -> Path:
    """A response export keyed by the titles the shipped generator emits."""
    questions = {question.key: question for question in form_questions()}
    row = {
        "Timestamp": "2026-08-11 14:02:31",
        questions["criteria_hard_filters"].title: HARD_FILTERS,
        questions["criteria_tie_breaker"].title: TIE_BREAKER,
        questions["values_pro"].title: DINNER,
    }
    path = tmp_path / "shipped-responses.csv"
    path.write_text(_csv([row], list(row)), encoding="utf-8")
    return path


def test_a_run_without_apply_writes_absolutely_nothing(tmp_path: Path) -> None:
    """The operator is writing into somebody else's career record. Preview
    first is the whole design — RFC-025's confirmation gate and the dry-run
    default of wingman-migrate-tenant.sh, in the one place where the writer
    and the subject are different people."""
    data_dir = _tenant(tmp_path, "jason")
    registry = _registry(tmp_path, ("jason", data_dir))
    responses = _shipped_form_csv(tmp_path)

    result = cli.invoke(
        app, ["tenant", "ingest-form", "jason", str(responses), "--registry", str(registry)]
    )

    assert result.exit_code == 0
    assert "Ada Lovelace" in result.output  # it showed exactly what it would capture
    assert str(data_dir) in result.output  # ...and whose workspace it would go into
    assert "Nothing was written" in result.output
    assert not (data_dir / "job-criteria.md").exists()
    with Storage(data_dir / "wingman.db") as storage:
        assert storage.list_profile_items() == []
        assert storage.list_answers() == []
    assert not (data_dir / "inbox").exists()


def test_apply_writes_only_into_the_named_tenants_workspace(tmp_path: Path) -> None:
    """#333 from the other direction: the target is named on the command
    line, and no other workspace on the box is touched."""
    jason = _tenant(tmp_path, "jason")
    taylor = _tenant(tmp_path, "taylor")
    registry = _registry(tmp_path, ("jason", jason), ("taylor", taylor))
    responses = _shipped_form_csv(tmp_path)

    result = cli.invoke(
        app,
        ["tenant", "ingest-form", "jason", str(responses), "--registry", str(registry), "--apply"],
    )

    assert result.exit_code == 0
    assert (jason / "job-criteria.md").exists()
    with Storage(jason / "wingman.db") as storage:
        assert storage.list_profile_items()
    assert not (taylor / "job-criteria.md").exists()
    with Storage(taylor / "wingman.db") as storage:
        assert storage.list_profile_items() == []


def test_an_unknown_slug_never_writes_anywhere(tmp_path: Path) -> None:
    registry = _registry(tmp_path, ("jason", _tenant(tmp_path, "jason")))
    responses = _shipped_form_csv(tmp_path)
    result = cli.invoke(
        app,
        ["tenant", "ingest-form", "nobody", str(responses), "--registry", str(registry), "--apply"],
    )
    assert result.exit_code == 1


def test_the_ingest_is_not_reachable_as_an_mcp_tool() -> None:
    """A tenant's MCP session is bound to that tenant's own Config; a tool
    that wrote across workspaces would reopen exactly what #333 closed. The
    gate for this verb is shell access, like 'tenant urls'."""
    from wingman import mcp_server

    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert "form_ingest" not in source
    assert "ingest_form" not in source


# --------------------------------------------------------------------------
# Blanks, unknowns, repeats
# --------------------------------------------------------------------------


def test_a_blank_answer_is_skipped_and_never_stored_as_empty_evidence(workspace: Config) -> None:
    """Every question on the form is optional by design. An empty capture
    would file 'they answered' as a fact, with an empty quote as its
    evidence."""
    row = _full_row(**{QUESTIONS[2].title: "   ", QUESTIONS[5].title: ""})
    path = _write_responses(workspace.data_dir, row)
    plan, _outcome = _ingest(workspace, path)

    assert not [item for item in _items(workspace) if item.subtype == "values_pro"]
    with Storage(workspace.db_path) as storage:
        assert storage.list_answers() == []
    blanks = {item.what for item in plan.skipped if "blank" in item.reason}
    assert blanks == {QUESTIONS[2].title, QUESTIONS[5].title}


def test_an_unknown_title_is_reported_rather_than_silently_dropped(workspace: Config) -> None:
    """A form the owner edited by hand, or an older form still out with
    somebody. The answer cannot be routed — but a person wrote it, and an
    operator who is never told simply believes it landed."""
    row = _full_row(**{"What is your favourite colour?": "Green, obviously."})
    path = _write_responses(workspace.data_dir, row)
    plan, _outcome = _ingest(workspace, path)

    unknown = [item for item in plan.skipped if "favourite colour" in item.what]
    assert unknown and "no question with this title" in unknown[0].reason
    assert not [item for item in _items(workspace) if "colour" in item.name.lower()]
    assert "favourite colour" in render_plan(plan, workspace.data_dir, False)


def test_only_the_latest_submission_is_ingested_and_the_earlier_one_is_named(
    workspace: Config,
) -> None:
    """A second submission is somebody redoing the form, not adding to it.
    Merging both would leave the answers they abandoned active alongside
    the ones that replaced them, all citable, with nothing recording that a
    set was withdrawn — and RFC-028 supersession cannot clean that up,
    because it only fires for the same target."""
    first = _full_row(**{QUESTIONS[2].title: "Ada Lovelace — first thoughts"})
    second = _full_row(
        **{
            "Timestamp": "2026-08-12 09:15:00",
            QUESTIONS[2].title: "Grace Hopper — she made the machine speak English",
        }
    )
    path = _write_responses(workspace.data_dir, first, second)
    plan, _outcome = _ingest(workspace, path)

    assert {item.name for item in _items(workspace) if item.subtype == "values_pro"} == {
        "values_pro: Grace Hopper"
    }
    assert "submission 1 of 2" in _reported(plan)


def test_an_earlier_submission_can_be_chosen_explicitly(workspace: Config) -> None:
    first = _full_row(**{QUESTIONS[2].title: "Ada Lovelace — first thoughts"})
    second = _full_row(**{QUESTIONS[2].title: "Grace Hopper — second thoughts"})
    path = _write_responses(workspace.data_dir, first, second)
    _ingest(workspace, path, submission=1)
    assert {item.name for item in _items(workspace) if item.subtype == "values_pro"} == {
        "values_pro: Ada Lovelace"
    }


def test_ingesting_the_same_file_twice_changes_nothing(workspace: Config) -> None:
    """The operator will re-run it — after a --replace-criteria, or because
    they are not sure the first run worked."""
    path = _write_responses(workspace.data_dir, _full_row())
    _ingest(workspace, path)
    before = {item.item_id for item in _items(workspace)}
    _plan, outcome = _ingest(workspace, path)
    assert {item.item_id for item in _items(workspace)} == before
    assert all("already captured" in line or "revised" in line for line in outcome.written)


# --------------------------------------------------------------------------
# The criteria document
# --------------------------------------------------------------------------


def test_five_answers_become_one_document_under_the_five_headings() -> None:
    """job_criteria stores ONE Markdown document, not five fields, so
    something has to assemble it. Deterministically, verbatim, under the
    heading of the question that asked: a model-written version would be
    wingman's opinion of their criteria, and RFC-035 scores every opening
    they ever see against this text."""
    areas = [(name, f"{name} answer, in their words.") for name, _prompt in INTERVIEW_AREAS]
    document = compose_criteria_document("jason", areas, [], "2026-08-11 14:02:31", "2026-08-12")

    headings = [line[3:] for line in document.splitlines() if line.startswith("## ")]
    assert headings == [name for name, _prompt in INTERVIEW_AREAS]
    for name, _prompt in INTERVIEW_AREAS:
        assert f"{name} answer, in their words." in document
    assert document.startswith("# Job criteria — jason")


def test_a_blank_area_is_absent_from_the_document_and_named_in_its_header() -> None:
    """An empty '## Anti-signals' heading reads to the judge as 'no
    anti-signals', which is a claim the person never made."""
    document = compose_criteria_document(
        "jason", [("Hard filters", HARD_FILTERS)], ["Anti-signals"], "", "2026-08-12"
    )
    assert "## Anti-signals" not in document
    assert "Left blank in the form, and so absent here: Anti-signals." in document


def test_an_existing_criteria_document_is_not_replaced_without_the_flag(workspace: Config) -> None:
    """RFC-035's document is the person's confirmed wording, possibly
    refined in conversation since. Overwriting it on the strength of a form
    answer, in somebody else's workspace, is not a call this command makes
    silently."""
    criteria_path(workspace).write_text(
        "# Job criteria\n\nWhat we agreed last month.\n", encoding="utf-8"
    )
    path = _write_responses(workspace.data_dir, _full_row())

    _plan, outcome = _ingest(workspace, path)
    assert "What we agreed last month." in criteria_path(workspace).read_text(encoding="utf-8")
    assert any("already exists" in item.reason for item in outcome.failed)

    _plan, outcome = _ingest(workspace, path, replace_criteria=True)
    assert HARD_FILTERS in criteria_path(workspace).read_text(encoding="utf-8")
    assert "What we agreed last month." in Path(outcome.criteria_backup).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_provenance_says_the_answer_arrived_in_a_form_and_surfaces(workspace: Config) -> None:
    """A form answer is the person's own words — but written months
    earlier, offline, without any follow-up question, and put here by an
    operator. Somebody reading their own profile should be able to tell
    which lines those are without querying source records."""
    from wingman.application.interview import FORM_SOURCE_TYPE
    from wingman.application.profile_manage import render_profile_listing
    from wingman.domain.provenance import FORM_ARRIVAL, FORM_EXTRACTOR

    path = _write_responses(workspace.data_dir, _full_row())
    _ingest(workspace, path)

    items = _items(workspace)
    assert items and all(item.extracted_by == FORM_EXTRACTOR for item in items)
    with Storage(workspace.db_path) as storage:
        records = [
            storage.get_source_record(item.evidence[0].source_record_id)
            for item in items
            if item.kind is ProfileItemKind.INTERVIEW
        ]
        answers = storage.list_answers()
    assert records and all(
        record is not None and record.source_type == FORM_SOURCE_TYPE for record in records
    )
    assert FORM_ARRIVAL in answers[0].context
    assert FORM_ARRIVAL in criteria_path(workspace).read_text(encoding="utf-8")
    assert "(answered in a form)" in render_profile_listing(items)


def test_a_conversational_capture_is_still_unmarked(workspace: Config) -> None:
    """The marker has to mean something: an ordinary capture must not
    acquire it, or 'answered in a form' stops distinguishing anything."""
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.profile_manage import render_profile_listing

    with Storage(workspace.db_path) as storage:
        capture_interview_reaction(
            "values_pro", "Grace Hopper", "she made the machine speak English", workspace, storage
        )
    listing = render_profile_listing(_items(workspace))
    assert "Grace Hopper" in listing
    assert "(answered in a form)" not in listing


def test_the_unasked_fields_are_named_rather_than_defaulted(workspace: Config) -> None:
    """The conversational path asks for intensity, the company reason and
    the value statement (RFC-050, RFC-057); the form asks for none of them.
    Unset is legitimate — every one of those fields is optional by design —
    but a default would be wingman's guess at how strongly somebody feels,
    filed under a field built to hold their own answer."""
    path = _write_responses(workspace.data_dir, _full_row())
    plan, _outcome = _ingest(workspace, path)
    nominations = [item for item in _items(workspace) if item.kind is ProfileItemKind.INTERVIEW]
    assert nominations
    for item in nominations:
        assert item.intensity is None
        assert item.company_reason is None
        assert item.value_statement == ""
    preview = render_plan(plan, workspace.data_dir, False)
    assert "intensity" in preview and "value statement" in preview


# --------------------------------------------------------------------------
# Reading the export and the manifest
# --------------------------------------------------------------------------


def test_a_json_export_reads_the_same_as_a_csv(workspace: Config) -> None:
    path = workspace.data_dir / "responses.json"
    path.write_text(json.dumps({"responses": [_full_row()]}), encoding="utf-8")
    plan = plan_ingest("jason", parse_responses(path), source=str(path), questions=QUESTIONS)
    assert plan.criteria_areas and plan.captures


def test_a_shipped_manifest_routes_the_shipped_questions() -> None:
    """The manifest 'wingman tenant form' writes IS the routing table this
    reads — the two halves are one feature or neither works."""
    questions = questions_from_manifest(render_manifest("jason"))
    assert [question.title for question in questions] == [q.title for q in form_questions()]
    assert {question.destination for question in questions} == {"criteria", "profile"}


def test_a_title_that_survived_a_spreadsheet_still_routes() -> None:
    """An export that went through a spreadsheet comes back with its dashes
    and spacing changed. Exact equality would unroute every question whose
    title carries an em dash — silently, since an unmatched title is only a
    line in a report."""
    question = form_questions()[0]
    mangled = question.title.replace("—", "-").upper() + "  "
    plan = plan_ingest(
        "jason", [FormResponse(answers={mangled: HARD_FILTERS})], source="responses.csv"
    )
    assert plan.criteria_areas and plan.criteria_areas[0][1] == HARD_FILTERS
