"""Job interest scoring (RFC-035): the criteria doc decides, with evidence.

Three auditable layers. **Recall** is deterministic today — the jobish
links a research diff surfaces, capped — with embedding ranking engaged
only when candidates exceed the judge budget: vectors recall, they never
decide. **Judgment** is a model pass against `job-criteria.md`, the
human-editable workspace document that is the source of truth for what
the user cares about: a posting that violates a hard filter never becomes
an action regardless of score, and every surviving score carries quotes
verified verbatim-modulo-whitespace against the posting (RFC-026
discipline — an unverifiable quote is dropped, visibly). **Learning**
belongs to the user: scored openings carry RFC-031 triage keys, and
criteria edits are proposed in conversation behind a confirmation gate,
never applied silently.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from wingman.application.evidence import fold_whitespace
from wingman.application.ingest import IngestError
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelProvider, ModelRequest
from wingman.providers.embeddings import EmbeddingError, EmbeddingProvider

_logger = get_logger("application.job_scoring")

CRITERIA_FILENAME = "job-criteria.md"
JUDGE_PROMPT_VERSION = "job-judge-v1"
# Recall and judge budgets per company per overnight run: enough to cover a
# normal careers-page diff, small enough that a bulk repost can't burn the
# night's tokens. Overflow is reported, never silent (RFC-031 discipline),
# and — since #481 — never lost either: OpeningScores.pending names exactly
# the links this budget skipped (never fetched, or fetched but never
# judged), and the caller (focus.py) persists that list as still-pending so
# the next run's scoring sees these links again regardless of what the
# research snapshot's baseline has already absorbed them into.
# Raised from 8/4 (#480): a hiring-burst company (22 links in one run) was
# routinely burning its entire fetch budget.
MAX_FETCHED_PER_COMPANY = 50
MAX_JUDGED_PER_COMPANY = 20
_MIN_POSTING_CHARS = 200
_MAX_QUOTES = 3
_MAX_REASONS = 3
# Ashby-hosted postings (#486) never server-render posting text to a plain
# GET -- the body is a JS shell, so the length check below would otherwise
# misreport every one of them as a login wall. Ashby publishes a public,
# unauthenticated job-board API per org that lists every open job with a
# plain-text description; verified live against jobs.ashbyhq.com/notion.
_ASHBY_JOB_URL_RE = re.compile(r"^https://jobs\.ashbyhq\.com/(?P<org>[^/?#]+)/(?P<job_id>[^/?#]+)")

# Shared with focus.py's overnight diff path (#488): a link is worth judging
# against job-criteria.md only if it looks jobish. One list, so the overnight
# "new links" filter and the full-board sweep below can never drift apart.
JOBISH_KEYWORDS = ("job", "career", "opening", "position", "role")

# The full-board sweep (#488) is deliberately NOT bounded by
# MAX_FETCHED_PER_COMPANY/MAX_JUDGED_PER_COMPANY -- that budget exists to
# keep the automatic overnight run cheap, and the sweep is the opposite: a
# user-triggered, explicitly-more-expensive pass over EVERY current link,
# the RFC-018 "explicitly asked, allowed to be slower" reasoning. It still
# needs some ceiling -- a malformed or enormous careers page could otherwise
# queue thousands of fetch+judge model calls from one invocation -- so this
# is a distinctly-named sanity cap, never the overnight budget silently
# reapplied, and firing it is always reported (RFC-031 visible-suppression
# discipline).
SWEEP_LINK_CAP = 300


def criteria_path(config: Config) -> Path:
    return config.data_dir / CRITERIA_FILENAME


def load_criteria(config: Config) -> str | None:
    """The criteria document's text, or None when absent or empty."""
    path = criteria_path(config)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def save_criteria(config: Config, text: str) -> Path:
    """Write the criteria document — the user's confirmed words, verbatim."""
    if not text.strip():
        raise IngestError(
            "the criteria text is empty — nothing was saved. "
            "An empty document would filter nothing and score everything."
        )
    path = criteria_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    _logger.info("criteria saved chars=%d", len(text))
    return path


# The interview loop: one question set, used for seeding AND for periodic
# review, so the criteria doc never fossilizes. Each area is (name, prompt);
# every surface — the MCP packet, the CLI, the digest nudge — reads this one
# list, so revising the questions is one edit.
INTERVIEW_AREAS: list[tuple[str, str]] = [
    (
        "Hard filters",
        (
            "The never-mind-how-good-it-sounds constraints: location/remote "
            "requirements, minimum seniority or scope, comp floor, industries "
            "you will not touch."
        ),
    ),
    (
        "Role families",
        (
            "The 2-3 shapes of job you would actually take. Describe the work, "
            "not the title — titles lie."
        ),
    ),
    (
        "Strong attractors",
        (
            "What makes you lean in when you see it: technologies, problem "
            "domains, company stage, team shape — whatever it really is."
        ),
    ),
    (
        "Anti-signals",
        (
            "Phrases or facts in a posting that make you close the tab even when "
            "everything else fits. Be blunt; similarity can never learn this."
        ),
    ),
    (
        "Tie-breaker",
        (
            "When two plausible jobs compete for your attention, what wins: "
            "comp, mission, people, growth, low chaos?"
        ),
    ),
]

# When the doc is this old, the digest nudges a review. The nudge is an
# ordinary RFC-031 action (key: criteria-review): snoozing it sets the
# user's own cadence, muting it turns the loop off — their call, not ours.
REVIEW_EVERY_DAYS = 30


def criteria_modified_at(config: Config) -> datetime | None:
    """When the criteria doc was last saved, or None when absent."""
    path = criteria_path(config)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def criteria_age_days(config: Config) -> int | None:
    """Whole days since the criteria doc was last saved, or None when absent."""
    modified = criteria_modified_at(config)
    if modified is None:
        return None
    return max(0, (datetime.now(UTC) - modified).days)


def criteria_sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) pairs from the doc's own '## ' headings (#489).

    The interview flow (INTERVIEW_AREAS) writes headed sections — 'Hard
    filters', 'Wants', and so on — but a doc edited by hand may carry none.
    Content before the first heading, or a headingless doc entirely, is one
    section under the file's own name, so a standing preference stated in
    plain prose is still reachable rather than silently unsearchable.
    """
    sections: list[tuple[str, list[str]]] = []
    title = CRITERIA_FILENAME
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if body:
                sections.append((title, body))
            title = line[3:].strip() or CRITERIA_FILENAME
            body = []
        else:
            body.append(line)
    sections.append((title, body))
    return [
        (section_title, joined)
        for section_title, section_body in sections
        if (joined := "\n".join(section_body).strip())
    ]


def criteria_review_due(config: Config, every_days: int = REVIEW_EVERY_DAYS) -> int | None:
    """The doc's age when a review is due, else None (absent docs get the
    seeding hint from the scoring path instead, not a review nudge)."""
    age = criteria_age_days(config)
    return age if age is not None and age >= every_days else None


def render_interview(config: Config) -> str:
    """The interview packet: current doc plus the five areas, for either
    seeding (no doc yet) or periodic review (doc shown, area by area)."""
    current = load_criteria(config)
    age = criteria_age_days(config)
    lines: list[str] = []
    if current is None:
        lines.append("Job criteria interview — SEEDING (no job-criteria.md yet).")
        lines.append(
            "Walk the five areas below with the user, one at a time; capture "
            "answers in their words."
        )
    else:
        stamp = f"last updated {age} day(s) ago" if age is not None else "age unknown"
        lines.append(f"Job criteria interview — REVIEW ({stamp}).")
        lines.append("Current document:")
        lines.append("---")
        lines.append(current)
        lines.append("---")
        lines.append(
            "Walk the five areas below one at a time: read what the current "
            "document says about the area, ask what changed, offer Keep / Update."
        )
    for number, (name, prompt) in enumerate(INTERVIEW_AREAS, start=1):
        lines.append(f"{number}. {name} — {prompt}")
    lines.append(
        "When every area is done: draft the full revised document, show it, "
        "iterate until the user confirms the wording, then call "
        "job_criteria(action='save'). Save nothing without confirmation."
    )
    return "\n".join(lines)


class ScoredOpening(BaseModel):
    """One judged posting: score, the criteria reasoning, verified quotes."""

    url: str
    company: str
    title: str = ""
    score: int = Field(default=0, ge=0, le=100)
    hard_filter_failed: str = ""
    reasons: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)

    @property
    def filtered(self) -> bool:
        return bool(self.hard_filter_failed)


class OpeningScores(BaseModel):
    """Everything one company's jobish links produced this run."""

    scored: list[ScoredOpening] = Field(default_factory=list)  # judge survivors, best first
    filtered: list[ScoredOpening] = Field(default_factory=list)  # hard-filter failures
    notes: list[str] = Field(default_factory=list)  # fetch failures, budget overflow
    # Input links that got no outcome at all this run because the fetch or
    # judge budget cut them off first — never fetched, or fetched but never
    # judged. Distinct from a fetch/judge failure (those DID get an attempt
    # and are reported in notes instead): a budget skip is the one case the
    # caller must keep pending rather than treat as resolved (#481).
    pending: list[str] = Field(default_factory=list)


JUDGE_SYSTEM_PROMPT = (
    "You judge whether ONE job posting matches ONE person's written job criteria. "
    "The criteria document is the only authority: do not reward qualities it does not ask for. "
    "Return ONLY a JSON object: "
    '{"title": string (the role title as posted), '
    '"score": integer 0-100 (how well the posting matches the weighted wants), '
    '"hard_filter_failed": string (the criteria clause this posting violates, or "" if none), '
    f'"reasons": array of at most {_MAX_REASONS} short strings, each naming the criteria clause '
    "it applies, "
    f'"quotes": array of at most {_MAX_QUOTES} strings copied EXACTLY from the posting text '
    "that drove the score}. "
    "If any hard filter is violated, name it and give score 0. Never invent quotes."
)


def build_judge_prompt(criteria: str, posting: str) -> str:
    return (
        f"THE PERSON'S JOB CRITERIA:\n\n{criteria}\n\n"
        f"THE JOB POSTING:\n\n{posting}\n\n"
        "Judge the posting against the criteria. JSON only."
    )


class _Judgment(BaseModel):
    title: str = ""
    score: int = 0
    hard_filter_failed: str = ""
    reasons: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)


def _parse_judgment(model_text: str) -> _Judgment:
    from wingman.agents._json import extract_json_block

    try:
        return _Judgment.model_validate(extract_json_block(model_text))
    except (ValueError, ValidationError) as exc:
        raise IngestError(f"the judge's output is not valid judgment JSON: {exc}") from exc


def judge_posting(
    posting: str, criteria: str, provider: ModelProvider, url: str = "", company: str = ""
) -> ScoredOpening:
    """One model judgment, bracketed by deterministic validation.

    The score is clamped; quotes that do not appear verbatim-modulo-
    whitespace in the posting are dropped with a visible note rather than
    presented as evidence (RFC-026 — polished fiction is worse than a gap).
    """
    response = provider.complete(
        ModelRequest(system=JUDGE_SYSTEM_PROMPT, prompt=build_judge_prompt(criteria, posting))
    )
    judgment = _parse_judgment(response.text)
    folded = fold_whitespace(posting)
    quotes: list[str] = []
    dropped = 0
    for quote in judgment.quotes[:_MAX_QUOTES]:
        if quote.strip() and fold_whitespace(quote) in folded:
            quotes.append(quote.strip())
        else:
            dropped += 1
    reasons = [reason.strip() for reason in judgment.reasons[:_MAX_REASONS] if reason.strip()]
    if dropped:
        reasons.append(f"({dropped} unverifiable quote(s) dropped)")
    return ScoredOpening(
        url=url,
        company=company,
        title=judgment.title.strip(),
        score=max(0, min(100, judgment.score)),
        hard_filter_failed=judgment.hard_filter_failed.strip(),
        reasons=reasons,
        quotes=quotes,
    )


def rank_candidates(
    candidates: list[tuple[str, str]],
    positives: list[str],
    embedder: EmbeddingProvider,
) -> list[str]:
    """Order candidate (key, text) pairs by cosine to the positive centroid.

    The embedding recall layer, engaged only when candidates exceed the
    judge budget. Anchors are the user's own acts (titles of roles they
    assessed); with no anchors the original order stands — recall never
    invents a preference.
    """
    if not candidates:
        return []
    keys = [key for key, _ in candidates]
    if not positives:
        return keys
    from wingman.application.similarity import _dot, _mean, _normalize

    anchor_vectors = embedder.embed(positives, input_type="document")
    centroid = _mean([_normalize(vector) for vector in anchor_vectors])
    candidate_vectors = embedder.embed([text for _, text in candidates], input_type="document")
    scored = [
        (key, _dot(_normalize(vector), centroid))
        for key, vector in zip(keys, candidate_vectors, strict=True)
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [key for key, _ in scored]


def _ashby_posting_text(url: str, fetch: Callable[[str], bytes]) -> str | None:
    """Route an Ashby job URL through Ashby's public job-board API (#486).

    Returns the posting's plain-text description, or None on any mismatch
    or failure — never raises. This is a best-effort shortcut: the caller
    falls back to the ordinary plain-GET path (and its ordinary error) on
    None, so a wrong guess here costs nothing.
    """
    match = _ASHBY_JOB_URL_RE.match(url)
    if match is None:
        return None
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{match['org']}"
    try:
        board = json.loads(fetch(api_url))
        jobs = board["jobs"] if isinstance(board, dict) else []
        job = next((j for j in jobs if j.get("id") == match["job_id"]), None)
        text = str(job["descriptionPlain"]).strip() if job else ""
    except Exception:  # noqa: BLE001 — best-effort shortcut, caller falls back on any failure
        return None
    return text or None


def _posting_text(url: str, fetcher: Callable[[str], bytes] | None) -> str:
    """Fetch one posting to judgeable text — read-only, no inbox archive.

    Scoring is triage; 'wingman assess' remains the archival act that
    persists a SourceRecord. A page with no extractable text is skipped
    with a visible note, never judged on nothing.
    """
    from wingman.application.research import extract_page
    from wingman.infrastructure.fetch import FetchError, fetch_url

    url = url.strip()
    if not url.startswith("https://"):
        raise IngestError(f"only https:// postings are fetched (RFC-009); got {url!r}")
    fetch = fetcher if fetcher is not None else fetch_url

    ashby_text = _ashby_posting_text(url, fetch)
    if ashby_text is not None and len(ashby_text) >= _MIN_POSTING_CHARS:
        return ashby_text

    try:
        data = fetch(url)
    except FetchError as exc:
        raise IngestError(f"fetch failed: {exc}") from exc
    text, _links = extract_page(data, url)
    text = text.strip()
    if len(text) < _MIN_POSTING_CHARS:
        if _ASHBY_JOB_URL_RE.match(url) is not None:
            hint = (
                "this is an Ashby-hosted posting, and its public job-board API lookup "
                "also failed (org may not publish a public board, or the job was "
                "removed) — the page itself is JavaScript-rendered and not fetchable "
                "via plain GET"
            )
        else:
            hint = (
                "likely a login wall, or a JavaScript-rendered job board (Ashby, "
                "etc.) that never server-renders posting text to a plain GET"
            )
        raise IngestError(f"almost no readable text ({len(text)} chars) — {hint}")
    return text


def _positive_anchors(storage: Storage) -> list[str]:
    """Titles of roles the user actually assessed: their acts, not guesses."""
    return [opportunity.title for opportunity in storage.list_opportunities()]


def score_company_openings(
    company: str,
    links: list[str],
    config: Config,
    storage: Storage,
    provider: ModelProvider,
    fetcher: Callable[[str], bytes] | None = None,
    embedder: EmbeddingProvider | None = None,
    apply_budget: bool = True,
) -> OpeningScores:
    """Fetch, recall-rank when over budget, and judge one company's openings.

    Requires the criteria doc; callers keep the old unscored behavior when
    it is absent. Per-link failures become notes, never exceptions — this
    runs inside overnight, where every failure is reported and none is
    fatal.

    apply_budget defaults to True: MAX_FETCHED_PER_COMPANY/MAX_JUDGED_PER_COMPANY
    are read fresh from the module globals at call time (not bound as
    parameter defaults), so a caller can still monkeypatch either constant
    and see it take effect. Pass apply_budget=False to lift both caps
    entirely — the full-board sweep (#488, score_full_board) does this; it
    has its own, separately-named ceiling upstream rather than this
    function's overnight budget.
    """
    criteria = load_criteria(config)
    if criteria is None:
        raise IngestError(f"no {CRITERIA_FILENAME} in the workspace — nothing to judge against.")
    result = OpeningScores()
    handled: set[str] = set()  # links that got a real attempt this run, any outcome
    max_fetched = MAX_FETCHED_PER_COMPANY if apply_budget else None
    max_judged = MAX_JUDGED_PER_COMPANY if apply_budget else None
    if max_fetched is not None and len(links) > max_fetched:
        result.notes.append(
            f"{len(links) - max_fetched} of {len(links)} job link(s) "
            f"not fetched (budget {max_fetched}/run) — carried forward to the next run"
        )
    fetch_candidates = links if max_fetched is None else links[:max_fetched]
    postings: list[tuple[str, str]] = []
    for url in fetch_candidates:
        try:
            postings.append((url, _posting_text(url, fetcher)))
        except IngestError as exc:
            handled.add(url)
            result.notes.append(f"not scored [link]({url}): {exc}")
    if max_judged is not None and len(postings) > max_judged:
        try:
            if embedder is None:
                from wingman.providers.router import get_embedding_provider

                embedder = get_embedding_provider(config)
            by_url = dict(postings)
            ranked = rank_candidates(postings, _positive_anchors(storage), embedder)
            postings = [(url, by_url[url]) for url in ranked]
        except (EmbeddingError, IngestError) as exc:
            result.notes.append(f"recall ranking unavailable ({exc}); judging in page order")
        result.notes.append(
            f"{len(postings) - max_judged} opening(s) beyond the judge budget "
            f"({max_judged}/run) left unscored — carried forward to the next run"
        )
        postings = postings[:max_judged]
    for url, text in postings:
        handled.add(url)
        try:
            opening = judge_posting(text, criteria, provider, url=url, company=company)
        except IngestError as exc:
            result.notes.append(f"judge failed [link]({url}): {exc}")
            continue
        (result.filtered if opening.filtered else result.scored).append(opening)
    result.scored.sort(key=lambda opening: opening.score, reverse=True)
    result.pending = [url for url in links if url not in handled]
    _logger.info(
        "scored company=%s judged=%d filtered=%d notes=%d pending=%d",
        company,
        len(result.scored),
        len(result.filtered),
        len(result.notes),
        len(result.pending),
    )
    return result


def score_full_board(
    name: str,
    config: Config,
    storage: Storage,
    provider: ModelProvider,
    fetcher: Callable[[str], bytes] | None = None,
    embedder: EmbeddingProvider | None = None,
) -> OpeningScores:
    """Score EVERY jobish link currently on a company's watched pages (#488).

    Overnight scoring only ever judges links NEW since the last research
    diff (research_company/_scored_opening_actions): a posting already on
    the page before scoring existed, or before the tenant started watching,
    is structurally invisible to it forever, however well it matches. This
    is the deliberate, user-triggered "score everything currently open"
    sweep instead.

    Reuses research_company's own fetch rather than adding a second one:
    research_company already does one GET per approved source and saves the
    page's FULL current link set to its ResearchSnapshot (extract_page's
    complete `links`, not just the diffed-new subset a ResearchReport
    surfaces) — this reads those just-refreshed snapshots for every link
    currently on the page, instead of re-fetching the source pages itself.
    Each individual job posting is still fetched once here, same as
    overnight, via score_company_openings.

    Bypasses the overnight per-run budgets (MAX_FETCHED_PER_COMPANY,
    MAX_JUDGED_PER_COMPANY) entirely — see score_company_openings — but is
    bounded by the separately-named SWEEP_LINK_CAP, reported visibly if it
    fires (RFC-031 discipline: nothing here is silently dropped).
    """
    from wingman.application.research import research_company
    from wingman.application.similarity import company_key

    key = company_key(name)
    if not key:
        raise IngestError("company name is empty — nothing to score.")
    research_company(name, config, storage, fetcher)
    links: list[str] = []
    seen: set[str] = set()
    for source in storage.list_company_sources(key):
        snapshot = storage.get_research_snapshot(key, source.url)
        if snapshot is None:
            continue
        for link in snapshot.links:
            if link in seen:
                continue
            if any(word in link.lower() for word in JOBISH_KEYWORDS):
                seen.add(link)
                links.append(link)
    capped_note: str | None = None
    if len(links) > SWEEP_LINK_CAP:
        capped_note = (
            f"{len(links) - SWEEP_LINK_CAP} of {len(links)} jobish link(s) on the board "
            f"not swept (sweep cap {SWEEP_LINK_CAP}/invocation)"
        )
        links = links[:SWEEP_LINK_CAP]
    result = score_company_openings(
        name,
        links,
        config,
        storage,
        provider,
        fetcher=fetcher,
        embedder=embedder,
        apply_budget=False,
    )
    if capped_note:
        result.notes.insert(0, capped_note)
    _logger.info("full board sweep company=%s links=%d", name, len(links))
    return result


def render_opening_scores(company: str, outcome: OpeningScores) -> str:
    """Plain-text report for the CLI/MCP surfaces: scored, filtered, notes —
    RFC-031's visible-suppression discipline (nothing here vanishes quietly)."""
    lines = [
        (
            f"Score board: {company} — {len(outcome.scored)} scored, "
            f"{len(outcome.filtered)} filtered, {len(outcome.notes)} note(s)"
        )
    ]
    for opening in outcome.scored:
        title = opening.title or "opening"
        lines.append(f"\n{opening.score}/100 — {title}")
        lines.append(f"  {opening.url}")
        lines.extend(f"  - {reason}" for reason in opening.reasons)
        lines.extend(f'  quote: "{quote}"' for quote in opening.quotes)
    if outcome.filtered:
        lines.append("\nFiltered by criteria:")
        for opening in outcome.filtered:
            lines.append(f"  - ({opening.hard_filter_failed}) {opening.url}")
    if outcome.notes:
        lines.append("\nNotes:")
        lines.extend(f"  - {note}" for note in outcome.notes)
    return "\n".join(lines)
