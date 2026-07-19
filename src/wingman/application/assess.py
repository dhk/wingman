"""Job-description assessment: the Phase 2 slice.

Two model steps, each bracketed by deterministic validation:
read/hash/persist SourceRecord -> requirement extraction -> quote validation ->
fit assessment -> evidence-ID validation (met/partial without resolvable
profile evidence is downgraded to unknown) -> persist Opportunity -> fit brief.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wingman.agents.opportunity_analyst import (
    ASSESSMENT_PROMPT_VERSION,
    ASSESSMENT_SYSTEM_PROMPT,
    REQUIREMENTS_PROMPT_VERSION,
    REQUIREMENTS_SYSTEM_PROMPT,
    build_assessment_prompt,
    build_requirements_prompt,
    parse_assessments,
    parse_requirements,
)
from wingman.application.evidence import fold_whitespace
from wingman.application.ingest import IngestError, RejectedItem
from wingman.domain import SourceRecord
from wingman.domain.source_record import derive_document_key
from wingman.domain.opportunity import (
    FitVerdict,
    Opportunity,
    Requirement,
    RequirementAssessment,
)
from wingman.domain.profile import EvidenceSpan, ItemStatus, ProfileItem
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelProvider, ModelRequest
from wingman.reporting.fit_brief import render_fit_brief

_logger = get_logger("application.assess")


class AssessReport(BaseModel):
    opportunity_id: str
    source_record_id: str
    source_reused: bool
    title: str
    requirements: int
    rejected_requirements: list[RejectedItem]
    verdicts: dict[str, int]
    downgraded: list[str]
    next_action: str
    brief_json_path: Path
    brief_md_path: Path
    models: dict[str, str]


def _read_job(job_path: Path) -> str:
    try:
        text = job_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(
            f"could not read {job_path} ({exc}). Nothing was assessed; "
            "check the path and re-run 'wingman assess'."
        ) from exc
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"{job_path} is not UTF-8 text ({exc}). Nothing was assessed; "
            "convert the job description to Markdown or plain text first."
        ) from exc
    if not text.strip():
        raise IngestError(f"{job_path} is empty. Nothing was assessed.")
    return text


def _persist_source(
    job_path: Path, text: str, config: Config, storage: Storage
) -> tuple[SourceRecord, bool]:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = storage.get_source_record_by_hash(content_hash)
    if existing is not None:
        return existing, True
    resolved = job_path.resolve()
    if resolved.is_relative_to(config.inbox_dir.resolve()):
        stored = resolved
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        stored = config.inbox_dir / f"{stamp}-{content_hash[:8]}-{job_path.name}"
        shutil.copy2(resolved, stored)
    record = SourceRecord(
        source_type="job_description",
        source_locator=str(stored.relative_to(config.data_dir.resolve()))
        if stored.is_relative_to(config.data_dir.resolve())
        else str(stored),
        content_hash=content_hash,
        document_key=derive_document_key(job_path.name),
    )
    storage.add_source_record(record)
    return record, False


def _job_title(text: str, job_path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return job_path.stem


def _extract_requirements(
    text: str, record: SourceRecord, provider: ModelProvider
) -> tuple[list[Requirement], list[RejectedItem], str]:
    response = provider.complete(
        ModelRequest(system=REQUIREMENTS_SYSTEM_PROMPT, prompt=build_requirements_prompt(text))
    )
    proposal = parse_requirements(response.text)
    requirements: list[Requirement] = []
    rejected: list[RejectedItem] = []
    # Verbatim modulo whitespace (RFC-026): job postings arrive hard-wrapped.
    folded_text = fold_whitespace(text)
    for proposed in proposal.requirements:
        blank = any(not quote.strip() for quote in proposed.quotes)
        missing = [
            quote
            for quote in proposed.quotes
            if quote.strip() and fold_whitespace(quote) not in folded_text
        ]
        if blank or missing:
            reason = (
                "empty evidence quote"
                if blank
                else f"evidence quote not found verbatim in source: {missing[0][:80]!r}"
            )
            rejected.append(RejectedItem(name=proposed.name, reason=reason))
            continue
        requirements.append(
            Requirement(
                name=proposed.name,
                detail=proposed.detail,
                kind=proposed.kind,
                evidence=[
                    EvidenceSpan(source_record_id=record.record_id, quote=quote)
                    for quote in proposed.quotes
                ],
            )
        )
    return requirements, rejected, f"{response.provider}/{response.model}"


def _validate_assessments(
    raw_text: str, requirements: list[Requirement], items: list[ProfileItem]
) -> tuple[list[RequirementAssessment], list[str]]:
    """Deterministic evidence-ID validation and abstention enforcement."""
    proposal = parse_assessments(raw_text)
    known_requirements = {r.requirement_id for r in requirements}
    known_items = {i.item_id for i in items}
    adjustments: list[str] = []
    by_requirement: dict[str, RequirementAssessment] = {}

    for proposed in proposal.assessments:
        if proposed.requirement_id not in known_requirements:
            adjustments.append(
                f"dropped assessment for unknown requirement_id {proposed.requirement_id!r}"
            )
            continue
        if proposed.requirement_id in by_requirement:
            adjustments.append(
                f"dropped duplicate assessment for requirement {proposed.requirement_id}; "
                "kept the first"
            )
            continue
        valid_ids = [i for i in proposed.evidence_item_ids if i in known_items]
        invalid_ids = [i for i in proposed.evidence_item_ids if i not in known_items]
        verdict = proposed.verdict
        rationale = proposed.rationale
        if invalid_ids:
            adjustments.append(
                f"removed invented evidence ID(s) {invalid_ids} from requirement "
                f"{proposed.requirement_id}"
            )
        if verdict in (FitVerdict.MET, FitVerdict.PARTIAL) and not valid_ids:
            adjustments.append(
                f"downgraded {verdict.value!r} to 'unknown' for requirement "
                f"{proposed.requirement_id}: no resolvable profile evidence"
            )
            verdict = FitVerdict.UNKNOWN
            rationale = f"{rationale} [downgraded: no resolvable profile evidence]".strip()
        by_requirement[proposed.requirement_id] = RequirementAssessment(
            requirement_id=proposed.requirement_id,
            verdict=verdict,
            rationale=rationale,
            evidence_item_ids=valid_ids,
            confidence=proposed.confidence,
        )

    for requirement in requirements:
        if requirement.requirement_id not in by_requirement:
            adjustments.append(
                f"requirement {requirement.requirement_id} was not assessed by the model; "
                "recorded as unknown"
            )
            by_requirement[requirement.requirement_id] = RequirementAssessment(
                requirement_id=requirement.requirement_id,
                verdict=FitVerdict.UNKNOWN,
                rationale="Not assessed by the model.",
                confidence=0.0,
            )
    return [by_requirement[r.requirement_id] for r in requirements], adjustments


def _next_action(assessments: list[RequirementAssessment]) -> str:
    gaps = sum(1 for a in assessments if a.verdict is FitVerdict.GAP)
    unknowns = sum(1 for a in assessments if a.verdict is FitVerdict.UNKNOWN)
    if unknowns:
        return (
            f"Resolve {unknowns} unknown requirement(s): ingest more evidence "
            "(wingman ingest) or note the answer, then re-run 'wingman assess'."
        )
    if gaps:
        return f"Decide whether {gaps} gap(s) are disqualifying or addressable before pursuing."
    return "Requirements look covered — decide whether to pursue this role."


def fetch_job_posting(url: str, config: Config, fetcher: object = None) -> Path:
    """Fetch a job posting page into the inbox as assessable text (RFC-024).

    One explicit, user-invoked HTTPS GET (RFC-009 shape); the page is
    reduced to its visible text and archived under inbox/ so the original
    fetch stays the provenance record. A page with no extractable text
    (login wall, JS-only) fails visibly.
    """
    from collections.abc import Callable

    from wingman.application.research import extract_page
    from wingman.infrastructure.fetch import FetchError, fetch_url

    url = url.strip()
    if not url.startswith("https://"):
        raise IngestError(f"only https:// postings are fetched (RFC-009); got {url!r}")
    fetch: Callable[[str], bytes] = fetcher if callable(fetcher) else fetch_url
    try:
        data = fetch(url)
    except FetchError as exc:
        raise IngestError(f"could not fetch the posting: {exc}. Nothing was assessed.") from exc
    text, _links = extract_page(data, url)
    text = text.strip()
    if len(text) < 200:
        raise IngestError(
            f"{url} yielded almost no readable text ({len(text)} chars) — likely a "
            "login wall or a JavaScript-only page. Save the posting as a file and "
            "run 'wingman assess <file>' instead."
        )
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = config.inbox_dir / f"{stamp}-job-posting.md"
    path.write_text(f"Source: {url}\n\n{text}\n", encoding="utf-8")
    return path


def assess_job(
    job_path: Path,
    config: Config,
    storage: Storage,
    extract_provider: ModelProvider,
    assess_provider: ModelProvider,
) -> AssessReport:
    items = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
    if not items:
        raise IngestError(
            "the career profile is empty, so there is nothing to assess against. "
            "Nothing was changed; run 'wingman ingest <resume.md>' first."
        )
    text = _read_job(job_path)
    record, reused = _persist_source(job_path, text, config, storage)
    title = _job_title(text, job_path)

    requirements, rejected, extract_model = _extract_requirements(text, record, extract_provider)
    if not requirements:
        raise IngestError(
            "no requirements survived evidence validation. The source record was "
            "preserved; the opportunity was not created. Re-run 'wingman assess' to retry."
        )

    response = assess_provider.complete(
        ModelRequest(
            system=ASSESSMENT_SYSTEM_PROMPT,
            prompt=build_assessment_prompt(requirements, items),
        )
    )
    assessments, adjustments = _validate_assessments(response.text, requirements, items)

    existing = storage.find_opportunity_by_source(record.record_id)
    opportunity = Opportunity(
        title=title,
        source_record_id=record.record_id,
        next_action=_next_action(assessments),
        requirements=requirements,
        assessments=assessments,
    )
    if existing is not None:
        opportunity = opportunity.model_copy(
            update={"opportunity_id": existing.opportunity_id, "created_at": existing.created_at}
        )
    storage.save_opportunity(opportunity)

    models = {
        "extract_fast": extract_model,
        "synthesize_balanced": f"{response.provider}/{response.model}",
        "prompts": f"{REQUIREMENTS_PROMPT_VERSION}+{ASSESSMENT_PROMPT_VERSION}",
    }
    brief_json, brief_md = render_fit_brief(opportunity, items, config, models)

    verdict_counts: dict[str, int] = {}
    for assessment in assessments:
        verdict_counts[assessment.verdict.value] = (
            verdict_counts.get(assessment.verdict.value, 0) + 1
        )
    _logger.info(
        "assess source=%s reused=%s title=%r requirements=%d rejected=%d verdicts=%s"
        " adjustments=%d models=%s",
        record.record_id,
        reused,
        title,
        len(requirements),
        len(rejected),
        verdict_counts,
        len(adjustments),
        models,
    )
    return AssessReport(
        opportunity_id=opportunity.opportunity_id,
        source_record_id=record.record_id,
        source_reused=reused,
        title=title,
        requirements=len(requirements),
        rejected_requirements=rejected,
        verdicts=verdict_counts,
        downgraded=adjustments,
        next_action=opportunity.next_action,
        brief_json_path=brief_json,
        brief_md_path=brief_md,
        models=models,
    )
