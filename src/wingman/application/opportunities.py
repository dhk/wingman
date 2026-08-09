"""Opportunity summaries: everything assessed, one row each (#312).

A read-only view over opportunities already saved by application.assess —
no new storage, no pipeline-stage tracking, and no link from answer_bank
to an opportunity_id (all explicitly out of scope for #312).

Two of the summary's fields are best-effort approximations rather than
derived facts, per AGENTS.md invariant 9 ("partial truth over polished
fiction") — callers must not present them as exact:

- ``pack_composed`` is a filesystem glob against ``reports/packs/`` for a
  file named from the opportunity's title slug (application.pack's own
  naming convention). A pack written to a custom ``--out-dir``, or an
  opportunity whose title changed since its pack was made, will read as
  "no pack" even though one exists.
- ``answers_matched`` counts answer_bank entries whose free-text
  ``company``/``role_title`` loosely match the opportunity. There is no
  ``opportunity_id`` foreign key on answers — this is a text-similarity
  guess, not a join, and can both over- and under-count.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from wingman.application.pack import _slug
from wingman.application.similarity import company_key, infer_company_from_title
from wingman.domain.answer import AnswerRecord
from wingman.domain.opportunity import Opportunity, count_verdicts
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage


class OpportunitySummary(BaseModel):
    """One row of the opportunity list.

    See the module docstring for which fields are exact
    (opportunity_id, title, verdict_counts, next_action, assessed_at) and
    which are best-effort approximations (company, pack_composed,
    answers_matched).
    """

    opportunity_id: str
    title: str
    company: str | None
    verdict_counts: dict[str, int]
    next_action: str
    assessed_at: datetime
    pack_composed: bool
    answers_matched: int


def _pack_composed(opportunity: Opportunity, config: Config) -> bool:
    """Best-effort: does a pack file matching this title's slug exist?

    Filename-convention lookup only (see module docstring) — pack
    composition is never recorded in the database.
    """
    pattern = f"pack-{_slug(opportunity.title)}-*.md"
    packs_dir = config.reports_dir / "packs"
    return any(packs_dir.glob(pattern))


def _answer_matches(record: AnswerRecord, opportunity: Opportunity, company: str | None) -> bool:
    """Best-effort: does this banked answer look like it belongs to this role?

    No foreign key ties an answer to an opportunity (see module
    docstring) — this is a free-text company/role_title approximation,
    not a real join.
    """
    if record.company and company and company_key(record.company) == company_key(company):
        return True
    if record.role_title:
        title_key = " ".join(opportunity.title.lower().split())
        role_key = " ".join(record.role_title.lower().split())
        if role_key and (role_key in title_key or title_key in role_key):
            return True
    return False


def list_opportunity_summaries(storage: Storage, config: Config) -> list[OpportunitySummary]:
    """One summary per assessed opportunity, in list_opportunities' order.

    Ordering, count, and membership are exactly storage.list_opportunities'
    (ORDER BY created_at) — this never reorders, dedups, or drops entries.
    """
    opportunities = storage.list_opportunities()
    if not opportunities:
        return []
    answers = storage.list_answers()
    summaries = []
    for opportunity in opportunities:
        company = infer_company_from_title(opportunity.title, storage)
        verdict_counts = {
            verdict.value: count
            for verdict, count in count_verdicts(opportunity.assessments).items()
        }
        answers_matched = sum(
            1 for record in answers if _answer_matches(record, opportunity, company)
        )
        summaries.append(
            OpportunitySummary(
                opportunity_id=opportunity.opportunity_id,
                title=opportunity.title,
                company=company,
                verdict_counts=verdict_counts,
                next_action=opportunity.next_action,
                assessed_at=opportunity.created_at,
                pack_composed=_pack_composed(opportunity, config),
                answers_matched=answers_matched,
            )
        )
    return summaries


def render_opportunity_listing(summaries: list[OpportunitySummary]) -> str:
    """One line per opportunity, ending with a total count (mirrors people_list).

    pack_composed and answers_matched render as best-effort markers
    (``pack?``/``answers~N``) — see list_opportunity_summaries for why
    they can be wrong.
    """
    if not summaries:
        return (
            "No assessed opportunities yet — run 'wingman assess <job.md>' or assess_job_url first."
        )
    lines = []
    for summary in summaries:
        where = f" @ {summary.company}" if summary.company else ""
        counts = " ".join(f"{key}:{value}" for key, value in summary.verdict_counts.items())
        pack = "pack composed (best-effort)" if summary.pack_composed else "no pack found"
        lines.append(
            f"{summary.title}{where}  [{counts}]  next: {summary.next_action}"
            f"  assessed {summary.assessed_at.date().isoformat()}  {pack}"
            f"  answers~{summary.answers_matched} (approx)"
            f"  ({summary.opportunity_id[:8]})"
        )
    lines.append(f"{len(summaries)} opportunities.")
    return "\n".join(lines)
