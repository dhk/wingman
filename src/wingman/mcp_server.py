"""Wingman MCP server: the workspace as tools for a local MCP client (RFC-008).

A stdio server for Claude Desktop / Claude Code on the same machine, or —
with --http — a loopback streamable-HTTP server for remote clients behind a
tunnel the user runs themselves (RFC-017). Same tools either way. The
workspace never leaves the machine; every tool runs the same deterministic
validation pipelines as the CLI, so the connected model can request work but
cannot bypass evidence rules. Network access mirrors the CLI exactly:
fetch/sync/discover tools do the same explicit, read-only public-feed reads
as their CLI counterparts (RFC-009/011), and embedding tools carry the same
RFC-010 egress semantics.

Parity rule (RFC-008): the MCP surface tracks the CLI — every user-facing
capability ships on both. The one adaptation: the CLI's interactive
confirm-before-attach for feeds becomes a two-tool pair here
(feed_discover, then feed_attach only after the user says yes in
conversation).
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.assess import assess_job as assess_job_use_case
from wingman.application.assess import fetch_job_posting
from wingman.application.pack import build_application_pack
from wingman.application.backup import create_backup
from wingman.application.corpus import find_evidence
from wingman.application.ingest import IngestError, ingest_resume, ingest_resume_from_url
from wingman.application.feature_request import (
    file_feature_request,
    get_feature_repo,
    render_preview,
    stamp_operator,
)
from wingman.application.focus import (
    follow_company,
    latest_digest,
    overnight_run,
    render_follow_report,
)
from wingman.application.pipeline import MisoReport
from wingman.application.pipeline import make_it_so as make_it_so_use_case
from wingman.application.people import (
    add_person,
    attach_feed,
    delete_person,
    discover_feed,
    discover_recommendations,
    fetch_person_feed,
    find_people_evidence,
    fix_person,
    match_people,
    rename_person,
    seed_from_connections,
)
from wingman.application.dossier import build_company_dossier, delete_dossier_reports
from wingman.application.outreach import build_outreach_brief, render_outreach_brief
from wingman.domain.outreach import OutreachPurpose
from wingman.application.pov import (
    CORPUS_PERSON_ID,
    build_company_pov,
    build_own_pov,
    build_pov_card,
    company_card_id,
    render_pov_card,
)
from wingman.application.triage import (
    mute_action,
    render_verdicts,
    snooze_action,
    unmute_action,
)
from wingman.application.answers import (
    find_answer,
    find_similar,
    remove_answer,
    render_answer,
    render_answer_listing,
    save_answer,
)
from wingman.application.company_feeds import (
    attach_company_feed,
    fetch_company_feeds,
    is_company_anchor,
    list_company_feeds,
    remove_company_feed,
)
from wingman.application.profile_manage import (
    clear_profile,
    remove_item,
    render_profile_listing,
    resolve_item,
)
from wingman.application.research import (
    add_company_source,
    delete_company,
    list_company_sources,
    remove_company_source,
    rename_company,
    render_research_report,
    research_company,
)
from wingman.reporting.export import export_career, export_company, export_person
from wingman.application.similarity import (
    CompanySimilarityReport,
    SimilarPerson,
    companies_like,
    company_key,
    embed_missing,
    similar_companies,
    similar_people,
)
from wingman.application.search import render_search_report, search_workspace
from wingman.application.similarity import people_like as people_like_use_case
from wingman.domain.person import FeedAttribution, FeedKind, FeedSource, Person
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.host_config import migrate_legacy_host_file
from wingman.infrastructure.keys import ensure_env
from wingman.infrastructure.logs import configure_logging, get_logger
from wingman.infrastructure.mcp_process import (
    clear_pidfile,
    orphan_http_pids,
    read_server_pid,
    write_pidfile,
)
from wingman.version import wingman_version
from wingman.infrastructure.telemetry import (
    count_events as telemetry_count,
)
from wingman.infrastructure.telemetry import (
    is_enabled as telemetry_is_enabled,
)
from wingman.infrastructure.telemetry import (
    list_events as telemetry_list,
)
from wingman.infrastructure.telemetry import record_event
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.providers.base import CapabilityClass, ProviderError
from wingman.providers.embeddings import EmbeddingError
from wingman.providers.router import ModelConfigError, get_embedding_provider, get_provider

server = FastMCP("wingman")

_NOT_INITIALIZED = (
    "The Wingman workspace is not initialized on this machine. "
    "Run 'wingman init' in a terminal first."
)


def _ready_config() -> Config | None:
    config = load_config()
    return config if config.db_path.exists() else None


@server.tool()
def status() -> str:
    """Workspace status: counts of source records, profile items, opportunities, and corpus documents."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        return (
            f"Wingman: {wingman_version()}\n"
            f"Workspace: {config.data_dir}\n"
            f"Source records: {storage.count_source_records()}\n"
            f"Profile items: {storage.count_profile_items()}\n"
            f"Opportunities: {storage.count_opportunities()}\n"
            f"Corpus documents: {storage.count_corpus_documents()}"
        )


@server.tool()
def search(query: str, limit: int = 12) -> str:
    """Search everything the workspace knows: the user's corpus, watched people's
    writing, POV stances and company themes, news snapshots, research links, and
    outreach briefs — one ranked list with attribution, dates, and sources.

    START HERE for any question about what the workspace knows ("what do we
    know about X", "who said anything about Y") before reaching for the
    narrower per-store tools. Keyword matching plus a semantic pass that
    surfaces documents matching by meaning; with a remote embeddings provider
    configured, the query text is sent to it (the same egress as 'embed') —
    with the local 'hashed' provider or no embeddings, nothing leaves the
    machine and the skip is reported.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 50))
    try:
        with Storage(config.db_path) as storage:
            report = search_workspace(query, storage, config, limit=limit)
    except IngestError as exc:
        return f"search failed: {exc}"
    return render_search_report(report)


@server.tool()
def evidence(query: str, limit: int = 10) -> str:
    """Search the user's own writing (corpus) for cited evidence.

    Full-text query: plain words, quoted phrases, or AND/OR/NOT. Returns
    ranked excerpts with their source documents; quotes are verbatim.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            hits = find_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        return f"Search failed: {exc}"
    if not hits:
        return f"No corpus evidence found for {query!r}."
    lines = []
    for number, hit in enumerate(hits, start=1):
        when = (
            hit.document.published_at.date().isoformat() if hit.document.published_at else "undated"
        )
        lines.append(
            f"{number}. {hit.document.title} [{hit.document.source_type}, {when}]\n"
            f"   {hit.snippet}\n"
            f"   source: {hit.source_locator}"
        )
    return "\n".join(lines)


@server.tool()
def career_profile() -> str:
    """Return the current cited career profile (career.md), including roles, achievements, skills, and testimonials with their evidence."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    career_md = config.reports_dir / "career.md"
    if not career_md.exists():
        return (
            "No career profile has been generated yet. Ingest a resume "
            "('wingman ingest') or a LinkedIn export ('wingman ingest-linkedin') first."
        )
    return career_md.read_text(encoding="utf-8")


@server.tool()
def action_triage(action: str, key: str = "", days: int = 7) -> str:
    """Triage the morning digest's action list: the user's verdicts persist (RFC-031).

    action is 'mute' (never show this action key again), 'snooze' (hide it
    for `days` days), 'unmute' (clear a verdict), or 'list' (standing
    verdicts). Every digest action carries its key on a 'key:' line.

    Protocol when the user asks to triage or clean up their digest: read
    the latest digest (the digest tool), then walk the action list item by
    item with AskUserQuestion where available — offer Keep / Mute forever /
    Snooze — and record each verdict here. Never mute on your own
    judgement: what counts as uninteresting is the user's call, made one
    verdict at a time, and their answers ARE the filtering logic.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "mute":
                mute_action(key, storage)
                return f"Muted {key} — it will not appear in future digests."
            if action == "snooze":
                until = snooze_action(key, storage, days=days)
                return f"Snoozed {key} until {until}."
            if action == "unmute":
                if unmute_action(key, storage):
                    return f"Unmuted {key}."
                return f"No verdict recorded for {key}."
            if action == "list":
                return render_verdicts(storage)
    except IngestError as exc:
        return f"action triage {action} failed: {exc}"
    return f"unknown action {action!r}; use mute, snooze, unmute, or list."


@server.tool()
def job_criteria(action: str = "show", text: str = "") -> str:
    """The job-criteria document that drives opening scoring (RFC-035).

    action is 'show' (the current document), 'review' (the interview
    packet: current document plus the five question areas), or 'save'
    (replace the document with `text`). Overnight runs judge every new
    job link on watched careers pages against this document: hard filters
    remove postings outright, weighted wants set the 0-100 score, and
    every score arrives in the digest with verbatim quotes from the
    posting. When the document is a month old the digest adds a
    'criteria-review' action — snoozing it sets the review cadence,
    muting it turns the nudge off.

    Interview protocol — the SAME loop seeds a first document and reviews
    an existing one; never write criteria unilaterally. (1) Call 'review'
    to get the packet. (2) Walk its five areas one at a time, with
    AskUserQuestion where the client supports it: in review mode read
    what the current document says about the area first and offer
    Keep / Update; in seeding mode ask fresh. (3) Draft the full
    document, show it, and iterate until the user confirms the wording.
    (4) Only after explicit confirmation call 'save'. The criteria are
    the user's own words — never save wording they have not seen; the
    judge treats this document as the only authority on what they care
    about.
    """
    from wingman.application.job_scoring import (
        CRITERIA_FILENAME,
        load_criteria,
        render_interview,
        save_criteria,
    )

    config = load_config()
    if action == "show":
        current = load_criteria(config)
        if current is None:
            return (
                f"No {CRITERIA_FILENAME} yet — openings are listed unscored. "
                "Call this tool with action='review' for the seeding interview."
            )
        return current
    if action == "review":
        return render_interview(config)
    if action == "save":
        try:
            path = save_criteria(config, text)
        except IngestError as exc:
            return f"criteria save failed: {exc}"
        return (
            f"Saved {path.name} ({len(text.strip())} chars). Overnight runs now "
            "score new openings against it; edits take effect next run."
        )
    return f"unknown action {action!r}; use show, review, or save."


@server.tool()
def qa_capture(question: str, answer: str, kind: str = "achievement") -> str:
    """Save a clarifying Q&A as durable, citable profile evidence (#96, RFC-036).

    The pair lands in the inbox as a source file and becomes one profile
    item — name: the question, detail and evidence quote: the answer
    VERBATIM — so future assessments cite it like any other evidence.
    Re-answering the same question supersedes the earlier answer (RFC-028
    lineage); it never piles up conflicts. kind is achievement, skill,
    role, or testimonial.

    Protocol: when the user answers a clarifying question during
    assess/pack work, OFFER to save it — show the exact question and
    answer text that will be stored, and save only after they agree.
    Store their words verbatim; a paraphrase needs their confirmation
    first. Never capture silently.
    """
    from wingman.application.qa_capture import capture_qa

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = capture_qa(question, answer, config, storage, kind=kind)
    except IngestError as exc:
        return f"qa capture failed: {exc}"
    return (
        f"{report.outcome}: [{report.kind}] {report.question}\n"
        f"Evidence file: {report.source_path}\n"
        "Future assessments cite this like any other profile item."
    )


@server.tool()
def interview_react(
    subtype: str,
    target: str,
    why: str,
    primary_purpose: str = "",
    persona: str = "",
    persona_authored: bool = False,
) -> str:
    """Capture one interview reaction OR nomination as citable profile
    evidence (docs/PROFILE-BOOTSTRAP-DESIGN.md, docs/UX-0001-interview-flow.md)
    — a way to bootstrap profile evidence without pre-existing published
    writing.

    Three mechanics, chosen by subtype:
    - Reaction (Alignment of perspective): target is an https:// URL or a
      local PDF/DOCX/MD/TXT file the user chose to react to. subtype is
      'alignment_of_perspective_agree' or 'alignment_of_perspective_disagree'.
      A mixed/ambiguous reaction ("it's complicated") routes to _disagree
      and lets 'why' carry the nuance — never invent a third subtype for it.
    - Nomination — Values: target is a person's name (or, for the fallback
      subtypes, a company's). subtype is 'values_pro' / 'values_con'
      (three people, living or dead, they'd have dinner with / be
      horrified to see their name in print alongside — 'values_con'
      rejects Hitler specifically, too easy a nomination to discriminate
      anything), or 'values_fallback_pro' / 'values_fallback_con'
      (companies whose products/services they're proud to buy / would
      never buy — offer these ONLY if the person struggles to name
      people).
    - Nomination — Mission alignment: target is an organization's name —
      a company, club, or any nominated group aligned for a purpose, not
      "company" specifically. subtype is 'mission_alignment_pro' (they'd
      like to work there, or be proud to be associated with it) /
      'mission_alignment_con' (horrified to be associated with it — no
      exclusion list here, unlike values_con). primary_purpose is
      REQUIRED for this pair only: what the user understands the
      nominated org's primary purpose to be, e.g. "Pepsi sells cola" or
      "the fire department puts out fires" — ask it every time an org is
      nominated, pro or con, and present it as context (its own, visually
      distinct answer), never inside the evidence quote itself.
    - Nomination — Network admired: target is a LinkedIn profile URL
      (never fetched — captured as an identifier only, same as any other
      nomination target). subtype is 'network_admired' — pro-only, no con
      counterpart. Ask for three people the user genuinely admires,
      preferably first-degree LinkedIn connections of theirs (a soft
      preference, not a hard requirement — capture whoever they name), and
      why, in a few words rather than a full essay. Distinct from Values:
      the point here isn't a values signal, it's surfacing admired people
      who are also reachable through the user's own network.

    Either way, why is the user's own reasoning, stored verbatim as the
    ONLY evidence — the target itself (fetched page or nominee name) is
    never quoted as if it were the user's own words. Capturing the same
    target again under the same subtype supersedes the earlier answer.

    UX-0001's interaction pattern — apply for every category:
    - BP-01, explain then ask: 2-3 sentences before any question — what
      this section is, why it's asked, roughly what's coming. No question
      arrives cold.
    - BP-02, one decision per card: ask the target/nominee, then the
      reaction (where one exists), then 'why' as SEPARATE structured
      questions — never bundle "who, and why" into one turn.
    - BP-03, options for structure, prose for substance: agree/disagree,
      which-category-next, and continue-or-stop are fine as preset
      options. 'why' (and a nominee's name) are NEVER preset options —
      free text only, not even illustrative examples, which anchor the
      answer.
    - BP-04, every card has an exit: free text, "nothing comes to mind"
      (see the anchor fallback below), and "I'm done for now" stay
      reachable as options on every question, not things the user has to
      know to type.
    - BP-06, echo verbatim before you commit: show the exact 'why' text
      that will be stored, in a quote block ("Saving this as your own
      words, verbatim: …"), with Save / Let me reword / Discard options —
      call this tool only after Save. The single largest risk on this
      path is a model quietly tidying the user's words before calling
      this tool; never do that.
    - BP-07, name the reason for an odd order: say once, in the section's
      opening explainer, why con comes before pro and nominee #2 before
      #1 — said, it reads as craft; unsaid, it reads as arbitrary.
    - BP-08, empty is a valid answer: if nothing comes to mind, one
      anchor-first re-ask only — "the last thing you sent someone, or
      argued with" for Alignment of perspective; "an organization you've
      actually been part of — school, employer, club, team" for Mission
      alignment — then a clean stop naming the command that would produce
      a stance later ('wingman pov'/'my_pov'). Never a third attempt;
      coerced answers become bad evidence.
    - BP-09, suggest, never gate: every category stays independently
      reachable, any order, any number of times — perspectives_start's
      trust-ladder framing is option order, never a locked sequence.

    Protocol for Values and Mission alignment specifically — con nominees
    before pro nominees (ends the section on a high note); within EACH
    block, ask why about the SECOND nominee first, then the first, then
    the third (dodges the rehearsed, front-loaded answer). This tool does
    not enforce that ordering; it's the calling agent's protocol to
    follow, same as qa_capture/resolve_requirement elsewhere in this
    codebase.

    The response reports position ("N of M captured for this subtype")
    once a subtype reaches half its per-subtype cap
    (WINGMAN_INTERVIEW_MAX_PER_SUBTYPE, default 6) — fold that into the
    NEXT section's opening explainer (BP-05) rather than waiting for it to
    surface as an error at the cap.

    Coaching mode (docs/COACHING-MODE-DESIGN.md): if a persona is active
    (coach_persona 'set'), this capture is scoped to them automatically —
    leave persona="" to use whatever's active, or pass a name to act for
    someone else for just this one call without switching the active
    pointer. persona_authored distinguishes who's actually answering:
    False (the default) means YOU are speculating on the persona's
    behalf ("how would Mike answer this") — stored as your own inference
    about them, never presented as Mike's own words. True means the
    persona is answering for themselves right now (e.g. dictating while
    you type) — a verified first-person statement, same as any other
    interview capture. Every response echoes who this was captured for.
    """
    from wingman.application.coaching import render_acting_as, resolve_persona
    from wingman.application.interview import capture_interview_reaction, subtype_progress

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            active_persona = resolve_persona(persona, storage, config)
            persona_id = active_persona.persona_id if active_persona is not None else None
            report = capture_interview_reaction(
                subtype,
                target,
                why,
                config,
                storage,
                primary_purpose=primary_purpose,
                persona_id=persona_id,
                persona_authored=persona_authored,
            )
            count, cap = subtype_progress(storage, subtype, persona_id=persona_id)
    except IngestError as exc:
        return f"interview capture failed: {exc}"
    title = f" ({report.title})" if report.title else ""
    position = (
        f" Position: {count} of {cap} captured for {subtype} so far." if count * 2 >= cap else ""
    )
    return (
        f"{render_acting_as(active_persona)}\n"
        f"{report.outcome}: [{report.subtype}] {report.target}{title}\n"
        "Review with 'wingman profile list', or 'my_pov'/'wingman pov' to synthesize "
        f"captures (and any corpus writing) into a cited stance.{position}"
    )


@server.tool()
def perspectives_start() -> str:
    """Perspectives: the onboarding entry point for a new profile
    (docs/PROFILE-BOOTSTRAP-DESIGN.md, docs/UX-0001-interview-flow.md) —
    call this whenever a user is starting fresh, or asks how to build
    their profile.

    This tool collects nothing itself; it computes what's already been
    captured (if anything) and returns the entry card for the calling
    agent to present via AskUserQuestion (UX-0001 §4) — single select,
    header "Start". Before the card, 2-3 sentences (BP-01): what
    Perspectives is, that only their own words ever become evidence, that
    nothing is saved until they confirm it, and that every option is
    independently reachable in any order, any number of times, forever
    (BP-09 — no gating, no locked sequence).

    Card options, in order:
    - Pick up where I left off — ONLY when prior captures exist (this
      tool's return says so); promoted to first when present, describing
      what's already captured.
    - React to things I've read — suggested first step when nothing's
      been captured yet: 2-3 things they agree with, 2-3 they don't,
      ~10 min. Drives interview_react with subtype
      'alignment_of_perspective_agree'/'_disagree'.
    - Name people — three they'd have dinner with, three they'd hate to
      be listed beside, ~8 min. Drives interview_react with subtype
      'values_pro'/'values_con' (or the 'values_fallback_*' company
      variant if naming people is hard).
    - Name organisations — places they'd want, or refuse, to be
      associated with, ~8 min. Drives interview_react with subtype
      'mission_alignment_pro'/'mission_alignment_con'.
    - I do have writing to add — skip the interview; point them to
      'wingman corpus add <path>' (or this client's own corpus-upload
      path). Offer the interview afterward too — additive, not either/or.
    - Other — free text, always present, always last.

    No option is ever disabled and no category is a prerequisite for any
    other — the ordering above is a suggested default, not a gate. Once a
    category is chosen, see interview_react's own docstring for that
    category's protocol (BP-01…09, con-then-pro/ask-#2-first, echo before
    save).

    Coaching mode (docs/COACHING-MODE-DESIGN.md): if a persona is active
    (coach_persona 'set'), "pick up where I left off" and every category
    below are automatically scoped to them, not the coach's own work —
    the response says who. "I do have writing to add" always means the
    COACH's own shared corpus, even with a persona active, since corpus
    isn't persona-scoped — call that out plainly rather than letting it
    read as if it were the persona's own writing.
    """
    from wingman.application.coaching import get_active_persona, render_acting_as
    from wingman.application.interview import capture_progress_summary

    config = _ready_config()
    summary = None
    active_persona = None
    if config is not None:
        with Storage(config.db_path) as storage:
            active_persona = get_active_persona(storage, config)
            persona_id = active_persona.persona_id if active_persona is not None else None
            summary = capture_progress_summary(storage, persona_id=persona_id)

    resume_line = (
        f"- Pick up where I left off — you've captured {summary} so far. "
        "Promote this to the FIRST option; ask which category to continue.\n"
        if summary
        else ""
    )
    corpus_note = (
        " (this is always YOUR OWN corpus, even while acting as a persona — corpus isn't "
        "persona-scoped, say so plainly)"
        if active_persona is not None
        else ""
    )
    return (
        f"{render_acting_as(active_persona)}\n"
        "Perspectives — profile onboarding entry point. Present this as a single "
        'AskUserQuestion (header "Start", single select): "Where would you like to '
        'start?"\n\n'
        f"{resume_line}"
        "- React to things I've read (suggested first step if nothing's captured yet "
        "— 2-3 things you agree with, 2-3 you don't, ~10 min)\n"
        "- Name people (three you'd have dinner with, three you'd hate to be listed "
        "beside, ~8 min)\n"
        "- Name organisations (places you'd want — or refuse — to be associated with, "
        "~8 min)\n"
        f"- I do have writing to add{corpus_note} (skip the interview — use 'wingman "
        "corpus add' or this client's own corpus-upload path instead; offer the "
        "interview afterward too, additive not either/or)\n"
        "- Other (free text, always present, always last)\n\n"
        "Before the card: 2-3 sentences (BP-01) — what Perspectives is, that only "
        "their own words ever become evidence, that nothing is saved until they "
        "confirm it, and that every option stays reachable in any order, any number "
        "of times, forever (BP-09, no gating). Then call interview_react for the "
        "chosen category — see its own docstring for that category's protocol."
    )


@server.tool()
def coach_persona(action: str, name: str = "") -> str:
    """Coaching mode (docs/COACHING-MODE-DESIGN.md): act as coach for
    someone, or check/clear who's currently active.

    action:
    - 'set': name required. Finds-or-creates a persona by that name
      (case/whitespace-insensitive — "Mike" and "mike " are the same
      persona) and makes it the default scope for every persona-aware
      tool call (interview_react, perspectives_start, my_pov, ...) until
      cleared. "Act as coach for Mike" then behaves exactly that way —
      you keep typing, but every capture and every POV call is scoped to
      Mike unless a call explicitly overrides it.
    - 'clear': back to your own work.
    - 'who': reports the currently active persona, or that none is set —
      cheap to call any time you're not sure.
    - 'list': every persona ever coached, so you can tell 'set' apart
      from accidentally creating a near-duplicate (e.g. "Mike" vs "Mike
      Chen").

    Coach-mediated only: you always drive every call yourself, on a
    persona's behalf — there is no separate login or access token for
    them (a deliberate v1 boundary, not an oversight). Everything you
    already know — corpus, watchlist, company research — stays visible
    regardless of which persona is active, via the same 'search' tool as
    always; only a persona's OWN captured evidence (interview answers,
    profile items, POV) is scoped to them, and nothing crosses from your
    own POV into theirs unless you explicitly capture it under their
    name. When you type an answer speculating on a persona's behalf
    ("how would Mike answer this") rather than something they said
    themselves, that's exactly what interview_react's own docstring
    covers — pass persona_authored=False (the default) so it's stored as
    your own inference about them, not mistaken later for their verified
    words.
    """
    from wingman.application.coaching import (
        clear_active_persona_and_report,
        get_active_persona,
        render_acting_as,
        set_active_persona,
    )

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    action = action.strip().lower()
    try:
        with Storage(config.db_path) as storage:
            if action == "set":
                if not name.strip():
                    return "coach_persona 'set' needs a name — nothing changed."
                persona = set_active_persona(name, storage, config)
                return f"{render_acting_as(persona)} Everything from here scopes to them."
            if action == "clear":
                clear_active_persona_and_report(config)
                return render_acting_as(None)
            if action == "who":
                return render_acting_as(get_active_persona(storage, config))
            if action == "list":
                personas = storage.list_personas()
                if not personas:
                    return "No personas yet — 'coach_persona set <name>' starts one."
                lines = [
                    f"- {persona.name}" + (f" — {persona.notes}" if persona.notes else "")
                    for persona in personas
                ]
                return "Personas coached so far:\n" + "\n".join(lines)
    except IngestError as exc:
        return f"coach_persona failed: {exc}"
    return f"unknown action {action!r}; use set, clear, who, or list."


@server.tool()
def relationship_objective(
    action: str, person: str, goal: str = "", thesis: str = "", next_move: str = ""
) -> str:
    """The relationship objective for one watched person (RFC-037): goal,
    thesis, next move — the user's own confirmed words.

    action is 'show' (the current triple), 'review' (the interview packet:
    current triple, if any, plus the three question areas), or 'save'
    (replace the triple with goal/thesis/next_move). This is the source of
    truth for the relationship: any proposal you make about this person
    must cite it plus evidence (their writing, logged interactions via
    relationship_log) — never a free-floating judgment about what they
    want from another human being.

    Interview protocol — the SAME loop seeds a first objective and revises
    an existing one; never write it unilaterally. (1) Call 'review' to get
    the packet. (2) Walk its three areas one at a time, with
    AskUserQuestion where the client supports it: when revising — whether
    the user asked directly or is here because the digest flagged a
    'relationship-review:<person>' action — ask whether the relationship
    strengthened, stalled, or the thesis was wrong, read what the current
    objective says about the area first, and offer Keep / Update; when
    seeding, ask fresh. (3) Draft the full goal/thesis/next_move triple,
    show it, and iterate until the user confirms the wording. (4) Only
    after explicit confirmation call 'save'. The triple is the user's own
    words — never save wording they have not seen.
    """
    from wingman.application.relationship import (
        load_objective,
        render_interview,
        render_objective,
        save_objective,
    )

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "show":
                who, objective = load_objective(person, storage)
                if objective is None:
                    return (
                        f"No relationship objective for {who.name} yet. Call this tool "
                        "with action='review' for the seeding interview."
                    )
                return render_objective(who, objective)
            if action == "review":
                return render_interview(person, storage)
            if action == "save":
                objective = save_objective(person, goal, thesis, next_move, storage)
                saved_for = storage.get_person(objective.person_id)
                name = saved_for.name if saved_for else person
                return (
                    f"Saved the relationship objective for {name}.\n"
                    f"{render_objective(saved_for, objective) if saved_for else ''}\n"
                    "Overnight runs and future proposals about this person cite it."
                )
    except IngestError as exc:
        return f"relationship objective {action} failed: {exc}"
    return f"unknown action {action!r}; use show, review, or save."


@server.tool()
def relationship_log(person: str, note: str = "", action: str = "add") -> str:
    """Record what actually happened with a watched person (RFC-037):
    'coffee with R., discussed the eval harness role' — the qa_capture
    way (#96, RFC-036). action is 'add' (log `note` for `person`) or
    'list' (show the person's interaction log, oldest first).

    Deterministic and zero-model: the note becomes a source file and a
    log entry whose evidence quote is the user's words verbatim. This is
    raw material a future brief or objective revision can cite — never a
    summary, never a model's characterization of the interaction.

    Protocol: when the user describes something that happened with a
    watched person in conversation, OFFER to log it — show the exact
    note text that will be stored, and save only after they agree. Store
    their words verbatim; never paraphrase without confirmation.
    """
    from wingman.application.relationship import list_log, log_interaction, render_log

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "add":
                report = log_interaction(person, note, config, storage)
                return (
                    f"Logged for {report.person}: {report.entry.note}\n"
                    f"Evidence file: {report.source_path}"
                )
            if action == "list":
                who, entries = list_log(person, storage)
                return render_log(who, entries)
    except IngestError as exc:
        return f"relationship log {action} failed: {exc}"
    return f"unknown action {action!r}; use add or list."


@server.tool()
def heap(
    action: str = "show",
    items: list[str] | None = None,
    heat: str = "warm",
    item_id: str = "",
    note: str = "",
) -> str:
    """The heap (#113): a capture-first inbox for leads that arrive faster
    than they can be sorted — a job posting, a LinkedIn profile, a company
    site, all dropped at once with zero processing.

    action is 'add' (capture `items` — one or more URLs or short
    references — at `heat`: hot/warm/cold, default warm), 'show' (list
    everything, hottest first), or 'remove' (drop `item_id`, a prefix of
    the id shown by 'show'). Capture is unconditional: never fetches,
    never spends a token, never fails on a weird reference. Hot items
    sitting unsorted earn a digest nudge; cold ones never do.

    This ships the capture surface only — classification, screenshot
    extraction, company clustering, and confirmation-gated routing
    ('heap sort' in the fuller spec) are not implemented yet.

    Protocol: when the user drops a burst of links (or describes several
    leads at once), offer to capture them here rather than routing each
    one by hand — ask how hot each is only if they haven't said, and
    default to warm rather than blocking capture on the question.
    """
    from wingman.application.heap import add_to_heap, remove_from_heap, list_heap, render_heap

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "add":
                saved = add_to_heap(items or [], storage, heat=heat, note=note)
                return f"Captured {len(saved)} item(s) at heat={heat}."
            if action == "show":
                return render_heap(list_heap(storage))
            if action == "remove":
                removed = remove_from_heap(item_id, storage)
                return f"Removed: {removed.item}"
    except IngestError as exc:
        return f"heap {action} failed: {exc}"
    return f"unknown action {action!r}; use add, show, or remove."


@server.tool()
def resolve_requirement(requirement: str, limit: int = 5) -> str:
    """What the workspace already knows about one job requirement (#98, RFC-036).

    Returns similar banked answers (RFC-030) plus unified workspace search
    hits (RFC-022) for the requirement — the recall step before anyone is
    asked anything.

    Protocol when working an assessed opportunity's Unknown/Partial
    requirements: go one requirement at a time, and for each one
    (1) call this tool FIRST and offer any close match as the starting
    point — never re-ask what the workspace can already answer;
    (2) only when nothing fits, ask the user directly — AskUserQuestion
    where the client supports it — and get a real answer instead of
    leaving the verdict Unknown; (3) offer to persist the fresh answer
    with qa_capture so the same gap never resurfaces; (4) capture
    substance as terse raw bullets, one per requirement — refining the
    wording into application prose is a later phase, never this one.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            parts: list[str] = []
            similar = find_similar(requirement, storage, limit=limit)
            if similar:
                parts.append("Banked answers (closest first):")
                parts.extend(render_answer(record) for record, _snippet in similar)
            report = search_workspace(requirement, storage, config, limit=limit)
            parts.append(render_search_report(report))
    except IngestError as exc:
        return f"resolve recall failed: {exc}"
    return "\n\n".join(parts)


@server.tool()
def answer_bank(
    action: str,
    question: str = "",
    answer: str = "",
    company: str = "",
    role_title: str = "",
    asked_on: str = "",
    answer_id: str = "",
) -> str:
    """The application answer bank (RFC-030): refined Q+A+context, reused across applications.

    action is 'find', 'save', 'list', 'show', or 'remove'.

    Protocol when the user is working through a job application: they
    announce the context (company, role title, date) and paste the
    questions. For EACH question: (1) call find first — surface any
    previously refined answer for a similar question and offer it as the
    starting point; (2) interview the user and iterate on the wording —
    use the AskUserQuestion tool where available to offer concrete
    refinement choices — until THEY confirm the answer is concise and
    sounds like them (never save a one-shot draft); (3) only then save
    with question, answer, and the announced context. Pass answer_id to
    revise an existing entry instead of duplicating it. The bank is
    local, persistent, and searched by 'find' and workspace search.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "find":
                hits = find_similar(question, storage)
                if not hits:
                    return "No similar answers banked yet."
                blocks = [
                    f"{render_answer(record)}\n  match: {snippet}" for record, snippet in hits
                ]
                return "\n\n".join(blocks)
            if action == "save":
                record, created = save_answer(
                    question,
                    answer,
                    storage,
                    company=company,
                    role_title=role_title,
                    asked_on=asked_on,
                    answer_id=answer_id,
                )
                verb = "Saved" if created else "Revised"
                return f"{verb} [{record.answer_id[:8]}] — {record.context}"
            if action == "list":
                return render_answer_listing(storage.list_answers())
            if action == "show":
                return render_answer(find_answer(answer_id, storage))
            if action == "remove":
                record = remove_answer(answer_id, storage)
                return f"Removed [{record.answer_id[:8]}] {record.question[:60]!r}"
    except IngestError as exc:
        return f"answer bank {action} failed: {exc}"
    return f"unknown action {action!r}; use find, save, list, show, or remove."


@server.tool()
def profile_manage(action: str, item_id: str = "") -> str:
    """List, remove, resolve, or clear career-profile items (RFC-027).

    action is 'list', 'rm', 'resolve', or 'clear'. 'list' shows every item
    with its id — active by kind, then unresolved conflicts. 'rm' deletes
    the one item whose id starts with item_id (any unambiguous prefix).
    'resolve' settles a duplicate/conflict: the item_id item is kept and
    promoted to active, every rival with the same kind and name is dropped.
    'clear' deletes EVERY profile item for a clean re-ingest — not
    reversible except via 'wingman restore', so suggest a backup first.
    Mutations re-render career.md/career.json; stored job assessments cite
    item ids that stop existing, so re-run assess_job afterwards.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "list":
                return render_profile_listing(storage.list_profile_items())
            if action == "rm":
                item = remove_item(item_id, config, storage)
                return f"Removed {item.kind.value} {item.name!r} ({item.item_id[:8]})."
            if action == "resolve":
                winner, rivals = resolve_item(item_id, config, storage)
                dropped = ", ".join(rival.item_id[:8] for rival in rivals) or "none"
                return (
                    f"Kept {winner.kind.value} {winner.name!r} ({winner.item_id[:8]}); "
                    f"dropped: {dropped}."
                )
            if action == "clear":
                removed = clear_profile(config, storage)
                return (
                    f"Removed {removed} profile items. Re-ingest the source of truth "
                    "(ingest_resume_text or 'wingman ingest') to rebuild the profile."
                )
    except IngestError as exc:
        return f"profile {action} failed: {exc}"
    return f"unknown action {action!r}; use list, rm, resolve, or clear."


@server.tool()
def assess_job(job_description: str) -> str:
    """Assess a job description against the career profile; returns the cited fit brief.

    Requirements are extracted with verbatim quotes and each is judged
    met/partial/gap/unknown citing only real profile items — unsupported
    verdicts are downgraded by deterministic validation.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not job_description.strip():
        return "The job description is empty; nothing was assessed."
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    job_path = config.inbox_dir / f"{stamp}-pasted-job.md"
    job_path.write_text(job_description, encoding="utf-8")
    try:
        with Storage(config.db_path) as storage:
            report = assess_job_use_case(
                job_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
                get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Assessment failed: {exc}"
    return Path(report.brief_md_path).read_text(encoding="utf-8")


@server.tool()
def ingest_resume_text(resume_markdown: str, filename: str = "resume.md") -> str:
    """Ingest resume text into the canonical profile (model extraction plus deterministic evidence validation). Returns the ingestion summary.

    filename is the document's identity (RFC-028): re-ingesting under the
    same filename means "this is a newer version of that document" — its
    changed claims replace the earlier version's (Updated), claims it no
    longer makes are retired, and nothing piles up as a conflict. Use a
    different filename only for a genuinely different source document.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not resume_markdown.strip():
        return "The resume text is empty; nothing was ingested."
    # Untrusted input: strip any path components so the write stays in the inbox.
    safe_name = Path(filename).name or "resume.md"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    resume_path = config.inbox_dir / f"{stamp}-{safe_name}"
    resume_path.write_text(resume_markdown, encoding="utf-8")
    try:
        with Storage(config.db_path) as storage:
            report = ingest_resume(
                resume_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Ingestion failed: {exc}"
    rejected = "".join(f"\n  rejected {item.name!r}: {item.reason}" for item in report.rejected)
    return (
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Updated: {report.updated}  Retired: {report.retired}  "
        f"Rejected: {len(report.rejected)}{rejected}\n"
        f"Profile written to {report.career_md_path}"
    )


@server.tool()
def ingest_resume_url(url: str) -> str:
    """Ingest a resume from a link-accessible Google Docs or Drive URL.

    One explicit HTTPS fetch (RFC-009): the document is downloaded (Docs via
    its plain-text export; Drive files sniffed as PDF/DOCX/text), archived
    to the inbox, then flows through the ordinary extraction + evidence
    validation pipeline. Returns the ingestion summary.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = ingest_resume_from_url(
                url,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Ingestion failed: {exc}"
    rejected = "".join(f"\n  rejected {item.name!r}: {item.reason}" for item in report.rejected)
    return (
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Updated: {report.updated}  Retired: {report.retired}  "
        f"Rejected: {len(report.rejected)}{rejected}\n"
        f"Profile written to {report.career_md_path}"
    )


def _name_key(name: str) -> str:
    return " ".join(name.lower().split())


def _find_person(storage: Storage, name: str) -> Person | str:
    """Resolve a possibly-partial name; a string result is the error/did-you-mean reply.

    MCP has no interactive picker, so a unique match resolves silently and an
    ambiguous one lists the candidates for the caller to re-ask with.
    """
    candidates = match_people(storage, name)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        options = "; ".join(person.name for person in candidates[:5])
        more = "" if len(candidates) <= 5 else f" (+{len(candidates) - 5} more)"
        return f"{name!r} matches several people: {options}{more}. Call again with the full name."
    return f"No person named {name!r}; see people_list."


def _similarity_lines(reference: str, people: list[SimilarPerson]) -> str:
    lines = [f"Closest to {reference}:"]
    for number, entry in enumerate(people, start=1):
        where = ", ".join(part for part in (entry.position, entry.company) if part)
        detail = f" ({where})" if where else ""
        lines.append(
            f"{number}. {entry.name}{detail}  score {entry.score:.3f}  [{entry.documents} docs]"
        )
    return "\n".join(lines)


@server.tool()
def people_add(
    name: str,
    substack_url: str = "",
    company: str = "",
    position: str = "",
    linkedin_url: str = "",
    email: str = "",
) -> str:
    """Add a person to the watchlist (or update them), optionally with their Substack URL,
    LinkedIn URL, and email (manual entry only — imports never read emails)."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            person, created = add_person(
                name,
                storage,
                substack_url=substack_url or None,
                company=company or None,
                position=position or None,
                linkedin_url=linkedin_url or None,
                email=email or None,
            )
    except IngestError as exc:
        return f"people add failed: {exc}"
    verb = "Added" if created else "Updated"
    sources = ", ".join(source.url for source in person.sources) or "no sources yet"
    return f"{verb} {person.name}. Sources: {sources}"


@server.tool()
def people_manage(action: str, name: str, new_name: str = "") -> str:
    """Rename, delete, or fix a watchlist person. action is 'rename', 'delete', or 'fix'.

    'rename' changes name in place (person_id and all their data are untouched); it fails
    if new_name already belongs to someone else. 'fix' also corrects a name, but merges
    into an existing person of that name instead of failing — e.g. fix('Ed Wong', 'Edmund
    Wong') merges the 'Ed Wong' record into 'Edmund Wong' if that person already exists,
    filling any blank fields and moving documents/POV card/outreach brief over. 'delete'
    removes the person and everything keyed to them (not reversible).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "rename":
                person = rename_person(name, new_name, storage)
                return f"Renamed to {person.name} ({person.person_id})"
            if action == "fix":
                person, merged = fix_person(name, new_name, storage)
                verb = "Merged into" if merged else "Renamed to"
                return f"{verb} {person.name} ({person.person_id})"
            if action == "delete":
                person = delete_person(name, storage)
                return f"Deleted {person.name} ({person.person_id})"
    except IngestError as exc:
        return f"people {action} failed: {exc}"
    return f"unknown action {action!r}; use rename, delete, or fix."


@server.tool()
def people_list(watched_only: bool = False) -> str:
    """List watchlist people; watched_only limits to those with at least one source."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        people = storage.list_people()
    people = [person for person in people if not is_company_anchor(person)]
    if watched_only:
        people = [person for person in people if person.sources]
    if not people:
        return "No people yet — add one with people_add."
    lines = []
    for person in people:
        where = ", ".join(part for part in (person.position, person.company) if part)
        sources = ", ".join(source.url for source in person.sources)
        detail = f" ({where})" if where else ""
        linkedin = f"  {person.linkedin_url}" if person.linkedin_url else ""
        feed = f"  [{sources}]" if sources else ""
        lines.append(f"{person.name}{detail}{linkedin}{feed}")
    return "\n".join([*lines, f"{len(people)} people."])


@server.tool()
def people_fetch(name: str = "") -> str:
    """Fetch new posts from watched public sources (explicit read-only HTTPS, RFC-009).

    Give a person's name, or leave empty to fetch everyone with sources.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        if name.strip():
            found = _find_person(storage, name)
            if isinstance(found, str):
                return found
            targets = [found]
        else:
            targets = [person for person in storage.list_people() if person.sources]
            if not targets:
                return "No people have sources configured. Nothing was fetched."
        lines = []
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                lines.append(f"{person.name}: fetch failed: {exc}")
                continue
            lines.append(
                f"{person.name}: {report.items} items  added: {report.added}  "
                f"duplicates: {report.skipped_duplicates}"
            )
    return "\n".join(lines)


@server.tool()
def sync() -> str:
    """Fetch every watched source and embed whatever is new (mirrors 'wingman sync').

    Embedding sends new document text to the configured embeddings provider
    (RFC-010) unless the local 'hashed' provider is configured.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    lines = []
    with Storage(config.db_path) as storage:
        targets = [person for person in storage.list_people() if person.sources]
        if not targets:
            return "No people have sources configured — nothing to sync."
        fetched = 0
        new_posts = 0
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                lines.append(f"{person.name}: fetch failed: {exc}")
                continue
            fetched += 1
            new_posts += report.added
        lines.append(f"Fetched {fetched}/{len(targets)} people  new posts: {new_posts}")
        if fetched == 0:
            return "\n".join([*lines, "Every fetch failed; embedding was not attempted."])
        try:
            provider = get_embedding_provider(config)
            embed_report = embed_missing(storage, provider)
            lines.append(
                f"Embedded {embed_report.corpus_embedded + embed_report.external_embedded} "
                f"new documents ({embed_report.provider}/{embed_report.model})"
            )
        except (ModelConfigError, EmbeddingError) as exc:
            lines.append(f"Embedding skipped: {exc} — fetched posts were kept.")
    return "\n".join(lines)


@server.tool()
def embed() -> str:
    """Embed corpus and people's writing for similarity (RFC-010 — the explicit egress step)."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        provider = get_embedding_provider(config)
        with Storage(config.db_path) as storage:
            report = embed_missing(storage, provider)
    except (ModelConfigError, EmbeddingError) as exc:
        return f"embed failed: {exc}"
    return (
        f"Embedded {report.corpus_embedded} corpus + {report.external_embedded} external "
        f"({report.provider}/{report.model}); already embedded: {report.already_embedded}"
    )


@server.tool()
def people_evidence(query: str, limit: int = 10) -> str:
    """Search watched people's writing: who has said what about a topic, with attribution."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            hits = find_people_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        return f"Search failed: {exc}"
    if not hits:
        return f"No evidence found in people's writing for {query!r}."
    lines = []
    for number, hit in enumerate(hits, start=1):
        via = f" (via {hit.document.organization})" if hit.document.organization else ""
        lines.append(
            f"{number}. {hit.person_name}{via} — {hit.document.title}\n   {hit.snippet}\n"
            f"   source: {hit.document.url or hit.document.source_record_id}"
        )
    return "\n".join(lines)


@server.tool()
def people_similar(name: str = "", limit: int = 10) -> str:
    """Rank people by similarity to one person's writing — or, with no name, to the user's own corpus."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = similar_people(storage, name=name.strip() or None, limit=limit)
    except IngestError as exc:
        return f"people similar failed: {exc}"
    if not report.people:
        return "No other people have embedded writing yet — fetch feeds and run the embed tool."
    return _similarity_lines(report.reference, list(report.people))


@server.tool()
def people_like(names: list[str], limit: int = 10) -> str:
    """'If you like these people, talk to…': rank people near the centroid of two or more names."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = people_like_use_case(storage, names=names, limit=limit)
    except IngestError as exc:
        return f"people like failed: {exc}"
    if not report.people:
        return "No other people have embedded writing yet — fetch feeds and run the embed tool."
    return _similarity_lines(report.reference, list(report.people))


def _company_lines(report: CompanySimilarityReport) -> str:
    if not report.companies:
        return (
            "No other companies have embedded writing yet — add people with a "
            "company (people_add) or attach an org-attributed feed "
            "(feed_attach), then run the sync tool."
        )
    lines = [f"Closest to {report.reference}:"]
    for number, entry in enumerate(report.companies, start=1):
        lines.append(
            f"{number}. {entry.name}  score {entry.score:.3f}  "
            f"[{entry.people} people, {entry.documents} docs]"
        )
    return "\n".join(lines)


@server.tool()
def company_similar(name: str = "", limit: int = 10) -> str:
    """Rank companies by the writing of their people and blogs — vs one company, or vs the user's corpus with no name."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = similar_companies(storage, name=name.strip() or None, limit=limit)
    except IngestError as exc:
        return f"company similar failed: {exc}"
    return _company_lines(report)


@server.tool()
def woven_warm_path(person: str = "", company: str = "", from_person: str = "") -> str:
    """Who in your network could make an introduction — a live, on-demand
    call to Woven (#81, RFC-043), a separate warm-intro-path graph server.

    Give exactly one of person (Woven's warmest path to that name) or
    company (Woven's full picture of that company: reachable people,
    coverage, best entry point). from_person narrows the search to paths
    starting from a specific network owner, when Woven pools more than one.

    Nothing is cached or stored — every call is a fresh read, and the
    result is Woven's own text, not wingman's interpretation of it; cite it
    as "from Woven" rather than restating it as wingman's own judgment.
    Identity resolution is entirely Woven's fuzzy name/company matching —
    if Woven's result shows ambiguity (e.g. two similarly-named people),
    surface that to the user rather than guessing which one was meant.
    Requires WINGMAN_WOVEN_URL to be configured; otherwise returns a clear
    "not configured" message rather than failing.
    """
    from wingman.application.warm_intro import warm_overview_for_company, warm_paths_to_person

    if bool(person.strip()) == bool(company.strip()):
        return "give exactly one of person or company."
    try:
        if person.strip():
            return warm_paths_to_person(person, from_person=from_person)
        return warm_overview_for_company(company, from_person=from_person)
    except IngestError as exc:
        return f"woven warm path failed: {exc}"


@server.tool()
def company_like(names: list[str], limit: int = 10) -> str:
    """'If these companies interest you, look at…': rank companies near the centroid of two or more names."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    try:
        with Storage(config.db_path) as storage:
            report = companies_like(storage, names=names, limit=limit)
    except IngestError as exc:
        return f"company like failed: {exc}"
    return _company_lines(report)


@server.tool()
def company_dossier(name: str) -> str:
    """A dated, cited company snapshot from data already in the workspace.

    Deterministic composition — no model call, no network: watched people at
    the company, org-attributed sources, their validated POV stances (each an
    [inference] backed by a verbatim [fact] quote), similarity signals when
    embeddings exist, staleness warnings, and gaps. Also written as Markdown
    under reports/companies/.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = build_company_dossier(name, config, storage)
    except IngestError as exc:
        return f"company dossier failed: {exc}"
    return report.markdown + f"\n(written to {report.path})"


@server.tool()
def export_pdf(
    target: str, name: str = "", out_dir: str = "", as_html: bool = False, title: str = ""
) -> str:
    """Write a print-ready, design-system-styled Letter export under reports/pdf/.

    target is 'career' (portrait profile one-pager), 'company' (dossier,
    requires name), or 'person' (landscape three-column sheet — outreach
    brief | point of view | related links — requires name). Returns the
    written path; the user renders it with md-to-pdf or any Markdown
    previewer. No model call, no network.

    title, if given, becomes the export's displayed name (its frontmatter
    title, and what the web UI's report list shows) instead of the default
    ('Career Profile', 'Company dossier: <name>', or the person's name).

    Protocol: before calling, ask the user what they'd like this report
    called — do not invent a title yourself or generate silently with a
    placeholder.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    destination = Path(out_dir).expanduser() if out_dir.strip() else None
    report_title = title.strip() or None
    try:
        with Storage(config.db_path) as storage:
            if target == "career":
                path = export_career(config, storage, out_dir=destination, title=report_title)
            elif target == "company":
                if not name.strip():
                    return "export company needs a company name."
                path = export_company(
                    name, config, storage, out_dir=destination, title=report_title
                )
            elif target == "person":
                if not name.strip():
                    return "export person needs a person's name."
                found = _find_person(storage, name)
                if isinstance(found, str):
                    return found
                path = export_person(
                    found.name,
                    config,
                    storage,
                    out_dir=destination,
                    as_html=as_html,
                    title=report_title,
                )
            else:
                return f"unknown export target {target!r}; use career, company, or person."
    except IngestError as exc:
        return f"export failed: {exc}"
    return f'Wrote {path}\nRender: npx md-to-pdf "{path}"\nPDF lands at: {path.with_suffix(".pdf")}'


@server.tool()
def people_pov(name: str, refresh: bool = False) -> str:
    """What this person thinks: an evidence-backed POV card from their stored writing.

    Returns the stored card when one exists; refresh=True rebuilds it (a
    model call — the person's stored posts go to the synthesize_balanced
    provider, and every stance is kept only if its quote appears verbatim
    in the stored document).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        found = _find_person(storage, name)
        if isinstance(found, str):
            return found
        person = found
        if not refresh:
            stored = storage.get_pov_card(person.person_id)
            if stored is not None:
                return render_pov_card(stored) + "\n\n(stored card — rebuild with refresh=True)"
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_pov_card(person.name, storage, provider)
        except ProposalParseError as exc:
            return f"people pov failed: {exc}. Nothing was stored; call again to retry."
        except (IngestError, ModelConfigError, ProviderError) as exc:
            return f"people pov failed: {exc}"
    rejected = "".join(
        f"\n  rejected stance {item.statement!r}: {item.reason}" for item in report.rejected
    )
    return render_pov_card(report.card) + rejected


@server.tool()
def company_pov(name: str, refresh: bool = False) -> str:
    """Synthesized company themes from its people's writing (a model call on refresh).

    The company's document pool — writing by watched people there plus
    org-attributed feeds, author attribution on every document — goes to the
    synthesize_balanced provider, and every theme is kept only if its quote
    appears verbatim in a stored document. Returns the stored card when one
    exists; refresh=True rebuilds. The dossier renders the stored card.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        if not refresh:
            stored = storage.get_pov_card(company_card_id(company_key(name)))
            if stored is not None:
                return render_pov_card(stored) + "\n\n(stored card — rebuild with refresh=True)"
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_company_pov(name, storage, provider)
        except ProposalParseError as exc:
            return f"company pov failed: {exc}. Nothing was stored; call again to retry."
        except (IngestError, ModelConfigError, ProviderError) as exc:
            return f"company pov failed: {exc}"
    rejected = "".join(
        f"\n  rejected theme {item.statement!r}: {item.reason}" for item in report.rejected
    )
    return render_pov_card(report.card) + rejected


@server.tool()
def my_pov(refresh: bool = False) -> str:
    """The user's own point of view: subject areas where their corpus takes a position.

    The same evidence-validated card machinery as people_pov, pointed at the
    user's own writing (a model call on refresh). Use it to help the user
    choose which of their positions to lead with in outreach.

    Coaching mode (docs/COACHING-MODE-DESIGN.md): if a persona is active
    (coach_persona 'set'), this builds THEIR stance instead — from only
    their own scoped interview captures, never the coach's corpus or POV
    (deliberately excluded, not just unused). Says so in the response
    either way.
    """
    from wingman.application.coaching import get_active_persona, render_acting_as
    from wingman.application.pov import persona_card_id

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        active_persona = get_active_persona(storage, config)
        card_id = (
            persona_card_id(active_persona.persona_id)
            if active_persona is not None
            else CORPUS_PERSON_ID
        )
        if not refresh:
            stored = storage.get_pov_card(card_id)
            if stored is not None:
                return (
                    f"{render_acting_as(active_persona)}\n"
                    f"{render_pov_card(stored)}\n\n(stored card — rebuild with refresh=True)"
                )
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_own_pov(storage, provider, persona=active_persona)
        except ProposalParseError as exc:
            return f"pov failed: {exc}. Nothing was stored; call again to retry."
        except (IngestError, ModelConfigError, ProviderError) as exc:
            return f"pov failed: {exc}"
    rejected = "".join(
        f"\n  rejected stance {item.statement!r}: {item.reason}" for item in report.rejected
    )
    return f"{render_acting_as(active_persona)}\n{render_pov_card(report.card)}{rejected}"


def _miso_lines(report: MisoReport) -> str:
    marks = {"ok": "✓", "skipped": "–", "failed": "✗"}
    lines = [f"{report.target} ({report.kind}):"]
    lines.extend(
        f"  {marks.get(step.status, '?')} {step.name}: {step.detail}" for step in report.steps
    )
    if report.export_path:
        lines.append(f'Render: npx md-to-pdf "{report.export_path}"')
    return "\n".join(lines)


@server.tool()
def make_it_so(name: str, purpose: str = "introduction", out_dir: str = "") -> str:
    """The easy daily command: everything end to end for a person or company.

    Fetch, news, embed, POV, brief, and both exports, with honest per-step
    results — model steps skip visibly without API keys. Network use is the
    same as the underlying tools (feed fetch, news RSS, embeddings egress).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        outreach_purpose = OutreachPurpose(purpose.strip().lower())
    except ValueError:
        valid = ", ".join(entry.value for entry in OutreachPurpose)
        return f"unknown purpose {purpose!r}; use one of: {valid}."
    destination = Path(out_dir).expanduser() if out_dir.strip() else None
    try:
        with Storage(config.db_path) as storage:
            report = make_it_so_use_case(
                name, config, storage, purpose=outreach_purpose, out_dir=destination
            )
    except IngestError as exc:
        return f"make-it-so failed: {exc}"
    return _miso_lines(report)


@server.tool()
def watchlist(action: str, list_name: str = "", member: str = "", company: bool = False) -> str:
    """Manage named groups of people/companies: action is add, remove, list, show, or run.

    'run' cycles every member through make_it_so, continuing past failures.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    kind = "company" if company else "person"
    with Storage(config.db_path) as storage:
        if action == "add":
            if not list_name.strip() or not member.strip():
                return "watchlist add needs list_name and member."
            member_name = member.strip()
            if kind == "person":
                found = _find_person(storage, member)
                if isinstance(found, str):
                    return found
                member_name = found.name
            added = storage.watchlist_add(list_name, kind, member_name)
            state = "Added" if added else "Already on"
            return f"{state} {member_name} ({kind}) — watchlist {list_name!r}."
        if action == "remove":
            removed = storage.watchlist_remove(list_name, kind, member)
            return (
                f"Removed {member} from {list_name!r}."
                if removed
                else f"{member} was not on {list_name!r}."
            )
        if action == "list":
            lists = storage.watchlists()
            if not lists:
                return "No watchlists yet."
            return "\n".join(f"{name}  [{count} members]" for name, count in lists)
        if action == "show":
            members = storage.watchlist_members(list_name)
            if not members:
                return f"Watchlist {list_name!r} has no members."
            return "\n".join(f"{name}  ({member_kind})" for member_kind, name in members)
        if action == "run":
            members = storage.watchlist_members(list_name)
            if not members:
                return f"Watchlist {list_name!r} has no members. Nothing was run."
            blocks: list[str] = []
            failures = 0
            for member_kind, member_name in members:
                try:
                    report = make_it_so_use_case(member_name, config, storage, kind=member_kind)
                except IngestError as exc:
                    failures += 1
                    blocks.append(f"✗ {member_name} ({member_kind}): {exc}")
                    continue
                if any(step.status == "failed" for step in report.steps):
                    failures += 1
                blocks.append(_miso_lines(report))
            blocks.append(f"{len(members)} members processed, {failures} failed.")
            return "\n\n".join(blocks)
    return f"unknown action {action!r}; use add, remove, list, show, or run."


@server.tool()
def company_follow(name: str, url: str = "") -> str:
    """Turn a company into a standing focus: enroll it (and known people there with
    writing) on the overnight watchlist; with url, probe the domain's conventional
    pages once and approve live ones as research sources (RFC-018).

    Enrollment is the consent record for overnight runs — enumerable via
    watchlist(action='show', list_name='overnight'), revocable via watchlist remove.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = follow_company(name, storage, url=url or None)
    except IngestError as exc:
        return f"follow failed: {exc}"
    return render_follow_report(report)


@server.tool()
def overnight() -> str:
    """Deep-refresh every followed target and write the dated digest (RFC-018).

    Deliberately expensive: research diffs, feed fetches, news queries (each
    enrolled name+company goes to the news provider), embeddings, fresh POV
    cards, company themes, briefs, exports. Returns the per-target results and
    the digest path. Nothing is ever sent on the user's behalf (RFC-006).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = overnight_run(config, storage)
    except IngestError as exc:
        return f"overnight failed: {exc}"
    lines = [
        f"{'✓' if target.status == 'ok' else '✗'} {target.name} ({target.kind})"
        for target in report.targets
    ]
    lines.append(f"{report.processed} targets, {report.failed} with failures.")
    lines.append(f"Digest: {report.digest_path}")
    return "\n".join(lines)


@server.tool()
def assess_job_url(url: str) -> str:
    """Fetch a job posting from its https:// URL and assess it against the profile.

    One explicit GET (RFC-009/024): the page's visible text is archived to the
    inbox as the provenance record, then assessed exactly like a pasted job
    description — verdicts cite only real profile items. Fails visibly on
    login walls and JavaScript-only pages.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        job_path = fetch_job_posting(url, config)
        with Storage(config.db_path) as storage:
            report = assess_job_use_case(
                job_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
                get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"assess failed: {exc}"
    return Path(report.brief_md_path).read_text(encoding="utf-8")


@server.tool()
def pack(query: str, company: str = "", out_dir: str = "") -> str:
    """Compose the application pack for an assessed role (RFC-024): cited fit
    summary, cover-letter fodder quoting the user's own evidence verbatim (they
    compose the letter in their voice — Wingman never sends), and the company
    intelligence already in the workspace. Returns the pack markdown; the file
    also lands under reports/packs/ for md-to-pdf rendering.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = build_application_pack(
                query,
                config,
                storage,
                company=company or None,
                out_dir=Path(out_dir).expanduser() if out_dir else None,
            )
    except IngestError as exc:
        return f"pack failed: {exc}"
    return report.markdown


@server.tool()
def feature_request(title: str = "", body: str = "", confirmed: bool = False) -> str:
    """File a Wingman feature request as a GitHub issue — the RFC-025 protocol.

    PROTOCOL — when the user says "feature request: <idea>" (or similar):
    1. Read what they wrote. If anything material is ambiguous — the problem
       being solved, the desired behavior, scope — ask clarifying questions
       FIRST, in conversation. Skip the questions when the request is clear.
    2. Compose a crisp title and a body with: the problem, the requested
       behavior, and any acceptance criteria the user gave.
    3. Call this tool with confirmed=false: it returns the EXACT issue
       preview. Show it to the user and ask whether to file it.
    4. Only after the user explicitly says yes, call again with
       confirmed=true. Never set confirmed=true without that explicit yes —
       this is Wingman's one external write, and the confirmation IS the
       approval gate (RFC-006).

    Filing uses the user's own gh CLI and auth, into the repo they set with
    'wingman feature repo'. A missing repo or gh failure is reported, never
    silent.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not title.strip():
        return "feature_request needs a title. Gather the idea first (see the protocol)."
    body = stamp_operator(body)
    if not confirmed:
        return (
            render_preview(get_feature_repo(config), title, body)
            + "\n\nNot filed. Show this preview to the user; call again with "
            "confirmed=true only after they explicitly approve."
        )
    try:
        filed = file_feature_request(config, title, body)
    except IngestError as exc:
        return f"feature request failed: {exc}"
    return f"Filed: {filed.url}"


@server.tool()
def digest() -> str:
    """The newest overnight digest — what changed, what failed, and the action
    list (what/why/who/evidence). The morning starting point after a scheduled
    'wingman overnight' run; pair with 'search' to dig into anything it raises.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    newest = latest_digest(config)
    if newest is None:
        return (
            "No digests yet — 'wingman overnight' writes one per run "
            "(enroll targets first with company_follow)."
        )
    return newest.read_text(encoding="utf-8")


@server.tool()
def changelog() -> str:
    """Recent wingman development activity — merged PR titles, verbatim, dated
    newest first, curated to user-facing changes including docs (RFC-038,
    issue #145). No model rewrite. This describes the tool itself, not your
    workspace, so it needs no initialized workspace to answer "what's new
    in wingman?".
    """
    from wingman.domain.changelog import render_changelog

    return render_changelog(datetime.now(UTC).date())


@server.tool()
def company_feed(action: str, name: str, url: str = "", index_page: bool = False) -> str:
    """Manage feeds attached directly to a company — no person needed (RFC-029).

    action is 'attach', 'list', 'remove', or 'fetch'. attach takes a feed
    URL (RSS/Atom; index_page=true for a blog index with no feed) and only
    after the user confirmed the exact URL (RFC-011) — use feed_discover
    to find it from a blog page first. Posts are organization-attributed
    to the company and flow into its dossier, themes, news, and search;
    'fetch' pulls them now, and overnight runs fetch them automatically
    for followed companies.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "attach":
                anchor, source = attach_company_feed(name, url, storage, index_page=index_page)
                return f"Attached to {anchor.company}: {source.url} ({source.kind.value})"
            if action == "list":
                feeds = list_company_feeds(name, storage)
                if not feeds:
                    return f"No company feeds for {name.strip()!r}."
                return "\n".join(f"{feed.url} ({feed.kind.value})" for feed in feeds)
            if action == "remove":
                if remove_company_feed(name, url, storage):
                    return f"Removed {url}"
                return f"No such feed on {name.strip()!r}: {url}"
            if action == "fetch":
                report = fetch_company_feeds(name, config, storage)
                titles = "".join(f"\n  + {title}" for title in report.titles)
                return (
                    f"{report.items} item(s) seen, {report.added} added, "
                    f"{report.skipped_duplicates} duplicate(s) skipped.{titles}"
                )
    except IngestError as exc:
        return f"company feed {action} failed: {exc}"
    return f"unknown action {action!r}; use attach, list, remove, or fetch."


@server.tool()
def company_source(action: str, name: str, url: str = "", label: str = "") -> str:
    """Manage approved research URLs for a company: action is add, remove, or list.

    Adding a source IS the approval (RFC-015): 'company_research' will fetch
    exactly the pages approved here, nothing else. https:// only.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "add":
                if not url.strip():
                    return "company_source add needs a url."
                source, created = add_company_source(name, url, storage, label=label or None)
                state = "Approved" if created else "Already approved"
                return f"{state} for {source.company_name}: {source.url}"
            if action == "remove":
                removed = remove_company_source(name, url, storage)
                return (
                    f"Withdrawn: {url}"
                    if removed
                    else f"{url} was not an approved source for {name!r}."
                )
            if action == "list":
                sources = list_company_sources(name, storage)
                if not sources:
                    return (
                        f"No approved research sources for {name!r}. "
                        "Approve one with company_source(action='add', ...)."
                    )
                lines = []
                for entry in sources:
                    snapshot = storage.get_research_snapshot(entry.company_key, entry.url)
                    state = (
                        f"snapshot {snapshot.fetched_at.date().isoformat()},"
                        f" {len(snapshot.links)} links"
                        if snapshot
                        else "no snapshot yet"
                    )
                    tag = f" ({entry.label})" if entry.label else ""
                    lines.append(f"- {entry.url}{tag} — {state}")
                return "\n".join(lines)
    except IngestError as exc:
        return f"company_source failed: {exc}"
    return f"unknown action {action!r}; use add, remove, or list."


@server.tool()
def company_manage(action: str, name: str, new_name: str = "") -> str:
    """Rename or delete a company. action is 'rename', 'delete', or 'delete-dossier'.

    'rename' re-keys approved sources, research snapshots, the POV card, and watchlist
    memberships to new_name. 'delete' removes all of those for a company (not reversible,
    but leaves any generated dossier files on disk). 'delete-dossier' removes only the
    generated dossier report files, leaving the underlying sources/research/POV card
    untouched — use it to clear out a dossier before regenerating one.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            if action == "rename":
                moved, people_moved = rename_company(name, new_name, storage)
                return (
                    f"Renamed {name!r} to {new_name!r} "
                    f"({moved} source(s), {people_moved} person/people moved)"
                )
            if action == "delete":
                removed, cleared = delete_company(name, storage)
                if not removed:
                    return f"Nothing found for {name!r}."
                message = f"Deleted {name!r} (sources, research, POV card, watchlist memberships)."
                if cleared:
                    message += f" Company cleared on: {', '.join(cleared)}"
                return message
    except IngestError as exc:
        return f"company_manage {action} failed: {exc}"
    if action == "delete-dossier":
        removed_paths = delete_dossier_reports(name, config)
        if not removed_paths:
            return f"No dossier files found for {name!r}."
        return f"Deleted {len(removed_paths)} dossier file(s): {', '.join(removed_paths)}"
    return f"unknown action {action!r}; use rename, delete, or delete-dossier."


@server.tool()
def company_research(name: str) -> str:
    """Fetch every approved research source for a company and report what changed.

    One read-only HTTPS GET per user-approved URL (RFC-015). Findings are
    deterministic diffs against the previous snapshot: new links (the
    hiring/announcement signal) and changed page text. A failed source keeps
    its previous snapshot and is reported, never fatal.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = research_company(name, storage)
    except IngestError as exc:
        return f"research failed: {exc}"
    return render_research_report(report)


@server.tool()
def people_news(name: str) -> str:
    """Fetch and store recent news mentioning a person or their company.

    One read-only GET of Google News's public RSS search (RFC-009 shape).
    The query — their name and company — is sent to the news provider; that
    is the entire egress. Replaces the stored snapshot shown in the person
    export's News quadrant.
    """
    from wingman.application.news import STALE_AFTER_DAYS, fetch_person_news

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        found = _find_person(storage, name)
        if isinstance(found, str):
            return found
        try:
            report = fetch_person_news(found, storage)
        except IngestError as exc:
            return f"people news failed: {exc}"
    if not report.titles:
        if report.dropped or report.dropped_stale:
            reasons = []
            if report.dropped:
                reasons.append(f"{report.dropped} low-relevance")
            if report.dropped_stale:
                reasons.append(f"{report.dropped_stale} too old (>{STALE_AFTER_DAYS}d)")
            return f"{' and '.join(reasons)} for {report.query}; nothing stored."
        return f"No recent news found for {report.query}."
    lines = [f"News for {report.query}:"]
    lines.extend(f"{number}. {title}" for number, title in enumerate(report.titles, start=1))
    summary = f"{report.stored} items stored"
    dropped_bits = []
    if report.dropped:
        dropped_bits.append(f"{report.dropped} low-relevance")
    if report.dropped_stale:
        dropped_bits.append(f"{report.dropped_stale} too old")
    if dropped_bits:
        summary += f", {' and '.join(dropped_bits)} dropped"
    lines.append(summary + ".")
    return "\n".join(lines)


@server.tool()
def people_docs(name: str) -> str:
    """List a person's stored documents: title, date, and source URL, newest first."""
    from wingman.reporting.export import newest_first

    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        found = _find_person(storage, name)
        if isinstance(found, str):
            return found
        person = found
        documents = storage.list_external_documents(person.person_id)
    if not documents:
        return f"{person.name} has no stored documents yet — call people_fetch first."
    lines = []
    for number, document in enumerate(newest_first(documents), start=1):
        when = document.published_at.date().isoformat() if document.published_at else "undated"
        via = f" (via {document.organization})" if document.organization else ""
        lines.append(f"{number}. {document.title} [{when}]{via}")
        lines.append(f"   {document.url or document.source_record_id}")
    lines.append(f"{len(documents)} documents.")
    return "\n".join(lines)


@server.tool()
def people_brief(name: str, purpose: str = "introduction", refresh: bool = False) -> str:
    """Draft outreach talking points and intro bullets connecting a person's POV to the user's writing.

    purpose is why the user is reaching out: introduction, reconnection,
    job, or advice — it shapes the point selection and intro material.
    Returns the stored brief when one exists; refresh=True rebuilds it (a
    model call — the person's POV card plus excerpts of the user's corpus go
    to the synthesize_balanced provider, and a talking point is kept only if
    it cites a card stance exactly and quotes the corpus verbatim). Drafts
    only — Wingman never sends anything (RFC-006).

    When the person has a relationship objective and/or logged
    interactions (RFC-037), a deterministic context footer is appended —
    goal/thesis/next-move plus recent interactions, verbatim, never
    model-generated. Cite it; don't restate it as your own judgment.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        outreach_purpose = OutreachPurpose(purpose.strip().lower())
    except ValueError:
        valid = ", ".join(entry.value for entry in OutreachPurpose)
        return f"unknown purpose {purpose!r}; use one of: {valid}."
    from wingman.application.relationship import render_relationship_context

    with Storage(config.db_path) as storage:
        found = _find_person(storage, name)
        if isinstance(found, str):
            return found
        person = found
        context = render_relationship_context(person, storage)
        context_suffix = f"\n\n{context}" if context else ""
        if not refresh:
            stored = storage.get_outreach_brief(person.person_id)
            if stored is not None:
                hint = "rebuild with refresh=True"
                if stored.purpose is not outreach_purpose:
                    hint = f"stored purpose is {stored.purpose.value!r} — rebuild with refresh=True"
                return (
                    render_outreach_brief(stored) + f"\n\n(stored brief — {hint})" + context_suffix
                )
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_outreach_brief(person.name, storage, provider, purpose=outreach_purpose)
        except ProposalParseError as exc:
            return f"people brief failed: {exc}. Nothing was stored; call again to retry."
        except (IngestError, ModelConfigError, ProviderError) as exc:
            return f"people brief failed: {exc}"
    rejected = "".join(
        f"\n  rejected point {item.point!r}: {item.reason}" for item in report.rejected
    )
    return render_outreach_brief(report.brief) + rejected + context_suffix


@server.tool()
def people_discover(limit: int = 10) -> str:
    """Suggest new publications from the recommendations of watched Substacks (RFC-009).

    Suggestions only — nothing is added; use people_add after the user picks.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    limit = max(1, min(limit, 25))
    with Storage(config.db_path) as storage:
        report = discover_recommendations(storage, limit=limit)
    lines = [f"failed: {failure}" for failure in report.failures]
    if report.scanned == 0 and not report.failures:
        return "No watched Substacks to walk — add some with people_add first."
    if report.scanned == 0:
        return "\n".join([*lines, "Every recommendations page failed to fetch."])
    if not report.candidates:
        return "\n".join(
            [*lines, f"Scanned {report.scanned} publications — no new recommendations found."]
        )
    lines.append(f"Scanned {report.scanned} publications. Worth a look:")
    for number, candidate in enumerate(report.candidates, start=1):
        who = ", ".join(candidate.recommenders[:3])
        lines.append(
            f"{number}. {candidate.url}  (recommended by {len(candidate.recommenders)}: {who})"
        )
    return "\n".join(lines)


@server.tool()
def feed_discover(person_name: str, url: str) -> str:
    """Find a feed for a URL (direct, HTML autodiscovery, or conventional paths) — step 1 of 2.

    Never attaches anything. Present the findings to the user and call
    feed_attach ONLY after they explicitly confirm — discovery can succeed
    on the wrong person's feed (RFC-011).
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        found = _find_person(storage, person_name)
    if isinstance(found, str):
        return found
    person = found
    try:
        discovery = discover_feed(url)
    except IngestError as exc:
        return f"feed discovery failed: {exc}"
    if discovery.feed_url:
        return (
            f"Found feed: {discovery.feed_url} (titled {discovery.feed_title!r}). "
            f"Ask the user to confirm before calling feed_attach for {person.name} — "
            "do not attach without their explicit yes."
        )
    return (
        f"No feed found at or near {url} (probed {len(discovery.probed)} URLs). "
        "The page can be watched as an index source instead: call feed_attach with "
        "kind='index_page' after the user explicitly confirms."
    )


@server.tool()
def feed_attach(person_name: str, url: str, kind: str = "rss", organization: str = "") -> str:
    """Attach a feed or index-page source to a person — step 2 of 2, after user confirmation.

    Call ONLY after the user explicitly confirmed the exact URL in
    conversation (RFC-011). kind is 'rss' or 'index_page'; set organization
    to attribute a company blog's posts to the organization.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if kind not in {FeedKind.RSS.value, FeedKind.INDEX_PAGE.value}:
        return f"kind must be 'rss' or 'index_page'; got {kind!r}."
    url = url.rstrip("/")  # match attach_feed's stored normalization in the echoed message
    with Storage(config.db_path) as storage:
        found = _find_person(storage, person_name)
        if isinstance(found, str):
            return found
        person = found
        source = FeedSource(
            url=url,
            kind=FeedKind(kind),
            attribution=FeedAttribution.ORGANIZATION if organization else FeedAttribution.PERSON,
            org_name=organization or None,
        )
        try:
            attach_feed(person, source, storage)
        except IngestError as exc:
            return f"feed attach failed: {exc}"
    label = f" (attributed to {organization})" if organization else ""
    return f"Attached {kind} source to {person.name}: {url}{label}"


@server.tool()
def people_import_connections(export_path: str) -> str:
    """Seed the watchlist from a LinkedIn export zip's Connections.csv (names/roles only, never emails)."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            report = seed_from_connections(Path(export_path).expanduser(), storage)
    except IngestError as exc:
        return f"import failed: {exc}"
    return (
        f"Created: {report.created}  Already known: {report.skipped_existing}  "
        f"Incomplete rows skipped: {report.skipped_incomplete}"
    )


@server.tool()
def backup(dest: str = "", keep: int = 10) -> str:
    """Snapshot the workspace into a dated tarball (database, models.toml, inbox, reports).

    dest: destination folder (default: the workspace's backups/). Point it at a
    synced folder — a closed tarball syncs safely where the live database does not.
    keep: backups to retain at the destination (0 keeps all). Restoring is
    deliberately CLI-only ('wingman restore') because it overwrites the workspace.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        report = create_backup(config, dest=Path(dest).expanduser() if dest else None, keep=keep)
    except IngestError as exc:
        return f"backup failed: {exc}"
    lines = [f"Backup written: {report.path}", f"{report.files} files, {report.size_bytes} bytes"]
    lines.extend(f"pruned old backup: {name}" for name in report.pruned)
    lines.append(f'Restore (CLI only): wingman restore "{report.path}"')
    return "\n".join(lines)


_TOKEN_FILENAME = "mcp-http-token"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _http_token(config: Config, rotate: bool = False) -> str:
    """The capability-path token for the HTTP transport (RFC-017).

    Generated once into the workspace with owner-only permissions; rotating
    it is how a leaked URL is revoked.
    """
    path = config.data_dir / _TOKEN_FILENAME
    if rotate or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def _tailscale_dns_name(runner: Callable[..., Any] = subprocess.run) -> str | None:
    """This machine's Tailscale hostname (host.tailnet.ts.net), or None.

    Best-effort by design: no tailscale binary, daemon down, or odd output
    all mean None — the server must come up fine on a box without Tailscale.
    """
    try:
        proc = runner(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        name = json.loads(proc.stdout)["Self"]["DNSName"]
    except Exception:
        return None
    return str(name).rstrip(".") or None


def _extra_allowed_hosts(
    cli_hosts: Sequence[str] | None,
    env: Mapping[str, str] | None = None,
    tailscale: Callable[[], str | None] = _tailscale_dns_name,
) -> list[str]:
    """Front-door hostnames the HTTP transport should accept beyond loopback (#100).

    Merged, in order: --allowed-host flags, the WINGMAN_ALLOWED_HOSTS env var
    (comma-separated; systemd-friendly), and the machine's own Tailscale name —
    auto-detected so the documented serve/funnel workflow needs no extra config.
    """
    env = os.environ if env is None else env
    merged = list(cli_hosts or [])
    merged.extend(env.get("WINGMAN_ALLOWED_HOSTS", "").split(","))
    detected = tailscale()
    if detected:
        merged.append(detected)
    ordered: dict[str, None] = {}
    for entry in merged:
        # tolerate a pasted URL: strip scheme, path, trailing slash
        host = entry.strip().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        if host:
            ordered.setdefault(host, None)
    return list(ordered)


def _tunnel_port(cli_value: int | None = None, env: Mapping[str, str] | None = None) -> int | None:
    """The external port a tunnel front listens on, when it differs from
    --port: two instances can share one Tailscale hostname on distinct
    funnel ports (443/8443/10000) instead of colliding on the default root
    mapping. CLI flag wins; WINGMAN_TUNNEL_PORT (systemd EnvironmentFile-
    friendly) is the fallback; unset means 'omit the port, assume 443' —
    unchanged behavior for the common single-instance case.
    """
    if cli_value is not None:
        return cli_value
    env = os.environ if env is None else env
    raw = env.get("WINGMAN_TUNNEL_PORT", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def connector_urls(
    token: str,
    extra_hosts: Sequence[str],
    host: str = "127.0.0.1",
    port: int = 8787,
    prefix: str = "",
    tunnel_port: int | None = None,
) -> list[tuple[str, str]]:
    """(label, url) pairs: loopback MCP + web UI, plus a tunnel pair per
    accepted hostname. The single source of truth behind both 'render_urls'
    (the --http banner and 'wingman mcp url' text output) and the web UI's
    Connect tab — pure formatting over already-computed token/extra_hosts.
    'tunnel_port' is the tunnel's own external port when it isn't the
    implicit 443 (e.g. a second instance on the same Tailscale hostname via
    a distinct funnel port) — orthogonal to 'port', which is always the
    local bind.
    """
    from wingman.webui import normalize_prefix

    prefix = normalize_prefix(prefix)
    pairs = [
        ("MCP over HTTP", f"http://{host}:{port}{prefix}/mcp/{token}"),
        ("Web UI (read + upload)", f"http://{host}:{port}{prefix}/ui/{token}"),
    ]
    for tunnel_host in extra_hosts:
        authority = tunnel_host if tunnel_port is None else f"{tunnel_host}:{tunnel_port}"
        pairs.append(("Tunnel MCP connector", f"https://{authority}{prefix}/mcp/{token}"))
        pairs.append(("Tunnel web UI", f"https://{authority}{prefix}/ui/{token}/"))
    return pairs


def render_urls(
    token: str,
    extra_hosts: Sequence[str],
    host: str = "127.0.0.1",
    port: int = 8787,
    prefix: str = "",
    tunnel_port: int | None = None,
) -> list[str]:
    """The ready-to-paste URL lines, formatted from 'connector_urls' — backs
    both the --http startup banner and 'wingman mcp url', which computes
    those fresh from the token file and Tailscale auto-detection without
    starting a server.
    """
    pairs = connector_urls(token, extra_hosts, host, port, prefix, tunnel_port)
    return [f"{label}: {url}" for label, url in pairs]


def build_transport_security(extra_hosts: Sequence[str]) -> TransportSecuritySettings:
    """DNS-rebinding settings for a loopback bind: the SDK's loopback allow-list
    plus each extra hostname (#100). Protection stays ON — a tunnel widens the
    list; it never switches the check off.
    """
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for host in extra_hosts:
        allowed_hosts += [host, f"{host}:*"]
        allowed_origins += [
            f"https://{host}",
            f"https://{host}:*",
            f"http://{host}",
            f"http://{host}:*",
        ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


@server.tool()
def telemetry(action: str = "status", limit: int = 20) -> str:
    """The opt-in local usage journal (RFC-023): action is 'status' or 'show'.

    'show' returns recent events — CLI invocations, MCP tool calls with
    their arguments and results, harvested transcript messages. The journal
    is local-only and default-off; turning it on/off is deliberately
    CLI-only ('wingman telemetry on|off'), like key management.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if action == "status":
        state = "ON" if telemetry_is_enabled(config) else "OFF"
        return f"Telemetry: {state}  events: {telemetry_count(config)}"
    if action == "show":
        events = telemetry_list(config, limit=max(1, min(limit, 100)))
        if not events:
            return "No telemetry events recorded."
        lines = []
        for event in events:
            payload = json.dumps(event["payload"], ensure_ascii=False)
            lines.append(
                f"{event['ts']}  [{event['surface']}] {event['name']} ({event['outcome']})\n"
                f"   {payload[:300]}{'…' if len(payload) > 300 else ''}"
            )
        return "\n".join(lines)
    return f"unknown action {action!r}; use status or show."


@server.tool()
def tenant_url(slug: str, host: str = "127.0.0.1", port: int = 8787) -> str:
    """A registered tenant's MCP + web UI connector URLs, by slug (#209,
    RFC-048's operator-assisted URL recovery). 'host'/'port' should match
    how the shared process was actually started, same as 'wingman mcp
    url' for a single-tenant instance. Operator-only: refuses when called
    from within any tenant's own scoped session, so a tenant can never
    use their own MCP session to look up another tenant's URL.

    Every tenant Config built by Tenant.config() sets
    strict_provider_keys=True by construction (RFC-048's key-isolation
    invariant); this reuses that same flag as the gate here, rather than
    inventing a second "is this an operator" concept — a genuinely
    single-tenant instance (an operator's own) never sets it.
    """
    config = load_config()
    if config.strict_provider_keys:
        return "Not available from a tenant session — this is an operator-only tool."
    from wingman.infrastructure.tenants import TenantRegistryError, load_registry, tenant_registry_path

    registry_path = tenant_registry_path()
    try:
        tenants = load_registry(registry_path)
    except TenantRegistryError as exc:
        return f"Could not read the tenant registry ({registry_path}): {exc}"
    tenant = next((t for t in tenants if t.slug == slug), None)
    if tenant is None:
        return f"No tenant {slug!r} in the registry ({registry_path})."
    token = tenant.read_token()
    if token is None:
        return f"Tenant {slug!r} has no token yet ({tenant.token_path()} is missing or empty)."
    extra_hosts = _extra_allowed_hosts(None)
    return "\n".join(render_urls(token, extra_hosts, host=host, port=port))


def _instrument_tools() -> None:
    """Wrap every registered tool so calls land in the opt-in journal (RFC-023).

    Recording is a no-op unless the owner ran 'wingman telemetry on'; a
    failure to record never breaks the tool call being recorded.
    """
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if not tools:  # pragma: no cover — internals moved; better unrecorded than broken
        return
    for tool in tools.values():
        original = tool.fn

        def wrapped(
            *args: object,
            __original: Callable[..., str] = original,
            __name: str = tool.name,
            **kwargs: object,
        ) -> str:
            started = time.monotonic()
            try:
                result = __original(*args, **kwargs)
            except Exception as exc:
                record_event(
                    load_config(),
                    "mcp",
                    __name,
                    {"args": kwargs, "error": str(exc)},
                    outcome="error",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                raise
            record_event(
                load_config(),
                "mcp",
                __name,
                {"args": kwargs, "result": result},
                outcome="ok",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return result

        tool.fn = wrapped


_instrument_tools()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="wingman-mcp",
        description=(
            "Wingman MCP server. Default: stdio for a local client (Claude Desktop / "
            "Claude Code). With --http: streamable HTTP on loopback for remote use "
            "through a tunnel you run yourself (RFC-017) — e.g. 'tailscale serve' "
            "for your own devices, 'tailscale funnel' for claude.ai connectors."
        ),
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve streamable HTTP instead of stdio, at /mcp/<token> (loopback only "
        "by default; the URL is a capability — treat it like a password).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for --http (default 127.0.0.1; non-loopback prints a warning).",
    )
    parser.add_argument("--port", type=int, default=8787, help="Port for --http (default 8787).")
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="Generate a fresh capability token before serving (revokes every old URL).",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Native path prefix for --http (e.g. /trent): the server itself listens on "
        "<prefix>/mcp/… and <prefix>/ui/…. For pass-through fronts (nginx, Caddy, direct "
        "access); do NOT combine with a stripping proxy like 'tailscale serve --set-path'.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        metavar="HOST",
        help="Extra Host header to accept over --http (repeatable) — the hostname of "
        "whatever fronts the loopback port. Merged with WINGMAN_ALLOWED_HOSTS "
        "(comma-separated) and this machine's Tailscale name, which is auto-detected, "
        "so 'tailscale serve/funnel' needs no flag.",
    )
    parser.add_argument(
        "--tunnel-port",
        type=int,
        default=None,
        help="External port the tunnel front uses, if not the implicit 443 — e.g. a second "
        "instance sharing this Tailscale hostname on its own funnel port. Only changes the "
        "printed/displayed URLs, not the local bind. Falls back to WINGMAN_TUNNEL_PORT.",
    )
    parser.add_argument(
        "--tenant-registry",
        default=None,
        metavar="PATH",
        help="Serve a SHARED multi-tenant process instead of one workspace (RFC-048): PATH to "
        "the tenant registry TOML ({slug, data_dir} per tenant, no secrets — see "
        "infrastructure.tenants). Presence of this flag switches to tenant mode; --host/--port/"
        "--prefix/--allowed-host/--tunnel-port still apply (uniformly, to every tenant), but "
        "--rotate-token does not — each tenant's own token lives in their own data_dir; rotate "
        "one via 'wingman tenant rotate-token <slug>'.",
    )
    args = parser.parse_args(argv)
    configure_logging()
    get_logger("mcp").info("wingman-mcp %s starting", wingman_version())
    if args.tenant_registry and not args.http:
        parser.error("--tenant-registry only makes sense with --http")
    if args.tenant_registry and args.rotate_token:
        parser.error(
            "--rotate-token doesn't apply with --tenant-registry — each tenant's own token "
            "lives in their own data_dir; rotate one via 'wingman tenant rotate-token <slug>'"
        )
    # One-time move off the legacy flat '~/.config/keys.env' (RFC-046) —
    # idempotent, so this logs nothing on every subsequent start; called
    # here (not just inside 'ensure_env') so a migration on someone's
    # production box is never a silent background action.
    migration = migrate_legacy_host_file()
    if migration.migrated:
        get_logger("mcp").info("one-time host config migration: %s", migration.detail)
    if args.tenant_registry:
        # No single workspace to hydrate the workspace tier for (RFC-048)
        # — each tenant's Anthropic/Voyage keys are resolved strictly from
        # their OWN workspace file at request time (Tenant.config's
        # strict_provider_keys), never from process env. Keychain/host/
        # global tiers still get hydrated normally (e.g. GITHUB_API_ISSUES_KEY,
        # RFC-047's deliberately-shared credential).
        ensure_env()
    else:
        # Hydrate missing API keys: Keychain (RFC-019), then the workspace
        # key file written by the web UI's validated form (RFC-034).
        ensure_env(data_dir=load_config().data_dir)
    if not args.http:
        if args.rotate_token:
            parser.error("--rotate-token only makes sense with --http")
        server.run()
        return

    from wingman.webui import normalize_prefix

    prefix = normalize_prefix(args.prefix)

    if args.tenant_registry:
        _run_tenant_server(args, prefix)
        return

    config = load_config()
    # Preflight before anything is printed or rotated: uvicorn's bind error
    # arrives AFTER the full banner and reads like a crash — and when the
    # port-holder has no pidfile, 'wingman mcp status' swears nothing is
    # running. Refuse up front, naming the process and the remedy.
    existing = read_server_pid(config)
    if existing is not None:
        print(
            f"ERROR: the HTTP MCP server is already running (pid {existing}). "
            "Stop it first: wingman mcp stop — or give a second instance its "
            "own WINGMAN_DATA_DIR.",
            file=sys.stderr,
        )
        sys.exit(1)
    orphans = orphan_http_pids(config, port=args.port)
    if orphans:
        pids = ", ".join(str(orphan) for orphan in orphans)
        print(
            f"ERROR: an orphaned wingman-mcp --http (no pidfile) is holding "
            f"port {args.port}: pid {pids}. Clear it with: wingman mcp stop",
            file=sys.stderr,
        )
        sys.exit(1)
    _probe_bind(args.host, args.port)
    token = _http_token(config, rotate=args.rotate_token)
    server.settings.host = args.host
    server.settings.port = args.port
    server.settings.streamable_http_path = f"{prefix}/mcp/{token}"
    # The SDK bakes a loopback-only Host allow-list into FastMCP at import
    # time, which rejects every request arriving through a tunnel with
    # "Invalid Host header" (#100). Rebuild it: loopback + the tunnel names
    # for a loopback bind; off for a non-loopback bind (the SDK's own
    # semantics for that case — the warning below covers the trade).
    extra_hosts = _extra_allowed_hosts(args.allowed_host)
    if args.host in _LOOPBACK_HOSTS:
        server.settings.transport_security = build_transport_security(extra_hosts)
    else:
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    if args.host not in _LOOPBACK_HOSTS:
        print(
            f"WARNING: binding {args.host} exposes the whole workspace (and its fetch/model "
            "surfaces) to that network. The capability path is the only guard. "
            "Prefer 127.0.0.1 plus a tunnel.",
            file=sys.stderr,
        )
    from wingman.admin import register_admin
    from wingman.webui import register_ui

    register_ui(server, prefix=prefix)
    register_admin(server)
    # Ready-to-paste URLs (#94): the claude.ai connector needs the https
    # form, and hunting the token file to build it by hand was the
    # friction this replaces. Same renderer 'wingman mcp url' uses.
    tunnel_port = _tunnel_port(args.tunnel_port)
    for line in render_urls(
        token, extra_hosts, host=args.host, port=args.port, prefix=prefix, tunnel_port=tunnel_port
    ):
        print(line)
    print("The URL is a capability — anyone holding it can use the workspace.")
    print("Revoke it any time: wingman-mcp --http --rotate-token")
    print(f"Reach it from elsewhere via your own tunnel, e.g.: tailscale serve {args.port}")
    # Under 'wingman-ctl start' stdout is a redirected file, which Python
    # block-buffers: without this flush the banner sits in the buffer for
    # the life of the process and wg's URL echo greps an empty log.
    sys.stdout.flush()
    # The capability token lives in the URL path; uvicorn's access log would
    # write it on every request, silently defeating rotation-as-revocation (#70).
    logging.getLogger("uvicorn.access").disabled = True
    # Pidfile so 'wingman mcp status|stop' can manage exactly this process
    # (RFC-032); cleared on clean exit, verified-then-ignored if we crash.
    write_pidfile(config)
    atexit.register(clear_pidfile, config)
    server.run(transport="streamable-http")


def _probe_bind(host: str, port: int) -> None:
    """Fail loud, before anything is printed or rotated: uvicorn's own
    bind error arrives after the full banner and reads like a crash."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as exc:
            print(
                f"ERROR: cannot bind {host}:{port} ({exc.strerror}). "
                f"Something else holds the port — find it with: lsof -ti :{port}",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_tenant_server(args: argparse.Namespace, prefix: str) -> None:
    """The shared multi-tenant process (RFC-048): one OS process, share-
    nothing per-tenant data, capability tokens per tenant. Bypasses
    'server.run()' deliberately — FastMCP exposes no hook to run tenant
    resolution before its own routing, so this builds the same Starlette
    app 'server.run(transport="streamable-http")' would internally
    (verified against FastMCP's own 'run_streamable_http_async', which
    does exactly 'streamable_http_app()' + 'uvicorn.Config'/'Server') and
    wraps ONE route with 'bind_tenant_routing' before serving it by hand.
    """
    import uvicorn

    from wingman.infrastructure.tenant_asgi import bind_tenant_routing
    from wingman.infrastructure.tenant_process import (
        clear_tenant_pidfile,
        read_tenant_process_pid,
        register_reload_handler,
        write_tenant_pidfile,
    )
    from wingman.infrastructure.tenants import TenantIndex, TenantRegistryError
    from wingman.webui import configure_tenant_index, register_ui

    registry_path = Path(args.tenant_registry).expanduser()
    try:
        index = TenantIndex.from_registry_path(registry_path)
    except TenantRegistryError as exc:
        print(f"ERROR: tenant registry {registry_path} is malformed: {exc}", file=sys.stderr)
        sys.exit(1)
    if len(index) == 0:
        print(
            f"WARNING: tenant registry {registry_path} lists no tenants yet — "
            "the server will start, but every request will 401.",
            file=sys.stderr,
        )
    existing = read_tenant_process_pid(registry_path)
    if existing is not None:
        print(
            f"ERROR: a shared multi-tenant server is already running (pid {existing}) for "
            f"{registry_path}. Stop it first, or point --tenant-registry at a different file.",
            file=sys.stderr,
        )
        sys.exit(1)
    _probe_bind(args.host, args.port)

    server.settings.host = args.host
    server.settings.port = args.port
    server.settings.streamable_http_path = f"{prefix}/mcp/{{token}}"
    extra_hosts = _extra_allowed_hosts(args.allowed_host)
    if args.host in _LOOPBACK_HOSTS:
        server.settings.transport_security = build_transport_security(extra_hosts)
    else:
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    if args.host not in _LOOPBACK_HOSTS:
        print(
            f"WARNING: binding {args.host} exposes every tenant's workspace to that network. "
            "The capability path is the only guard, per tenant. Prefer 127.0.0.1 plus a tunnel.",
            file=sys.stderr,
        )

    # Order matters: configure_tenant_index BEFORE register_ui, so the
    # per-tenant restart route is never mounted at all (RFC-041 withdrawal
    # — see webui.configure_tenant_index's own docstring).
    configure_tenant_index(index)
    register_ui(server, prefix=prefix)
    from wingman.admin import register_admin

    register_admin(server)

    app = server.streamable_http_app()
    bind_tenant_routing(app, f"{prefix}/mcp/{{token}}", index)
    register_reload_handler(index, registry_path)

    print(f"Shared multi-tenant server (RFC-048) — {len(index)} tenant(s) from {registry_path}")
    for tenant in sorted(index.tenants, key=lambda t: t.slug):
        print(f"  {tenant.slug}: wingman tenant url {tenant.slug}")
    print("Rotate a leaked token: wingman tenant rotate-token <slug>")
    print("Reload after editing the registry by hand: kill -HUP " + str(os.getpid()))
    print(f"Reach it from elsewhere via your own tunnel, e.g.: tailscale serve {args.port}")
    sys.stdout.flush()
    logging.getLogger("uvicorn.access").disabled = True

    write_tenant_pidfile(registry_path)
    atexit.register(clear_tenant_pidfile, registry_path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
