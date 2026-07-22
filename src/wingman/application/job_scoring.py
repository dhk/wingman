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
from collections.abc import Callable
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
# night's tokens. Overflow is reported, never silent (RFC-031 discipline).
MAX_FETCHED_PER_COMPANY = 8
MAX_JUDGED_PER_COMPANY = 4
_MIN_POSTING_CHARS = 200
_MAX_QUOTES = 3
_MAX_REASONS = 3


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
        "The never-mind-how-good-it-sounds constraints: location/remote "
        "requirements, minimum seniority or scope, comp floor, industries "
        "you will not touch.",
    ),
    (
        "Role families",
        "The 2-3 shapes of job you would actually take. Describe the work, "
        "not the title — titles lie.",
    ),
    (
        "Strong attractors",
        "What makes you lean in when you see it: technologies, problem "
        "domains, company stage, team shape — whatever it really is.",
    ),
    (
        "Anti-signals",
        "Phrases or facts in a posting that make you close the tab even when "
        "everything else fits. Be blunt; similarity can never learn this.",
    ),
    (
        "Tie-breaker",
        "When two plausible jobs compete for your attention, what wins: "
        "comp, mission, people, growth, low chaos?",
    ),
]

# When the doc is this old, the digest nudges a review. The nudge is an
# ordinary RFC-031 action (key: criteria-review): snoozing it sets the
# user's own cadence, muting it turns the loop off — their call, not ours.
REVIEW_EVERY_DAYS = 30


def criteria_age_days(config: Config) -> int | None:
    """Whole days since the criteria doc was last saved, or None when absent."""
    path = criteria_path(config)
    if not path.exists():
        return None
    from datetime import UTC, datetime

    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return max(0, (datetime.now(UTC) - modified).days)


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
    text = model_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json") :]
        text = text.strip()
    try:
        return _Judgment.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
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
    try:
        data = fetch(url)
    except FetchError as exc:
        raise IngestError(f"fetch failed: {exc}") from exc
    text, _links = extract_page(data, url)
    text = text.strip()
    if len(text) < _MIN_POSTING_CHARS:
        raise IngestError(
            f"almost no readable text ({len(text)} chars) — likely a login wall "
            "or JavaScript-only page"
        )
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
) -> OpeningScores:
    """Fetch, recall-rank when over budget, and judge one company's openings.

    Requires the criteria doc; callers keep the old unscored behavior when
    it is absent. Per-link failures become notes, never exceptions — this
    runs inside overnight, where every failure is reported and none is
    fatal.
    """
    criteria = load_criteria(config)
    if criteria is None:
        raise IngestError(f"no {CRITERIA_FILENAME} in the workspace — nothing to judge against.")
    result = OpeningScores()
    if len(links) > MAX_FETCHED_PER_COMPANY:
        result.notes.append(
            f"{len(links) - MAX_FETCHED_PER_COMPANY} of {len(links)} new job link(s) "
            f"not fetched (budget {MAX_FETCHED_PER_COMPANY}/run)"
        )
    postings: list[tuple[str, str]] = []
    for url in links[:MAX_FETCHED_PER_COMPANY]:
        try:
            postings.append((url, _posting_text(url, fetcher)))
        except IngestError as exc:
            result.notes.append(f"not scored [link]({url}): {exc}")
    if len(postings) > MAX_JUDGED_PER_COMPANY:
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
            f"{len(postings) - MAX_JUDGED_PER_COMPANY} opening(s) beyond the judge budget "
            f"({MAX_JUDGED_PER_COMPANY}/run) left unscored"
        )
        postings = postings[:MAX_JUDGED_PER_COMPANY]
    for url, text in postings:
        try:
            opening = judge_posting(text, criteria, provider, url=url, company=company)
        except IngestError as exc:
            result.notes.append(f"judge failed [link]({url}): {exc}")
            continue
        (result.filtered if opening.filtered else result.scored).append(opening)
    result.scored.sort(key=lambda opening: opening.score, reverse=True)
    _logger.info(
        "scored company=%s judged=%d filtered=%d notes=%d",
        company,
        len(result.scored),
        len(result.filtered),
        len(result.notes),
    )
    return result
