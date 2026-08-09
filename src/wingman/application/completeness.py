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


class CompletenessReport(BaseModel):
    generated_at: datetime
    career: CareerProfileCompleteness
    job_criteria: JobCriteriaCompleteness
    people: list[PersonCompleteness]
    companies: list[CompanyCompleteness]


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
    companies = sorted({person.company for person in watched if person.company})

    all_documents = storage.list_external_documents()
    people_by_id = {person.person_id: person for person in all_people}

    results: list[CompanyCompleteness] = []
    for name in companies:
        key = company_key(name)
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
        cards = sum(1 for person in people if storage.get_pov_card(person.person_id) is not None)
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
    )
