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

import re
from datetime import UTC, datetime

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.pov import company_card_id
from wingman.application.research import RESEARCH_STALE_AFTER_DAYS
from wingman.application.similarity import (
    company_key,
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
    """A filesystem-safe filename fragment: company names are arbitrary user
    data, so everything outside [a-z0-9] becomes a hyphen (no path separators,
    no dot-dot)."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", company_key(name)).strip("-")
    return cleaned or "company"


def build_company_dossier(name: str, config: Config, storage: Storage) -> DossierReport:
    """Compose and write the dated dossier for one company. Local data only."""
    key = company_key(name)
    if not key:
        raise IngestError(
            "company name is empty — an empty key would match every person with no company set."
        )
    all_people = storage.list_people()
    people = [person for person in all_people if company_key(person.company or "") == key]
    people_by_id = {person.person_id: person for person in all_people}
    documents: list[ExternalDocument] = []
    for document in storage.list_external_documents():
        person = people_by_id.get(document.person_id)
        via_person = person is not None and company_key(person.company or "") == key
        via_org = company_key(document.organization or "") == key
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
        and company_key(feed.org_name or "") == key
    ]
    if org_sources:
        lines.extend(["", "## Organization sources", ""])
        for person, feed in org_sources:
            lines.append(f"- {feed.url} ({feed.kind.value}, via {person.name})")

    research_sources = storage.list_company_sources(key)
    if research_sources:
        lines.extend(["", "## Research (approved sources)", ""])
        for source in research_sources:
            label = f" ({source.label})" if source.label else ""
            snapshot = storage.get_research_snapshot(key, source.url)
            if snapshot is None:
                lines.append(
                    f"- {source.url}{label} — no snapshot yet; run 'wingman company research'"
                )
                continue
            entry = (
                f"- {source.url}{label} — {len(snapshot.links)} links, "
                f"snapshot {snapshot.fetched_at.date().isoformat()}"
            )
            age_days = (now - snapshot.fetched_at).days
            if age_days > RESEARCH_STALE_AFTER_DAYS:
                entry += f" — ⚠ stale ({age_days} days old); re-run 'wingman company research'"
            lines.append(entry)

    company_card = storage.get_pov_card(company_card_id(key))
    if company_card is not None:
        lines.extend(
            [
                "",
                f"## Company themes (synthesized {company_card.generated_at.date().isoformat()},"
                f" {company_card.provider}/{company_card.model})",
                "",
            ]
        )
        for stance in company_card.stances:
            tag = f"[{stance.dimension.value}] " if stance.dimension else ""
            lines.append(f"- [inference] {tag}{stance.statement}")
            lines.append(f'  [fact] "{stance.quote}" ({stance.doc_title})')
        if company_card.topics:
            lines.append("")
            lines.append("Writes about: " + ", ".join(company_card.topics))

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
    if company_card is None and documents:
        gaps.append(
            f"no synthesized company themes — 'wingman company pov \"{display}\"' "
            "builds them (a model call, validated quote-by-quote)"
        )
    if not research_sources:
        gaps.append(
            f'no approved research sources — \'wingman company add-source "{display}" '
            "<https-url>' names a careers page or newsroom to watch"
        )
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
