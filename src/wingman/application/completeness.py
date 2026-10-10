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
from typing import Literal

from pydantic import BaseModel

from wingman.application.company_feeds import is_company_anchor
from wingman.application.job_scoring import load_criteria
from wingman.application.similarity import company_key
from wingman.domain.person import Person, PersonOrigin
from wingman.domain.profile import ItemStatus, ProfileItemKind
from wingman.infrastructure.broadcast import (
    OperatorMessage,
    OperatorQuestion,
    pending_operator_message,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.model_health import describe
from wingman.infrastructure.storage import Storage
from wingman.providers.router import metered_key, model_rejection


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


# The one sentence that keeps an instruction from passing as a fact.
# Every surface that renders a next-actions list must show it alongside an
# operator action — see NextAction.attribution below for why it is a
# constant here rather than prose repeated in three renderers.
OPERATOR_ATTRIBUTION = (
    "Asked for by whoever runs this machine — an instruction, "
    "not something wingman measured in your workspace."
)

# The same sentence for a question, plus the one thing a question needs
# that a message does not: who will read the reply. Said BEFORE the answer
# exists, because consent that arrives after the words are stored is not
# consent, and somebody who does not know their audience answers a
# different question than the one they were asked (RFC-067).
OPERATOR_QUESTION_ATTRIBUTION = (
    "Asked by whoever runs this machine — a question, not something wingman "
    "measured in your workspace. Your answer is stored in your own workspace, "
    "in your own words, and they can read it."
)


class NextAction(BaseModel):
    """One thing worth doing next, and why it is worth doing.

    'Testimonials: 0' is a number. "Nothing here is in anyone else's words"
    is the reason to care, and 'how' is the sentence to actually say. The
    band on the profile page already works this way; this is the same idea
    across the whole workspace.

    'origin' is load-bearing, not decoration (issue #224). Every other
    action in this list is DERIVED: the workspace says roles == 0, so the
    action is checkable against the workspace, and wrong only if the count
    is wrong. An operator action is somebody's instruction, arriving from
    outside the workspace entirely, and nothing in the data can confirm or
    contradict it. Rendering the two identically would let an instruction
    read as a measured fact — the same conflation the commentary corpus
    (RFC-058) exists to prevent one layer up, where the assistant's
    reading of your material is kept in a store no evidence path can
    reach. So the distinction is carried in the model, and every renderer
    is obliged to show it.
    """

    title: str
    why: str
    how: str
    origin: Literal["workspace", "operator", "operator_question"] = "workspace"

    @property
    def from_the_operator(self) -> bool:
        """Whether this came from a person rather than from the workspace.

        The test every renderer needs, so adding a third operator-side
        origin (the question, #224's second half) could not silently stop
        one of them labelling the line — which comparing to the string
        "operator" would have done.
        """
        return self.origin != "workspace"

    @property
    def attribution(self) -> str:
        """The provenance line a renderer must show, or '' when derived."""
        if self.origin == "operator":
            return OPERATOR_ATTRIBUTION
        if self.origin == "operator_question":
            return OPERATOR_QUESTION_ATTRIBUTION
        return ""


class CompletenessReport(BaseModel):
    generated_at: datetime
    career: CareerProfileCompleteness
    job_criteria: JobCriteriaCompleteness
    # Curated people only (#424/#465/#545): added by hand, given a feed, or
    # logged with. A LinkedIn-connections import puts thousands of contacts in
    # the same store, and measuring every one of them against "has a POV card"
    # or "has a log entry" turns completeness into a wall nobody can act on.
    people: list[PersonCompleteness]
    # Imported contacts nobody has attended to yet. A count, not a list and not
    # a debt: the size of the address book is worth seeing, but an entry the
    # owner has never touched is not a gap in their workspace.
    imported_people: int = 0
    companies: list[CompanyCompleteness]
    values: ValuesCompleteness
    interview: list[InterviewCompleteness]
    opportunities: OpportunityCompleteness
    # The box-wide broadcast this account has not been shown yet, if any
    # (issue #224). None whenever there is no message, the account has
    # already seen this one, or the shared file is absent/unreadable/
    # malformed — infrastructure.broadcast collapses all of those to the
    # same answer on purpose. Serialised into completeness.json alongside
    # everything else, so what an account was told is as inspectable as
    # what was counted.
    operator_action: OperatorMessage | None = None
    # The operator's question this account has been asked and not yet
    # answered (issue #224's second half, RFC-067). Same collapse-to-None
    # rules as the message above, plus two of its own: a question addressed
    # to another tenant is not this account's, and a question this workspace
    # has already answered is finished. Unlike the message it is not
    # "delivered once" — it stands until answered, like every computed
    # action, because a question nobody saw is worse than one asked twice.
    operator_question: OperatorQuestion | None = None
    # Can this workspace make a model call at all (#514)? False for a
    # hosted tenant with no key of its own that the operator has not
    # funded. Kept on the report rather than recomputed in next_actions
    # because it changes what the whole list is allowed to recommend: the
    # original bug was telling somebody to "build your values profile" —
    # the one call that could not succeed — as their top next step, on
    # evidence that comfortably cleared the floor.
    inference_available: bool = True
    # Why not, when a key exists and the provider refused it (#528) — a
    # spend limit or a revoked key. None when the reason is simply that
    # there is no key, which the action below already words for.
    inference_refused: str | None = None


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

    **An operator action outranks every computed one** (issue #224), and
    the operator's QUESTION comes second, still ahead of everything
    computed. This is a written rule, not an accident of insertion order,
    and it costs job criteria the lead it otherwise holds. Three reasons,
    in order of weight:

    1. The operator knows something the workspace cannot. Every computed
       action is inferred from local state; an operator action exists
       precisely because something happened outside it — a parser changed,
       a migration is coming, a box is moving. There is no way to derive
       it, so there is no way for a computed action to be more informed.
    2. A computed action is durable; this one is not. "Set your job
       criteria" persists until criteria exist, so burying it costs a
       reading, not the message. An operator action is shown ONCE and then
       never again, so ranking it below anything is functionally the same
       as dropping it.
    3. It is usually time-bound in a way none of the others are. "Do this
       at your earliest convenience" was the phrasing that produced this
       feature.

    A message outranks a question, because a message asks somebody to act
    and a question only asks them to speak; and because a message is shown
    ONCE while a question stands until it is answered, so ranking the
    question first would be spending the message's single delivery on the
    less urgent of the two.

    The cost, accepted deliberately: an operator can push the single most
    unblocking computed step down the list, for any account on the box, by
    writing one file — two files now, and the second one asks for the
    person's words rather than their time. That is a real power and the
    reason origin is visible everywhere it renders — the person reading it
    can see that line came from a human and weigh it accordingly, which
    they could not do if it were dressed as a measurement.

    Otherwise deterministic and derived from the report — no model, no
    guessing, and nothing here that the numbers above do not already say.
    """
    actions: list[NextAction] = []
    if report.operator_action is not None:
        broadcast = report.operator_action
        actions.append(
            NextAction(
                title=broadcast.action,
                # The fallbacks are honest about their own emptiness rather
                # than inventing a rationale on the operator's behalf: if
                # they did not say why, saying "because you were asked" is
                # the whole of the truth, and any richer sentence would be
                # wingman making up somebody else's reasoning.
                why=broadcast.why or "No reason was given beyond the request itself.",
                how=broadcast.how or "ask whoever runs this machine if this is not clear",
                origin="operator",
            )
        )
    if report.operator_question is not None:
        question = report.operator_question
        actions.append(
            NextAction(
                # The question itself is the line, not "answer a question" —
                # a person decides whether to answer by reading what was
                # asked, and a title that hides it behind a label costs the
                # question the only chance it gets to be read.
                title=question.question,
                why=question.why or "No reason was given beyond the question itself.",
                how="say: my answer to the question of the day is …",
                origin="operator_question",
            )
        )
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
        if values.ready and not report.inference_available and report.inference_refused:
            actions.append(
                NextAction(
                    title="Model calls are refused — your values profile has to wait",
                    why=(
                        f"You have {values.captures} captures across {values.subtypes} kinds — "
                        "more than enough to infer what you actually value, but "
                        f"{report.inference_refused}. Nothing here is lost; the profile "
                        "is the one thing your evidence cannot buy until that clears."
                    ),
                    how="wait for the provider's reset, or replace the key under Manage → Keys",
                )
            )
        elif values.ready and not report.inference_available:
            actions.append(
                NextAction(
                    title="Add a model key to unlock your values profile",
                    why=(
                        f"You have {values.captures} captures across {values.subtypes} kinds — "
                        "more than enough to infer what you actually value. Inferring it takes "
                        "a model call, and this workspace has no model key, so the profile and "
                        "its chart are the one thing your evidence cannot buy yet."
                    ),
                    how="add your own key under Manage → Keys, or ask the operator to enable "
                    "shared inference for this workspace",
                )
            )
        elif values.ready:
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


def _split_people(storage: Storage) -> tuple[list[Person], int, Counter[str]]:
    """Curated people, the count of untouched imports, and log counts by person.

    Curated means somebody deliberately engaged with the person: added them by
    hand, attached a feed, or logged an interaction. That is deterministic and
    needs no new stored field — `origin` and the feed and log stores already say
    it. An import that has none of the three is just an address-book row, and
    counting it against "build a POV card" or "log what happened" measures a
    population the metric does not mean (#465).
    """
    log_counts = Counter(entry.person_id for entry in storage.list_log_entries())
    curated: list[Person] = []
    imported = 0
    for person in storage.list_people():
        if is_company_anchor(person):
            continue
        if (
            person.origin is not PersonOrigin.LINKEDIN_CONNECTIONS
            or person.sources
            or log_counts[person.person_id]
        ):
            curated.append(person)
        else:
            imported += 1
    return curated, imported, log_counts


def _people_completeness(storage: Storage) -> tuple[list[PersonCompleteness], int]:
    people, imported, log_counts = _split_people(storage)
    return (
        [
            PersonCompleteness(
                name=person.name,
                company=person.company,
                linked=bool(person.linkedin_url) or bool(person.sources),
                log_entries=log_counts[person.person_id],
            )
            for person in people
        ],
        imported,
    )


def _companies_completeness(storage: Storage) -> list[CompanyCompleteness]:
    all_people = storage.list_people()
    watched, _imported, _logs = _split_people(storage)

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
    """The current completeness snapshot across the four measurable sections.

    Reads the box-wide operator broadcast but never marks it delivered —
    this function is also called by paths with no human on the other end
    (the profile page's progress band, the profile HTML export), and
    consuming a once-only message there would burn it unread. Whoever
    actually shows a next-actions list calls
    `broadcast.acknowledge_delivery` after rendering.

    The operator's question needs no such care: it stands until this
    workspace has stored an answer to it, so reading it here costs nothing
    and there is no delivery act to forget.
    """
    from wingman.application.qotd import pending_question

    refused = model_rejection(config)
    people, imported_people = _people_completeness(storage)
    return CompletenessReport(
        generated_at=datetime.now(UTC),
        career=_career_completeness(storage),
        job_criteria=_job_criteria_completeness(config),
        people=people,
        imported_people=imported_people,
        companies=_companies_completeness(storage),
        values=_values_completeness(storage),
        interview=_interview_completeness(storage),
        opportunities=_opportunity_completeness(storage),
        operator_action=pending_operator_message(config),
        operator_question=pending_question(config, storage),
        inference_available=metered_key(config, "anthropic") is not None and refused is None,
        inference_refused=describe(refused) if refused is not None else None,
    )
