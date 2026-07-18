"""Wingman MCP server: the workspace as tools for a local MCP client (RFC-008).

A stdio server for Claude Desktop / Claude Code on the same machine. The
workspace never leaves the machine; every tool runs the same deterministic
validation pipelines as the CLI, so the connected model can request work but
cannot bypass evidence rules. Network access mirrors the CLI exactly:
fetch/sync/discover tools do the same explicit, read-only public-feed reads
as their CLI counterparts (RFC-009/011), and embedding tools carry the same
RFC-010 egress semantics.

Parity rule (RFC-008): the MCP surface tracks the CLI — every user-facing
capability ships on both. The one adaptation: the CLI's interactive
confirm-before-attach for feeds becomes a two-tool pair here
(feed_discover, then feed_attach only after the user says yes in
conversation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.assess import assess_job as assess_job_use_case
from wingman.application.corpus import find_evidence
from wingman.application.ingest import IngestError, ingest_resume
from wingman.application.people import (
    add_person,
    attach_feed,
    discover_feed,
    discover_recommendations,
    fetch_person_feed,
    find_people_evidence,
    seed_from_connections,
)
from wingman.application.pov import build_pov_card, render_pov_card
from wingman.application.similarity import (
    CompanySimilarityReport,
    SimilarPerson,
    companies_like,
    embed_missing,
    similar_companies,
    similar_people,
)
from wingman.application.similarity import people_like as people_like_use_case
from wingman.domain.person import FeedAttribution, FeedKind, FeedSource
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.logs import configure_logging
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.providers.base import CapabilityClass, ProviderError
from wingman.providers.embeddings import EmbeddingError
from wingman.providers.router import ModelConfigError, get_embedding_provider, get_provider

server = FastMCP("wingman")

_NOT_INITIALIZED = (
    "The Wingman workspace is not initialized on this machine. "
    "Run 'wingman init' in a terminal first."
)


def _ready_config() -> Config | None:
    config = load_config()
    return config if config.db_path.exists() else None


@server.tool()
def status() -> str:
    """Workspace status: counts of source records, profile items, opportunities, and corpus documents."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        return (
            f"Workspace: {config.data_dir}\n"
            f"Source records: {storage.count_source_records()}\n"
            f"Profile items: {storage.count_profile_items()}\n"
            f"Opportunities: {storage.count_opportunities()}\n"
            f"Corpus documents: {storage.count_corpus_documents()}"
        )


@server.tool()
def evidence(query: str, limit: int = 10) -> str:
    """Search the user's own writing (corpus) for cited evidence.

    Full-text query: plain words, quoted phrases, or AND/OR/NOT. Returns
    ranked excerpts with their source documents; quotes are verbatim.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            hits = find_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        return f"Search failed: {exc}"
    if not hits:
        return f"No corpus evidence found for {query!r}."
    lines = []
    for number, hit in enumerate(hits, start=1):
        when = (
            hit.document.published_at.date().isoformat() if hit.document.published_at else "undated"
        )
        lines.append(
            f"{number}. {hit.document.title} [{hit.document.source_type}, {when}]\n"
            f"   {hit.snippet}\n"
            f"   source: {hit.source_locator}"
        )
    return "\n".join(lines)


@server.tool()
def career_profile() -> str:
    """Return the current cited career profile (career.md), including roles, achievements, skills, and testimonials with their evidence."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    career_md = config.reports_dir / "career.md"
    if not career_md.exists():
        return (
            "No career profile has been generated yet. Ingest a resume "
            "('wingman ingest') or a LinkedIn export ('wingman ingest-linkedin') first."
        )
    return career_md.read_text(encoding="utf-8")


@server.tool()
def assess_job(job_description: str) -> str:
    """Assess a job description against the career profile; returns the cited fit brief.

    Requirements are extracted with verbatim quotes and each is judged
    met/partial/gap/unknown citing only real profile items — unsupported
    verdicts are downgraded by deterministic validation.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not job_description.strip():
        return "The job description is empty; nothing was assessed."
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    job_path = config.inbox_dir / f"{stamp}-pasted-job.md"
    job_path.write_text(job_description, encoding="utf-8")
    try:
        with Storage(config.db_path) as storage:
            report = assess_job_use_case(
                job_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
                get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Assessment failed: {exc}"
    return Path(report.brief_md_path).read_text(encoding="utf-8")


@server.tool()
def ingest_resume_text(resume_markdown: str, filename: str = "resume.md") -> str:
    """Ingest resume text into the canonical profile (model extraction plus deterministic evidence validation). Returns the ingestion summary."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not resume_markdown.strip():
        return "The resume text is empty; nothing was ingested."
    # Untrusted input: strip any path components so the write stays in the inbox.
    safe_name = Path(filename).name or "resume.md"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    resume_path = config.inbox_dir / f"{stamp}-{safe_name}"
    resume_path.write_text(resume_markdown, encoding="utf-8")
    try:
        with Storage(config.db_path) as storage:
            report = ingest_resume(
                resume_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Ingestion failed: {exc}"
    rejected = "".join(f"\n  rejected {item.name!r}: {item.reason}" for item in report.rejected)
    return (
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Rejected: {len(report.rejected)}{rejected}\n"
        f"Profile written to {report.career_md_path}"
    )


def _name_key(name: str) -> str:
    return " ".join(name.lower().split())


def _similarity_lines(reference: str, people: list[SimilarPerson]) -> str:
    lines = [f"Closest to {reference}:"]
    for number, entry in enumerate(people, start=1):
        where = ", ".join(part for part in (entry.position, entry.company) if part)
        detail = f" ({where})" if where else ""
        lines.append(
            f"{number}. {entry.name}{detail}  score {entry.score:.3f}  [{entry.documents} docs]"
        )
    return "\n".join(lines)


@server.tool()
def people_add(name: str, substack_url: str = "", company: str = "", position: str = "") -> str:
    """Add a person to the watchlist (or update them), optionally with their Substack URL."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            person, created = add_person(
                name,
                storage,
                substack_url=substack_url or None,
                company=company or None,
                position=position or None,
            )
    except IngestError as exc:
        return f"people add failed: {exc}"
    verb = "Added" if created else "Updated"
    sources = ", ".join(source.url for source in person.sources) or "no sources yet"
    return f"{verb} {person.name}. Sources: {sources}"


@server.tool()
def people_list(watched_only: bool = False) -> str:
    """List watchlist people; watched_only limits to those with at least one source."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        people = storage.list_people()
    if watched_only:
        people = [person for person in people if person.sources]
    if not people:
        return "No people yet — add one with people_add."
    lines = []
    for person in people:
        where = ", ".join(part for part in (person.position, person.company) if part)
        sources = ", ".join(source.url for source in person.sources)
        detail = f" ({where})" if where else ""
        feed = f"  [{sources}]" if sources else ""
        lines.append(f"{person.name}{detail}{feed}")
    return "\n".join([*lines, f"{len(people)} people."])


@server.tool()
def people_fetch(name: str = "") -> str:
    """Fetch new posts from watched public sources (explicit read-only HTTPS, RFC-009).

    Give a person's name, or leave empty to fetch everyone with sources.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        if name.strip():
            person = storage.find_person_by_name_key(_name_key(name))
            if person is None:
                return f"No person named {name!r}; see people_list."
            targets = [person]
        else:
            targets = [person for person in storage.list_people() if person.sources]
            if not targets:
                return "No people have sources configured. Nothing was fetched."
        lines = []
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                lines.append(f"{person.name}: fetch failed: {exc}")
                continue
            lines.append(
                f"{person.name}: {report.items} items  added: {report.added}  "
                f"duplicates: {report.skipped_duplicates}"
            )
    return "\n".join(lines)


@server.tool()
def sync() -> str:
    """Fetch every watched source and embed whatever is new (mirrors 'wingman sync').

    Embedding sends new document text to the configured embeddings provider
    (RFC-010) unless the local 'hashed' provider is configured.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    lines = []
    with Storage(config.db_path) as storage:
        targets = [person for person in storage.list_people() if person.sources]
        if not targets:
            return "No people have sources configured — nothing to sync."
        fetched = 0
        new_posts = 0
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                lines.append(f"{person.name}: fetch failed: {exc}")
                continue
            fetched += 1
            new_posts += report.added
        lines.append(f"Fetched {fetched}/{len(targets)} people  new posts: {new_posts}")
        if fetched == 0:
            return "\n".join([*lines, "Every fetch failed; embedding was not attempted."])
        try:
            provider = get_embedding_provider(config)
            embed_report = embed_missing(storage, provider)
            lines.append(
                f"Embedded {embed_report.corpus_embedded + embed_report.external_embedded} "
                f"new documents ({embed_report.provider}/{embed_report.model})"
            )
        except (ModelConfigError, EmbeddingError) as exc:
            lines.append(f"Embedding skipped: {exc} — fetched posts were kept.")
    return "\n".join(lines)


@server.tool()
def embed() -> str:
    """Embed corpus and people's writing for similarity (RFC-010 — the explicit egress step)."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        provider = get_embedding_provider(config)
        with Storage(config.db_path) as storage:
            report = embed_missing(storage, provider)
    except (ModelConfigError, EmbeddingError) as exc:
        return f"embed failed: {exc}"
    return (
        f"Embedded {report.corpus_embedded} corpus + {report.external_embedded} external "
        f"({report.provider}/{report.model}); already embedded: {report.already_embedded}"
    )


@server.tool()
def people_evidence(query: str, limit: int = 10) -> str:
    """Search watched people's writing: who has said what about a topic, with attribution."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            hits = find_people_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        return f"Search failed: {exc}"
    if not hits:
        return f"No evidence found in people's writing for {query!r}."
    lines = []
    for number, hit in enumerate(hits, start=1):
        via = f" (via {hit.document.organization})" if hit.document.organization else ""
        lines.append(
            f"{number}. {hit.person_name}{via} — {hit.document.title}\n   {hit.snippet}\n"
            f"   source: {hit.document.url or hit.document.source_record_id}"
        )
    return "\n".join(lines)


@server.tool()
def people_similar(name: str = "", limit: int = 10) -> str:
    """Rank people by similarity to one person's writing — or, with no name, to the user's own corpus."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = similar_people(storage, name=name.strip() or None, limit=limit)
    except IngestError as exc:
        return f"people similar failed: {exc}"
    if not report.people:
        return "No other people have embedded writing yet — fetch feeds and run the embed tool."
    return _similarity_lines(report.reference, list(report.people))


@server.tool()
def people_like(names: list[str], limit: int = 10) -> str:
    """'If you like these people, talk to…': rank people near the centroid of two or more names."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = people_like_use_case(storage, names=names, limit=limit)
    except IngestError as exc:
        return f"people like failed: {exc}"
    if not report.people:
        return "No other people have embedded writing yet — fetch feeds and run the embed tool."
    return _similarity_lines(report.reference, list(report.people))


def _company_lines(report: CompanySimilarityReport) -> str:
    if not report.companies:
        return (
            "No other companies have embedded writing yet — add people with a "
            "company (people_add) or attach an org-attributed feed "
            "(feed_attach), then run the sync tool."
        )
    lines = [f"Closest to {report.reference}:"]
    for number, entry in enumerate(report.companies, start=1):
        lines.append(
            f"{number}. {entry.name}  score {entry.score:.3f}  "
            f"[{entry.people} people, {entry.documents} docs]"
        )
    return "\n".join(lines)


@server.tool()
def company_similar(name: str = "", limit: int = 10) -> str:
    """Rank companies by the writing of their people and blogs — vs one company, or vs the user's corpus with no name."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = similar_companies(storage, name=name.strip() or None, limit=limit)
    except IngestError as exc:
        return f"company similar failed: {exc}"
    return _company_lines(report)


@server.tool()
def company_like(names: list[str], limit: int = 10) -> str:
    """'If these companies interest you, look at…': rank companies near the centroid of two or more names."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = companies_like(storage, names=names, limit=limit)
    except IngestError as exc:
        return f"company like failed: {exc}"
    return _company_lines(report)


@server.tool()
def people_pov(name: str, refresh: bool = False) -> str:
    """What this person thinks: an evidence-backed POV card from their stored writing.

    Returns the stored card when one exists; refresh=True rebuilds it (a
    model call — the person's stored posts go to the synthesize_balanced
    provider, and every stance is kept only if its quote appears verbatim
    in the stored document).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        person = storage.find_person_by_name_key(_name_key(name))
        if person is None:
            return f"No person named {name!r}; see people_list."
        if not refresh:
            stored = storage.get_pov_card(person.person_id)
            if stored is not None:
                return render_pov_card(stored) + "\n\n(stored card — rebuild with refresh=True)"
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_pov_card(name, storage, provider)
        except ProposalParseError as exc:
            return f"people pov failed: {exc}. Nothing was stored; call again to retry."
        except (IngestError, ModelConfigError, ProviderError) as exc:
            return f"people pov failed: {exc}"
    rejected = "".join(
        f"\n  rejected stance {item.statement!r}: {item.reason}" for item in report.rejected
    )
    return render_pov_card(report.card) + rejected


@server.tool()
def people_discover(limit: int = 10) -> str:
    """Suggest new publications from the recommendations of watched Substacks (RFC-009).

    Suggestions only — nothing is added; use people_add after the user picks.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    with Storage(config.db_path) as storage:
        report = discover_recommendations(storage, limit=limit)
    lines = [f"failed: {failure}" for failure in report.failures]
    if report.scanned == 0 and not report.failures:
        return "No watched Substacks to walk — add some with people_add first."
    if report.scanned == 0:
        return "\n".join([*lines, "Every recommendations page failed to fetch."])
    if not report.candidates:
        return "\n".join(
            [*lines, f"Scanned {report.scanned} publications — no new recommendations found."]
        )
    lines.append(f"Scanned {report.scanned} publications. Worth a look:")
    for number, candidate in enumerate(report.candidates, start=1):
        who = ", ".join(candidate.recommenders[:3])
        lines.append(
            f"{number}. {candidate.url}  (recommended by {len(candidate.recommenders)}: {who})"
        )
    return "\n".join(lines)


@server.tool()
def feed_discover(person_name: str, url: str) -> str:
    """Find a feed for a URL (direct, HTML autodiscovery, or conventional paths) — step 1 of 2.

    Never attaches anything. Present the findings to the user and call
    feed_attach ONLY after they explicitly confirm — discovery can succeed
    on the wrong person's feed (RFC-011).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        person = storage.find_person_by_name_key(_name_key(person_name))
    if person is None:
        return f"No person named {person_name!r}; add them with people_add first."
    try:
        discovery = discover_feed(url)
    except IngestError as exc:
        return f"feed discovery failed: {exc}"
    if discovery.feed_url:
        return (
            f"Found feed: {discovery.feed_url} (titled {discovery.feed_title!r}). "
            f"Ask the user to confirm before calling feed_attach for {person.name} — "
            "do not attach without their explicit yes."
        )
    return (
        f"No feed found at or near {url} (probed {len(discovery.probed)} URLs). "
        "The page can be watched as an index source instead: call feed_attach with "
        "kind='index_page' after the user explicitly confirms."
    )


@server.tool()
def feed_attach(person_name: str, url: str, kind: str = "rss", organization: str = "") -> str:
    """Attach a feed or index-page source to a person — step 2 of 2, after user confirmation.

    Call ONLY after the user explicitly confirmed the exact URL in
    conversation (RFC-011). kind is 'rss' or 'index_page'; set organization
    to attribute a company blog's posts to the organization.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if kind not in {FeedKind.RSS.value, FeedKind.INDEX_PAGE.value}:
        return f"kind must be 'rss' or 'index_page'; got {kind!r}."
    url = url.rstrip("/")  # match attach_feed's stored normalization in the echoed message
    with Storage(config.db_path) as storage:
        person = storage.find_person_by_name_key(_name_key(person_name))
        if person is None:
            return f"No person named {person_name!r}; add them with people_add first."
        source = FeedSource(
            url=url,
            kind=FeedKind(kind),
            attribution=FeedAttribution.ORGANIZATION if organization else FeedAttribution.PERSON,
            org_name=organization or None,
        )
        try:
            attach_feed(person, source, storage)
        except IngestError as exc:
            return f"feed attach failed: {exc}"
    label = f" (attributed to {organization})" if organization else ""
    return f"Attached {kind} source to {person.name}: {url}{label}"


@server.tool()
def people_import_connections(export_path: str) -> str:
    """Seed the watchlist from a LinkedIn export zip's Connections.csv (names/roles only, never emails)."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = seed_from_connections(Path(export_path).expanduser(), storage)
    except IngestError as exc:
        return f"import failed: {exc}"
    return (
        f"Created: {report.created}  Already known: {report.skipped_existing}  "
        f"Incomplete rows skipped: {report.skipped_incomplete}"
    )


def main() -> None:
    configure_logging()
    server.run()


if __name__ == "__main__":
    main()
