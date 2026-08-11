"""Company deep-dive: one-shot open-web research on an organisation (#350).

The organisation-side twin of `dossier_research.py` (#222's person
deep-dive) and it keeps that contract exactly, because the contract is the
valuable part: the paid call is gated by an explicit confirmation, the
findings are shown before anything is written, and storage is a separate
call whose input is the reviewed text itself.

Two things differ from the person version, both because a company dossier
has a consumer a person dossier does not — it is weighed against a
job-criteria document, and the follow-on is comparison across companies:

1. **Findings, not prose.** The model returns JSON; a finding is one claim
   on one of three named dimensions (market position / stated values /
   culture) with the source that supports it. Comparable by construction.
2. **Two source gates, not none.** At research time a finding survives only
   if its URL appears in the provider's OWN citation metadata (the
   deterministic 'Sources:' block `OpenRouterProvider` appends from
   OpenRouter's `annotations` array) — a model-invented URL never reaches
   the user. At save time the reviewed text is re-parsed and every finding
   must still carry a fetchable http(s) source, so the storage path cannot
   be handed hand-edited, unsourced content either.

What neither gate proves, stated plainly: OpenRouter reports citations per
RESPONSE, not per claim, so "this URL was really retrieved" is checkable
and "this URL supports this claim" is not. The claim-to-source binding
stays the model's assertion — which is why the source is rendered next to
every claim, for a human to open. See RFC-059.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError

from wingman.agents.company_researcher import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_company_findings,
)
from wingman.application.ingest import IngestError
from wingman.application.research import canonical_source_url
from wingman.application.similarity import company_key
from wingman.domain.company import (
    DIMENSION_HEADINGS,
    CompanyDimension,
    CompanyDossier,
    CompanyFinding,
)
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelProvider, ModelRequest, ModelResponse
from wingman.providers.openrouter_provider import DEFAULT_MAX_RESULTS, SEARCH_RESULT_PRICE_USD

_logger = get_logger("application.company_deep_dive")

# Same headroom as the person deep-dive (#260): the shared 8192 default was
# tuned for other capability classes and silently truncated a comprehensive
# one-shot pass mid-generation. Still a bound, just a bigger one.
_DOSSIER_MAX_TOKENS = 16384

# What one call costs in search fees, computed rather than asserted, so the
# number the user is asked to approve tracks the provider's actual cap.
SEARCH_FEE_USD = DEFAULT_MAX_RESULTS * SEARCH_RESULT_PRICE_USD

_SOURCES_HEADER = re.compile(r"^\s*sources:\s*$", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((\S+?)\)")
_CLAIM_LINE = re.compile(r"^\s*-\s*claim:\s*(.+)$", re.IGNORECASE)
_QUOTE_LINE = re.compile(r"^\s*quote:\s*(.+)$", re.IGNORECASE)
_SOURCE_LINE = re.compile(r"^\s*source:\s*(.+)$", re.IGNORECASE)
_HEADING_LINE = re.compile(r"^##\s+(.+?)\s*$")
_PROVENANCE_LINE = re.compile(r"^Researched\s+\S+\s+via\s+([^/\s]+)/(\S+?)\.?\s*$", re.MULTILINE)


class RejectedFinding(BaseModel):
    """A proposed finding that did not survive a gate, and why. Reported to
    the user rather than dropped silently — a research pass that returns
    four claims and discards six should say so."""

    claim: str
    reason: str


class FindingsReview(BaseModel):
    company: str
    findings: list[CompanyFinding] = Field(default_factory=list)
    rejected: list[RejectedFinding] = Field(default_factory=list)
    provider: str = ""
    model: str = ""


def spend_warning(name: str) -> str:
    """What the user is asked to approve BEFORE any paid call happens.

    Names the provider, what is searched, and the cost — 'confirm before
    spending' is worthless if the confirmation cannot be reasoned about.
    """
    return (
        f"About to research {name!r} on the open web via OpenRouter's "
        "web-search-grounded model. This is one bounded call — up to "
        f"{DEFAULT_MAX_RESULTS} retrieved pages (Exa via OpenRouter), no crawling, "
        "no follow-on fetches — asking for:\n"
        "  - market position: what it sells, to whom, at what scale, against which competitors\n"
        "  - stated values: what the organisation says it stands for, in its own words\n"
        "  - culture: hiring, promotion, layoffs, employee-reported experience\n"
        f"Rough cost: about ${SEARCH_FEE_USD:.2f} in search fees "
        f"({DEFAULT_MAX_RESULTS} results at ${SEARCH_RESULT_PRICE_USD:.3f} each) plus model "
        "tokens for the configured research model — a few cents to a few tens of cents.\n"
        "Nothing has been searched or stored yet."
    )


def research_company_dossier(name: str, provider: ModelProvider) -> ModelResponse:
    """The paid, open-web call. Returns the raw response; stores nothing."""
    if not name.strip():
        raise IngestError("a company name is required.")
    return provider.complete(
        ModelRequest(
            system=SYSTEM_PROMPT,
            prompt=build_prompt(name.strip()),
            max_tokens=_DOSSIER_MAX_TOKENS,
        )
    )


def _canonical(url: str) -> str:
    """URL identity for the citation check, reusing #348's rule.

    Two spellings of one page must not read as two pages here either: the
    model writes `acme.com/values`, the annotation says
    `https://www.acme.com/values/`, and a naive string compare would refuse
    a perfectly good finding. Trailing prose punctuation is stripped first
    because these URLs are lifted out of text, not out of a field.
    """
    return canonical_source_url(url.strip().rstrip(".,;)"))


def citation_urls(text: str) -> set[str]:
    """The URLs the PROVIDER says it retrieved, from its own 'Sources:' block.

    `OpenRouterProvider` builds that block deterministically from
    OpenRouter's `annotations` array — it is not the model's own prose — so
    it is the one part of the response that is evidence of retrieval rather
    than an assertion by the model.
    """
    urls: set[str] = set()
    in_sources = False
    for line in text.splitlines():
        if _SOURCES_HEADER.match(line):
            in_sources = True
            continue
        if not in_sources:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("-"):
            in_sources = False
            continue
        urls.update(_canonical(url) for _title, url in _MARKDOWN_LINK.findall(stripped))
    return urls


def review_findings(name: str, response: ModelResponse) -> FindingsReview:
    """Parse the model's findings and apply the retrieval gate. Stores nothing.

    Raises ProposalParseError when the response is not the agreed schema at
    all — a visible failure (RFC-009), never a silent empty result.
    """
    proposal = parse_company_findings(response.text)
    citations = citation_urls(response.text)
    findings: list[CompanyFinding] = []
    rejected: list[RejectedFinding] = []
    for proposed in proposal.findings:
        claim = " ".join(proposed.claim.split())
        label = claim or "(no claim text)"
        try:
            dimension = CompanyDimension(proposed.dimension.strip().lower())
        except ValueError:
            rejected.append(
                RejectedFinding(
                    claim=label,
                    reason=(
                        f"dimension {proposed.dimension!r} is not one of "
                        + ", ".join(item.value for item in CompanyDimension)
                    ),
                )
            )
            continue
        if not claim:
            rejected.append(RejectedFinding(claim=label, reason="no claim text"))
            continue
        if not citations:
            rejected.append(
                RejectedFinding(
                    claim=label,
                    reason=(
                        "the provider returned no citations at all, so no source on this "
                        "response can be verified as actually retrieved"
                    ),
                )
            )
            continue
        if _canonical(proposed.source_url) not in citations:
            rejected.append(
                RejectedFinding(
                    claim=label,
                    reason=(
                        f"source {proposed.source_url or '(none)'!r} was not among the pages "
                        "the search returned — an unverifiable citation is not stored"
                    ),
                )
            )
            continue
        try:
            findings.append(
                CompanyFinding(
                    dimension=dimension,
                    claim=claim,
                    quote=proposed.quote,
                    source_url=proposed.source_url,
                    source_title=proposed.source_title,
                )
            )
        except ValidationError as exc:
            rejected.append(RejectedFinding(claim=label, reason=str(exc)))
    _logger.info(
        "company_deep_dive company=%s kept=%d rejected=%d citations=%d prompt=%s",
        name,
        len(findings),
        len(rejected),
        len(citations),
        PROMPT_VERSION,
    )
    return FindingsReview(
        company=name.strip(),
        findings=findings,
        rejected=rejected,
        provider=response.provider,
        model=response.model,
    )


def render_findings(review: FindingsReview, generated_at: datetime | None = None) -> str:
    """The reviewed text: what the user reads AND what the save path parses.

    One rendering serves both, so 'what was previewed is what gets stored'
    is a property of the code rather than a promise (RFC-025's gate, the
    same shape `feature_request` uses).
    """
    when = (generated_at or datetime.now(UTC)).date().isoformat()
    lines = [f"# Company deep-dive: {review.company}", ""]
    provenance = f"{review.provider}/{review.model}" if review.provider else "an unnamed provider"
    lines.append(f"Researched {when} via {provenance}")
    lines.append("")
    for dimension in CompanyDimension:
        lines.append(f"## {DIMENSION_HEADINGS[dimension]}")
        lines.append("")
        entries = [finding for finding in review.findings if finding.dimension is dimension]
        if not entries:
            lines.extend(["(no sourced findings)", ""])
            continue
        for finding in entries:
            lines.append(f"- claim: {finding.claim}")
            if finding.quote:
                lines.append(f'  quote: "{finding.quote}"')
            title = finding.source_title or finding.source_url
            lines.append(f"  source: [{title}]({finding.source_url})")
        lines.append("")
    if review.rejected:
        lines.append(f"## Rejected ({len(review.rejected)}) — not stored")
        lines.append("")
        for item in review.rejected:
            lines.append(f"- {item.claim}")
            lines.append(f"  reason: {item.reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class ParsedFindings(BaseModel):
    findings: list[CompanyFinding] = Field(default_factory=list)
    rejected: list[RejectedFinding] = Field(default_factory=list)
    provider: str = ""
    model: str = ""


def _heading_dimension(heading: str) -> CompanyDimension | None:
    normalized = "_".join(re.findall(r"[a-z0-9]+", heading.lower()))
    for dimension in CompanyDimension:
        if normalized == dimension.value:
            return dimension
        if normalized == "_".join(DIMENSION_HEADINGS[dimension].lower().split()):
            return dimension
    return None


def parse_rendered_findings(content: str) -> ParsedFindings:
    """Re-read the reviewed text on the way into storage.

    The second source gate. Anything the parser cannot resolve to a claim
    under a known dimension with a fetchable http(s) source is rejected by
    name — including the '## Rejected' block the preview itself carries,
    whose entries have no source line and therefore cannot come back in.
    """
    findings: list[CompanyFinding] = []
    rejected: list[RejectedFinding] = []
    dimension: CompanyDimension | None = None
    heading: str = ""
    pending: dict[str, str] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        current = pending
        pending = None
        if dimension is None:
            rejected.append(
                RejectedFinding(
                    claim=current["claim"],
                    reason=f"under section {heading!r}, which is not a known dimension",
                )
            )
            return
        try:
            findings.append(
                CompanyFinding(
                    dimension=dimension,
                    claim=current["claim"],
                    quote=current.get("quote", ""),
                    source_url=current.get("source_url", ""),
                    source_title=current.get("source_title", ""),
                )
            )
        except ValidationError:
            rejected.append(
                RejectedFinding(
                    claim=current["claim"],
                    reason="no verifiable source URL — a finding with no source is not stored",
                )
            )

    provider = model = ""
    provenance = _PROVENANCE_LINE.search(content)
    if provenance:
        provider, model = provenance.group(1), provenance.group(2)

    for line in content.splitlines():
        heading_match = _HEADING_LINE.match(line)
        if heading_match:
            flush()
            heading = heading_match.group(1)
            dimension = _heading_dimension(heading)
            continue
        claim_match = _CLAIM_LINE.match(line)
        if claim_match:
            flush()
            pending = {"claim": " ".join(claim_match.group(1).split())}
            continue
        if pending is None:
            continue
        quote_match = _QUOTE_LINE.match(line)
        if quote_match:
            pending["quote"] = quote_match.group(1).strip().strip('"')
            continue
        source_match = _SOURCE_LINE.match(line)
        if source_match:
            raw = source_match.group(1).strip()
            link = _MARKDOWN_LINK.search(raw)
            if link:
                pending["source_title"] = link.group(1)
                pending["source_url"] = link.group(2)
            else:
                pending["source_url"] = raw
            continue
        if not line.strip():
            flush()
    flush()
    return ParsedFindings(findings=findings, rejected=rejected, provider=provider, model=model)


def save_company_dossier(
    name: str,
    content: str,
    storage: Storage,
    provider: str = "",
    model: str = "",
) -> CompanyDossier:
    """Store the reviewed findings text as this company's dossier.

    Call only after showing 'content' to the user and getting explicit
    approval — this function makes no model call and asks no questions; the
    separate call with reviewed content in hand IS the approval gate (the
    same shape as `save_person_dossier` and feed_discover/feed_attach).
    """
    key = company_key(name)
    if not key:
        raise IngestError("a company name is required.")
    parsed = parse_rendered_findings(content)
    if not parsed.findings:
        detail = ""
        if parsed.rejected:
            detail = " Rejected: " + "; ".join(
                f"{item.claim} ({item.reason})" for item in parsed.rejected[:5]
            )
        raise IngestError(
            "no finding in this text carries a verifiable source, so there is nothing "
            "to store — run the deep-dive search first, and keep the source lines." + detail
        )
    dossier = CompanyDossier(
        company_key=key,
        company_name=name.strip(),
        findings=parsed.findings,
        provider=parsed.provider or provider,
        model=parsed.model or model,
    )
    storage.save_company_dossier(dossier)
    _logger.info(
        "company_deep_dive_save company=%s findings=%d rejected=%d",
        dossier.company_name,
        len(dossier.findings),
        len(parsed.rejected),
    )
    return dossier


def render_company_dossier(dossier: CompanyDossier) -> str:
    """The stored dossier, read back in the same shape it was reviewed in."""
    review = FindingsReview(
        company=dossier.company_name,
        findings=dossier.findings,
        provider=dossier.provider,
        model=dossier.model,
    )
    return render_findings(review, generated_at=dossier.generated_at)
