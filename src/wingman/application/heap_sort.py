"""`heap sort` (#113): classify the pile, cluster it, propose routing.

The capture half (`application/heap.py`) is unconditional by design — drop
anything, spend nothing, sort later. This is "later": an explicitly invoked
pass that reads the heap hottest-first, works out what each drop IS, groups
the drops that belong to the same company, and proposes where each cluster
should go. Nothing is routed until the user confirms.

**Classification is deterministic, and that is not a shortcut.** AGENTS.md
forbids a model for file routing and exact matching, and "is this URL a
LinkedIn profile or a careers page" is exactly that: a question with a
correct answer that a model can only get wrong more expensively. So the
classifier reads URL shape — host, path, known patterns — and nothing else.
A drop it cannot place is `UNKNOWN` and says so, rather than being guessed
into a category where it would be routed somewhere wrong.

**Clustering keys on the company, because that is how leads actually
arrive** — the role, the person who posted it, and the company's own site,
in one burst. The key is derived from the URL's own registrable name
(`jobs.cursor.com` and `cursor.com/careers` both key on `cursor`), never
from a model's opinion about relatedness. Drops with no company signal stay
in an `unclustered` group where they remain visible; nothing silently
vanishes, which is the property the heap exists to protect.

**Near-namesakes are flagged, never merged.** Two LinkedIn slugs that
differ by a character (the `ishanagupta`/`ishangupta` case #113 names) are
two different people until somebody says otherwise. The sort reports the
collision and routes neither.

**Screenshots are recognised but not yet extracted.** #113 asks for an
extraction pass over dropped images, and that needs a vision path
`ModelRequest` does not have — it carries `system` and `prompt`, both text.
Rather than fake it, a screenshot is classified as a screenshot, reported
as awaiting extraction, and left in the heap. Sorting the other four kinds
is most of the value and none of the guessing; see the issue filed against
this module for the vision half.

Routing itself goes through the existing application functions and their
existing consent gates — `assess` is still the archival act, `follow_company`
still confirms its feed. This module proposes; it never invents a second
path into storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from wingman.application.heap import list_heap
from wingman.domain.heap import HeapItem
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.heap_sort")


class DropKind(StrEnum):
    """What a dropped item turned out to be."""

    POSTING = "posting"
    PERSON = "person"
    COMPANY = "company"
    ARTICLE = "article"
    SCREENSHOT = "screenshot"
    #: Placed nowhere, on purpose. Reported rather than guessed.
    UNKNOWN = "unknown"


#: Hosts whose URLs are a person, not a company, whatever the path says.
_PERSON_HOSTS = ("linkedin.com", "twitter.com", "x.com", "github.com")

#: Path fragments that mean "this is an opening", on any host. Ordered
#: longest-first so '/jobs/' does not shadow '/job-boards/'.
_POSTING_MARKERS = (
    "/job-boards/",
    "/careers/",
    "/jobs/",
    "/job/",
    "/opening",
    "/vacancy",
    "/position",
)

#: Job boards whose whole purpose is postings — the host alone decides.
_POSTING_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com")

#: Hosts that publish writing rather than represent a company.
_ARTICLE_HOSTS = ("substack.com", "medium.com", "wordpress.com", "ghost.io")

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif"})

_LINKEDIN_SLUG = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)

#: Hosts that are never a company signal — clustering on them would put
#: every LinkedIn profile in one meaningless 'linkedin' bucket.
_NON_COMPANY_HOSTS = frozenset(
    {
        "linkedin.com",
        "twitter.com",
        "x.com",
        "github.com",
        "medium.com",
        "substack.com",
        "docs.google.com",
        "drive.google.com",
        *(host for host in _POSTING_HOSTS),
    }
)

UNCLUSTERED = "unclustered"


@dataclass(frozen=True)
class Classification:
    """One heap item, placed — with the reason it was placed there.

    `evidence` is what the classifier actually keyed on, printed with the
    proposal. #113 asks for per-item classification WITH the evidence for
    it, and "this is a posting" with nothing behind it is the kind of
    assertion this codebase refuses everywhere else.
    """

    item: HeapItem
    kind: DropKind
    evidence: str
    company_key: str = ""
    #: A LinkedIn slug, when the drop carries one — the identity anchor.
    slug: str = ""


@dataclass
class Cluster:
    """Drops that belong to the same company, hottest first."""

    key: str
    classifications: list[Classification] = field(default_factory=list)

    @property
    def heat_rank(self) -> int:
        """A cluster is as hot as its hottest member — the ordering #113
        asks for ('hot clusters at the top'), not an average that would let
        one cold drop bury an urgent one."""
        from wingman.application.heap import _HEAT_ORDER

        return min(_HEAT_ORDER[c.item.heat] for c in self.classifications)


@dataclass
class SortReport:
    """Everything a sort worked out, and nothing it did."""

    clusters: list[Cluster] = field(default_factory=list)
    #: Slug collisions: near-namesakes that must not be merged.
    namesakes: list[tuple[str, str]] = field(default_factory=list)
    #: Screenshots recognised but not extracted (no vision path yet).
    awaiting_extraction: list[Classification] = field(default_factory=list)
    #: Drops whose own note carries a deadline or a date — surfaced at the
    #: top of the report, because a lead that expires is the one case where
    #: sorting later has a cost (#113's motivating hiring-event example).
    time_sensitive: list[Classification] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(cluster.classifications) for cluster in self.clusters)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def _host(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return netloc.removeprefix("www.").split(":")[0]


def _registrable(host: str) -> str:
    """'jobs.cursor.com' -> 'cursor'. Crude on purpose.

    A public-suffix list would be more correct and is not worth a
    dependency here: the key only has to group a burst of drops from one
    afternoon, and being wrong groups two companies under one heading in a
    report the user reads and confirms, rather than writing anything.
    """
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return ""
    # Strip a trailing country code after a two-letter SLD ('co.uk').
    if len(parts) >= 3 and len(parts[-1]) == 2 and len(parts[-2]) <= 3:
        return parts[-3]
    return parts[-2]


def _is_image_path(raw: str) -> bool:
    if raw.lower().startswith(("http://", "https://")):
        return False
    return Path(raw).suffix.lower() in _IMAGE_SUFFIXES


def classify(item: HeapItem) -> Classification:
    """What one dropped item is, by shape alone — never by model.

    Order matters: an image path is checked before anything URL-shaped, and
    a person-host wins over a posting marker, because
    'linkedin.com/in/someone/recent-activity/jobs' is a person's page, not
    an opening.
    """
    raw = item.item.strip()

    if _is_image_path(raw):
        return Classification(item=item, kind=DropKind.SCREENSHOT, evidence="a local image file")

    host = _host(raw)
    if not host:
        return Classification(
            item=item,
            kind=DropKind.UNKNOWN,
            evidence="not a URL and not an image path — nothing to key on",
        )

    slug_match = _LINKEDIN_SLUG.search(raw)
    slug = slug_match.group(1).lower() if slug_match else ""
    company_key = "" if host in _NON_COMPANY_HOSTS else _registrable(host)

    if any(host.endswith(person_host) for person_host in _PERSON_HOSTS):
        detail = f"a LinkedIn profile (/in/{slug})" if slug else f"a personal profile on {host}"
        return Classification(item=item, kind=DropKind.PERSON, evidence=detail, slug=slug)

    if any(host.endswith(board) for board in _POSTING_HOSTS):
        return Classification(
            item=item,
            kind=DropKind.POSTING,
            evidence=f"{host} is a job board — every URL on it is an opening",
            company_key=company_key,
        )

    path = urlparse(raw).path.lower()
    for marker in _POSTING_MARKERS:
        if marker in path:
            return Classification(
                item=item,
                kind=DropKind.POSTING,
                evidence=f"path contains {marker!r}",
                company_key=company_key,
            )

    if any(host.endswith(article_host) for article_host in _ARTICLE_HOSTS):
        return Classification(
            item=item,
            kind=DropKind.ARTICLE,
            evidence=f"{host} publishes writing rather than representing a company",
        )

    # A bare host with no path is the company; a deep path on a company
    # site is something it published.
    if path.strip("/"):
        return Classification(
            item=item,
            kind=DropKind.ARTICLE,
            evidence=f"a page on {host}, not its front door",
            company_key=company_key,
        )
    return Classification(
        item=item,
        kind=DropKind.COMPANY,
        evidence=f"the front door of {host}",
        company_key=company_key,
    )


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def _near_namesakes(classifications: list[Classification]) -> list[tuple[str, str]]:
    """Slug pairs one edit apart — two people until somebody says otherwise.

    #113's own example is ishanagupta/ishangupta. The sort never decides
    which was meant; it reports the pair and routes neither, because
    merging two people is the one mistake here that corrupts a record
    rather than merely mislabelling it.
    """
    slugs = sorted({c.slug for c in classifications if c.slug})
    pairs: list[tuple[str, str]] = []
    for index, left in enumerate(slugs):
        for right in slugs[index + 1 :]:
            if _within_one_edit(left, right):
                pairs.append((left, right))
    return pairs


def _within_one_edit(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    for cut in range(len(longer)):
        if longer[:cut] + longer[cut + 1 :] == shorter:
            return True
    return False


#: Words and shapes that mean "this has a clock on it", matched against the
#: user's OWN note only. Never inferred from a fetched page: the note is
#: the one text here the user wrote, so reading urgency into it is reading
#: what they said rather than deciding it for them.
_TIME_SENSITIVE = re.compile(
    r"\b("
    r"today|tonight|tomorrow|deadline|closes?|closing|expires?|"
    r"mon|tue|wed|thu|fri|sat|sun"
    r"|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    r"|\d{1,2}[/-]\d{1,2}"
    r")\b",
    re.IGNORECASE,
)


def _is_time_sensitive(item: HeapItem) -> bool:
    return bool(item.note.strip() and _TIME_SENSITIVE.search(item.note))


def sort_heap(storage: Storage) -> SortReport:
    """Read the heap hottest-first and work out what is in it. Pure: reads
    the heap, writes nothing, spends nothing."""
    items = list_heap(storage)
    classifications = [classify(item) for item in items]

    report = SortReport(
        namesakes=_near_namesakes(classifications),
        awaiting_extraction=[c for c in classifications if c.kind is DropKind.SCREENSHOT],
        time_sensitive=[c for c in classifications if _is_time_sensitive(c.item)],
    )

    grouped: dict[str, Cluster] = {}
    for classification in classifications:
        key = classification.company_key or UNCLUSTERED
        grouped.setdefault(key, Cluster(key=key)).classifications.append(classification)

    # Hot clusters at the top; the unclustered group always last, because it
    # is a residue rather than a lead — but never hidden.
    report.clusters = sorted(
        grouped.values(),
        key=lambda cluster: (cluster.key == UNCLUSTERED, cluster.heat_rank, cluster.key),
    )
    _logger.info(
        "heap sorted items=%d clusters=%d namesakes=%d",
        len(classifications),
        len(report.clusters),
        len(report.namesakes),
    )
    return report


# --------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------

#: What each kind would be routed to, named as the command the user knows.
ROUTES: dict[DropKind, str] = {
    DropKind.POSTING: "wingman assess --url",
    DropKind.PERSON: "wingman people add",
    DropKind.COMPANY: "wingman company follow",
    DropKind.ARTICLE: "wingman company source / corpus add",
    DropKind.SCREENSHOT: "(awaiting extraction — no vision path yet)",
    DropKind.UNKNOWN: "(nothing — say what this is and re-drop it)",
}


def render_sort(report: SortReport) -> str:
    """The report the user confirms against.

    Deliberately shows the evidence for every classification: what is
    previewed has to be enough to disagree with, or confirmation is
    theatre.
    """
    if not report.total:
        return "The heap is empty — nothing to sort."

    lines = [
        (
            f"{report.total} item(s) in {len(report.clusters)} cluster(s), hottest first. "
            "NOTHING is routed until you confirm."
        ),
        "",
    ]
    # Before anything else: the drops with a clock on them. Everything else
    # in this report keeps until tomorrow; these are the ones that do not.
    if report.time_sensitive:
        lines.append("TIME-SENSITIVE — your own note on these mentions a date or a deadline:")
        for classification in report.time_sensitive:
            lines.append(f"   {classification.item.item_id[:8]}  {classification.item.item}")
            lines.append(f"       your note: {classification.item.note}")
        lines.append("")
    for cluster in report.clusters:
        heading = (
            "unclustered (no company signal yet)" if cluster.key == UNCLUSTERED else cluster.key
        )
        lines.append(f"── {heading}")
        for classification in cluster.classifications:
            item = classification.item
            lines.append(f"   {item.item_id[:8]}  [{item.heat.value}]  {classification.kind.value}")
            lines.append(f"       {item.item}")
            lines.append(f"       because: {classification.evidence}")
            lines.append(f"       would route to: {ROUTES[classification.kind]}")
            if item.note:
                lines.append(f"       your note: {item.note}")
        lines.append("")

    if report.namesakes:
        lines.append("NEAR-NAMESAKES — two people until you say otherwise, neither routed:")
        for left, right in report.namesakes:
            lines.append(f"   /in/{left}  vs  /in/{right}")
        lines.append("")

    if report.awaiting_extraction:
        lines.append(
            f"{len(report.awaiting_extraction)} screenshot(s) recognised but NOT read — "
            "extracting them needs a vision path the model layer does not have yet. "
            "They stay in the heap rather than being silently dropped."
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
