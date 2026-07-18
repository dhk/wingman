"""Wingman command-line interface."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import typer

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.assess import assess_job
from wingman.application.corpus import add_to_corpus, find_evidence
from wingman.application.ingest import IngestError, ingest_resume
from wingman.application.linkedin import import_linkedin
from wingman.application.people import (
    add_person,
    attach_feed,
    discover_feed,
    fetch_person_feed,
    find_people_evidence,
    seed_from_connections,
)
from wingman.application.demo import DEMO_REFERENCE_PERSON, seed_demo_watchlist
from wingman.application.similarity import embed_missing, people_like, similar_people
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.logs import configure_logging
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.providers.base import CapabilityClass, ProviderError
from wingman.providers.embeddings import EmbeddingError
from wingman.providers.router import (
    DEFAULT_MODELS_TOML,
    ModelConfigError,
    get_embedding_provider,
    get_provider,
)

app = typer.Typer(help="Wingman: local-first career intelligence.")
corpus_app = typer.Typer(help="Manage the corpus: your writing as citable evidence.")
app.add_typer(corpus_app, name="corpus")
people_app = typer.Typer(help="Watchlist of people and their public writing.")
app.add_typer(people_app, name="people")

MIN_PYTHON = (3, 12)


def _workspace_dirs(config: Config) -> list[Path]:
    return [config.data_dir, config.inbox_dir, config.reports_dir]


@app.command()
def init() -> None:
    """Initialize the local Wingman workspace (idempotent).

    The workspace lives in $WINGMAN_DATA_DIR if set, otherwise the platform
    user data directory.
    """
    configure_logging()
    config = load_config()
    created: list[Path] = []
    for directory in _workspace_dirs(config):
        if not directory.exists():
            try:
                directory.mkdir(parents=True)
            except OSError as exc:
                typer.echo(
                    f"init failed: could not create {directory} ({exc}). "
                    f"Directories already created were left in place: {created or 'none'}. "
                    f"Fix permissions or set {ENV_DATA_DIR} to a writable path, then re-run "
                    "'wingman init'.",
                    err=True,
                )
                raise typer.Exit(code=1) from exc
            created.append(directory)
    try:
        Storage(config.db_path).close()
    except sqlite3.Error as exc:
        typer.echo(
            f"init failed: could not initialize the database at {config.db_path} ({exc}). "
            "The workspace directories were preserved. Check that the disk is writable, "
            "then re-run 'wingman init'.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    if not config.models_config_path.exists():
        config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    typer.echo(f"Workspace ready at {config.data_dir} (from {config.data_dir_source}).")


@app.command()
def doctor() -> None:
    """Check the local Wingman environment and report each result."""
    configure_logging()
    config = load_config()
    failures = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        if not ok:
            failures += 1
        typer.echo(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")

    python_ok = sys.version_info[:2] >= MIN_PYTHON
    report(
        "python",
        python_ok,
        f"{sys.version_info.major}.{sys.version_info.minor} "
        f"(requires >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
    )
    report("config", True, f"data dir {config.data_dir} resolved from {config.data_dir_source}")
    if config.data_dir.exists():
        try:
            probe = config.data_dir / ".doctor-write-probe"
            probe.touch()
            probe.unlink()
            report("data dir", True, f"{config.data_dir} is writable")
        except OSError as exc:
            report("data dir", False, f"{config.data_dir} is not writable ({exc})")
    else:
        report("data dir", False, f"{config.data_dir} does not exist; run 'wingman init'")
    if config.db_path.exists():
        try:
            with Storage(config.db_path) as storage:
                storage.count_source_records()
            report("database", True, f"{config.db_path} is reachable")
        except sqlite3.Error as exc:
            report("database", False, f"{config.db_path} could not be opened ({exc})")
    else:
        report("database", False, f"{config.db_path} does not exist; run 'wingman init'")
    if config.models_config_path.exists():
        try:
            embedder = get_embedding_provider(config)
        except ModelConfigError as exc:
            report("embeddings", False, str(exc))
        else:
            key_note = ""
            if (
                embedder.provider_name == "voyage"
                and not os.environ.get("VOYAGE_API_KEY", "").strip()
            ):
                key_note = " — VOYAGE_API_KEY is not set, so 'wingman embed' will fail until it is"
            report(
                "embeddings",
                True,
                f"{embedder.provider_name}/{embedder.model}{key_note}",
            )

    if failures:
        typer.echo(f"{failures} check(s) failed.", err=True)
        raise typer.Exit(code=1)
    typer.echo("All checks passed.")


@app.command()
def demo() -> None:
    """A guided tour on real data, in an isolated demo workspace.

    Runs entirely inside its own workspace (a 'demo' folder next to your
    real one) — your workspace, corpus, and watchlist are never read,
    written, or sent anywhere. Network behavior, stated exactly: public
    Substack feeds are fetched (RFC-009), and if a Voyage key is configured
    those fetched public posts are sent to the embeddings provider
    (RFC-010); without a key, similarity runs on the local 'hashed'
    provider and nothing leaves the machine. Re-running is safe; delete
    the demo folder to remove every trace.
    """
    configure_logging()
    real_config = load_config()
    config = Config(
        data_dir=real_config.data_dir / "demo",
        data_dir_source=f"demo workspace inside {real_config.data_dir}",
    )
    for directory in _workspace_dirs(config):
        directory.mkdir(parents=True, exist_ok=True)
    if not config.models_config_path.exists():
        config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    typer.echo("\n=== Wingman demo: a real watchlist, real public writing ===")
    typer.echo(f"    (isolated workspace: {config.data_dir} — your data is untouched)\n")

    with Storage(config.db_path) as storage:
        seed, _ = seed_demo_watchlist(storage)
        typer.echo(
            f"[1/4] Watchlist seeded: {seed.added} publications added, "
            f"{seed.already_present} already present."
        )

        typer.echo("[2/4] Fetching public feeds (explicit read-only HTTPS, RFC-009)...")
        fetched = 0
        failed = 0
        new_posts = 0
        for person in storage.list_people():
            if not person.substack_url:
                continue
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                failed += 1
                typer.echo(f"      {person.name}: {exc}", err=True)
                continue
            fetched += 1
            new_posts += report.added
        typer.echo(
            f"      {fetched} feeds fetched, {new_posts} new posts archived"
            + (f", {failed} feeds unreachable (shown above)" if failed else "")
        )
        if fetched == 0:
            typer.echo(
                "\nNo feeds could be fetched — the demo needs network access to public "
                "Substack feeds. Everything already stored remains usable.",
                err=True,
            )
            raise typer.Exit(code=1)

        typer.echo('[3/4] Evidence search — who has said what about "AI":')
        try:
            hits = find_people_evidence("AI", storage, limit=3)
        except CorpusSearchError as exc:
            typer.echo(f"      search failed: {exc}", err=True)
            hits = []
        for hit in hits:
            typer.echo(f"      {hit.person_name} — {hit.document.title}")
            typer.echo(f"        {hit.snippet}")
        if not hits:
            typer.echo("      (no matches in the fetched posts — try 'wingman people evidence')")

        typer.echo("[4/4] Similarity — embedding the fetched writing...")
        try:
            embedder = get_embedding_provider(config)
        except ModelConfigError as exc:
            typer.echo(f"      embed skipped: {exc}", err=True)
            embedder = None
        if (
            embedder is not None
            and embedder.provider_name == "voyage"
            and not os.environ.get("VOYAGE_API_KEY", "").strip()
        ):
            from wingman.providers.embeddings import HashedEmbeddingProvider

            typer.echo(
                "      VOYAGE_API_KEY is not set — using the local 'hashed' provider "
                "(keyword-level, no network). Set the key and re-run the demo for "
                "real semantic quality."
            )
            embedder = HashedEmbeddingProvider()
        elif embedder is not None and embedder.provider_name == "voyage":
            typer.echo(
                f"      Sending the fetched public posts to {embedder.provider_name}/"
                f"{embedder.model} for embedding (RFC-010 — demo posts only, "
                "never your own data)."
            )
        if embedder is not None:
            try:
                embed_report = embed_missing(storage, embedder)
                typer.echo(
                    f"      embedded {embed_report.external_embedded} posts "
                    f"({embed_report.provider}/{embed_report.model})"
                )
                similar = similar_people(storage, name=DEMO_REFERENCE_PERSON, limit=5)
                typer.echo(f"      Closest to {similar.reference}:")
                for number, entry in enumerate(similar.people, start=1):
                    typer.echo(f"      {number}. {entry.name}  score {entry.score:.3f}")
            except (IngestError, EmbeddingError) as exc:
                typer.echo(f"      similarity skipped: {exc}", err=True)

    typer.echo(
        "\n=== Demo complete. Make it yours ===\n"
        "  wingman init                                   # your real workspace\n"
        "  wingman ingest-linkedin <your-export.zip>      # your cited profile\n"
        "  wingman people import-connections <export.zip> # your network\n"
        "  wingman corpus add <your-writing>              # your evidence\n"
        "  wingman people add / fetch / similar           # your watchlist\n"
        f"Full guide: docs/SETUP.md. The demo lived in {config.data_dir} — "
        "delete that folder to remove every trace."
    )


@app.command()
def sync() -> None:
    """Fetch every watched source and embed whatever is new — one command.

    The maintenance loop as a single explicit invocation (RFC-009 holds:
    running this command is the consent). Per-person failures are reported
    and skipped, never silently hidden. Embedding degrades per RFC-010: with
    no VOYAGE_API_KEY, either configure the local 'hashed' provider in
    models.toml or expect the embed step to fail visibly (exit 1) while the
    fetched posts are kept.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "synced")
    exit_code = 0
    with Storage(config.db_path) as storage:
        targets = [person for person in storage.list_people() if person.sources]
        if not targets:
            typer.echo("No people have sources configured — nothing to sync.")
            return
        fetched = 0
        failed = 0
        new_posts = 0
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                failed += 1
                typer.echo(f"  {person.name}: fetch failed: {exc}", err=True)
                continue
            fetched += 1
            new_posts += report.added
            if report.added:
                typer.echo(f"  {person.name}: +{report.added} new")
        typer.echo(
            f"Fetched {fetched}/{len(targets)} people  new posts: {new_posts}"
            + (f"  failures: {failed}" if failed else "")
        )
        if fetched == 0:
            typer.echo("Every fetch failed; embedding was not attempted.", err=True)
            raise typer.Exit(code=1)
        try:
            provider = get_embedding_provider(config)
            embed_report = embed_missing(storage, provider)
            typer.echo(
                f"Embedded {embed_report.corpus_embedded + embed_report.external_embedded} "
                f"new documents ({embed_report.provider}/{embed_report.model})"
            )
        except (ModelConfigError, EmbeddingError) as exc:
            typer.echo(
                f"Embedding skipped: {exc}\nFetched posts were kept; keyword search works. "
                "Fix the embeddings configuration and re-run 'wingman sync' or 'wingman embed'.",
                err=True,
            )
            exit_code = 1
    if exit_code:
        raise typer.Exit(code=exit_code)
    typer.echo("Sync complete.")


@app.command()
def status() -> None:
    """Show the current Wingman workspace status."""
    configure_logging()
    config = load_config()
    typer.echo(f"Workspace: {config.data_dir} (from {config.data_dir_source})")
    if not config.db_path.exists():
        typer.echo("Database: not initialized — run 'wingman init'.")
        return
    with Storage(config.db_path) as storage:
        sources = storage.count_source_records()
        items = storage.count_profile_items()
        opportunities = storage.count_opportunities()
        documents = storage.count_corpus_documents()
        people = storage.count_people()
        external = storage.count_external_documents()
    typer.echo(f"Database: {config.db_path}")
    typer.echo(f"Source records: {sources}")
    typer.echo(f"Profile items: {items}")
    typer.echo(f"Opportunities: {opportunities}")
    typer.echo(f"Corpus documents: {documents}")
    typer.echo(f"People: {people}")
    typer.echo(f"External documents: {external}")


@app.command()
def ingest(
    resume: Path = typer.Argument(..., help="Path to a resume in Markdown or plain text."),
) -> None:
    """Ingest a resume into the canonical profile and write career.json / career.md."""
    configure_logging()
    config = load_config()
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was ingested; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
        with Storage(config.db_path) as storage:
            report = ingest_resume(resume, config, storage, provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"ingest failed: {exc}. The source record was preserved; the profile was not "
            "changed. Re-run 'wingman ingest' to retry the extraction.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Source record: {report.source_record_id}" + (" (reused)" if report.source_reused else "")
    )
    typer.echo(
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Rejected: {len(report.rejected)}"
    )
    for rejected in report.rejected:
        typer.echo(f"  rejected {rejected.name!r}: {rejected.reason}")
    typer.echo(f"Model: {report.provider}/{report.model} (prompt {report.prompt_version})")
    typer.echo(f"Wrote {report.career_json_path}")
    typer.echo(f"Wrote {report.career_md_path}")


@app.command()
def assess(
    job: Path = typer.Argument(..., help="Path to a job description in Markdown or plain text."),
) -> None:
    """Assess a job description against the profile and write a cited fit brief."""
    configure_logging()
    config = load_config()
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was assessed; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        extract_provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
        assess_provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        with Storage(config.db_path) as storage:
            report = assess_job(job, config, storage, extract_provider, assess_provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"assess failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"assess failed: {exc}. The source record was preserved; no opportunity was "
            "created or changed. Re-run 'wingman assess' to retry.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"Opportunity: {report.title} ({report.opportunity_id})")
    verdicts = "  ".join(f"{name}: {count}" for name, count in sorted(report.verdicts.items()))
    typer.echo(f"Requirements: {report.requirements}  {verdicts}")
    for rejected in report.rejected_requirements:
        typer.echo(f"  rejected requirement {rejected.name!r}: {rejected.reason}")
    for note in report.downgraded:
        typer.echo(f"  validation: {note}")
    typer.echo(f"Next action: {report.next_action}")
    typer.echo(f"Wrote {report.brief_json_path}")
    typer.echo(f"Wrote {report.brief_md_path}")


@app.command("ingest-linkedin")
def ingest_linkedin(
    export: Path = typer.Argument(..., help="Path to a LinkedIn data-export zip."),
) -> None:
    """Import positions, skills, and recommendations from a LinkedIn export."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "imported")
    try:
        with Storage(config.db_path) as storage:
            report = import_linkedin(export, config, storage)
    except IngestError as exc:
        typer.echo(f"ingest-linkedin failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Found: {report.positions} positions, {report.skills} skills, "
        f"{report.recommendations} recommendations"
    )
    counts = report.counts
    typer.echo(
        f"Accepted: {counts.accepted}  Duplicates skipped: {counts.skipped_duplicates}  "
        f"Evidence merged: {counts.evidence_merged}  Conflicts: {counts.conflicts}"
    )
    typer.echo(f"Wrote {report.career_json_path}")
    typer.echo(f"Wrote {report.career_md_path}")


def _require_workspace(config: Config, action: str) -> None:
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was {action}; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)


@corpus_app.command("add")
def corpus_add(
    path: Path = typer.Argument(
        ..., help="A file, a directory, or a zip export (e.g. a Substack export)."
    ),
    source_type: str = typer.Option(
        "writing",
        "--source-type",
        help="Provenance label, e.g. substack_post, github_readme, linkedin_export.",
    ),
) -> None:
    """Add writing to the corpus: Markdown, plain text, HTML, or a zip of them."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "added")
    try:
        with Storage(config.db_path) as storage:
            report = add_to_corpus(path, source_type, config, storage)
    except IngestError as exc:
        typer.echo(f"corpus add failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Added: {report.added}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Unsupported: {len(report.skipped_unsupported)}  Failures: {len(report.failures)}"
    )
    for title in report.titles:
        typer.echo(f"  + {title}")
    for failure in report.failures:
        typer.echo(f"  failed {failure.name!r}: {failure.reason}", err=True)


@corpus_app.command("list")
def corpus_list() -> None:
    """List corpus documents."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        documents = storage.list_corpus_documents()
    if not documents:
        typer.echo("Corpus is empty — add writing with 'wingman corpus add <path>'.")
        return
    for document in documents:
        when = document.published_at.date().isoformat() if document.published_at else "unknown"
        typer.echo(
            f"{document.doc_id}  [{document.source_type}]  {when}  {document.title}"
            f"  ({document.word_count} words)"
        )


@people_app.command("add")
def people_add(
    name: str = typer.Argument(..., help="The person's name."),
    substack: str | None = typer.Option(
        None, "--substack", help="Their public Substack URL, e.g. https://example.substack.com"
    ),
    company: str | None = typer.Option(None, "--company", help="Where they work."),
    position: str | None = typer.Option(None, "--position", help="What they do."),
) -> None:
    """Add a person to the watchlist (or update them if already known)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "added")
    try:
        with Storage(config.db_path) as storage:
            person, created = add_person(
                name, storage, substack_url=substack, company=company, position=position
            )
    except IngestError as exc:
        typer.echo(f"people add failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    verb = "Added" if created else "Updated"
    feed = f"  substack: {person.substack_url}" if person.substack_url else ""
    typer.echo(f"{verb} {person.name} ({person.person_id}){feed}")


@people_app.command("list")
def people_list(
    watched: bool = typer.Option(
        False, "--watched", help="Only people with at least one source configured."
    ),
) -> None:
    """List people on the watchlist."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        people = storage.list_people()
    if watched:
        people = [person for person in people if person.sources]
    if not people:
        typer.echo(
            "No people yet — add one with 'wingman people add' or seed from a LinkedIn "
            "export with 'wingman people import-connections'."
        )
        return
    for person in people:
        where = ", ".join(part for part in (person.position, person.company) if part)
        sources = person.sources
        feed = f"  [{', '.join(source.url for source in sources)}]" if sources else ""
        detail = f"  ({where})" if where else ""
        typer.echo(f"{person.name}{detail}{feed}")
    typer.echo(f"{len(people)} people.")


@people_app.command("add-feed")
def people_add_feed(
    name: str = typer.Argument(..., help="Person on the watchlist to attach the source to."),
    url: str = typer.Argument(..., help="A feed URL or a blog homepage/index page (https)."),
    org: str | None = typer.Option(
        None,
        "--org",
        help="Attribute posts to this organization (e.g. a company blog) instead of the person.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Attach without the confirmation prompt."),
) -> None:
    """Attach any public feed to a person: RSS/Atom, Medium, or a feed-less blog index.

    Paste a feed URL or a homepage — Wingman fetches it once, autodiscovers
    the feed (or offers the page as an index source when no feed exists), and
    attaches only after you confirm (RFC-011): discovery can succeed on the
    wrong person's feed, so you get the final say.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "attached")
    from wingman.domain.person import FeedAttribution, FeedKind, FeedSource

    attribution = FeedAttribution.ORGANIZATION if org else FeedAttribution.PERSON
    with Storage(config.db_path) as storage:
        name_key = " ".join(name.lower().split())
        person = storage.find_person_by_name_key(name_key)
        if person is None:
            typer.echo(
                f"No person named {name!r}. Nothing was attached; add them first with "
                "'wingman people add'.",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            discovery = discover_feed(url)
        except IngestError as exc:
            typer.echo(f"add-feed failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if discovery.feed_url:
            typer.echo(f"Found feed: {discovery.feed_url}  (titled {discovery.feed_title!r})")
            if not yes and not typer.confirm(f"Attach this feed to {person.name}?"):
                typer.echo("Nothing was attached.")
                raise typer.Exit(code=0)
            source = FeedSource(
                url=discovery.feed_url,
                kind=FeedKind.RSS,
                attribution=attribution,
                org_name=org,
            )
        else:
            typer.echo(
                f"No feed found at or near {url} (probed {len(discovery.probed)} URLs). "
                "The page can be watched as an index source instead: on each fetch, "
                "Wingman reads this one page and ingests new posts linked under it."
            )
            if not yes and not typer.confirm(f"Watch {url} as an index page for {person.name}?"):
                typer.echo("Nothing was attached.")
                raise typer.Exit(code=0)
            source = FeedSource(
                url=url.rstrip("/"),
                kind=FeedKind.INDEX_PAGE,
                attribution=attribution,
                org_name=org,
            )
        try:
            attach_feed(person, source, storage)
        except IngestError as exc:
            typer.echo(f"add-feed failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    label = f" (attributed to {org})" if org else ""
    typer.echo(f"Attached {source.kind.value} source to {person.name}: {source.url}{label}")


@people_app.command("import-connections")
def people_import_connections(
    export: Path = typer.Argument(..., help="Path to a LinkedIn data-export zip."),
) -> None:
    """Seed Person records from Connections.csv (names and roles only — never emails)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "seeded")
    try:
        with Storage(config.db_path) as storage:
            report = seed_from_connections(export, storage)
    except IngestError as exc:
        typer.echo(f"import-connections failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Created: {report.created}  Already known: {report.skipped_existing}  "
        f"Incomplete rows skipped: {report.skipped_incomplete}"
    )


@people_app.command("fetch")
def people_fetch(
    name: str | None = typer.Argument(None, help="Person to fetch; omit with --all."),
    fetch_all: bool = typer.Option(
        False, "--all", help="Fetch every person with at least one source configured."
    ),
) -> None:
    """Fetch new posts from a person's public sources (explicit, read-only)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "fetched")
    with Storage(config.db_path) as storage:
        if fetch_all:
            targets = [person for person in storage.list_people() if person.sources]
            if not targets:
                typer.echo("No people have any sources configured. Nothing was fetched.")
                return
        elif name is None:
            typer.echo("Name a person or pass --all. Nothing was fetched.", err=True)
            raise typer.Exit(code=1)
        else:
            name_key = " ".join(name.lower().split())
            person = storage.find_person_by_name_key(name_key)
            if person is None:
                typer.echo(
                    f"No person named {name!r}. Nothing was fetched; see 'wingman people list'.",
                    err=True,
                )
                raise typer.Exit(code=1)
            targets = [person]
        failures = 0
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                failures += 1
                typer.echo(f"  {person.name}: fetch failed: {exc}", err=True)
                continue
            typer.echo(
                f"{report.person_name}: {report.items} posts in feed  "
                f"added: {report.added}  duplicates: {report.skipped_duplicates}  "
                f"empty: {report.skipped_empty}"
            )
            for title in report.titles:
                typer.echo(f"  + {title}")
    if failures:
        raise typer.Exit(code=1)


@app.command()
def embed() -> None:
    """Embed corpus and people's writing for semantic similarity (RFC-010).

    The one explicit data-egress step: document text is sent to the configured
    embeddings provider, once per new document.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "embedded")
    try:
        provider = get_embedding_provider(config)
        with Storage(config.db_path) as storage:
            report = embed_missing(storage, provider)
    except (ModelConfigError, EmbeddingError) as exc:
        typer.echo(f"embed failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Provider: {report.provider}/{report.model}")
    typer.echo(
        f"Embedded: {report.corpus_embedded} corpus + {report.external_embedded} external  "
        f"(re-embedded after model change: {report.reembedded})  "
        f"Already embedded: {report.already_embedded}  Empty skipped: {report.skipped_empty}"
    )


@people_app.command("similar")
def people_similar(
    name: str | None = typer.Argument(
        None, help="Person to compare against; omit to compare against your own corpus."
    ),
    limit: int = typer.Option(10, "--limit", help="How many people to show."),
) -> None:
    """Who thinks about the same things — as this person, or as you."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "compared")
    try:
        with Storage(config.db_path) as storage:
            report = similar_people(storage, name=name, limit=limit)
    except IngestError as exc:
        typer.echo(f"people similar failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not report.people:
        typer.echo(
            "No other people have embedded writing yet — fetch feeds and run 'wingman embed'."
        )
        return
    typer.echo(f"Closest to {report.reference}:")
    for number, entry in enumerate(report.people, start=1):
        where = ", ".join(part for part in (entry.position, entry.company) if part)
        detail = f"  ({where})" if where else ""
        typer.echo(
            f"{number}. {entry.name}{detail}  score {entry.score:.3f}  [{entry.documents} docs]"
        )


@people_app.command("like")
def people_like_cmd(
    names: list[str] = typer.Argument(
        ..., help="Two or more people you find interesting, e.g. 'Mario Rossi' 'Brian Chen'."
    ),
    limit: int = typer.Option(10, "--limit", help="How many people to show."),
) -> None:
    """'If you like these people, you should be talking to…'"""
    configure_logging()
    config = load_config()
    _require_workspace(config, "compared")
    try:
        with Storage(config.db_path) as storage:
            report = people_like(storage, names=names, limit=limit)
    except IngestError as exc:
        typer.echo(f"people like failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not report.people:
        typer.echo(
            "No other people have embedded writing yet — fetch feeds and run 'wingman embed'."
        )
        return
    typer.echo(f"Closest to {report.reference}:")
    for number, entry in enumerate(report.people, start=1):
        where = ", ".join(part for part in (entry.position, entry.company) if part)
        detail = f"  ({where})" if where else ""
        typer.echo(
            f"{number}. {entry.name}{detail}  score {entry.score:.3f}  [{entry.documents} docs]"
        )


@people_app.command("evidence")
def people_evidence(
    query: str = typer.Argument(..., help="Words or a quoted phrase to search for."),
    limit: int = typer.Option(10, "--limit", help="Maximum number of excerpts."),
) -> None:
    """Search people's writing: who has said what about this topic."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "searched")
    try:
        with Storage(config.db_path) as storage:
            hits = find_people_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        typer.echo(f"people evidence search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not hits:
        typer.echo(f"No evidence found in people's writing for {query!r}.")
        return
    for number, hit in enumerate(hits, start=1):
        when = (
            hit.document.published_at.date().isoformat() if hit.document.published_at else "undated"
        )
        via = f" (via {hit.document.organization})" if hit.document.organization else ""
        typer.echo(f"{number}. {hit.person_name}{via} — {hit.document.title} [{when}]")
        typer.echo(f"   {hit.snippet}")
        typer.echo(f"   source: {hit.document.url or hit.document.source_record_id}")


@app.command()
def evidence(
    query: str = typer.Argument(..., help="Words or a quoted phrase to search for."),
    limit: int = typer.Option(10, "--limit", help="Maximum number of excerpts."),
) -> None:
    """Search the corpus: 'you have this evidence, here'."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "searched")
    try:
        with Storage(config.db_path) as storage:
            hits = find_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        typer.echo(f"evidence search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not hits:
        typer.echo(f"No corpus evidence found for {query!r}.")
        return
    for number, hit in enumerate(hits, start=1):
        typer.echo(f"{number}. {hit.document.title} [{hit.document.source_type}]")
        typer.echo(f"   {hit.snippet}")
        typer.echo(f"   source: {hit.source_locator} (doc {hit.document.doc_id})")


if __name__ == "__main__":
    app()
