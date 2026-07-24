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
from wingman.domain.person import Person
from wingman.domain.relationship import RelationshipObjective
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
    """One morning action: what to do, why now, about whom, on what evidence.

    key is the action's stable identity for triage (RFC-031): the same
    kind of action about the same subject keeps the same key across runs,
    so a mute/snooze verdict applies to every future digest.
    """

    what: str
    why: str
    who: str
    evidence: list[str] = Field(default_factory=list)
    key: str = ""


class OvernightReport(BaseModel):
    digest_path: str
    processed: int
    failed: int
    targets: list[OvernightTarget] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)


_JOBISH = ("job", "career", "opening", "position", "role")
_MAX_ACTIONS = 10
_MAX_ACTION_EVIDENCE = 3
# RFC-037's own motivating example set the cadence: "tickle him in a week
# when X happens". Past this many days without the objective being touched,
# the tickler fires on staleness alone, not just fresh material.
TICKLER_STALE_DAYS = 7
# Page-title resolution for evidence links (#109): one GET per titled link,
# capped per run so a link-heavy morning can't turn the digest into a crawl.
_TITLE_FETCH_BUDGET = 15
_MAX_TITLE_CHARS = 80


class _TitleBudget(BaseModel):
    remaining: int = _TITLE_FETCH_BUDGET


def _titled_link(url: str, budget: _TitleBudget) -> str:
    """'[Page title](url)' evidence, degrading to '[link](url)' on any
    failure or an exhausted budget — a title is a nicety, never worth
    failing (or slowing) an action over (#109)."""
    if budget.remaining <= 0:
        return f"[link]({url})"
    budget.remaining -= 1
    # Fetched through research's binding: the same page-fetch pathway (and
    # the same test seam) as the diff that surfaced the link.
    from wingman.application import research

    try:
        title = research.page_title(research.fetch_url(url))  # type: ignore[attr-defined]
    except FetchError:
        return f"[link]({url})"
    if not title:
        return f"[link]({url})"
    title = title.strip()[:_MAX_TITLE_CHARS].replace("[", "(").replace("]", ")")
    return f"[{title}]({url})"


def _scored_opening_actions(
    name: str,
    jobish: list[str],
    config: Config,
    storage: Storage,
    target: OvernightTarget,
    actions: list[ActionItem],
) -> bool:
    """Judge new openings against job-criteria.md (RFC-035) into per-opening
    scored actions. Returns False — leaving the generic 'assess' action to
    the caller — when there is no criteria doc or scoring failed entirely."""
    from wingman.application.job_scoring import (
        CRITERIA_FILENAME,
        load_criteria,
        score_company_openings,
    )
    from wingman.providers.base import CapabilityClass
    from wingman.providers.router import get_provider

    if load_criteria(config) is None:
        target.lines.append(
            f"{len(jobish)} new job link(s) unscored — write {CRITERIA_FILENAME} "
            "(the job_criteria tool interviews you) to get scored openings"
        )
        return False
    try:
        provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        outcome = score_company_openings(name, jobish, config, storage, provider)
    except Exception as exc:  # noqa: BLE001 — overnight reports failures, never dies on them
        target.lines.append(f"opening scoring failed: {exc}")
        return False
    for opening in outcome.scored:
        label = opening.title or "opening"
        actions.append(
            ActionItem(
                what=f"{label} at {name} — scored {opening.score}/100",
                why="; ".join(opening.reasons) or "judged against job-criteria.md",
                who=name,
                evidence=[f'"{quote}"' for quote in opening.quotes]
                + [f"[link]({opening.url})", f'run: wingman assess --url "{opening.url}"'],
                key=f"opening:{company_key(name)}:{opening.url}",
            )
        )
    if outcome.filtered:
        # Visible-suppression discipline (RFC-031): filtered is a count with
        # names, never a silent disappearance.
        for opening in outcome.filtered:
            target.lines.append(
                f"opening filtered by criteria ({opening.hard_filter_failed}): "
                f"[link]({opening.url})"
            )
    target.lines.extend(f"scoring: {note}" for note in outcome.notes)
    return bool(outcome.scored or outcome.filtered)


def _criteria_review_action(config: Config) -> ActionItem | None:
    """The periodic interview nudge (RFC-035): when job-criteria.md goes
    stale, the digest says so — once per run, as an ordinary triageable
    action. Snoozing 'criteria-review' sets the user's own cadence; muting
    turns the loop off. Preferences drift; the doc should not."""
    from wingman.application.job_scoring import criteria_review_due

    age = criteria_review_due(config)
    if age is None:
        return None
    return ActionItem(
        what="Review your job criteria",
        why=f"job-criteria.md was last updated {age} days ago — opening scores "
        "are only as current as the document they judge against",
        who="you",
        evidence=[
            "say: review my job criteria (the job_criteria tool walks the five areas)",
            "or: wingman criteria review",
        ],
        key="criteria-review",
    )


def _company_deep(
    name: str,
    config: Config,
    storage: Storage,
    actions: list[ActionItem],
    titles: _TitleBudget | None = None,
) -> OvernightTarget:
    from wingman.application.pov import build_company_pov
    from wingman.providers.base import CapabilityClass
    from wingman.providers.router import get_provider

    titles = titles if titles is not None else _TitleBudget()
    target = OvernightTarget(name=name, kind="company", status="ok")
    try:
        research = research_company(name, storage)
        for result in research.results:
            target.lines.append(f"research {result.url}: {result.detail}")
            target.lines.extend(f"  new: [link]({link})" for link in result.new_links)
            if result.new_links:
                jobish = [
                    link
                    for link in result.new_links
                    if any(word in link.lower() for word in _JOBISH)
                ]
                if jobish and _scored_opening_actions(
                    name, jobish, config, storage, target, actions
                ):
                    continue
                what = (
                    f"Assess the new opening(s) at {name}"
                    if jobish
                    else f"Review what changed on {name}'s {result.label or 'watched'} page"
                )
                # Long URLs (job boards, trackers) read as noise: markdown
                # links with the page's own title keep the line scannable
                # (#109); the runnable command below keeps the raw URL where
                # it's needed verbatim.
                evidence = [
                    _titled_link(link, titles)
                    for link in (jobish or result.new_links)[:_MAX_ACTION_EVIDENCE]
                ]
                if jobish:
                    evidence = evidence + [f'run: wingman assess --url "{jobish[0]}"']
                actions.append(
                    ActionItem(
                        what=what,
                        why=result.detail + f" ({result.url})",
                        who=name,
                        evidence=evidence,
                        key=f"research:{company_key(name)}:{result.url}",
                    )
                )
            elif result.status == "failed":
                actions.append(
                    ActionItem(
                        what=f"Fix the research source for {name}",
                        why=result.detail,
                        who=name,
                        key=f"fix-source:{company_key(name)}:{result.url}",
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
                        key=f"company-posts:{company_key(name)}",
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


def _relationship_tickler(
    person: Person, objective: RelationshipObjective, fresh: list[str]
) -> ActionItem:
    """One tickler action citing the objective's own words as the why
    (RFC-037): fresh material this run, or the next move having gone
    stale. Keyed relationship:{person} — RFC-031 verdicts apply, so
    snooze IS the tickler cadence and mute ends the thread's nudges."""
    if fresh:
        why = f'objective: "{objective.thesis}"'
        evidence = fresh[:_MAX_ACTION_EVIDENCE] + [f'next move: "{objective.next_move}"']
    else:
        age = (datetime.now(UTC) - objective.updated_at).days
        why = f'next move hasn\'t moved in {age} day(s) — objective: "{objective.thesis}"'
        evidence = [f'next move: "{objective.next_move}"', f'goal: "{objective.goal}"']
    return ActionItem(
        what=f"{person.name}: {objective.next_move}",
        why=why,
        who=person.name,
        evidence=evidence,
        key=f"relationship:{person.name_key}",
    )


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
                f"[{doc.title}]({doc.url})" if doc.url else doc.title
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
            actions.append(
                ActionItem(
                    what=what,
                    why=why,
                    who=name,
                    evidence=newest_titled,
                    key=f"person-posts:{' '.join(name.lower().split())}",
                )
            )
        news = steps.get("news")
        news_items = []
        if news is not None and news.status == "ok":
            news_items = storage.list_person_news(person.person_id) if person else []
            if news_items:
                top = news_items[-1]
                actions.append(
                    ActionItem(
                        what=f"Skim the news snapshot for {name}",
                        why=news.detail + " in the refreshed snapshot",
                        who=name,
                        evidence=[f"[{top.title}]({top.url})" if top.url else top.title],
                    )
                )
        # Objective-driven tickler (RFC-037): fresh material or a stale
        # next-move earns a nudge, citing the objective's own words.
        if person is not None:
            objective = storage.get_objective(person.person_id)
            if objective is not None:
                fresh: list[str] = []
                if added:
                    fresh.extend(newest_titled)
                if news_items:
                    top = news_items[-1]
                    fresh.append(f"news: {top.title}")
                stale_days = (datetime.now(UTC) - objective.updated_at).days
                if fresh or stale_days >= TICKLER_STALE_DAYS:
                    actions.append(_relationship_tickler(person, objective, fresh))
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
    titles = _TitleBudget()  # one per run: the #109 fetch cap spans all companies
    for member_kind, member_name in members:
        if member_kind == "company":
            companies.append(member_name)
            targets.append(_company_deep(member_name, config, storage, actions, titles=titles))
        else:
            targets.append(_person_deep(member_name, config, storage, actions))
    review = _criteria_review_action(config)
    if review is not None:
        actions.append(review)
    # Standing triage verdicts (RFC-031): what the user muted or snoozed
    # never reaches the digest — applied before the cap so a suppressed
    # item can't crowd out a live one.
    from wingman.application.triage import filter_actions

    actions, suppressed = filter_actions(actions, storage)
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
            if action.key:
                lines.append(f"   - key: {action.key}")
    else:
        lines.append("Nothing changed enough to act on — no new links, posts, or news.")
    if suppressed:
        lines.extend(
            [
                "",
                f"({suppressed} action(s) suppressed by your triage verdicts — "
                "'wingman actions list' shows them, 'wingman actions unmute <key>' reverses.)",
            ]
        )

    digest_dir = (out_dir.expanduser() if out_dir else config.reports_dir / "digests").resolve()
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"overnight-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
    content = "\n".join(lines) + "\n"
    digest_path.write_text(content, encoding="utf-8")
    # A stable pointer for editors, scripts, and habit: always the newest digest.
    (digest_dir / "latest.md").write_text(content, encoding="utf-8")
    # The pretty twin: same run data in the design system, action list first.
    # Markdown stays canonical (greppable, searchable); HTML is for reading.
    from wingman.reporting.digest_html import render_digest_html

    html_content = render_digest_html(now, targets, actions, suppressed, suggestion_lines)
    digest_path.with_suffix(".html").write_text(html_content, encoding="utf-8")
    (digest_dir / "latest.html").write_text(html_content, encoding="utf-8")
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
