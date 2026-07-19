"""Follow a company: assembled focus + consented overnight deep-refresh (RFC-018).

'wingman company follow "Anthropic"' turns a company name into a standing
focus in one act: the company and every known person there with attached
writing are enrolled on the reserved 'overnight' watchlist, and — when the
user names the company's domain — its conventional pages (careers, blog,
newsroom) are probed and the live ones approved as research sources
(RFC-015: the invocation is the approval, and the report lists exactly what
was approved).

'wingman overnight' is the explicit spend-the-tokens command: every enrolled
target gets the full deep pipeline (research diffs, feeds, news, embeddings,
fresh POV cards and company themes, briefs, exports), and the run ends in a
dated digest under reports/digests/ — what changed, what failed, and who or
what to consider following next. Enrollment IS the consent record: it is
enumerable ('wingman watchlist show overnight') and revocable ('wingman
watchlist remove'). Wingman still runs no daemon — scheduling the command is
the user's act, on their machine (RFC-006 untouched: nothing is ever sent).
"""

from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.application.people import match_people
from wingman.application.pipeline import MisoReport, make_it_so
from wingman.application.research import add_company_source, research_company
from wingman.application.similarity import company_key, similar_companies
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.focus")

# The reserved watchlist that IS the overnight enrollment (and consent) record.
OVERNIGHT_LIST = "overnight"

# Conventional pages worth watching on a company domain, probed only when the
# user names the domain. Each live one becomes an approved research source.
_RESEARCH_PATHS = ("/careers", "/jobs", "/blog", "/news", "/newsroom", "/about")
_PATH_LABELS = {
    "/careers": "careers",
    "/jobs": "careers",
    "/blog": "blog",
    "/news": "newsroom",
    "/newsroom": "newsroom",
    "/about": "about",
}
# How many people-you-know-there to surface as follow suggestions.
_MAX_KNOWN_SUGGESTIONS = 8
_SIMILAR_LIMIT = 3


class FollowReport(BaseModel):
    company: str
    company_enrolled: bool
    people_enrolled: list[str] = Field(default_factory=list)
    known_without_sources: list[str] = Field(default_factory=list)
    sources_approved: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)


def follow_company(
    name: str,
    storage: Storage,
    url: str | None = None,
    fetcher: Callable[[str], bytes] | None = None,
) -> FollowReport:
    """One act that assembles a standing focus on a company.

    Enrolls the company (and known people there who have writing attached) on
    the overnight watchlist, and probes the named domain's conventional pages
    into approved research sources. Deterministic and idempotent — following
    twice changes nothing.
    """
    key = company_key(name)
    if not key:
        raise IngestError("company name is empty — nothing to follow.")
    display = name.strip()
    people = [
        person
        for person in storage.list_people()
        if person.company and company_key(person.company) == key
    ]
    if people:
        display = people[0].company or display

    report = FollowReport(
        company=display,
        company_enrolled=storage.watchlist_add(OVERNIGHT_LIST, "company", display),
    )

    for person in people:
        if person.sources:
            if storage.watchlist_add(OVERNIGHT_LIST, "person", person.name):
                report.people_enrolled.append(person.name)
        else:
            report.known_without_sources.append(person.name)
    report.known_without_sources = report.known_without_sources[:_MAX_KNOWN_SUGGESTIONS]

    if url:
        base = url.strip().rstrip("/")
        if not base.startswith("https://"):
            raise IngestError(f"only https:// domains are probed (RFC-009); got {url!r}")
        fetch = fetcher if fetcher is not None else fetch_url
        for path in _RESEARCH_PATHS:
            candidate = base + path
            try:
                fetch(candidate)
            except FetchError:
                continue
            _, created = add_company_source(display, candidate, storage, label=_PATH_LABELS[path])
            if created:
                report.sources_approved.append(candidate)

    if not people:
        report.hints.append(
            f"nobody on the watchlist works at {display} — import connections or "
            f"'wingman people add' someone there so overnight has people to research"
        )
    if report.known_without_sources:
        names = ", ".join(report.known_without_sources)
        report.hints.append(
            f"known there but no writing attached: {names} — "
            "'wingman people add-feed \"<name>\" <url>' brings them into overnight runs"
        )
    if not url:
        report.hints.append(
            f"name the domain to auto-approve research pages: "
            f'wingman company follow "{display}" --url https://<their-domain>'
        )
    _logger.info(
        "follow company=%s people=%d sources=%d",
        display,
        len(report.people_enrolled),
        len(report.sources_approved),
    )
    return report


def render_follow_report(report: FollowReport) -> str:
    state = "enrolled" if report.company_enrolled else "already enrolled"
    lines = [f"Following {report.company} ({state} on the overnight watchlist)"]
    for person in report.people_enrolled:
        lines.append(f"  + {person} (person, has writing)")
    for source in report.sources_approved:
        lines.append(f"  + approved research source: {source}")
    lines.extend(f"  hint: {hint}" for hint in report.hints)
    lines.append(
        "Deep-refresh everything: wingman overnight  (revoke any time: "
        f"wingman watchlist remove {OVERNIGHT_LIST} ...)"
    )
    return "\n".join(lines)


class OvernightTarget(BaseModel):
    name: str
    kind: str  # "person" | "company"
    status: str  # "ok" | "failed"
    lines: list[str] = Field(default_factory=list)


class ActionItem(BaseModel):
    """One morning action: what to do, why now, about whom, on what evidence."""

    what: str
    why: str
    who: str
    evidence: list[str] = Field(default_factory=list)


class OvernightReport(BaseModel):
    digest_path: str
    processed: int
    failed: int
    targets: list[OvernightTarget] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)


_JOBISH = ("job", "career", "opening", "position", "role")
_MAX_ACTIONS = 10
_MAX_ACTION_EVIDENCE = 3


def _company_deep(
    name: str, config: Config, storage: Storage, actions: list[ActionItem]
) -> OvernightTarget:
    from wingman.application.pov import build_company_pov
    from wingman.providers.base import CapabilityClass
    from wingman.providers.router import get_provider

    target = OvernightTarget(name=name, kind="company", status="ok")
    try:
        research = research_company(name, storage)
        for result in research.results:
            target.lines.append(f"research {result.url}: {result.detail}")
            target.lines.extend(f"  new: {link}" for link in result.new_links)
            if result.new_links:
                jobish = [
                    link
                    for link in result.new_links
                    if any(word in link.lower() for word in _JOBISH)
                ]
                what = (
                    f"Assess the new opening(s) at {name}"
                    if jobish
                    else f"Review what changed on {name}'s {result.label or 'watched'} page"
                )
                evidence = (jobish or result.new_links)[:_MAX_ACTION_EVIDENCE]
                if jobish:
                    evidence = evidence + [f'run: wingman assess --url "{jobish[0]}"']
                actions.append(
                    ActionItem(
                        what=what,
                        why=result.detail + f" ({result.url})",
                        who=name,
                        evidence=evidence,
                    )
                )
            elif result.status == "failed":
                actions.append(
                    ActionItem(
                        what=f"Fix the research source for {name}",
                        why=result.detail,
                        who=name,
                        evidence=[result.url],
                    )
                )
    except IngestError as exc:
        target.lines.append(f"research skipped: {exc}")
    # Company-attached feeds (RFC-029): fetched like a person's, before the
    # themes pass so fresh posts are part of what gets synthesized.
    from wingman.application.company_feeds import fetch_company_feeds, get_company_anchor

    anchor = get_company_anchor(name, storage)
    if anchor is not None and anchor.sources:
        try:
            feeds = fetch_company_feeds(name, config, storage)
            target.lines.append(
                f"company feeds: {feeds.added} new post(s) from {len(anchor.sources)} feed(s)"
            )
            if feeds.added:
                actions.append(
                    ActionItem(
                        what=f"Read {name}'s new posts",
                        why=f"{feeds.added} new company post(s) fetched overnight",
                        who=name,
                        evidence=feeds.titles[:_MAX_ACTION_EVIDENCE],
                    )
                )
        except IngestError as exc:
            target.lines.append(f"company feeds failed: {exc}")
    try:
        provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        themes = build_company_pov(name, storage, provider)
        target.lines.append(f"themes refreshed: {len(themes.card.stances)} stances")
    except Exception as exc:  # noqa: BLE001 — every failure is reported, none is fatal
        target.lines.append(f"themes skipped: {exc}")
    try:
        miso = make_it_so(name, config, storage, kind="company")
        target.lines.append(f"dossier: {miso.export_path}")
    except IngestError as exc:
        target.status = "failed"
        target.lines.append(f"dossier failed: {exc}")
    return target


def _person_deep(
    name: str, config: Config, storage: Storage, actions: list[ActionItem]
) -> OvernightTarget:
    target = OvernightTarget(name=name, kind="person", status="ok")
    try:
        miso: MisoReport = make_it_so(name, config, storage, kind="person")
        target.lines.extend(f"{step.name}: {step.status} — {step.detail}" for step in miso.steps)
        if any(step.status == "failed" for step in miso.steps):
            target.status = "failed"
        steps = {step.name: step for step in miso.steps}
        matches = match_people(storage, name)
        person = matches[0] if len(matches) == 1 else None
        fetch = steps.get("fetch")
        added = 0
        if fetch is not None and fetch.status == "ok":
            match = re.search(r"(\d+) added", fetch.detail)
            added = int(match.group(1)) if match else 0
        if added:
            newest = storage.list_external_documents(person.person_id) if person else []
            newest_titled = [
                f"{doc.title} — {doc.url}" if doc.url else doc.title
                for doc in newest[-_MAX_ACTION_EVIDENCE:]
            ]
            brief = storage.get_outreach_brief(person.person_id) if person else None
            what = (
                f"Compose outreach to {name} in your voice (material is fresh)"
                if brief is not None
                else f"Read {name}'s new writing"
            )
            why = f"{added} new post(s) fetched overnight" + (
                "; outreach brief refreshed" if brief is not None else ""
            )
            actions.append(ActionItem(what=what, why=why, who=name, evidence=newest_titled))
        news = steps.get("news")
        if news is not None and news.status == "ok":
            items = storage.list_person_news(person.person_id) if person else []
            if items:
                top = items[-1]
                actions.append(
                    ActionItem(
                        what=f"Skim the news snapshot for {name}",
                        why=news.detail + " in the refreshed snapshot",
                        who=name,
                        evidence=[f"{top.title} — {top.url}"],
                    )
                )
    except IngestError as exc:
        target.status = "failed"
        target.lines.append(str(exc))
    return target


def _suggestions(storage: Storage, companies: list[str]) -> list[str]:
    """Deterministic follow-next leads from what the workspace already knows."""
    lines: list[str] = []
    for company in companies:
        try:
            similar = similar_companies(storage, name=company, limit=_SIMILAR_LIMIT)
        except IngestError:
            continue
        for entry in similar.companies:
            lines.append(
                f"company like {company}: {entry.name} (score {entry.score:.3f}) — "
                f'follow with: wingman company follow "{entry.name}"'
            )
    card_topics: list[str] = []
    from wingman.application.pov import company_card_id

    for company in companies:
        card = storage.get_pov_card(company_card_id(company_key(company)))
        if card and card.topics:
            card_topics.append(f"{company}: {', '.join(card.topics)}")
    lines.extend(f"themes to watch — {entry}" for entry in card_topics)
    return lines


def overnight_run(config: Config, storage: Storage, out_dir: Path | None = None) -> OvernightReport:
    """Deep-refresh every enrolled target and write the dated digest.

    The explicit spend-the-tokens command: model calls (POV cards, themes,
    briefs) and the enrolled targets' enumerable egress (feeds, news queries,
    approved research pages) all happen here, and nowhere implicitly.
    """
    members = storage.watchlist_members(OVERNIGHT_LIST)
    if not members:
        raise IngestError(
            "nothing is enrolled for overnight runs. Start with "
            "'wingman company follow \"<company>\"'."
        )
    targets: list[OvernightTarget] = []
    companies: list[str] = []
    actions: list[ActionItem] = []
    for member_kind, member_name in members:
        if member_kind == "company":
            companies.append(member_name)
            targets.append(_company_deep(member_name, config, storage, actions))
        else:
            targets.append(_person_deep(member_name, config, storage, actions))
    actions = actions[:_MAX_ACTIONS]

    failed = sum(1 for target in targets if target.status == "failed")
    now = datetime.now(UTC)
    lines = [
        f"# Overnight digest — {now.date().isoformat()}",
        "",
        f"{len(targets)} targets processed, {failed} with failures. "
        f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')}.",
    ]
    for target in targets:
        marker = "✓" if target.status == "ok" else "✗"
        lines.extend(["", f"## {marker} {target.name} ({target.kind})", ""])
        lines.extend(f"- {entry}" for entry in target.lines)
    suggestion_lines = _suggestions(storage, companies)
    if suggestion_lines:
        lines.extend(["", "## Consider following", ""])
        lines.extend(f"- {entry}" for entry in suggestion_lines)

    # The morning's marching orders, last so they are the takeaway.
    lines.extend(["", "## Action list", ""])
    if actions:
        for number, action in enumerate(actions, start=1):
            lines.append(f"{number}. **{action.what}**")
            lines.append(f"   - why: {action.why}")
            lines.append(f"   - who: {action.who}")
            lines.extend(f"   - evidence: {item}" for item in action.evidence)
    else:
        lines.append("Nothing changed enough to act on — no new links, posts, or news.")

    digest_dir = (out_dir.expanduser() if out_dir else config.reports_dir / "digests").resolve()
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"overnight-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
    content = "\n".join(lines) + "\n"
    digest_path.write_text(content, encoding="utf-8")
    # A stable pointer for editors, scripts, and habit: always the newest digest.
    (digest_dir / "latest.md").write_text(content, encoding="utf-8")
    _logger.info("overnight targets=%d failed=%d digest=%s", len(targets), failed, digest_path)
    return OvernightReport(
        digest_path=str(digest_path),
        processed=len(targets),
        failed=failed,
        targets=targets,
        actions=actions,
    )


def latest_digest(config: Config) -> Path | None:
    """The newest dated digest in the default location, or None."""
    digest_dir = config.reports_dir / "digests"
    if not digest_dir.exists():
        return None
    candidates = sorted(digest_dir.glob("overnight-*.md"))
    return candidates[-1] if candidates else None
