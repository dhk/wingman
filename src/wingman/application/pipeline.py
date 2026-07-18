"""make-it-so: the whole pipeline for one person or company, end to end.

The easy daily command: allowed to be slower and more expensive, because
it remembers every step and edge case so the user doesn't have to. It
runs everything the workspace knows how to do for a target — fetch
writing, fetch news, embed, POV card, outreach brief, both exports —
with each step's result reported honestly. Steps degrade
independently: a missing API key skips the model steps visibly, a failed
fetch is reported and the rest continues. Every step is the same code the
individual commands run; this is orchestration, not new behavior, and it
sends nothing anywhere (RFC-006).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.application.news import fetch_person_news
from wingman.application.outreach import build_outreach_brief
from wingman.application.people import fetch_person_feed, match_people
from wingman.application.pov import build_pov_card
from wingman.application.similarity import embed_missing
from wingman.domain.outreach import OutreachPurpose
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import CapabilityClass
from wingman.providers.router import get_embedding_provider, get_provider

_logger = get_logger("application.pipeline")


class MisoStep(BaseModel):
    name: str
    status: str  # "ok" | "skipped" | "failed"
    detail: str


class MisoReport(BaseModel):
    target: str
    kind: str  # "person" | "company"
    steps: list[MisoStep] = Field(default_factory=list)
    export_path: str | None = None
    html_path: str | None = None


def _step(report: MisoReport, name: str, status: str, detail: str) -> None:
    report.steps.append(MisoStep(name=name, status=status, detail=detail))


def make_it_so(
    name: str,
    config: Config,
    storage: Storage,
    purpose: OutreachPurpose = OutreachPurpose.INTRODUCTION,
    out_dir: Path | None = None,
    kind: str | None = None,
) -> MisoReport:
    """Run everything for one target. A unique person match runs the person
    pipeline; otherwise the name is tried as a company. kind ('person' or
    'company') forces the interpretation — watchlists pass it explicitly."""
    if kind == "company":
        return _company_pipeline(name, config, storage, out_dir)
    candidates = match_people(storage, name)
    if len(candidates) == 1:
        return _person_pipeline(candidates[0].name, config, storage, purpose, out_dir)
    if len(candidates) > 1:
        pretty = ", ".join(person.name for person in candidates[:5])
        raise IngestError(f"{name!r} matches several people ({pretty}); be more specific.")
    if kind == "person":
        raise IngestError(f"no person named {name!r}; see 'wingman people list'.")
    return _company_pipeline(name, config, storage, out_dir)


def _person_pipeline(
    name: str,
    config: Config,
    storage: Storage,
    purpose: OutreachPurpose,
    out_dir: Path | None,
) -> MisoReport:
    from wingman.reporting.export import export_person

    person = match_people(storage, name)[0]
    report = MisoReport(target=person.name, kind="person")

    if person.sources:
        try:
            fetched = fetch_person_feed(person, config, storage)
            _step(
                report,
                "fetch",
                "ok",
                f"{fetched.items} items seen, {fetched.added} added",
            )
        except IngestError as exc:
            _step(report, "fetch", "failed", str(exc))
    else:
        _step(report, "fetch", "skipped", "no sources — add one with 'wingman people add-feed'")

    try:
        news = fetch_person_news(person, storage)
        _step(report, "news", "ok", f"{news.stored} items stored")
    except IngestError as exc:
        _step(report, "news", "failed", str(exc))

    try:
        embedded = embed_missing(storage, get_embedding_provider(config))
        _step(
            report,
            "embed",
            "ok",
            f"{embedded.corpus_embedded + embedded.external_embedded} new vectors "
            f"({embedded.provider}/{embedded.model})",
        )
    except Exception as exc:  # noqa: BLE001 — every failure is reported, none is fatal
        _step(report, "embed", "skipped", str(exc))

    try:
        provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        pov = build_pov_card(person.name, storage, provider)
        _step(report, "pov", "ok", f"{len(pov.card.stances)} stances")
    except Exception as exc:  # noqa: BLE001 — every failure is reported, none is fatal
        _step(report, "pov", "skipped", str(exc))

    try:
        provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        brief = build_outreach_brief(person.name, storage, provider, purpose=purpose)
        _step(
            report,
            "brief",
            "ok",
            f"{len(brief.brief.talking_points)} talking points ({purpose.value})",
        )
    except Exception as exc:  # noqa: BLE001
        _step(report, "brief", "skipped", str(exc))

    try:
        path = export_person(person.name, config, storage, out_dir=out_dir)
        report.export_path = str(path)
        _step(report, "export", "ok", str(path))
        html_path = export_person(person.name, config, storage, out_dir=out_dir, as_html=True)
        report.html_path = str(html_path)
        _step(report, "export-html", "ok", str(html_path))
    except IngestError as exc:
        _step(report, "export", "failed", str(exc))

    _logger.info(
        "miso person=%s steps=%s",
        person.name,
        ",".join(f"{step.name}:{step.status}" for step in report.steps),
    )
    return report


def _company_pipeline(
    name: str, config: Config, storage: Storage, out_dir: Path | None
) -> MisoReport:
    from wingman.reporting.export import export_company

    report = MisoReport(target=name, kind="company")
    try:
        path = export_company(name, config, storage, out_dir=out_dir)
        report.export_path = str(path)
        _step(report, "dossier", "ok", str(path))
    except IngestError as exc:
        raise IngestError(
            f"{name!r} is neither a watched person nor an attributable company. {exc}"
        ) from exc
    _logger.info("miso company=%s", name)
    return report
