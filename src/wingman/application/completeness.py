"""Completeness: how filled-in each section of the workspace is (issue draft,
Gmail-triage pilot follow-up).

Deterministic composition, no model call and no network — same posture as
`dossier.py` and `career.py`. Scoped to the four sections that already have
a read path today: Career Profile, Job Criteria, People, and Companies.
Interview Bootstrap and Applications are NOT computed here — there is no
tool yet that reads back `interview_react` captures (#311) or lists
`Opportunity` records by name (#312), so any number this module could
produce for those two would be invented, not measured. Callers should show
those two sections as blocked, not zero.

Company facts are recomputed directly from storage (people/documents/POV
cards), the same way `dossier.py`'s `build_company_dossier` does — but
without calling that function, so checking completeness never has the side
effect of writing a fresh dossier file to reports/companies/ for every
tracked company on every call.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from pydantic import BaseModel

from wingman.application.company_feeds import is_company_anchor
from wingman.application.job_scoring import load_criteria
from wingman.application.similarity import company_key
from wingman.domain.profile import ItemStatus, ProfileItemKind
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage


class CareerProfileCompleteness(BaseModel):
    roles: int
    achievements: int
    skills: int
    testimonials: int


class JobCriteriaCompleteness(BaseModel):
    exists: bool


class PersonCompleteness(BaseModel):
    name: str
    company: str | None
    linked: bool  # has a LinkedIn URL or at least one feed source
    log_entries: int


class CompanyCompleteness(BaseModel):
    name: str
    people_watched: int
    documents: int
    pov_cards: int
    missing_pov_cards: int


class ValuesCompleteness(BaseModel):
    """Progress toward a value profile, against the REAL thresholds.

    application.values refuses to infer below MIN_ITEMS captures across
    MIN_SUBTYPES kinds, so 'N of 6' here is the actual contract rather than
    an invented denominator — the distinction AGENTS.md invariant 9 draws.
    """

    captures: int
    subtypes: int
    min_items: int
    min_subtypes: int
    profile_built: bool

    @property
    def ready(self) -> bool:
        return self.captures >= self.min_items and self.subtypes >= self.min_subtypes


class InterviewCompleteness(BaseModel):
    """Per-subtype interview standing, against the REAL per-subtype cap.

    The cap is `application.interview`'s own
    WINGMAN_INTERVIEW_MAX_PER_SUBTYPE (default 6), so "3 of 6" here is the
    contract the interview enforces, not a target invented to fill a
    progress bar (AGENTS.md invariant 9). Same distinction that lets the
    values floor be shown as a fraction.
    """

    subtype: str
    category: str
    count: int
    cap: int


class OpportunityCompleteness(BaseModel):
    assessed: int


class NextAction(BaseModel):
    """One thing worth doing next, and why it is worth doing.

    'Testimonials: 0' is a number. "Nothing here is in anyone else's words"
    is the reason to care, and 'how' is the sentence to actually say. The
    band on the profile page already works this way; this is the same idea
    across the whole workspace.
    """

    title: str
    why: str
    how: str


class CompletenessReport(BaseModel):
    generated_at: datetime
    career: CareerProfileCompleteness
    job_criteria: JobCriteriaCompleteness
    people: list[PersonCompleteness]
    companies: list[CompanyCompleteness]
    values: ValuesCompleteness
    interview: list[InterviewCompleteness]
    opportunities: OpportunityCompleteness


def _interview_completeness(storage: Storage) -> list[InterviewCompleteness]:
    """Every interview subtype and where it stands (#311 shipped as
    `interview_status`; this report went on claiming no read-back existed
    long after it did, so the one place somebody looks for progress told
    them the data was unavailable while the tool sat right there).

    Ordered by _STATUS_CATEGORIES rather than by iterating the subtype
    sets, for the same reason that list exists: set order is not stable,
    and this is read by humans.
    """
    from wingman.application.interview import _STATUS_CATEGORIES, subtype_status

    status = subtype_status(storage)
    rows: list[InterviewCompleteness] = []
    for category, subtypes in _STATUS_CATEGORIES:
        for subtype in subtypes:
            entry = status.get(subtype)
            if entry is None:
                continue
            rows.append(
                InterviewCompleteness(
                    subtype=subtype,
                    category=category,
                    count=entry.count,
                    cap=entry.cap,
                )
            )
    return rows


def _opportunity_completeness(storage: Storage) -> OpportunityCompleteness:
    return OpportunityCompleteness(assessed=len(storage.list_opportunities()))


def _values_completeness(storage: Storage) -> ValuesCompleteness:
    from wingman.application.pov import CORPUS_PERSON_ID
    from wingman.application.values import MIN_ITEMS, MIN_SUBTYPES, _eligible_items

    items = _eligible_items(storage, persona_id=None)
    return ValuesCompleteness(
        captures=len(items),
        subtypes=len({item.subtype for item in items if item.subtype}),
        min_items=MIN_ITEMS,
        min_subtypes=MIN_SUBTYPES,
        profile_built=storage.get_value_profile(CORPUS_PERSON_ID) is not None,
    )


def next_actions(report: CompletenessReport) -> list[NextAction]:
    """What to do next, hardest-working first — the answer to "what's my
    status" and "what should I do next".

    Ordered by what unblocks the most downstream, not by what is emptiest.
    Job criteria leads because every scored opening, every digest brief and
    the whole overnight judgement depend on a document that takes ten
    minutes to write; a workspace can be full of everything else and still
    tell you nothing about which job to look at.

    Deterministic and derived from the report — no model, no guessing, and
    nothing here that the numbers above do not already say.
    """
    actions: list[NextAction] = []
    if not report.job_criteria.exists:
        actions.append(
            NextAction(
                title="Set your job criteria",
                why=(
                    "Until this exists every opening arrives unscored — wingman can find "
                    "postings but cannot tell you which ones are worth your attention."
                ),
                how="say: let's set up my job criteria",
            )
        )
    if report.career.roles == 0:
        actions.append(
            NextAction(
                title="Add your CV or resume",
                why="With no roles, nothing here shows tenure, title or seniority.",
                how="upload it on this page, or say: here's my resume",
            )
        )
    values = report.values
    if not values.profile_built:
        if values.ready:
            actions.append(
                NextAction(
                    title="Build your values profile",
                    why=(
                        f"You have {values.captures} captures across {values.subtypes} kinds — "
                        "enough to infer what you actually value, and to draw the chart."
                    ),
                    how="say: what do I actually value?",
                )
            )
        else:
            actions.append(
                NextAction(
                    title="Say who you admire, and who you would rather not be",
                    why=(
                        f"{values.captures} of {values.min_items} captures across "
                        f"{values.subtypes} of {values.min_subtypes} kinds. Below that there is "
                        "not enough contrast to infer a value profile, so nothing is inferred "
                        "rather than something thin being made up — and the radar chart has "
                        "nothing to draw."
                    ),
                    how="say: help me build my profile",
                )
            )
    if report.career.testimonials == 0:
        actions.append(
            NextAction(
                title="Capture a testimonial",
                why="Nothing in this profile is yet in anyone else's words.",
                how="paste a recommendation, or import your LinkedIn export",
            )
        )
    missing_pov = sum(company.missing_pov_cards for company in report.companies)
    if missing_pov:
        actions.append(
            NextAction(
                title=f"Build POV cards for {missing_pov} watched people",
                why=(
                    "A point of view is what you can actually walk into a conversation with; "
                    "a watched person without one is a name on a list."
                ),
                how="say: build <name>'s point-of-view card",
            )
        )
    silent = sum(1 for person in report.people if person.log_entries == 0)
    if silent:
        actions.append(
            NextAction(
                title=f"Log what happened with {silent} people",
                why=(
                    "An interaction wingman never hears about cannot become evidence for the "
                    "next conversation, and the tickler stays silent on it."
                ),
                how="say: log that I had coffee with <name>, we talked about X",
            )
        )
    return actions


def _career_completeness(storage: Storage) -> CareerProfileCompleteness:
    items = [item for item in storage.list_profile_items() if item.status is ItemStatus.ACTIVE]
    counts = Counter(item.kind for item in items)
    return CareerProfileCompleteness(
        roles=counts[ProfileItemKind.ROLE],
        achievements=counts[ProfileItemKind.ACHIEVEMENT],
        skills=counts[ProfileItemKind.SKILL],
        testimonials=counts[ProfileItemKind.TESTIMONIAL],
    )


def _job_criteria_completeness(config: Config) -> JobCriteriaCompleteness:
    return JobCriteriaCompleteness(exists=load_criteria(config) is not None)


def _people_completeness(storage: Storage) -> list[PersonCompleteness]:
    people = [person for person in storage.list_people() if not is_company_anchor(person)]
    log_counts = Counter(entry.person_id for entry in storage.list_log_entries())
    return [
        PersonCompleteness(
            name=person.name,
            company=person.company,
            linked=bool(person.linkedin_url) or bool(person.sources),
            log_entries=log_counts[person.person_id],
        )
        for person in people
    ]


def _companies_completeness(storage: Storage) -> list[CompanyCompleteness]:
    all_people = storage.list_people()
    watched = [person for person in all_people if not is_company_anchor(person)]

    # Keyed by company_key(), not the raw string: two people whose company
    # fields are spelled or cased differently ("Acme" vs "ACME") share a key
    # and must land in one row, not two duplicate, double-counted ones — the
    # same normalization every other company-keyed code path (dossier.py,
    # pack.py's company inference) already relies on.
    display_by_key: dict[str, str] = {}
    for person in sorted(watched, key=lambda p: p.name):
        if person.company and (key := company_key(person.company)):
            display_by_key.setdefault(key, person.company)

    all_documents = storage.list_external_documents()
    people_by_id = {person.person_id: person for person in all_people}
    pov_person_ids = {card.person_id for card in storage.list_pov_cards()}

    results: list[CompanyCompleteness] = []
    for key, name in sorted(display_by_key.items(), key=lambda kv: kv[1]):
        people = [person for person in watched if company_key(person.company or "") == key]
        documents = [
            document
            for document in all_documents
            if (
                (via := people_by_id.get(document.person_id))
                and company_key(via.company or "") == key
            )
            or company_key(document.organization or "") == key
        ]
        cards = sum(1 for person in people if person.person_id in pov_person_ids)
        results.append(
            CompanyCompleteness(
                name=name,
                people_watched=len(people),
                documents=len(documents),
                pov_cards=cards,
                missing_pov_cards=len(people) - cards,
            )
        )
    return results


def compute_completeness(storage: Storage, config: Config) -> CompletenessReport:
    """The current completeness snapshot across the four measurable sections."""
    return CompletenessReport(
        generated_at=datetime.now(UTC),
        career=_career_completeness(storage),
        job_criteria=_job_criteria_completeness(config),
        people=_people_completeness(storage),
        companies=_companies_completeness(storage),
        values=_values_completeness(storage),
        interview=_interview_completeness(storage),
        opportunities=_opportunity_completeness(storage),
    )
