"""Company dossiers: a dated, cited snapshot of a company (Phase 3, slice ii).

Deterministic composition, no model call and no network: the dossier
assembles what the workspace already validated — watched people at the
company, org-attributed sources, POV-card stances (each one a labeled
inference backed by a verbatim-quote fact), and embedding-based signals
when available. Every claim is labeled fact / inference / computed
(RFC-005 discipline), staleness is stated rather than hidden, and the
artifact is written as dated Markdown under reports/ like every other
generated report.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.similarity import (
    _company_key,
    company_alignment,
    similar_companies,
)
from wingman.domain.person import ExternalDocument, FeedAttribution
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.dossier")

# A signal older than this is flagged, not silently presented as current.
STALE_AFTER_DAYS = 90


class DossierReport(BaseModel):
    company: str
    path: str
    markdown: str


def _slug(name: str) -> str:
    return "-".join(_company_key(name).split())


def build_company_dossier(name: str, config: Config, storage: Storage) -> DossierReport:
    """Compose and write the dated dossier for one company. Local data only."""
    key = _company_key(name)
    people = [
        person for person in storage.list_people() if _company_key(person.company or "") == key
    ]
    people_by_id = {person.person_id: person for person in storage.list_people()}
    documents: list[ExternalDocument] = []
    for document in storage.list_external_documents():
        person = people_by_id.get(document.person_id)
        via_person = person is not None and _company_key(person.company or "") == key
        via_org = _company_key(document.organization or "") == key
        if via_person or via_org:
            documents.append(document)
    if not people and not documents:
        raise IngestError(
            f"nothing in the workspace is attributable to {name!r}. Companies come from "
            "watched people's company field and org-attributed feeds."
        )
    display = next(
        (person.company for person in people if person.company),
        next((document.organization for document in documents if document.organization), name),
    )
    assert display is not None  # at least one branch above produced a name

    now = datetime.now(UTC)
    lines = [
        f"# Company dossier: {display}",
        "",
        f"Generated: {now.date().isoformat()} (local data only — no fetch, no model call)",
    ]

    # Feed dates arrive in mixed forms; naive timestamps are treated as UTC so
    # freshness arithmetic and sorting never crash on a tz-less feed.
    dated = sorted(
        date if date.tzinfo else date.replace(tzinfo=UTC)
        for document in documents
        if (date := document.published_at) is not None
    )
    if dated:
        newest = dated[-1]
        age_days = (now - newest).days
        freshness = f"Newest attributable document: {newest.date().isoformat()}"
        if age_days > STALE_AFTER_DAYS:
            freshness += f" — ⚠ stale ({age_days} days old); consider 'wingman sync'"
        lines.append(freshness)
    elif documents:
        lines.append("Newest attributable document: unknown (documents are undated)")
    undated = len(documents) - len(dated)
    if undated:
        lines.append(f"({undated} of {len(documents)} documents carry no publication date)")

    lines.extend(["", f"## People watched here ({len(people)})", ""])
    if people:
        for person in sorted(people, key=lambda entry: entry.name):
            doc_count = sum(1 for document in documents if document.person_id == person.person_id)
            position = f" — {person.position}" if person.position else ""
            lines.append(f"- {person.name}{position} [{doc_count} docs]")
    else:
        lines.append("- none — documents come from org-attributed feeds only")

    # Org feeds can be attached to any watcher, not only people employed here
    # (a partner's firm blog is attached to the partner).
    org_sources = [
        (person, feed)
        for person in sorted(people_by_id.values(), key=lambda entry: entry.name)
        for feed in person.sources
        if feed.attribution == FeedAttribution.ORGANIZATION
        and _company_key(feed.org_name or "") == key
    ]
    if org_sources:
        lines.extend(["", "## Organization sources", ""])
        for person, feed in org_sources:
            lines.append(f"- {feed.url} ({feed.kind.value}, via {person.name})")

    lines.extend(["", "## What its people argue", ""])
    cards = 0
    missing_cards: list[str] = []
    for person in sorted(people, key=lambda entry: entry.name):
        card = storage.get_pov_card(person.person_id)
        if card is None:
            missing_cards.append(person.name)
            continue
        cards += 1
        lines.append(f"### {person.name} (POV card of {card.generated_at.date().isoformat()})")
        for stance in card.stances:
            via = f" — via {stance.organization}" if stance.organization else ""
            lines.append(f"- [inference] {stance.statement}")
            lines.append(f'  [fact] "{stance.quote}" ({stance.doc_title}{via})')
        lines.append("")
    if cards == 0:
        lines.append("No POV cards yet for anyone here.")
        lines.append("")

    lines.append("## Signals (computed)")
    lines.append("")
    alignment = company_alignment(storage, display)
    if alignment is not None:
        lines.append(f"- Alignment with your corpus: {alignment:.3f}")
    else:
        lines.append("- Alignment with your corpus: unavailable (run 'wingman embed')")
    try:
        similar = similar_companies(storage, name=display, limit=5)
    except IngestError:
        similar = None
    if similar is not None and similar.companies:
        neighbours = ", ".join(f"{entry.name} ({entry.score:.3f})" for entry in similar.companies)
        lines.append(f"- Companies writing about similar things: {neighbours}")

    gaps: list[str] = []
    if missing_cards:
        pretty = ", ".join(missing_cards)
        gaps.append(f"POV cards missing for: {pretty} — build with 'wingman people pov <name>'")
    if not documents:
        gaps.append("no stored writing yet — 'wingman people fetch' or attach an org feed")
    if gaps:
        lines.extend(["", "## Gaps", ""])
        lines.extend(f"- {gap}" for gap in gaps)

    markdown = "\n".join(lines).rstrip() + "\n"
    directory = config.reports_dir / "companies"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_slug(display)}-{now.date().isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    _logger.info(
        "company_dossier company=%s people=%d documents=%d cards=%d path=%s",
        display,
        len(people),
        len(documents),
        cards,
        path,
    )
    return DossierReport(company=display, path=str(path), markdown=markdown)
