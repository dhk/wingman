"""Wingman command-line interface."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import click
import typer

if TYPE_CHECKING:
    from wingman.infrastructure.tenants import Tenant

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.answers import (
    find_answer,
    find_similar,
    remove_answer,
    render_answer,
    render_answer_listing,
    save_answer,
)
from wingman.application.assess import assess_job, fetch_job_posting
from wingman.application.backup import create_backup, restore_backup
from wingman.application.company_deep_dive import (
    render_findings,
    research_company_dossier,
    review_findings,
    save_company_dossier,
    spend_warning,
)
from wingman.application.company_feeds import (
    attach_company_feed,
    fetch_company_feeds,
    is_company_anchor,
    list_company_feeds,
    remove_company_feed,
)
from wingman.application.corpus import add_to_corpus, find_evidence
from wingman.application.demo import DEMO_REFERENCE_PERSON, seed_demo_watchlist
from wingman.application.dossier import build_company_dossier, delete_dossier_reports
from wingman.application.dossier_research import (
    dossier_truncation_warning,
    research_person_dossier,
    save_person_dossier,
)
from wingman.application.feature_request import (
    file_feature_request,
    get_feature_repo,
    render_preview,
    set_feature_repo,
    stamp_operator,
)
from wingman.application.focus import (
    OvernightReport,
    follow_company,
    latest_digest,
    overnight_run,
    render_follow_report,
    target_mark,
)
from wingman.application.gdrive_push import push_backup, push_digest
from wingman.application.ingest import IngestError, ingest_resume, ingest_resume_from_url
from wingman.application.linkedin import import_linkedin
from wingman.application.news import STALE_AFTER_DAYS, fetch_person_news
from wingman.application.outreach import build_outreach_brief, render_outreach_brief
from wingman.application.pack import build_application_pack
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
from wingman.application.pipeline import MisoReport, make_it_so
from wingman.application.pov import (
    CORPUS_PERSON_ID,
    build_company_pov,
    build_own_pov,
    build_pov_card,
    company_card_id,
    render_pov_card,
)
from wingman.application.profile_manage import (
    amend_item,
    clear_profile,
    correct_item,
    describe_amendment,
    describe_correction,
    preview_correction,
    rekind_item,
    remove_item,
    rename_item,
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
from wingman.application.search import render_search_report, search_workspace
from wingman.application.similarity import (
    companies_like,
    company_key,
    embed_missing,
    people_like,
    similar_companies,
    similar_people,
)
from wingman.application.telemetry_harvest import harvest_transcript
from wingman.application.telemetry_summary import (
    DEFAULT_GAP_MINUTES,
    DEFAULT_TOP_N,
    render_summary,
)
from wingman.application.telemetry_summary import (
    summarize as summarize_telemetry,
)
from wingman.application.triage import (
    mute_action,
    render_verdicts,
    snooze_action,
    unmute_action,
)
from wingman.domain.outreach import OutreachPurpose
from wingman.domain.person import Person
from wingman.infrastructure import doctor_deep
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.gdrive_auth import GDriveAuthError
from wingman.infrastructure.gdrive_auth import drive_auth as run_drive_auth
from wingman.infrastructure.host_config import (
    legacy_host_keys_path,
    migrate_legacy_host_file,
    wingman_env_path,
)
from wingman.infrastructure.keys import (
    KNOWN_KEYS,
    KeyLocation,
    KeyStoreError,
    KeyValidation,
    describe_key_locations,
    describe_tenant_key_locations,
    ensure_env,
    host_keys_path,
    key_status,
    resolve_key_sources,
    set_key,
    store_global_key,
    store_host_key,
    store_workspace_key,
    test_keys,
    unset_key,
    validate_keys,
    validate_tenant_keys,
)
from wingman.infrastructure.logs import configure_logging
from wingman.infrastructure.mcp_process import server_status, stop_server
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.infrastructure.telemetry import (
    count_events as telemetry_count,
)
from wingman.infrastructure.telemetry import (
    is_enabled as telemetry_is_enabled,
)
from wingman.infrastructure.telemetry import (
    iter_events as telemetry_iter,
)
from wingman.infrastructure.telemetry import (
    list_events as telemetry_list,
)
from wingman.infrastructure.telemetry import (
    record_event,
    redact_argv,
)
from wingman.infrastructure.telemetry import (
    set_enabled as telemetry_set_enabled,
)
from wingman.providers.base import CapabilityClass, ProviderError
from wingman.providers.embeddings import EmbeddingError
from wingman.providers.router import (
    DEFAULT_MODELS_TOML,
    ModelConfigError,
    get_embedding_provider,
    get_provider,
    metered_key,
)
from wingman.reporting.export import (
    export_career,
    export_company,
    export_person,
    materialize_person_export,
)
from wingman.version import wingman_version

app = typer.Typer(help="Wingman: local-first career intelligence.")
corpus_app = typer.Typer(help="Manage the corpus: your writing as citable evidence.")
app.add_typer(corpus_app, name="corpus")
people_app = typer.Typer(help="Watchlist of people and their public writing.")
app.add_typer(people_app, name="people")
company_app = typer.Typer(help="Companies, seen through the writing of their people and blogs.")
app.add_typer(company_app, name="company")
export_app = typer.Typer(help="Print-ready Letter-format exports (render with md-to-pdf).")
app.add_typer(export_app, name="export")
watchlist_app = typer.Typer(help="Named groups of people and companies to cycle through.")
app.add_typer(watchlist_app, name="watchlist")
keys_app = typer.Typer(help="API keys in the macOS Keychain — no plaintext files (RFC-019).")
app.add_typer(keys_app, name="keys")
examples_app = typer.Typer(help="Good and bad examples of the documents wingman writes (#438).")
app.add_typer(examples_app, name="examples")
telemetry_app = typer.Typer(help="Opt-in local usage journal — never leaves the machine (RFC-023).")
app.add_typer(telemetry_app, name="telemetry")
feature_app = typer.Typer(
    help="Feature requests: previewed, confirmed, filed to your repo via gh (RFC-025)."
)
app.add_typer(feature_app, name="feature")
profile_app = typer.Typer(
    help="Manage the career profile: list, remove, resolve conflicts, clear (RFC-027)."
)
app.add_typer(profile_app, name="profile")
answers_app = typer.Typer(
    help="The application answer bank: refined Q+A+context, reused across applications (RFC-030)."
)
app.add_typer(answers_app, name="answers")
actions_app = typer.Typer(
    help="Triage digest actions: mute/snooze what should stop rolling over (RFC-031)."
)
app.add_typer(actions_app, name="actions")
mcp_app = typer.Typer(
    help="The HTTP MCP server process: status and stop, via its pidfile (RFC-032)."
)
app.add_typer(mcp_app, name="mcp")
tenant_app = typer.Typer(
    help="Operator actions over the shared multi-tenant registry (RFC-048, #209/#210)."
)
app.add_typer(tenant_app, name="tenant")
criteria_app = typer.Typer(
    help="The job-criteria doc that scores new openings in the digest (RFC-035)."
)
app.add_typer(criteria_app, name="criteria")
objective_app = typer.Typer(
    help="Per-person relationship objective: goal, thesis, next move (RFC-037)."
)
app.add_typer(objective_app, name="objective")
heap_app = typer.Typer(help="Capture-first inbox for leads, heat-rated, sorted on demand (#113).")
app.add_typer(heap_app, name="heap")
commentary_app = typer.Typer(
    help="The assistant's readings of your material — attributed, and never evidence (#339)."
)
app.add_typer(commentary_app, name="commentary")


@app.command("setup-guide")
def setup_guide_cmd() -> None:
    """How to get started, shaped by what this workspace already has (#360)."""
    from wingman.application.completeness import compute_completeness
    from wingman.application.setup_guide import render_setup_guide
    from wingman.infrastructure.broadcast import acknowledge_delivery

    configure_logging()
    config = load_config()
    _require_workspace(config, "described")
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    typer.echo(render_setup_guide(config, report))
    # Printed, therefore delivered (issue #224).
    acknowledge_delivery(config, report.operator_action)


@app.command("rubrics")
def rubrics_cmd() -> None:
    """The external standards this build can measure you against (#436).

    A rubric is the ruler, never a measurement — nothing listed here is a
    claim about you. Each line says where its content came from, because a
    reconstruction must never be read as the organisation's own document.
    """
    from wingman.application.rubrics import load_all_rubrics

    configure_logging()
    for rubric in load_all_rubrics():
        typer.echo(
            f"{rubric.id}  v{rubric.version}  "
            f"[{rubric.provenance.tier.value} · {rubric.provenance.license}]"
        )
        typer.echo(f"  {rubric.title}")
        typer.echo(f"  dimensions: {', '.join(item.name for item in rubric.dimensions)}")
        typer.echo(f"  {rubric.provenance.disclaimer}")


@app.command("gap-map")
def gap_map_cmd(
    rubric_id: str = typer.Option(
        "", "--rubric", help="Rubric id. Optional while only one ships; see 'wingman rubrics'."
    ),
) -> None:
    """What your profile can and cannot answer against a rubric (#436).

    Reports, per dimension, the evidence this workspace holds — and which
    dimensions hold none. It does NOT place you on a rung: three of five
    dimensions having nothing to read is the normal case, and a level
    derived from that would be mostly invention (design doc Q2). No model
    is called; every match is a rubric's own declared signal, so a wrong
    one is visible in the output and fixable in the data file.
    """
    from wingman.application.gap_map import build_gap_map, render_gap_map
    from wingman.application.rubrics import RubricError, load_rubric, resolve_rubric_id

    configure_logging()
    config = load_config()
    _require_workspace(config, "read")
    try:
        rubric = load_rubric(resolve_rubric_id(rubric_id))
    except RubricError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    with Storage(config.db_path) as storage:
        report = build_gap_map(rubric, storage)
    typer.echo(render_gap_map(report))


motd_app = typer.Typer(
    help="The operator's box-wide message — one shared file, delivered once per account "
    "as a thing to do rather than a banner (#224)."
)
app.add_typer(motd_app, name="motd")


@motd_app.command("show")
def motd_show() -> None:
    """What the shared file currently says, and whether this account has seen it.

    Read-only and deliberately non-consuming: an operator checking their own
    message must not spend their own copy of it. This is also the answer to
    "why did nobody get it" — a malformed file degrades to silence
    everywhere else on purpose, and this is where that silence is visible.
    """
    from wingman.infrastructure.broadcast import (
        OPERATOR_MESSAGE_PATH,
        last_seen_id,
        permission_problem,
        read_operator_message,
    )

    configure_logging()
    config = load_config()
    typer.echo(f"Shared file: {OPERATOR_MESSAGE_PATH}")
    denied = permission_problem(OPERATOR_MESSAGE_PATH)
    if denied:
        typer.echo(denied, err=True)
        raise typer.Exit(code=1)
    if not OPERATOR_MESSAGE_PATH.exists():
        typer.echo("No message set — the file does not exist. Nobody is being told anything.")
        return
    message = read_operator_message()
    if message is None:
        typer.echo(
            "The file exists but could not be read as a message (unreadable, too large, "
            "not JSON, or missing 'id'/'action'). Every account is silently getting "
            "nothing — 'wingman motd set' writes a file this reader accepts."
        )
        return
    seen = last_seen_id(config)
    typer.echo(f"  id:     {message.id}")
    typer.echo(f"  action: {message.action}")
    typer.echo(f"  why:    {message.why or '(none given)'}")
    typer.echo(f"  how:    {message.how or '(none given)'}")
    typer.echo(
        f"This account ({config.data_dir}): "
        + ("already shown" if seen == message.id else "not yet shown")
    )


@motd_app.command("history")
def motd_history(
    limit: int = typer.Option(10, "--limit", help="How many to show, most recent first."),
) -> None:
    """What this account was TOLD — the messages already delivered to it.

    "What was today's message?" has no answer from the shared file: a
    message is shown once, and the operator replaces the file whenever they
    have something new to say. Each delivery keeps a copy in THIS
    workspace, so this reports what you were told rather than what is
    currently being said — `wingman motd show` is the other question.
    """
    from wingman.application.motd import recent_messages, render_messages
    from wingman.infrastructure.broadcast import pending_operator_message

    configure_logging()
    config = load_config()
    _require_workspace(config, "read")
    with Storage(config.db_path) as storage:
        delivered = recent_messages(storage, limit)
    typer.echo(render_messages(delivered, pending=pending_operator_message(config) is not None))


@motd_app.command("set")
def motd_set(
    action: str = typer.Argument(..., help="The imperative, e.g. 'Re-ingest your CV'."),
    message_id: str = typer.Option(
        "", "--id", help="Opaque id. Changing it re-delivers to everyone. Defaults to today."
    ),
    why: str = typer.Option("", "--why", help="What the request costs if ignored."),
    how: str = typer.Option("", "--how", help="The exact sentence that does it."),
    to: str = typer.Option(
        "all", "--to", help="A tenant slug, or 'all'. A slug is checked against the registry."
    ),
    path: Path | None = typer.Option(None, "--path", help="Write somewhere other than /etc."),
) -> None:
    """Broadcast one action to every account on this box, or to one tenant.

    Needs write access to the shared file (root, or a member of a group the
    operator has granted). Writing by hand is still fine — this exists so
    the JSON is guaranteed to be the shape the reader accepts, because a
    typo there costs every account its message with no error anywhere.
    """
    from datetime import UTC, datetime

    from wingman.infrastructure.broadcast import OperatorMessage, write_operator_message

    configure_logging()
    addressee = _addressee_or_exit(to)
    resolved_id = message_id.strip() or datetime.now(UTC).date().isoformat()
    message = OperatorMessage(
        id=resolved_id, action=action.strip(), why=why.strip(), how=how.strip(), to=addressee
    )
    if not message.action:
        typer.echo("The action is empty; nothing was written.")
        raise typer.Exit(code=1)
    try:
        written = write_operator_message(message, path)
    except OSError as exc:
        typer.echo(f"Could not write the shared message file: {exc}")
        raise typer.Exit(code=1) from exc
    who = "Every account" if addressee == "all" else f"Tenant {addressee!r}"
    typer.echo(f"Broadcast written to {written} (id {message.id}), addressed to {addressee}.")
    typer.echo(
        f"{who} sees it once, the next time they ask what to do next. "
        "Change --id to say something new; delete the file to stop saying anything."
    )


qotd_app = typer.Typer(
    help="The operator's question of the day — asked to one tenant or all, answered in "
    "the answerer's OWN workspace, never in shared space (#224, RFC-067)."
)
app.add_typer(qotd_app, name="qotd")


def _addressee_or_exit(to: str) -> str:
    """A validated addressee for a broadcast: a real slug, or 'all'.

    A mistyped slug reaches nobody, silently and forever — the same silence
    a malformed file degrades to, but with no `show` command able to tell
    it from a question nobody has answered yet. So the typo is caught at
    write time, where the operator is standing. A registry that cannot be
    read at all is a warning rather than a refusal: an operator setting up
    a box before the registry exists is a legitimate order of work.

    The registry named here is `tenant_registry_path()` — the box's
    canonical one — and it is PRINTED, because delivery resolves against
    that same file. A shared server started with an explicit
    `--tenant-registry` while `WINGMAN_TENANT_REGISTRY` is unset would
    address against a different file than it serves from, and seeing the
    path is how an operator notices before wondering why nobody answered.
    """
    from wingman.infrastructure.broadcast import ALL_TENANTS
    from wingman.infrastructure.tenants import load_registry, tenant_registry_path

    wanted = to.strip() or ALL_TENANTS
    if wanted.lower() == ALL_TENANTS:
        return ALL_TENANTS
    registry_path: Path | None = None
    try:
        registry_path = tenant_registry_path()
        slugs = [tenant.slug for tenant in load_registry(registry_path)]
    except Exception as exc:  # noqa: BLE001 — same total catch as broadcast.account_slug
        # Everything the registry read can throw, for the reason
        # `broadcast.account_slug` catches everything: a '~olduser' entry
        # whose home is gone raises RuntimeError, a non-UTF-8 file raises
        # UnicodeDecodeError, and a traceback here would be an operator
        # unable to send a message because somebody else's line is stale.
        typer.echo(f"warning: could not read the tenant registry ({registry_path}): {exc}")
        typer.echo(
            f"warning: writing it addressed to {wanted!r} unchecked — and note that "
            "delivery reads the same registry, so nobody will receive it until that "
            "file parses. 'wingman qotd show' on the addressee's account confirms."
        )
        return wanted
    if wanted not in slugs:
        known = ", ".join(sorted(slugs)) or "(none registered)"
        typer.echo(
            f"No tenant {wanted!r} in {registry_path} — it would reach nobody. Known: {known}.",
            err=True,
        )
        raise typer.Exit(code=1)
    # Deliberately phrased as a fact about THIS moment. Delivery re-reads
    # the same file every time somebody asks what to do next, so a slug
    # that is registered now can stop resolving later — a decommissioned
    # account elsewhere in the file is enough. Saying "it will be
    # delivered" would be a promise this read cannot make.
    typer.echo(f"{wanted!r} is in {registry_path} as of now; delivery re-reads that file.")
    return wanted


@qotd_app.command("set")
def qotd_set(
    question: str = typer.Argument(..., help="The question, in your own words."),
    to: str = typer.Option(
        "all", "--to", help="A tenant slug, or 'all'. A slug is checked against the registry."
    ),
    question_id: str = typer.Option(
        "", "--id", help="Opaque id. Changing it asks a NEW question. Defaults to today."
    ),
    why: str = typer.Option("", "--why", help="Why you are asking — optional, and shown."),
    path: Path | None = typer.Option(None, "--path", help="Write somewhere other than /etc."),
) -> None:
    """Ask one tenant, or everybody, one question.

    It arrives in their "what to do next" list, labelled as yours and
    carrying the sentence that says you will be able to read the answer.
    Each answer is stored in that person's own workspace — nothing here is
    group-writable, and no account can see another's answer. Read them back
    with 'wingman tenant answers'.

    The question stands until it is answered, unlike a message, which is
    shown once: change --id to ask something new, delete the file to stop
    asking.
    """
    from datetime import UTC, datetime

    from wingman.infrastructure.broadcast import OperatorQuestion, write_operator_question

    configure_logging()
    addressee = _addressee_or_exit(to)
    resolved_id = question_id.strip() or datetime.now(UTC).date().isoformat()
    asked = OperatorQuestion(
        id=resolved_id, question=question.strip(), why=why.strip(), to=addressee
    )
    if not asked.question:
        typer.echo("The question is empty; nothing was written.")
        raise typer.Exit(code=1)
    try:
        written = write_operator_question(asked, path)
    except OSError as exc:
        typer.echo(f"Could not write the shared question file: {exc}")
        raise typer.Exit(code=1) from exc
    who = "every account on this box" if addressee == "all" else f"tenant {addressee!r}"
    typer.echo(f"Question written to {written} (id {asked.id}), addressed to {who}.")
    typer.echo("Answers land in each person's own workspace — read them: wingman tenant answers")


def _why_no_slug() -> str:
    """Why this account has no slug — the two cases, told apart.

    `broadcast.account_slug` returns None for both "the registry could not
    be read" and "this account is not in it", because delivery must fail
    closed either way. But those are very different problems for a person
    to have: one is a broken file that has silenced slug-addressed
    broadcasts for EVERYBODY on the box, the other is an ordinary
    unregistered account. Reporting them with one sentence would make the
    first invisible, which is exactly what this command exists to prevent.
    """
    from wingman.infrastructure.broadcast import account_slug
    from wingman.infrastructure.tenants import load_registry, tenant_registry_path

    config = load_config()
    slug = account_slug(config)
    if slug:
        return f"its slug is {slug!r}."
    registry_path: Path | None = None
    try:
        registry_path = tenant_registry_path()
        load_registry(registry_path)
    except Exception as exc:  # noqa: BLE001 — reporting the failure IS the job here
        return (
            f"the tenant registry ({registry_path}) could not be read: {exc}. "
            "Until that is fixed, NOBODY on this box receives a slug-addressed "
            "message or question."
        )
    return f"it has no slug in the registry ({registry_path})."


@qotd_app.command("show")
def qotd_show() -> None:
    """What is being asked, of whom, and whether THIS account has answered.

    Read-only, and the one place a malformed question file is visible:
    everywhere else it degrades to silence on purpose, because this sits on
    the path of every account's status.
    """
    from wingman.infrastructure.broadcast import (
        ALL_TENANTS,
        OPERATOR_QUESTION_PATH,
        is_addressed_to,
        permission_problem,
        read_operator_question,
    )

    configure_logging()
    config = load_config()
    typer.echo(f"Shared file: {OPERATOR_QUESTION_PATH}")
    denied = permission_problem(OPERATOR_QUESTION_PATH)
    if denied:
        typer.echo(denied, err=True)
        raise typer.Exit(code=1)
    if not OPERATOR_QUESTION_PATH.exists():
        typer.echo("No question set — the file does not exist. Nobody is being asked anything.")
        return
    question = read_operator_question()
    if question is None:
        typer.echo(
            "The file exists but could not be read as a question (unreadable, too large, "
            "not JSON, or missing 'id'/'question'). Every account is silently getting "
            "nothing — 'wingman qotd set' writes a file this reader accepts."
        )
        return
    typer.echo(f"  id:       {question.id}")
    typer.echo(f"  to:       {question.to}")
    typer.echo(f"  question: {question.question}")
    typer.echo(f"  why:      {question.why or '(none given)'}")
    if not is_addressed_to(question.to, config):
        typer.echo(f"This account ({config.data_dir}) is not the addressee — {_why_no_slug()}")
        return
    if not config.db_path.exists():
        # Reading must never CREATE a workspace: Storage() would write a
        # full schema here and leave a half-initialized data dir that every
        # later db_path.exists() check reads as a real workspace.
        typer.echo(f"No workspace here yet ({config.db_path} missing) — run 'wingman init'.")
        return
    with Storage(config.db_path) as storage:
        answered = question.id in storage.answered_question_ids()
    scope = "everyone" if question.to.lower() == ALL_TENANTS else f"tenant {question.to!r}"
    typer.echo(f"Addressed to {scope}; this account: " + ("answered" if answered else "unanswered"))


@qotd_app.command("answer")
def qotd_answer(
    answer: str = typer.Argument(..., help="Your answer, in your own words — stored verbatim."),
    yes: bool = typer.Option(
        False, "--yes", help="Skip the confirmation prompt (it shows exactly what will be stored)."
    ),
) -> None:
    """Answer the current question, in your own words.

    Stored in YOUR workspace, in your own words, and nowhere else — but
    whoever runs this machine can read it, which is why the confirmation
    shows you exactly what will be stored before anything is (BP-06).
    """
    from wingman.application.ingest import IngestError
    from wingman.application.qotd import save_operator_answer
    from wingman.domain.operator_answer import OPERATOR_ANSWER_DISCLOSURE
    from wingman.infrastructure.broadcast import read_operator_question

    configure_logging()
    config = load_config()
    _require_workspace(config, "saved")
    question = read_operator_question()
    if question is not None:
        typer.echo(f"Question: {question.question}")
    typer.echo("This is exactly what will be stored, word for word:")
    typer.echo("")
    for line in answer.strip().splitlines() or [""]:
        typer.echo(f"  {line}")
    typer.echo("")
    typer.echo(OPERATOR_ANSWER_DISCLOSURE)
    if not yes and not typer.confirm("Save it?", default=False):
        typer.echo("Nothing was saved.")
        raise typer.Exit(code=1)
    try:
        with Storage(config.db_path) as storage:
            record = save_operator_answer(answer, config, storage)
    except IngestError as exc:
        typer.echo(f"qotd answer failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved [{record.answer_id[:8]}] against question {record.question_id}.")


@qotd_app.command("answers")
def qotd_answers() -> None:
    """Every answer THIS workspace has given, oldest first."""
    from wingman.application.qotd import list_operator_answers, render_answers

    configure_logging()
    config = load_config()
    _require_workspace(config, "read")
    with Storage(config.db_path) as storage:
        typer.echo(render_answers(list_operator_answers(storage)))


artifacts_app = typer.Typer(
    help="Where this workspace's rendered views were published — wingman records the url, "
    "it never publishes (#355)."
)
app.add_typer(artifacts_app, name="artifacts")


@artifacts_app.command("list")
def artifacts_list() -> None:
    """Every published view this workspace knows the url for."""
    from wingman.application.artifacts import render_artifacts

    configure_logging()
    config = load_config()
    with Storage(config.db_path) as storage:
        typer.echo(render_artifacts(storage.list_published_artifacts()))


@artifacts_app.command("show")
def artifacts_show(
    kind: str = typer.Argument(
        ..., help="values_radar, values_radar_work, completeness, or profile."
    ),
) -> None:
    """The recorded url for ONE view, or nothing — the question asked before
    publishing (#399).

    This is the call the update flow turns on. Publishing a second page for
    a kind that already has one leaves the first quietly wrong while it is
    still shared and still being read, which is the failure RFC-061 exists
    to prevent — so ask here first, and update THAT page.

    Exits non-zero when no url is recorded, so a refresh script can branch
    on it without parsing prose. 'artifacts list' prints every kind and is
    for reading, not for scripting one.
    """
    from wingman.application.artifacts import _valid_kind, published_artifact, render_artifacts

    configure_logging()
    config = load_config()
    with Storage(config.db_path) as storage:
        try:
            checked = _valid_kind(kind)
        except IngestError as exc:
            typer.echo(f"artifacts show failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        found = published_artifact(checked, storage)
    if found is None:
        typer.echo(
            f"No {checked!r} artifact recorded. Publishing one now creates a new page; "
            "record its url with 'wingman artifacts remember' afterwards so later "
            "runs can update it."
        )
        raise typer.Exit(code=1)
    typer.echo(render_artifacts([found]))


@artifacts_app.command("remember")
def artifacts_remember(
    kind: str = typer.Argument(
        ..., help="values_radar, values_radar_work, completeness, or profile."
    ),
    url: str = typer.Argument(..., help="The https:// url the client published it at."),
    title: str = typer.Option("", "--title", help="Optional label for listings."),
) -> None:
    """Record where a view was published, so later runs update THAT page.

    Wingman cannot publish or update it — a server has no route back into
    the client. Keeping the url is what stops the next refresh creating a
    second page and leaving this one quietly wrong.

    The record is stamped with what the view would be built from right now,
    so 'wingman artifacts stale' can later tell a page built from today's
    profile from one built from the profile before it (#355).
    """
    from wingman.application.artifacts import remember_artifact
    from wingman.application.freshness import current_fingerprint

    configure_logging()
    config = load_config()
    with Storage(config.db_path) as storage:
        try:
            artifact = remember_artifact(
                kind, url, storage, title=title, built_from=current_fingerprint(kind, storage)
            )
        except IngestError as exc:
            typer.echo(f"artifacts remember failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"Recorded {artifact.kind} -> {artifact.url}")


@artifacts_app.command("stale")
def artifacts_stale() -> None:
    """What no longer reflects current inputs OR current code, and the exact
    command that rebuilds each (#355).

    Two ways a derived artefact stops being true: new evidence arrived, or
    the code that shaped it changed. Only the first was ever answerable, and
    #340 is what the second costs — a scoring change inverted every axis
    evidenced by a condemnation, and the charts already on disk went on
    drawing the inverted shape with nothing on them to say so.

    Reports, never refuses. Every stale line carries its rebuild command.
    """
    from wingman.application.freshness import render_staleness, stale_artefacts

    configure_logging()
    config = load_config()
    with Storage(config.db_path) as storage:
        typer.echo(render_staleness(stale_artefacts(config, storage)))


@artifacts_app.command("forget")
def artifacts_forget(
    kind: str = typer.Argument(
        ..., help="values_radar, values_radar_work, completeness, or profile."
    ),
) -> None:
    """Drop a recorded url (the published page itself is untouched)."""
    from wingman.application.artifacts import forget_artifact

    configure_logging()
    config = load_config()
    with Storage(config.db_path) as storage:
        try:
            dropped = forget_artifact(kind, storage)
        except IngestError as exc:
            typer.echo(f"artifacts forget failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(
        f"Forgot the {kind!r} artifact url." if dropped else f"No {kind!r} artifact recorded."
    )


admin_app = typer.Typer(help="The cross-instance installations page for a shape-B box (#130).")
app.add_typer(admin_app, name="admin")
drive_app = typer.Typer(
    help="Google Drive push for backups + digests: per-account device-code OAuth (RFC-053, #205)."
)
app.add_typer(drive_app, name="drive")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wingman {wingman_version()}")
        raise typer.Exit()


# The environment as the process actually received it, snapshotted before
# '_bootstrap' hydrates os.environ from the Keychain/host file/workspace
# file — 'doctor' needs this to report which source a key really came
# from (#122); os.environ itself no longer carries that distinction once
# hydration has copied a non-environment value into it.
_pre_hydration_env: dict[str, str] = {}


@app.callback()
def _bootstrap(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the installed build (git-derived) and exit.",
    ),
) -> None:
    """Migrate the legacy host file if needed, then hydrate missing API keys
    (Keychain, host file, workspace file) before any command."""
    global _pre_hydration_env
    migration = migrate_legacy_host_file()
    if migration.migrated:
        typer.echo(f"one-time host config migration: {migration.detail}", err=True)
    _pre_hydration_env = dict(os.environ)
    ensure_env(data_dir=load_config().data_dir)


MIN_PYTHON = (3, 12)
_OUT_HELP = "Destination folder (default: the workspace's reports/pdf/)."


def _workspace_dirs(config: Config) -> list[Path]:
    return [config.data_dir, config.inbox_dir, config.reports_dir]


@app.command()
def init() -> None:
    """Initialize the local Wingman workspace (idempotent).

    The workspace lives in $WINGMAN_DATA_DIR if set, otherwise the platform
    user data directory.
    """
    configure_logging()
    config = load_config()
    created: list[Path] = []
    for directory in _workspace_dirs(config):
        if not directory.exists():
            try:
                directory.mkdir(parents=True)
            except OSError as exc:
                typer.echo(
                    f"init failed: could not create {directory} ({exc}). "
                    f"Directories already created were left in place: {created or 'none'}. "
                    f"Fix permissions or set {ENV_DATA_DIR} to a writable path, then re-run "
                    "'wingman init'.",
                    err=True,
                )
                raise typer.Exit(code=1) from exc
            created.append(directory)
    try:
        Storage(config.db_path).close()
    except sqlite3.Error as exc:
        typer.echo(
            f"init failed: could not initialize the database at {config.db_path} ({exc}). "
            "The workspace directories were preserved. Check that the disk is writable, "
            "then re-run 'wingman init'.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    if not config.models_config_path.exists():
        config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    typer.echo(f"Workspace ready at {config.data_dir} (from {config.data_dir_source}).")


def _doctor_deep(config: Config, continue_: bool, reset: bool, port: int) -> None:
    """One step of the guided diagnostic ladder (#137, #138) — see
    wingman.infrastructure.doctor_deep for the design rationale. Prints
    exactly one step's result and one next action, then stops; a bare
    rerun or '--continue' moves to the next step.
    """
    if reset:
        doctor_deep.reset_cursor(config)
        typer.echo("Ladder reset to step 1.\n")

    total = doctor_deep.total_steps()
    step = doctor_deep.read_cursor(config)
    result = doctor_deep.run_step(config, step, port)

    status = "ok" if result.ok else "FAIL"
    typer.echo(f"[{step}/{total}] {result.name}: {status}")
    typer.echo(result.summary)
    if result.next_action:
        typer.echo(f"\nnext: {result.next_action}")

    advance = result.ok or continue_
    if advance and step < total:
        doctor_deep.write_cursor(config, step + 1)
        typer.echo(f"\n(rerun 'wingman doctor --deep' to continue to step {step + 1}/{total})")
    elif advance and step == total:
        doctor_deep.write_cursor(config, step)
        typer.echo("\nAll diagnostic steps complete. ('wingman doctor --deep --reset' to redo.)")
    elif not result.ok:
        typer.echo(
            "\n(take the action above, then rerun 'wingman doctor --deep' to re-check this "
            "step — or pass --continue to move on without re-checking)"
        )
        raise typer.Exit(code=1)


@app.command()
def doctor(
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Walk the guided incident-diagnostic ladder (#137) instead of the fast checklist.",
    ),
    continue_: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="With --deep: advance past the current step regardless of its result "
        "(same as a bare rerun once you've acted on its instruction).",
    ),
    reset: bool = typer.Option(
        False, "--reset", help="With --deep: start the ladder over from step 1."
    ),
    port: int = typer.Option(
        8787, "--port", help="With --deep: port the HTTP MCP server should be bound to."
    ),
) -> None:
    """Check the local Wingman environment and report each result."""
    configure_logging()
    config = load_config()
    if deep:
        _doctor_deep(config, continue_=continue_, reset=reset, port=port)
        return
    failures = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        if not ok:
            failures += 1
        typer.echo(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")

    python_ok = sys.version_info[:2] >= MIN_PYTHON
    report(
        "python",
        python_ok,
        f"{sys.version_info.major}.{sys.version_info.minor} "
        f"(requires >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
    )
    report("version", True, wingman_version())
    report("config", True, f"data dir {config.data_dir} resolved from {config.data_dir_source}")
    if config.data_dir.exists():
        try:
            probe = config.data_dir / ".doctor-write-probe"
            probe.touch()
            probe.unlink()
            report("data dir", True, f"{config.data_dir} is writable")
        except OSError as exc:
            report("data dir", False, f"{config.data_dir} is not writable ({exc})")
    else:
        report("data dir", False, f"{config.data_dir} does not exist; run 'wingman init'")
    if config.db_path.exists():
        try:
            with Storage(config.db_path) as storage:
                storage.count_source_records()
            report("database", True, f"{config.db_path} is reachable")
        except sqlite3.Error as exc:
            report("database", False, f"{config.db_path} could not be opened ({exc})")
    else:
        report("database", False, f"{config.db_path} does not exist; run 'wingman init'")
    if config.models_config_path.exists():
        try:
            embedder = get_embedding_provider(config)
        except ModelConfigError as exc:
            report("embeddings", False, str(exc))
        else:
            key_note = ""
            if (
                embedder.provider_name == "voyage"
                and not os.environ.get("VOYAGE_API_KEY", "").strip()
            ):
                key_note = " — VOYAGE_API_KEY is not set, so 'wingman embed' will fail until it is"
            report(
                "embeddings",
                True,
                f"{embedder.provider_name}/{embedder.model}{key_note}",
            )

    legacy = legacy_host_keys_path()
    wingman_env = wingman_env_path()
    secrets_env = host_keys_path()
    if legacy.is_file():
        # '_bootstrap' already ran the migration once before this command
        # body started — reaching this point with the legacy file still
        # here means migration was skipped because the new files already
        # existed (RFC-046 never overwrites a layout a human may have set
        # up by hand), so the leftover old file needs a human's eyes, not
        # another silent auto-migration attempt.
        report(
            "host config",
            False,
            f"legacy {legacy} is still present even though the new layout "
            f"({wingman_env.name}, {secrets_env.name}) already exists at {wingman_env.parent} — "
            "verify the new files hold everything you need, then remove the old one by hand "
            "(never auto-deleted)",
        )
    else:
        report(
            "host config",
            True,
            f"{wingman_env.parent} ({wingman_env.name} for host settings, "
            f"{secrets_env.name} for secrets)",
        )

    # The operational surface, not this workspace (RFC-072, #212). Reported
    # as one line here — 'wingman host-check' is the full picture — because
    # doctor's job is "is anything wrong", and host drift is the class of
    # wrong that otherwise waits to be found in production.
    from wingman.infrastructure.host_manifest import check_host, host_context

    # Informational, never a failure. Most of the manifest is about an
    # OPERATOR's box — the systemd units behind the cross-account upgrade,
    # the shared process's wrapper — and somebody running wingman on their
    # own laptop will never have those, correctly. Failing doctor over them
    # would teach every ordinary user to ignore a red line. 'wingman
    # host-check' is where the non-zero exit lives, because that is the
    # surface a deploy script gates on.
    host_drift = check_host(host_context())
    host_detail = (
        "matches this build"
        if not host_drift
        else f"{len(host_drift)} difference(s) — run 'wingman host-check' for the detail"
    )
    typer.echo(f"[info] host manifest: {host_detail}")

    # The box, not this workspace (#403). Reported, never graded: most of
    # it describes an OPERATOR's surface — a registry, a shared process,
    # another account's install — and somebody on their own laptop has
    # none of those, correctly. A red line every ordinary user learns to
    # ignore is worse than no line, which is the call #212 already made.
    from wingman.infrastructure.host_report import host_facts

    for fact in host_facts(config, wingman_version()):
        marker = " ← worth a look" if fact.notable else ""
        typer.echo(f"[info] {fact.name}: {fact.detail}{marker}")

    for source in resolve_key_sources(_pre_hydration_env, data_dir=config.data_dir):
        detail = source.winning_source
        if source.shadowed_by:
            detail += (
                f" — also defined, with a DIFFERENT value, in: {', '.join(source.shadowed_by)}"
                f" (ignored per the resolution order; remove the stale copy or make them match)"
            )
        report(f"key {source.short_name}", not source.shadowed_by, detail)

    if failures:
        typer.echo(f"{failures} check(s) failed.", err=True)
        raise typer.Exit(code=1)
    typer.echo("All checks passed.")


@app.command("host-check")
def host_check(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the auto-fixable drift (reversible file writes in your own config "
        "directory). Never touches systemd, sudo, or another account.",
    ),
) -> None:
    """Compare this host's operational surface against what this build expects
    (RFC-072, #212).

    Host-level state — the RFC-046 config layout, the systemd units behind
    the shared process and the cross-account upgrade, the 'wg' wrapper an
    operator types — has shipped as one-off migrations and prose somebody had
    to notice. Drift was therefore found when something broke (#197, #198),
    usually by somebody else. This answers the question directly.

    Two tiers, and the line between them is deliberate. Reversible file
    writes inside your own config directory apply with --apply, printing what
    moved — the precedent 'migrate_legacy_host_file' already set. Anything
    touching systemd, sudo, or another account is REPORTED with the command
    that fixes it and never applied, because those are external actions on
    shared state and AGENTS.md requires a human before one.

    Exits non-zero when there is drift, so a deploy script can gate on it.
    """
    configure_logging()
    from wingman.infrastructure.host_manifest import (
        apply_auto,
        check_host,
        host_context,
        render_drift,
    )

    context = host_context()
    drifts = check_host(context)
    typer.echo(render_drift(drifts))
    if apply and drifts:
        changes = apply_auto(drifts, context)
        if changes:
            typer.echo("Applied:")
            for change in changes:
                typer.echo(f"  {change}")
            drifts = check_host(context)
            typer.echo("")
            typer.echo(render_drift(drifts))
        else:
            typer.echo("Nothing was auto-fixable — every difference above needs a human.")
    if drifts:
        raise typer.Exit(code=1)


@app.command()
def demo() -> None:
    """A guided tour on real data, in an isolated demo workspace.

    Runs entirely inside its own workspace (a 'demo' folder next to your
    real one) — your workspace, corpus, and watchlist are never read,
    written, or sent anywhere. Network behavior, stated exactly: public
    Substack feeds are fetched (RFC-009), and if a Voyage key is configured
    those fetched public posts are sent to the embeddings provider
    (RFC-010); without a key, similarity runs on the local 'hashed'
    provider and nothing leaves the machine. Re-running is safe; delete
    the demo folder to remove every trace.
    """
    configure_logging()
    real_config = load_config()
    config = Config(
        data_dir=real_config.data_dir / "demo",
        data_dir_source=f"demo workspace inside {real_config.data_dir}",
    )
    for directory in _workspace_dirs(config):
        directory.mkdir(parents=True, exist_ok=True)
    if not config.models_config_path.exists():
        config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    typer.echo("\n=== Wingman demo: a real watchlist, real public writing ===")
    typer.echo(f"    (isolated workspace: {config.data_dir} — your data is untouched)\n")

    with Storage(config.db_path) as storage:
        seed, _ = seed_demo_watchlist(storage)
        typer.echo(
            f"[1/4] Watchlist seeded: {seed.added} publications added, "
            f"{seed.already_present} already present."
        )

        typer.echo("[2/4] Fetching public feeds (explicit read-only HTTPS, RFC-009)...")
        fetched = 0
        failed = 0
        new_posts = 0
        for person in storage.list_people():
            if not person.substack_url:
                continue
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                failed += 1
                typer.echo(f"      {person.name}: {exc}", err=True)
                continue
            fetched += 1
            new_posts += report.added
        typer.echo(
            f"      {fetched} feeds fetched, {new_posts} new posts archived"
            + (f", {failed} feeds unreachable (shown above)" if failed else "")
        )
        if fetched == 0:
            typer.echo(
                "\nNo feeds could be fetched — the demo needs network access to public "
                "Substack feeds. Everything already stored remains usable.",
                err=True,
            )
            raise typer.Exit(code=1)

        typer.echo('[3/4] Evidence search — who has said what about "AI":')
        try:
            hits = find_people_evidence("AI", storage, limit=3)
        except CorpusSearchError as exc:
            typer.echo(f"      search failed: {exc}", err=True)
            hits = []
        for hit in hits:
            typer.echo(f"      {hit.person_name} — {hit.document.title}")
            typer.echo(f"        {hit.snippet}")
        if not hits:
            typer.echo("      (no matches in the fetched posts — try 'wingman people evidence')")

        typer.echo("[4/4] Similarity — embedding the fetched writing...")
        try:
            embedder = get_embedding_provider(config)
        except ModelConfigError as exc:
            typer.echo(f"      embed skipped: {exc}", err=True)
            embedder = None
        if (
            embedder is not None
            and embedder.provider_name == "voyage"
            and not os.environ.get("VOYAGE_API_KEY", "").strip()
        ):
            from wingman.providers.embeddings import HashedEmbeddingProvider

            typer.echo(
                "      VOYAGE_API_KEY is not set — using the local 'hashed' provider "
                "(keyword-level, no network). Set the key and re-run the demo for "
                "real semantic quality."
            )
            embedder = HashedEmbeddingProvider()
        elif embedder is not None and embedder.provider_name == "voyage":
            typer.echo(
                f"      Sending the fetched public posts to {embedder.provider_name}/"
                f"{embedder.model} for embedding (RFC-010 — demo posts only, "
                "never your own data)."
            )
        if embedder is not None:
            try:
                embed_report = embed_missing(storage, embedder)
                typer.echo(
                    f"      embedded {embed_report.external_embedded} posts "
                    f"({embed_report.provider}/{embed_report.model})"
                )
                similar = similar_people(storage, name=DEMO_REFERENCE_PERSON, limit=5)
                typer.echo(f"      Closest to {similar.reference}:")
                for number, entry in enumerate(similar.people, start=1):
                    typer.echo(f"      {number}. {entry.name}  score {entry.score:.3f}")
            except (IngestError, EmbeddingError) as exc:
                typer.echo(f"      similarity skipped: {exc}", err=True)

    typer.echo(
        "\n=== Demo complete. Make it yours ===\n"
        "  wingman init                                   # your real workspace\n"
        "  wingman ingest-linkedin <your-export.zip>      # your cited profile\n"
        "  wingman people import-connections <export.zip> # your network\n"
        "  wingman corpus add <your-writing>              # your evidence\n"
        "  wingman people add / fetch / similar           # your watchlist\n"
        f"Full guide: docs/WALKTHROUGH.md. The demo lived in {config.data_dir} — "
        "delete that folder to remove every trace."
    )


@app.command()
def sync() -> None:
    """Fetch every watched source and embed whatever is new — one command.

    The maintenance loop as a single explicit invocation (RFC-009 holds:
    running this command is the consent). Per-person failures are reported
    and skipped, never silently hidden. Embedding degrades per RFC-010: with
    no VOYAGE_API_KEY, either configure the local 'hashed' provider in
    models.toml or expect the embed step to fail visibly (exit 1) while the
    fetched posts are kept.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "synced")
    exit_code = 0
    with Storage(config.db_path) as storage:
        targets = [person for person in storage.list_people() if person.sources]
        if not targets:
            typer.echo("No people have sources configured — nothing to sync.")
            return
        fetched = 0
        failed = 0
        new_posts = 0
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                failed += 1
                typer.echo(f"  {person.name}: fetch failed: {exc}", err=True)
                continue
            fetched += 1
            new_posts += report.added
            if report.added:
                typer.echo(f"  {person.name}: +{report.added} new")
        typer.echo(
            f"Fetched {fetched}/{len(targets)} people  new posts: {new_posts}"
            + (f"  failures: {failed}" if failed else "")
        )
        if fetched == 0:
            typer.echo("Every fetch failed; embedding was not attempted.", err=True)
            raise typer.Exit(code=1)
        try:
            provider = get_embedding_provider(config)
            embed_report = embed_missing(storage, provider)
            typer.echo(
                f"Embedded {embed_report.corpus_embedded + embed_report.external_embedded} "
                f"new documents ({embed_report.provider}/{embed_report.model})"
            )
        except (ModelConfigError, EmbeddingError) as exc:
            typer.echo(
                f"Embedding skipped: {exc}\nFetched posts were kept; keyword search works. "
                "Fix the embeddings configuration and re-run 'wingman sync' or 'wingman embed'.",
                err=True,
            )
            exit_code = 1
    if exit_code:
        raise typer.Exit(code=exit_code)
    typer.echo("Sync complete.")


@app.command()
def status() -> None:
    """Show the current Wingman workspace status."""
    configure_logging()
    config = load_config()
    typer.echo(f"Wingman: {wingman_version()}")
    typer.echo(f"Workspace: {config.data_dir} (from {config.data_dir_source})")
    if not config.db_path.exists():
        typer.echo("Database: not initialized — run 'wingman init'.")
        return
    with Storage(config.db_path) as storage:
        sources = storage.count_source_records()
        items = storage.count_profile_items()
        opportunities = storage.count_opportunities()
        documents = storage.count_corpus_documents()
        people = storage.count_people()
        external = storage.count_external_documents()
        commentary_entries = storage.count_commentary_entries()
    typer.echo(f"Database: {config.db_path}")
    typer.echo(f"Source records: {sources}")
    typer.echo(f"Profile items: {items}")
    typer.echo(f"Opportunities: {opportunities}")
    typer.echo(f"Corpus documents: {documents}")
    typer.echo(f"People: {people}")
    typer.echo(f"External documents: {external}")
    typer.echo(
        f"Commentary entries: {commentary_entries} (the assistant's readings — never evidence)"
    )
    # Said only when it is missing — the state where every count above looks
    # healthy while every model-backed command fails (#514). The MCP 'status'
    # tool says the same thing in the same case, through the same predicate;
    # the remedy differs because whoever reads this has a shell.
    if metered_key(config, "anthropic") is None:
        typer.echo(
            "Model calls: UNAVAILABLE — no model key for this workspace, so values, "
            "assessments, briefs and every other model-backed step will fail. Everything "
            "above is read from local data and is unaffected. 'wingman keys where' names "
            "every tier and which copy wins."
        )


def _human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


@app.command()
def backup(
    dest: Path | None = typer.Argument(
        None,
        help=(
            "Destination folder for the archive (default: the workspace's backups/). "
            "Point this at a synced folder — iCloud/Dropbox sync a closed tarball "
            "safely, unlike the live database."
        ),
    ),
    keep: int = typer.Option(
        10, "--keep", help="Backups to retain at the destination (0 keeps all)."
    ),
    drive: bool = typer.Option(
        True,
        "--drive/--no-drive",
        help="Push the finished tarball to Drive once authorized ('wingman drive auth'). "
        "A no-op before authorization; a push failure never affects the local backup.",
    ),
) -> None:
    """Snapshot the workspace into a dated tarball: database, models.toml, inbox, reports."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "backed up")
    try:
        report = create_backup(config, dest=dest, keep=keep)
    except IngestError as exc:
        typer.echo(f"Backup failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Backup written: {report.path}")
    typer.echo(f"  {report.files} files, {_human_size(report.size_bytes)}")
    for name in report.pruned:
        typer.echo(f"  pruned old backup: {name}")
    typer.echo(f'Restore with: wingman restore "{report.path}"')
    if drive:
        typer.echo(push_backup(Path(report.path)).detail)


@app.command()
def restore(
    archive: Path = typer.Argument(
        ..., help="A wingman-backup-*.tar.gz written by 'wingman backup'."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite the existing workspace database with the backup."
    ),
) -> None:
    """Restore a workspace from a backup tarball (refuses to overwrite without --force)."""
    configure_logging()
    config = load_config()
    try:
        report = restore_backup(archive, config, force=force)
    except IngestError as exc:
        typer.echo(f"Restore failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Restored {report.files} files from {report.archive} into {config.data_dir}")


@app.command("ingest-transcript")
def ingest_transcript(
    path: Path | None = typer.Argument(
        None, help="A Gemini call transcript: Markdown, plain text, or an exported PDF."
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Fetch a link-accessible Google Doc instead (the tabbed Notes + Transcript export).",
    ),
    note_for: str = typer.Option(
        "",
        "--note-for",
        help="After reviewing the preview: file this call as an interaction note on a "
        "watched person. Only ever the person you name — never inferred from the export.",
    ),
) -> None:
    """Read a Google Meet / Gemini call transcript and show what is in it (#134).

    Previews by default and writes nothing. Three things about the real
    export shape the output.

    Only the Transcript tab is anybody's words. The Summary, Next steps and
    Details sections are Gemini's paraphrase — Google's own footer says to
    check them — so they are carried as context and never quoted as
    evidence that a human said something.

    Speaker labels are generated and do get names wrong. In the first real
    export used to build this, the invitee 'Chuck Patel
    <cpatel@champsinc.com>' is transcribed throughout as 'Chuck Norris'. So
    attribution comes from the calendar Invited line, and any speaker that
    does not match an invitee is flagged for you to confirm rather than
    reconciled automatically.

    A call is often BOTH an interview and a company conversation, so this
    classifies nothing. It shows you what it found; --note-for files the
    call against the person you name.
    """
    configure_logging()
    from wingman.application.transcript import (
        match_speakers,
        parse_transcript,
        render_transcript,
    )

    config = load_config()
    _require_workspace(config, "read")
    if path is None and not url:
        typer.echo("Give a transcript file, or --url for a link-accessible Google Doc.", err=True)
        raise typer.Exit(code=2)

    try:
        from wingman.application.resume_formats import extract_resume_text

        if url:
            # The same one explicit, https-only fetch resume ingestion uses
            # (RFC-009): the raw bytes are archived to the inbox first, so
            # the original artifact is the provenance record.
            from wingman.application.resume_formats import fetch_resume_bytes

            name, raw = fetch_resume_bytes(url)
            config.inbox_dir.mkdir(parents=True, exist_ok=True)
            archived = config.inbox_dir / name
            archived.write_bytes(raw)
            text = extract_resume_text(archived)
        else:
            text = extract_resume_text(path)  # type: ignore[arg-type]
        transcript = parse_transcript(text)
    except IngestError as exc:
        typer.echo(f"transcript read failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    matches = match_speakers(transcript)
    typer.echo(render_transcript(transcript, matches))

    if not note_for:
        typer.echo(
            "Nothing was written. Re-run with --note-for '<person>' to file this call "
            "as an interaction note on somebody you name."
        )
        return

    from wingman.application.relationship import log_interaction

    summary = f"Call: {transcript.title or 'transcript'}"
    if transcript.date:
        summary += f" ({transcript.date})"
    summary += f". {len(transcript.turns)} turns transcribed by Gemini."
    if transcript.next_steps:
        summary += f" Next steps, as the export recorded them: {transcript.next_steps}"
    try:
        with Storage(config.db_path) as storage:
            log_interaction(note_for, summary, config, storage)
    except IngestError as exc:
        typer.echo(f"note failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Filed against {note_for}: {summary}")


@app.command()
def ingest(
    resume: Path | None = typer.Argument(
        None, help="Path to a resume: Markdown, plain text, PDF, DOCX, or LaTeX."
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Fetch the resume from a link-accessible Google Docs or Drive URL instead.",
    ),
) -> None:
    """Ingest a resume into the canonical profile and write career.json / career.md.

    Accepts a local file (.md, .txt, .pdf, .docx, .tex) or, with --url, a
    Google Docs/Drive link — one explicit HTTPS fetch (RFC-009), archived to
    the inbox before extraction. LaTeX is flattened to its visible words,
    not typeset.
    """
    configure_logging()
    config = load_config()
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was ingested; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    if (resume is None) == (url is None):
        typer.echo("Provide exactly one of: a resume path, or --url.", err=True)
        raise typer.Exit(code=1)
    try:
        provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
        with Storage(config.db_path) as storage:
            if resume is not None:
                report = ingest_resume(resume, config, storage, provider)
            else:
                assert url is not None
                report = ingest_resume_from_url(url, config, storage, provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"ingest failed: {exc}. The source record was preserved; the profile was not "
            "changed. Re-run 'wingman ingest' to retry the extraction.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Source record: {report.source_record_id}" + (" (reused)" if report.source_reused else "")
    )
    typer.echo(
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Updated: {report.updated}  Retired: {report.retired}  "
        f"Rejected: {len(report.rejected)}"
    )
    for rejected in report.rejected:
        typer.echo(f"  rejected {rejected.name!r}: {rejected.reason}")
    typer.echo(f"Model: {report.provider}/{report.model} (prompt {report.prompt_version})")
    typer.echo(f"Wrote {report.career_json_path}")
    typer.echo(f"Wrote {report.career_md_path}")


@app.command()
def assess(
    job: Path | None = typer.Argument(
        None, help="Path to a job description in Markdown or plain text."
    ),
    url: str = typer.Option(
        "",
        "--url",
        help="Fetch the posting straight from its https:// URL instead — one explicit "
        "GET (RFC-009), archived to the inbox as the provenance record (RFC-024).",
    ),
) -> None:
    """Assess a job description against the profile and write a cited fit brief."""
    configure_logging()
    config = load_config()
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was assessed; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    if (job is None) == (not url):
        typer.echo("assess needs exactly one of: a file path, or --url.", err=True)
        raise typer.Exit(code=1)
    try:
        if url:
            job = fetch_job_posting(url, config)
            typer.echo(f"Fetched posting → {job}")
        assert job is not None
        extract_provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
        assess_provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        with Storage(config.db_path) as storage:
            report = assess_job(job, config, storage, extract_provider, assess_provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"assess failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"assess failed: {exc}. The source record was preserved; no opportunity was "
            "created or changed. Re-run 'wingman assess' to retry.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"Opportunity: {report.title} ({report.opportunity_id})")
    verdicts = "  ".join(f"{name}: {count}" for name, count in sorted(report.verdicts.items()))
    typer.echo(f"Requirements: {report.requirements}  {verdicts}")
    for rejected in report.rejected_requirements:
        typer.echo(f"  rejected requirement {rejected.name!r}: {rejected.reason}")
    for note in report.downgraded:
        typer.echo(f"  validation: {note}")
    typer.echo(f"Next action: {report.next_action}")
    typer.echo(f"Wrote {report.brief_json_path}")
    typer.echo(f"Wrote {report.brief_md_path}")


@app.command("ingest-linkedin")
def ingest_linkedin(
    export: Path = typer.Argument(..., help="Path to a LinkedIn data-export zip."),
) -> None:
    """Import positions, skills, and recommendations from a LinkedIn export."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "imported")
    try:
        with Storage(config.db_path) as storage:
            report = import_linkedin(export, config, storage)
    except IngestError as exc:
        typer.echo(f"ingest-linkedin failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Found: {report.positions} positions, {report.skills} skills, "
        f"{report.recommendations} recommendations"
    )
    counts = report.counts
    typer.echo(
        f"Accepted: {counts.accepted}  Duplicates skipped: {counts.skipped_duplicates}  "
        f"Evidence merged: {counts.evidence_merged}  Conflicts: {counts.conflicts}  "
        f"Updated: {counts.updated}  Retired: {counts.retired}"
    )
    typer.echo(f"Wrote {report.career_json_path}")
    typer.echo(f"Wrote {report.career_md_path}")


def _require_workspace(config: Config, action: str) -> None:
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was {action}; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)


_PICKER_LIMIT = 5


def _resolve_person(storage: Storage, name: str, action: str) -> Person:
    """Resolve a possibly-partial name to exactly one person.

    Exact match wins; a unique partial match is used with a visible note; a
    small ambiguity (2-5 people) becomes a numbered picker; anything else
    fails with guidance. Deterministic lookups stay deterministic — the
    picker only appears when the input was genuinely ambiguous.
    """
    candidates = match_people(storage, name)
    if len(candidates) == 1:
        person = candidates[0]
        if person.name_key != " ".join(name.lower().split()):
            typer.echo(f"→ {person.name}")
        return person
    if 2 <= len(candidates) <= _PICKER_LIMIT:
        typer.echo(f"{name!r} matches {len(candidates)} people:")
        for number, person in enumerate(candidates, start=1):
            where = ", ".join(part for part in (person.position, person.company) if part)
            detail = f"  ({where})" if where else ""
            typer.echo(f"{number}. {person.name}{detail}")
        try:
            choice = int(typer.prompt("Which one? (number, 0 cancels)", type=int, default=0))
        except click.exceptions.Abort:
            choice = 0
        if 1 <= choice <= len(candidates):
            return candidates[choice - 1]
        typer.echo(f"Nothing was {action}.", err=True)
        raise typer.Exit(code=1)
    if candidates:
        typer.echo(
            f"{name!r} matches {len(candidates)} people — be more specific; "
            "see 'wingman people list'.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"No person named {name!r}; see 'wingman people list'.", err=True)
    raise typer.Exit(code=1)


@corpus_app.command("add")
def corpus_add(
    path: Path = typer.Argument(
        ..., help="A file, a directory, or a zip export (e.g. a Substack export)."
    ),
    source_type: str = typer.Option(
        "writing",
        "--source-type",
        help="Provenance label, e.g. substack_post, github_readme, linkedin_export.",
    ),
) -> None:
    """Add writing to the corpus: Markdown, plain text, HTML, or a zip of them."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "added")
    try:
        with Storage(config.db_path) as storage:
            report = add_to_corpus(path, source_type, config, storage)
    except IngestError as exc:
        typer.echo(f"corpus add failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Added: {report.added}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Unsupported: {len(report.skipped_unsupported)}  Failures: {len(report.failures)}"
    )
    for title in report.titles:
        typer.echo(f"  + {title}")
    for failure in report.failures:
        typer.echo(f"  failed {failure.name!r}: {failure.reason}", err=True)


@corpus_app.command("list")
def corpus_list() -> None:
    """List corpus documents."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        documents = storage.list_corpus_documents()
    if not documents:
        typer.echo("Corpus is empty — add writing with 'wingman corpus add <path>'.")
        return
    for document in documents:
        when = document.published_at.date().isoformat() if document.published_at else "unknown"
        typer.echo(
            f"{document.doc_id}  [{document.source_type}]  {when}  {document.title}"
            f"  ({document.word_count} words)"
        )


@people_app.command("add")
def people_add(
    name: str = typer.Argument(..., help="The person's name."),
    substack: str | None = typer.Option(
        None,
        "--substack",
        help="Their Substack URL, e.g. https://example.substack.com — verified against "
        "<url>/feed before it's stored. For any other blog, use 'people add-feed' instead.",
    ),
    company: str | None = typer.Option(None, "--company", help="Where they work."),
    position: str | None = typer.Option(None, "--position", help="What they do."),
    linkedin: str | None = typer.Option(None, "--linkedin", help="Their LinkedIn profile URL."),
    email: str | None = typer.Option(
        None, "--email", help="Their email (manual entry only — imports never read emails)."
    ),
) -> None:
    """Add a person to the watchlist (or update them if already known)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "added")
    try:
        with Storage(config.db_path) as storage:
            person, created = add_person(
                name,
                storage,
                substack_url=substack,
                company=company,
                position=position,
                linkedin_url=linkedin,
                email=email,
            )
    except IngestError as exc:
        typer.echo(f"people add failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    verb = "Added" if created else "Updated"
    feed = f"  substack: {person.substack_url}" if person.substack_url else ""
    typer.echo(f"{verb} {person.name} ({person.person_id}){feed}")


@people_app.command("rename")
def people_rename(
    current: str = typer.Argument(..., help="The person's current name (or a unique partial)."),
    new_name: str = typer.Argument(..., help="Their corrected name."),
) -> None:
    """Rename a watchlist person in place. Fails if new_name is already someone else's —
    use 'fix' if you want to merge into them instead."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "renamed")
    try:
        with Storage(config.db_path) as storage:
            person = rename_person(current, new_name, storage)
    except IngestError as exc:
        typer.echo(f"people rename failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Renamed to {person.name} ({person.person_id})")


@people_app.command("fix")
def people_fix(
    current: str = typer.Argument(..., help="The person's current (wrong) name."),
    correct_name: str = typer.Argument(..., help="Their correct name."),
) -> None:
    """Correct a person's name. If correct_name already belongs to someone else, current
    is merged into them (blank fields filled in, documents/POV card/outreach brief moved)
    rather than left as a duplicate — e.g. 'Ed Wong is actually Edmund Wong'."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "fixed")
    try:
        with Storage(config.db_path) as storage:
            person, merged = fix_person(current, correct_name, storage)
    except IngestError as exc:
        typer.echo(f"people fix failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    verb = "Merged into" if merged else "Renamed to"
    typer.echo(f"{verb} {person.name} ({person.person_id})")


@people_app.command("delete")
def people_delete(
    name: str = typer.Argument(..., help="Person to delete (or a unique partial name)."),
) -> None:
    """Delete a watchlist person and everything keyed to them — documents, POV card,
    news, outreach brief, watchlist memberships. Not reversible."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "deleted")
    try:
        with Storage(config.db_path) as storage:
            person = delete_person(name, storage)
    except IngestError as exc:
        typer.echo(f"people delete failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Deleted {person.name} ({person.person_id})")


@people_app.command("list")
def people_list(
    watched: bool = typer.Option(
        False, "--watched", help="Only people with at least one source configured."
    ),
) -> None:
    """List people on the watchlist."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        people = storage.list_people()
    people = [person for person in people if not is_company_anchor(person)]
    if watched:
        people = [person for person in people if person.sources]
    if not people:
        typer.echo(
            "No people yet — add one with 'wingman people add' or seed from a LinkedIn "
            "export with 'wingman people import-connections'."
        )
        return
    for person in people:
        where = ", ".join(part for part in (person.position, person.company) if part)
        sources = person.sources
        feed = f"  [{', '.join(source.url for source in sources)}]" if sources else ""
        detail = f"  ({where})" if where else ""
        linkedin = f"  {person.linkedin_url}" if person.linkedin_url else ""
        typer.echo(f"{person.name}{detail}{linkedin}{feed}")
    typer.echo(f"{len(people)} people.")


@people_app.command("add-feed")
def people_add_feed(
    name: str = typer.Argument(..., help="Person on the watchlist to attach the source to."),
    url: str = typer.Argument(..., help="A feed URL or a blog homepage/index page (https)."),
    org: str | None = typer.Option(
        None,
        "--org",
        help="Attribute posts to this organization (e.g. a company blog) instead of the person.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Attach without the confirmation prompt."),
) -> None:
    """Attach any public feed to a person: RSS/Atom, Medium, or a feed-less blog index.

    Paste a feed URL or a homepage — Wingman fetches it once, autodiscovers
    the feed (or offers the page as an index source when no feed exists), and
    attaches only after you confirm (RFC-011): discovery can succeed on the
    wrong person's feed, so you get the final say.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "attached")
    from wingman.domain.person import FeedAttribution, FeedKind, FeedSource

    attribution = FeedAttribution.ORGANIZATION if org else FeedAttribution.PERSON
    with Storage(config.db_path) as storage:
        person = _resolve_person(storage, name, "attached")
        try:
            discovery = discover_feed(url)
        except IngestError as exc:
            typer.echo(f"add-feed failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if discovery.feed_url:
            typer.echo(f"Found feed: {discovery.feed_url}  (titled {discovery.feed_title!r})")
            if not yes and not typer.confirm(f"Attach this feed to {person.name}?"):
                typer.echo("Nothing was attached.")
                raise typer.Exit(code=0)
            source = FeedSource(
                url=discovery.feed_url,
                kind=FeedKind.RSS,
                attribution=attribution,
                org_name=org,
            )
        else:
            typer.echo(
                f"No feed found at or near {url} (probed {len(discovery.probed)} URLs). "
                "The page can be watched as an index source instead: on each fetch, "
                "Wingman reads this one page and ingests new posts linked under it."
            )
            if not yes and not typer.confirm(f"Watch {url} as an index page for {person.name}?"):
                typer.echo("Nothing was attached.")
                raise typer.Exit(code=0)
            source = FeedSource(
                url=url.rstrip("/"),
                kind=FeedKind.INDEX_PAGE,
                attribution=attribution,
                org_name=org,
            )
        try:
            attach_feed(person, source, storage)
        except IngestError as exc:
            typer.echo(f"add-feed failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    label = f" (attributed to {org})" if org else ""
    typer.echo(f"Attached {source.kind.value} source to {person.name}: {source.url}{label}")


@people_app.command("import-connections")
def people_import_connections(
    export: Path = typer.Argument(..., help="Path to a LinkedIn data-export zip."),
) -> None:
    """Seed Person records from Connections.csv (names and roles only — never emails)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "seeded")
    try:
        with Storage(config.db_path) as storage:
            report = seed_from_connections(export, storage)
    except IngestError as exc:
        typer.echo(f"import-connections failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Created: {report.created}  Already known: {report.skipped_existing}  "
        f"Incomplete rows skipped: {report.skipped_incomplete}"
    )


@people_app.command("fetch")
def people_fetch(
    name: str | None = typer.Argument(None, help="Person to fetch; omit with --all."),
    fetch_all: bool = typer.Option(
        False, "--all", help="Fetch every person with at least one source configured."
    ),
) -> None:
    """Fetch new posts from a person's public sources (explicit, read-only)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "fetched")
    with Storage(config.db_path) as storage:
        if fetch_all:
            targets = [person for person in storage.list_people() if person.sources]
            if not targets:
                typer.echo("No people have any sources configured. Nothing was fetched.")
                return
        elif name is None:
            # Offer the most recently added person with sources before failing.
            latest = max(
                (person for person in storage.list_people() if person.sources),
                key=lambda person: person.created_at,
                default=None,
            )
            confirmed = False
            if latest is not None:
                try:
                    confirmed = typer.confirm(f"Fetch {latest.name} (added most recently)?")
                except click.exceptions.Abort:
                    confirmed = False
            if latest is None or not confirmed:
                typer.echo("Name a person or pass --all. Nothing was fetched.", err=True)
                raise typer.Exit(code=1)
            targets = [latest]
        else:
            targets = [_resolve_person(storage, name, "fetched")]
        failures = 0
        for person in targets:
            try:
                report = fetch_person_feed(person, config, storage)
            except IngestError as exc:
                failures += 1
                typer.echo(f"  {person.name}: fetch failed: {exc}", err=True)
                continue
            typer.echo(
                f"{report.person_name}: {report.items} posts in feed  "
                f"added: {report.added}  duplicates: {report.skipped_duplicates}  "
                f"empty: {report.skipped_empty}"
            )
            for title in report.titles:
                typer.echo(f"  + {title}")
            for failure in report.failed_sources:
                typer.echo(f"  ! {failure}", err=True)
    if failures:
        raise typer.Exit(code=1)


@app.command()
def embed() -> None:
    """Embed corpus and people's writing for semantic similarity (RFC-010).

    The one explicit data-egress step: document text is sent to the configured
    embeddings provider, once per new document.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "embedded")
    try:
        provider = get_embedding_provider(config)
        with Storage(config.db_path) as storage:
            report = embed_missing(storage, provider)
    except (ModelConfigError, EmbeddingError) as exc:
        typer.echo(f"embed failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Provider: {report.provider}/{report.model}")
    typer.echo(
        f"Embedded: {report.corpus_embedded} corpus + {report.external_embedded} external  "
        f"(re-embedded after model change: {report.reembedded})  "
        f"Already embedded: {report.already_embedded}  Empty skipped: {report.skipped_empty}"
    )


@people_app.command("similar")
def people_similar(
    name: str | None = typer.Argument(
        None, help="Person to compare against; omit to compare against your own corpus."
    ),
    limit: int = typer.Option(10, "--limit", help="How many people to show."),
) -> None:
    """Who thinks about the same things — as this person, or as you."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "compared")
    try:
        with Storage(config.db_path) as storage:
            if name is not None:
                name = _resolve_person(storage, name, "compared").name
            report = similar_people(storage, name=name, limit=limit)
    except IngestError as exc:
        typer.echo(f"people similar failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not report.people:
        typer.echo(
            "No other people have embedded writing yet — fetch feeds and run 'wingman embed'."
        )
        return
    typer.echo(f"Closest to {report.reference}:")
    for number, entry in enumerate(report.people, start=1):
        where = ", ".join(part for part in (entry.position, entry.company) if part)
        detail = f"  ({where})" if where else ""
        typer.echo(
            f"{number}. {entry.name}{detail}  score {entry.score:.3f}  [{entry.documents} docs]"
        )


@people_app.command("warm-path")
def people_warm_path(
    name: str = typer.Argument(..., help="Person to find a warm introduction path to."),
    from_person: str = typer.Option(
        "", "--from", help="Narrow the search to paths starting from this network owner."
    ),
) -> None:
    """Who in your network could introduce you — a live call to Woven (#81, RFC-043).

    Woven is a separate warm-intro-path graph server; nothing is cached or
    stored here, and identity resolution is entirely Woven's own fuzzy name
    matching. Requires WINGMAN_WOVEN_URL to be configured.
    """
    configure_logging()
    from wingman.application.warm_intro import warm_paths_to_person

    try:
        typer.echo(warm_paths_to_person(name, from_person=from_person))
    except IngestError as exc:
        typer.echo(f"warm-path failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@people_app.command("like")
def people_like_cmd(
    names: list[str] = typer.Argument(
        ..., help="Two or more people you find interesting, e.g. 'Mario Rossi' 'Brian Chen'."
    ),
    limit: int = typer.Option(10, "--limit", help="How many people to show."),
) -> None:
    """'If you like these people, you should be talking to…'"""
    configure_logging()
    config = load_config()
    _require_workspace(config, "compared")
    try:
        with Storage(config.db_path) as storage:
            if len(names) >= 2:
                names = [_resolve_person(storage, name, "compared").name for name in names]
            report = people_like(storage, names=names, limit=limit)
    except IngestError as exc:
        typer.echo(f"people like failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not report.people:
        typer.echo(
            "No other people have embedded writing yet — fetch feeds and run 'wingman embed'."
        )
        return
    typer.echo(f"Closest to {report.reference}:")
    for number, entry in enumerate(report.people, start=1):
        where = ", ".join(part for part in (entry.position, entry.company) if part)
        detail = f"  ({where})" if where else ""
        typer.echo(
            f"{number}. {entry.name}{detail}  score {entry.score:.3f}  [{entry.documents} docs]"
        )


@people_app.command("pov")
def people_pov(
    name: str = typer.Argument(..., help="Person to summarize."),
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild the card (a model call) even if one is stored."
    ),
) -> None:
    """What this person thinks: an evidence-backed POV card from their writing.

    Building a card is a model call (synthesize_balanced): the person's stored
    posts go to the configured provider, and every proposed stance is kept
    only if its quote appears verbatim in the stored document. A stored card
    is shown without any model call; --refresh rebuilds.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "summarized")
    with Storage(config.db_path) as storage:
        person = _resolve_person(storage, name, "summarized")
        if not refresh:
            stored = storage.get_pov_card(person.person_id)
            if stored is not None:
                typer.echo(render_pov_card(stored))
                typer.echo("\n(stored card — rebuild with --refresh)")
                return
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_pov_card(person.name, storage, provider)
        except (IngestError, ModelConfigError, ProviderError) as exc:
            typer.echo(f"people pov failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ProposalParseError as exc:
            typer.echo(
                f"people pov failed: {exc}. Nothing was stored; re-run to retry.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        materialize_person_export(person.name, config, storage)
    typer.echo(render_pov_card(report.card))
    for rejected in report.rejected:
        typer.echo(f"  rejected stance {rejected.statement!r}: {rejected.reason}")


@people_app.command("deep-dive")
def people_deep_dive(
    name: str = typer.Argument(..., help="Person to research."),
    yes: bool = typer.Option(False, "--yes", help="Skip both confirmation prompts."),
) -> None:
    """One-shot open-web research on a person (#222): current role,
    background, public viewpoints, recent activity — with citations.

    The only wingman lookup that reaches the open web (via OpenRouter) and
    the only one that costs API usage per call — every other lookup stays
    inside approved sources or stored data. Asks before searching (the
    paid call) and again before storing what it finds.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "researched")
    if not yes and not typer.confirm(
        f"Research {name!r} via OpenRouter's web-search-grounded model? "
        "This reaches the open web and costs API usage.",
        default=False,
    ):
        typer.echo("Nothing was searched.")
        raise typer.Exit(code=1)
    try:
        provider = get_provider(CapabilityClass.RESEARCH_WEBSEARCH, config)
        response = research_person_dossier(name, provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"deep-dive failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    warning = dossier_truncation_warning(response)
    if warning:
        typer.echo(warning, err=True)
        typer.echo("")
    typer.echo(response.text)
    typer.echo("")
    if not yes and not typer.confirm(f"Store this as {name}'s deep-dive?", default=False):
        typer.echo("Nothing was stored.")
        return
    with Storage(config.db_path) as storage:
        person = save_person_dossier(
            name, response.text, storage, provider=response.provider, model=response.model
        )
    typer.echo(f"Stored deep-dive for {person.name}.")


@people_app.command("dossier")
def people_dossier(
    name: str = typer.Argument(..., help="Person whose stored dossier to show."),
) -> None:
    """Show a person's stored deep-dive dossier (#222). Read-only — no network call."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "shown")
    with Storage(config.db_path) as storage:
        person = _resolve_person(storage, name, "shown")
        dossier = storage.get_person_dossier(person.person_id)
    if dossier is None:
        typer.echo(f"No deep-dive stored for {person.name} yet — try 'wingman people deep-dive'.")
        return
    typer.echo(dossier.content)


@people_app.command("brief")
def people_brief(
    name: str = typer.Argument(..., help="Person to draft outreach material for."),
    purpose: str = typer.Option(
        "introduction",
        "--purpose",
        help="Why you're reaching out: introduction, reconnection, job, or advice.",
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild the brief (a model call) even if one is stored."
    ),
) -> None:
    """Draft talking points and an intro connecting their POV to your writing.

    Building a brief is a model call (synthesize_balanced): the person's POV
    card and excerpts of your own corpus go to the configured provider, and a
    talking point is kept only if it cites a card stance exactly and quotes
    your corpus verbatim. Drafts only — Wingman never sends anything
    (RFC-006). A stored brief is shown without any model call; --refresh
    rebuilds.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "drafted")
    try:
        outreach_purpose = OutreachPurpose(purpose.strip().lower())
    except ValueError:
        valid = ", ".join(entry.value for entry in OutreachPurpose)
        typer.echo(f"unknown purpose {purpose!r}; use one of: {valid}.", err=True)
        raise typer.Exit(code=1) from None
    from wingman.application.relationship import render_relationship_context

    with Storage(config.db_path) as storage:
        person = _resolve_person(storage, name, "drafted")
        context = render_relationship_context(person, storage)
        if not refresh:
            stored = storage.get_outreach_brief(person.person_id)
            if stored is not None:
                typer.echo(render_outreach_brief(stored))
                hint = "rebuild with --refresh"
                if stored.purpose is not outreach_purpose:
                    hint = f"stored purpose is {stored.purpose.value!r} — rebuild with --refresh"
                typer.echo(f"\n(stored brief — {hint})")
                if context:
                    typer.echo(f"\n{context}")
                return
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_outreach_brief(person.name, storage, provider, purpose=outreach_purpose)
        except (IngestError, ModelConfigError, ProviderError) as exc:
            typer.echo(f"people brief failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ProposalParseError as exc:
            typer.echo(
                f"people brief failed: {exc}. Nothing was stored; re-run to retry.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        materialize_person_export(person.name, config, storage)
    typer.echo(render_outreach_brief(report.brief))
    for rejected in report.rejected:
        typer.echo(f"  rejected point {rejected.point!r}: {rejected.reason}")
    if context:
        typer.echo(f"\n{context}")


@people_app.command("discover")
def people_discover(
    limit: int = typer.Option(10, "--limit", help="Maximum suggestions to show."),
) -> None:
    """Suggest new publications via the recommendations of Substacks you watch.

    Reads each watched publication's public /recommendations page (one page
    each, RFC-009) and ranks publications you don't watch by how many of
    your watched ones recommend them. Suggestions only — nothing is ever
    added without you running 'wingman people add' yourself.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "discovered")
    with Storage(config.db_path) as storage:
        report = discover_recommendations(storage, limit=limit)
    for failure in report.failures:
        typer.echo(f"  {failure}", err=True)
    if report.scanned == 0 and not report.failures:
        typer.echo(
            "No watched Substacks to walk — add some with 'wingman people add --substack' first."
        )
        return
    if report.scanned == 0:
        typer.echo("Every recommendations page failed to fetch (shown above).", err=True)
        raise typer.Exit(code=1)
    if not report.candidates:
        typer.echo(f"Scanned {report.scanned} publications — no new recommendations found.")
        return
    typer.echo(f"Scanned {report.scanned} publications. Worth a look:")
    for number, candidate in enumerate(report.candidates, start=1):
        who = ", ".join(candidate.recommenders[:3])
        more = f" +{len(candidate.recommenders) - 3}" if len(candidate.recommenders) > 3 else ""
        typer.echo(
            f"{number}. {candidate.url}  (recommended by {len(candidate.recommenders)}: "
            f"{who}{more})"
        )
    typer.echo('Add one with: wingman people add "<Name>" --substack <url>')


@app.command("coach-persona")
def coach_persona_cmd(
    action: str = typer.Argument(..., help="One of: set, clear, who, list."),
    name: str = typer.Argument("", help="Required for 'set': the persona's name."),
) -> None:
    """Coaching mode (docs/COACHING-MODE-DESIGN.md): act as coach for
    someone, or check/clear who's currently active.

    'set <name>' finds-or-creates a persona (case/whitespace-insensitive
    matching) and makes it the default scope for every persona-aware
    command (interview, perspectives, pov, ...) until 'clear'. 'who'
    reports the currently active persona — cheap to call any time you're
    not sure. 'list' shows every persona ever coached, so you can tell
    'set' apart from accidentally creating a near-duplicate.

    Coach-mediated only: you always drive every command yourself, on a
    persona's behalf — there is no separate login for them. Everything
    you already know stays visible regardless of which persona is
    active; only a persona's own captured evidence is scoped to them.
    """
    from wingman.application.coaching import (
        clear_active_persona_and_report,
        get_active_persona,
        render_acting_as,
        set_active_persona,
    )

    configure_logging()
    config = load_config()
    _require_workspace(config, "changed")
    action = action.strip().lower()
    try:
        with Storage(config.db_path) as storage:
            if action == "set":
                if not name.strip():
                    typer.echo("coach-persona 'set' needs a name — nothing changed.", err=True)
                    raise typer.Exit(code=1)
                persona = set_active_persona(name, storage, config)
                typer.echo(f"{render_acting_as(persona)} Everything from here scopes to them.")
                return
            if action == "clear":
                clear_active_persona_and_report(config)
                typer.echo(render_acting_as(None))
                return
            if action == "who":
                typer.echo(render_acting_as(get_active_persona(storage, config)))
                return
            if action == "list":
                personas = storage.list_personas()
                if not personas:
                    typer.echo("No personas yet — 'coach-persona set <name>' starts one.")
                    return
                typer.echo("Personas coached so far:")
                for persona in personas:
                    suffix = f" — {persona.notes}" if persona.notes else ""
                    typer.echo(f"- {persona.name}{suffix}")
                return
    except IngestError as exc:
        typer.echo(f"coach-persona failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"unknown action {action!r}; use set, clear, who, or list.", err=True)
    raise typer.Exit(code=1)


@app.command("carve-off-persona")
def carve_off_persona_cmd(
    persona: str = typer.Argument(
        ..., help="Persona name (or id) to carve off — see 'wingman coach-persona list'."
    ),
    target: Path = typer.Argument(
        ...,
        help=(
            "Target workspace directory — brand-new (created if missing) or one that "
            "already has its own profile; either way, a merge."
        ),
    ),
) -> None:
    """#235: export a coached persona's captured interview data and write
    it into a Wingman workspace's own first-person profile — brand-new or
    already populated.

    Gathers every ACTIVE profile item captured under this persona in YOUR
    own workspace (docs/COACHING-MODE-DESIGN.md) and writes it into
    <target> — created if needed — as that workspace's own profile
    (persona_id cleared), via the same dedup/supersede/conflict machinery
    ('profile_store.persist_items') every other ingestion path in this
    codebase uses. Evidence quotes are preserved verbatim; each cited
    source record is replaced with an honestly-labeled placeholder in the
    target workspace (docs/RFC.md RFC-049) since the coach's own original
    records live only in the coach's own workspace and are not copied.

    <target> may already have its own profile items (docs/RFC.md
    RFC-054): a carved-off item matching nothing there is added; one
    matching an existing item's value merges evidence; one that genuinely
    contradicts an existing item is NEVER silently overwritten — it lands
    as a CONFLICT for the person to resolve themselves ('wingman profile
    list' / 'wingman profile resolve <id>', run against <target>). The
    printed report says how many items landed which way.
    """
    from wingman.application.persona_carveoff import carve_off_persona, render_carveoff_report

    configure_logging()
    config = load_config()
    _require_workspace(config, "carved off")
    try:
        with Storage(config.db_path) as storage:
            report = carve_off_persona(persona, config, storage, target)
    except IngestError as exc:
        typer.echo(f"carve-off-persona failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_carveoff_report(report))


@app.command()
def pov(
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild the card (a model call) even if one is stored."
    ),
) -> None:
    """Your own point of view: the subject areas where your corpus takes a position.

    The same machinery as a person's POV card, pointed at your writing — a
    model call (synthesize_balanced) whose every stance must quote your own
    documents verbatim. Use it to decide which of your positions to lead
    with in outreach. A stored card is shown without any model call;
    --refresh rebuilds.

    Coaching mode (docs/COACHING-MODE-DESIGN.md): if a persona is active
    ('wingman coach-persona set <name>'), this builds THEIR stance
    instead — from only their own scoped interview captures, never your
    corpus or POV.
    """
    from wingman.application.coaching import get_active_persona, render_acting_as
    from wingman.application.pov import persona_card_id

    configure_logging()
    config = load_config()
    _require_workspace(config, "summarized")
    with Storage(config.db_path) as storage:
        active_persona = get_active_persona(storage, config)
        typer.echo(render_acting_as(active_persona))
        card_id = (
            persona_card_id(active_persona.persona_id)
            if active_persona is not None
            else CORPUS_PERSON_ID
        )
        if not refresh:
            stored = storage.get_pov_card(card_id)
            if stored is not None:
                typer.echo(render_pov_card(stored))
                typer.echo("\n(stored card — rebuild with --refresh)")
                return
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_own_pov(storage, provider, persona=active_persona)
        except (IngestError, ModelConfigError, ProviderError) as exc:
            typer.echo(f"pov failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ProposalParseError as exc:
            typer.echo(f"pov failed: {exc}. Nothing was stored; re-run to retry.", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(render_pov_card(report.card))
    for rejected in report.rejected:
        typer.echo(f"  rejected stance {rejected.statement!r}: {rejected.reason}")


@app.command()
def values(
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild the profile (a model call) even if one is stored."
    ),
    view: str = typer.Option(
        "character",
        "--view",
        help="'character' (what you care about) or 'work' (how you want to work).",
    ),
) -> None:
    """Your inferred value dimensions (v2 of issue #240 — inference only,
    no chart; v3 is a separate, later PR that will render these axes as a
    radar chart).

    --view work (issue #356) reads the SAME captures as ways of working —
    what you want authority over, the conditions you need, the standard you
    hold work to. Same scoring, same evidence citations, different naming;
    it is the reading a fit brief can cite, and 'wingman assess' now prints
    it alongside the requirement verdicts. It additionally reads your
    alignment_of_perspective reactions, which are about ideas rather than
    people. Each view is stored and rebuilt separately.

    Reads your own accumulated Values/Mission-alignment interview
    nominations (see 'wingman interview') and infers a small, named set of
    value axes describing what you actually care about — each backed by
    the specific captures that informed it, never a black-box number.
    Refuses (below a minimum-evidence floor) rather than guessing from too
    little. A model call (synthesize_balanced) on refresh; a stored
    profile is shown without one.

    Coaching mode (docs/COACHING-MODE-DESIGN.md): if a persona is active
    ('wingman coach-persona set <name>'), this builds THEIR profile
    instead — from only their own scoped interview captures.
    """
    from wingman.application.coaching import get_active_persona, render_acting_as
    from wingman.application.pov import CORPUS_PERSON_ID, persona_card_id
    from wingman.application.values import (
        build_value_profile,
        new_captures_since,
        parse_value_view,
        render_value_profile,
    )

    configure_logging()
    config = load_config()
    _require_workspace(config, "profiled")
    try:
        value_view = parse_value_view(view)
    except IngestError as exc:
        typer.echo(f"values failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    with Storage(config.db_path) as storage:
        active_persona = get_active_persona(storage, config)
        typer.echo(render_acting_as(active_persona))
        subject_id = (
            persona_card_id(active_persona.persona_id)
            if active_persona is not None
            else CORPUS_PERSON_ID
        )
        persona_id = active_persona.persona_id if active_persona is not None else None
        if not refresh:
            stored = storage.get_value_profile(subject_id, value_view)
            if stored is not None:
                stale = new_captures_since(storage, stored, persona_id=persona_id)
                typer.echo(render_value_profile(stored, stale_new_captures=stale))
                typer.echo("\n(stored profile — rebuild with --refresh)")
                return
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_value_profile(storage, provider, persona=active_persona, view=value_view)
        except (IngestError, ModelConfigError, ProviderError) as exc:
            typer.echo(f"values failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ProposalParseError as exc:
            typer.echo(f"values failed: {exc}. Nothing was stored; re-run to retry.", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(render_value_profile(report.profile))
    for rejected in report.rejected:
        typer.echo(f"  rejected axis {rejected.name!r}: {rejected.reason}")


@app.command("values-chart")
def values_chart(
    out: Path | None = typer.Option(
        None, "--out", help="Destination folder (default: the workspace's reports/charts/)."
    ),
    view: str = typer.Option(
        "character",
        "--view",
        help="'character' (what you care about) or 'work' (how you want to work).",
    ),
) -> None:
    """Render your inferred value dimensions (see 'wingman values') as an SVG
    radar chart (v3 of issue #240 — presentation only; no model call, nothing
    recomputed). Writes under reports/charts/ and prints the path.

    Requires a stored profile — build one first with 'wingman values
    --refresh'. If the stored profile predates newer captures, the chart
    still renders, with the "N new captures" note printed on it as well as
    to this terminal, same as 'wingman values' already does for a stale
    stored profile.

    --view work charts the work reading instead (issue #356): one chart per
    view, same geometry, its own file — never overwriting the other.

    Coaching mode (docs/COACHING-MODE-DESIGN.md): if a persona is active
    ('wingman coach-persona set <name>'), this charts THEIR profile instead.
    """
    from wingman.application.coaching import get_active_persona, render_acting_as
    from wingman.application.values import parse_value_view, refresh_command
    from wingman.reporting.radar import export_value_radar

    configure_logging()
    config = load_config()
    _require_workspace(config, "charted")
    try:
        value_view = parse_value_view(view)
    except IngestError as exc:
        typer.echo(f"values-chart failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    with Storage(config.db_path) as storage:
        active_persona = get_active_persona(storage, config)
        typer.echo(render_acting_as(active_persona))
        try:
            export = export_value_radar(
                config, storage, persona=active_persona, out_dir=out, view=value_view
            )
        except IngestError as exc:
            typer.echo(f"values-chart failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {export.path}")
    # The superseded warning goes first and is stated as a shape problem,
    # not a completeness one: new captures make a chart incomplete, a
    # replaced scoring rule can make it point the wrong way (#340/#355).
    if export.scoring_superseded:
        typer.echo(
            "This chart was drawn from a profile scored under a rule this version of "
            "wingman no longer runs — the shape may be superseded, and axes evidenced by "
            f"'con' nominations may be inverted. Rebuild with "
            f"'{refresh_command(value_view)}', then re-export."
        )
    if export.stale_new_captures:
        noun = "capture" if export.stale_new_captures == 1 else "captures"
        typer.echo(
            f"{export.stale_new_captures} new {noun} since this profile was built — "
            f"'{refresh_command(value_view)}' to include them."
        )
    typer.echo(f'Open it: open "{export.path}"')


_STEP_MARKS = {"ok": "✓", "skipped": "–", "failed": "✗"}


def _echo_miso(report: MisoReport) -> None:
    typer.echo(f"{report.target} ({report.kind}):")
    for step in report.steps:
        typer.echo(f"  {_STEP_MARKS.get(step.status, '?')} {step.name}: {step.detail}")
    if report.export_path:
        typer.echo(f'Render: npx md-to-pdf "{report.export_path}"')


def _parse_purpose(purpose: str) -> OutreachPurpose:
    try:
        return OutreachPurpose(purpose.strip().lower())
    except ValueError:
        valid = ", ".join(entry.value for entry in OutreachPurpose)
        typer.echo(f"unknown purpose {purpose!r}; use one of: {valid}.", err=True)
        raise typer.Exit(code=1) from None


def _make_it_so_impl(name: str, purpose: str, out: Path | None) -> None:
    configure_logging()
    config = load_config()
    _require_workspace(config, "run")
    outreach_purpose = _parse_purpose(purpose)
    try:
        with Storage(config.db_path) as storage:
            report = make_it_so(name, config, storage, purpose=outreach_purpose, out_dir=out)
    except IngestError as exc:
        typer.echo(f"make-it-so failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _echo_miso(report)


@app.command("make-it-so")
def make_it_so_cmd(
    name: str = typer.Argument(..., help="A person or company to run everything for."),
    purpose: str = typer.Option("introduction", "--purpose", help="Outreach purpose."),
    out: Path | None = typer.Option(None, "--out", help=_OUT_HELP),
) -> None:
    """The easy daily command: everything end to end, so you don't remember steps.

    Fetch, news, embed, POV, brief, and both exports (PDF sheet + tabbed
    HTML), in order, with honest per-step results — model steps skip
    visibly without API keys, fetch failures don't stop the rest.
    Alias: 'wingman miso'.
    """
    _make_it_so_impl(name, purpose, out)


@app.command("miso", hidden=True)
def miso_cmd(
    name: str = typer.Argument(..., help="A person or company to run everything for."),
    purpose: str = typer.Option("introduction", "--purpose", help="Outreach purpose."),
    out: Path | None = typer.Option(None, "--out", help=_OUT_HELP),
) -> None:
    """Alias for make-it-so."""
    _make_it_so_impl(name, purpose, out)


@watchlist_app.command("add")
def watchlist_add(
    list_name: str = typer.Argument(..., help="Watchlist name (created on first add)."),
    member: str = typer.Argument(..., help="Person or company to add."),
    company: bool = typer.Option(False, "--company", help="The member is a company."),
) -> None:
    """Add a person (default) or company to a named watchlist."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "added")
    with Storage(config.db_path) as storage:
        if company:
            kind, member_name = "company", member.strip()
        else:
            kind, member_name = "person", _resolve_person(storage, member, "added").name
        added = storage.watchlist_add(list_name, kind, member_name)
    if added:
        typer.echo(f"Added {member_name} ({kind}) to watchlist {list_name!r}.")
    else:
        typer.echo(f"{member_name} is already on watchlist {list_name!r}.")


@watchlist_app.command("remove")
def watchlist_remove(
    list_name: str = typer.Argument(..., help="Watchlist name."),
    member: str = typer.Argument(..., help="Member to remove (exact name)."),
    company: bool = typer.Option(False, "--company", help="The member is a company."),
) -> None:
    """Remove a member from a watchlist."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "removed")
    with Storage(config.db_path) as storage:
        removed = storage.watchlist_remove(list_name, "company" if company else "person", member)
    if removed:
        typer.echo(f"Removed {member} from watchlist {list_name!r}.")
    else:
        typer.echo(f"{member} was not on watchlist {list_name!r}.", err=True)
        raise typer.Exit(code=1)


@watchlist_app.command("list")
def watchlist_list() -> None:
    """All watchlists with member counts."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        lists = storage.watchlists()
    if not lists:
        typer.echo("No watchlists yet — create one with 'wingman watchlist add <list> <name>'.")
        return
    for name, count in lists:
        typer.echo(f"{name}  [{count} members]")


@watchlist_app.command("show")
def watchlist_show(
    list_name: str = typer.Argument(..., help="Watchlist to show."),
) -> None:
    """Members of one watchlist."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "shown")
    with Storage(config.db_path) as storage:
        members = storage.watchlist_members(list_name)
    if not members:
        typer.echo(f"Watchlist {list_name!r} has no members.")
        return
    for kind, member in members:
        typer.echo(f"{member}  ({kind})")


@watchlist_app.command("run")
def watchlist_run(
    list_name: str = typer.Argument(..., help="Watchlist to run make-it-so across."),
    purpose: str = typer.Option("introduction", "--purpose", help="Outreach purpose."),
    out: Path | None = typer.Option(None, "--out", help=_OUT_HELP),
) -> None:
    """Cycle every member of a watchlist through make-it-so.

    A member's failure is reported and the cycle continues — one broken
    feed never blocks the rest of the list.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "run")
    outreach_purpose = _parse_purpose(purpose)
    with Storage(config.db_path) as storage:
        members = storage.watchlist_members(list_name)
        if not members:
            typer.echo(f"Watchlist {list_name!r} has no members. Nothing was run.", err=True)
            raise typer.Exit(code=1)
        failures = 0
        for kind, member in members:
            try:
                report = make_it_so(
                    member, config, storage, purpose=outreach_purpose, out_dir=out, kind=kind
                )
            except IngestError as exc:
                failures += 1
                typer.echo(f"✗ {member} ({kind}): {exc}", err=True)
                continue
            # a member with any failed step counts as failed — systemic
            # provider breakage must never read as "0 failed" (#66)
            if any(step.status == "failed" for step in report.steps):
                failures += 1
            _echo_miso(report)
    typer.echo(f"{len(members)} members processed, {failures} failed.")
    if failures:
        raise typer.Exit(code=1)


@people_app.command("news")
def people_news(
    name: str = typer.Argument(..., help="Person to fetch recent news for."),
) -> None:
    """Fetch recent news mentioning this person or their company (explicit fetch).

    One read-only GET of Google News's public RSS search (RFC-009 shape).
    Privacy, stated plainly: the query — their name and company — is sent
    to the news provider. The result replaces the stored snapshot and
    appears in the person export's News quadrant.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "fetched")
    with Storage(config.db_path) as storage:
        person = _resolve_person(storage, name, "fetched")
        try:
            report = fetch_person_news(person, storage)
        except IngestError as exc:
            typer.echo(f"people news failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    if not report.titles:
        if report.dropped or report.dropped_stale:
            reasons = []
            if report.dropped:
                reasons.append(
                    f"{report.dropped} low-relevance (name/company not in the headline, "
                    "or a common-word company without corporate context)"
                )
            if report.dropped_stale:
                reasons.append(f"{report.dropped_stale} too old (>{STALE_AFTER_DAYS} days)")
            typer.echo(f"{' and '.join(reasons)} for {report.query} — nothing stored.")
        else:
            typer.echo(f"No recent news found for {report.query}.")
        return
    typer.echo(f"News for {report.query}:")
    for number, title in enumerate(report.titles, start=1):
        typer.echo(f"{number}. {title}")
    summary = f"{report.stored} items stored"
    dropped_bits = []
    if report.dropped:
        dropped_bits.append(f"{report.dropped} low-relevance")
    if report.dropped_stale:
        dropped_bits.append(f"{report.dropped_stale} too old")
    if dropped_bits:
        summary += f", {' and '.join(dropped_bits)} dropped"
    typer.echo(f"{summary} — they'll appear in 'wingman export person'.")


@people_app.command("docs")
def people_docs(
    name: str = typer.Argument(..., help="Person whose stored documents to list."),
) -> None:
    """List a person's stored documents: title, date, and source URL."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        person = _resolve_person(storage, name, "listed")
        documents = storage.list_external_documents(person.person_id)
    if not documents:
        typer.echo(
            f"{person.name} has no stored documents yet — "
            f"'wingman people fetch \"{person.name}\"' first."
        )
        return
    from wingman.reporting.export import newest_first

    for number, document in enumerate(newest_first(documents), start=1):
        when = document.published_at.date().isoformat() if document.published_at else "undated"
        via = f" (via {document.organization})" if document.organization else ""
        typer.echo(f"{number}. {document.title} [{when}]{via}")
        typer.echo(f"   {document.url or document.source_record_id}")
    typer.echo(f"{len(documents)} documents.")


@people_app.command("log")
def people_log(
    name: str = typer.Argument(..., help="Person whose interaction log to show."),
) -> None:
    """List a person's logged interactions, oldest first (RFC-037).

    Add entries with 'wingman log "<person>" "<what happened>"'.
    """
    configure_logging()
    from wingman.application.relationship import list_log, render_log

    config = load_config()
    _require_workspace(config, "listed")
    try:
        with Storage(config.db_path) as storage:
            person, entries = list_log(name, storage)
    except IngestError as exc:
        typer.echo(f"people log failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_log(person, entries))


@people_app.command("evidence")
def people_evidence(
    query: str = typer.Argument(..., help="Words or a quoted phrase to search for."),
    limit: int = typer.Option(10, "--limit", help="Maximum number of excerpts."),
) -> None:
    """Search people's writing: who has said what about this topic."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "searched")
    try:
        with Storage(config.db_path) as storage:
            hits = find_people_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        typer.echo(f"people evidence search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not hits:
        typer.echo(f"No evidence found in people's writing for {query!r}.")
        return
    for number, hit in enumerate(hits, start=1):
        when = (
            hit.document.published_at.date().isoformat() if hit.document.published_at else "undated"
        )
        via = f" (via {hit.document.organization})" if hit.document.organization else ""
        typer.echo(f"{number}. {hit.person_name}{via} — {hit.document.title} [{when}]")
        typer.echo(f"   {hit.snippet}")
        typer.echo(f"   source: {hit.document.url or hit.document.source_record_id}")


_NO_COMPANY_SIGNALS = (
    "No other companies have embedded writing yet — add people with --company "
    "or attach an org-attributed feed, then run `wingman sync`."
)


@company_app.command("similar")
def company_similar(
    name: str | None = typer.Argument(
        None, help="Company to compare against; omit to compare against your own corpus."
    ),
    limit: int = typer.Option(10, "--limit", help="How many companies to show."),
) -> None:
    """Which companies think about the same things — as this company, or as you.

    A company's signal is the embedded writing of watched people who work
    there plus posts from its org-attributed feeds (RFC-011). Deterministic
    arithmetic over stored vectors — no model call, no network.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "compared")
    try:
        with Storage(config.db_path) as storage:
            report = similar_companies(storage, name=name, limit=limit)
    except IngestError as exc:
        typer.echo(f"company similar failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not report.companies:
        typer.echo(_NO_COMPANY_SIGNALS)
        return
    typer.echo(f"Closest to {report.reference}:")
    for number, entry in enumerate(report.companies, start=1):
        typer.echo(
            f"{number}. {entry.name}  score {entry.score:.3f}  "
            f"[{entry.people} people, {entry.documents} docs]"
        )


@company_app.command("warm-path")
def company_warm_path(
    name: str = typer.Argument(..., help="Company to find warm-path coverage for."),
    from_person: str = typer.Option(
        "", "--from", help="Narrow the search to paths starting from this network owner."
    ),
) -> None:
    """Who in your network reaches this company — a live call to Woven (#81, RFC-043).

    Woven is a separate warm-intro-path graph server; nothing is cached or
    stored here, and identity resolution is entirely Woven's own fuzzy
    company matching. Requires WINGMAN_WOVEN_URL to be configured.
    """
    configure_logging()
    from wingman.application.warm_intro import warm_overview_for_company

    try:
        typer.echo(warm_overview_for_company(name, from_person=from_person))
    except IngestError as exc:
        typer.echo(f"warm-path failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _render_hint(path: Path) -> str:
    pdf = path.with_suffix(".pdf")
    return f'Render: npx md-to-pdf "{path}"\nPDF lands at: {pdf}'


@export_app.command("career")
def export_career_cmd(
    out: Path | None = typer.Option(None, "--out", help=_OUT_HELP),
) -> None:
    """Portrait one-pager of the canonical profile — every claim cited, design-system styled."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "exported")
    try:
        with Storage(config.db_path) as storage:
            path = export_career(config, storage, out_dir=out)
    except IngestError as exc:
        typer.echo(f"export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {path}")
    typer.echo(_render_hint(path))


@export_app.command("company")
def export_company_cmd(
    name: str = typer.Argument(..., help="Company to export a dossier for."),
    out: Path | None = typer.Option(None, "--out", help=_OUT_HELP),
) -> None:
    """The company dossier as a print-ready page, fact/inference labels styled."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "exported")
    try:
        with Storage(config.db_path) as storage:
            path = export_company(name, config, storage, out_dir=out)
    except IngestError as exc:
        typer.echo(f"export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {path}")
    typer.echo(_render_hint(path))


@export_app.command("person")
def export_person_cmd(
    name: str = typer.Argument(..., help="Person to export the landscape sheet for."),
    out: Path | None = typer.Option(None, "--out", help=_OUT_HELP),
    html: bool = typer.Option(
        False, "--html", help="Write a tabbed HTML page for reading on screen instead."
    ),
) -> None:
    """Landscape 2x2 briefing dock: brief | point of view | background | news.

    --html writes a self-contained tabbed page (brief | pov | related) for
    the screen; the default Markdown renders to the dense one-page PDF.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "exported")
    try:
        with Storage(config.db_path) as storage:
            person = _resolve_person(storage, name, "exported")
            path = export_person(person.name, config, storage, out_dir=out, as_html=html)
    except IngestError as exc:
        typer.echo(f"export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {path}")
    if html:
        typer.echo(f'Open it: open "{path}"')
    else:
        typer.echo(_render_hint(path))


@company_app.command("dossier")
def company_dossier(
    name: str = typer.Argument(..., help="Company to snapshot, e.g. 'Innovation Endeavors'."),
) -> None:
    """A dated, cited company snapshot from what the workspace already knows.

    Deterministic composition — no model call, no network: watched people
    there, org-attributed sources, their validated POV stances (each an
    [inference] backed by a verbatim [fact] quote), similarity signals when
    embeddings exist, staleness warnings, and gaps. Written as Markdown
    under reports/companies/.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "summarized")
    try:
        with Storage(config.db_path) as storage:
            report = build_company_dossier(name, config, storage)
    except IngestError as exc:
        typer.echo(f"company dossier failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(report.markdown)
    typer.echo(f"(written to {report.path})")


@company_app.command("deep-dive")
def company_deep_dive(
    name: str = typer.Argument(..., help="Company to research, e.g. 'Anthropic'."),
    yes: bool = typer.Option(False, "--yes", help="Skip both confirmation prompts."),
) -> None:
    """One-shot open-web research on a company (#350): market position,
    stated values, culture — every finding carrying the source that backs it.

    One of two wingman lookups that reach the open web (the other is
    'people deep-dive') and one of two that cost API usage per call —
    everything else stays inside approved sources or stored data. Asks
    before searching (the paid call) and again before storing. A finding
    whose URL was not among the pages the search actually returned is
    reported as rejected and never stored.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "researched")
    if not yes and not typer.confirm(
        f"{spend_warning(name)}\n\nSearch now?",
        default=False,
    ):
        typer.echo("Nothing was searched.")
        raise typer.Exit(code=1)
    try:
        provider = get_provider(CapabilityClass.RESEARCH_WEBSEARCH, config)
        response = research_company_dossier(name, provider)
        review = review_findings(name, response)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"company deep-dive failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"company deep-dive failed: {exc}. Nothing was stored; re-run to retry.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    warning = dossier_truncation_warning(response)
    if warning:
        typer.echo(warning, err=True)
        typer.echo("")
    content = render_findings(review)
    typer.echo(content)
    if not review.findings:
        typer.echo(
            "No finding survived the source gate — nothing to store.",
            err=True,
        )
        raise typer.Exit(code=1)
    if not yes and not typer.confirm(
        f"Store these {len(review.findings)} sourced findings as {name}'s deep-dive?",
        default=False,
    ):
        typer.echo("Nothing was stored.")
        return
    try:
        with Storage(config.db_path) as storage:
            dossier = save_company_dossier(
                name, content, storage, provider=response.provider, model=response.model
            )
    except IngestError as exc:
        typer.echo(f"company deep-dive save failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Stored deep-dive for {dossier.company_name} "
        f"({len(dossier.findings)} sourced findings). "
        f"It renders in 'wingman company dossier \"{dossier.company_name}\"'."
    )


@company_app.command("follow")
def company_follow_cmd(
    name: str = typer.Argument(..., help="Company to follow, e.g. 'Anthropic'."),
    url: str = typer.Option(
        "",
        "--url",
        help="The company's https:// domain — its conventional pages (careers, blog, "
        "newsroom) are probed once and live ones approved as research sources.",
    ),
) -> None:
    """Turn a company name into a standing focus, in one act (RFC-018).

    Enrolls the company — and everyone you know there who has writing
    attached — on the reserved 'overnight' watchlist, and (with --url)
    approves the domain's live conventional pages as research sources.
    'wingman overnight' then deep-refreshes everything enrolled.
    Enrollment is the consent record: enumerable via 'wingman watchlist
    show overnight', revocable via 'wingman watchlist remove'.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "followed")
    try:
        with Storage(config.db_path) as storage:
            report = follow_company(name, storage, url=url or None)
    except IngestError as exc:
        typer.echo(f"follow failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_follow_report(report))


@keys_app.command("set")
def keys_set(
    name: str = typer.Argument(..., help="Which key: " + ", ".join(sorted(KNOWN_KEYS)) + "."),
    value: str = typer.Option(
        "",
        "--value",
        help="The key itself. Omit to take it from the already-exported environment "
        "variable, or be prompted with hidden input.",
    ),
    scope: str = typer.Option(
        "keychain",
        "--scope",
        help="Where to write it: keychain (default), host, global, or workspace.",
    ),
    tenant: str = typer.Option(
        "", "--tenant", help="With --scope workspace: which tenant's workspace (operators)."
    ),
) -> None:
    """Store an API key in one tier — the Keychain by default.

    Pick the tier that actually holds the key you are replacing ('wingman
    keys where' names it). Writing to the wrong tier is the classic way an
    expired key survives: the new copy loses to the stale one that outranks
    it, and nothing looks different.

      --scope keychain   macOS Keychain, this account (default)
      --scope host       ~/.config/wingman/secrets.env — every consumer on this account
      --scope global     /etc/wingman/global-secrets.env — the whole box, and the
                         fallback every funded tenant spends. Root-owned: needs sudo.
      --scope workspace  this workspace's keys.env — one person only, and it
                         outranks every other tier (BYOK)

    Once stored, every wingman command and the MCP server pick it up
    automatically — no env blocks in claude_desktop_config.json, no
    EnvironmentVariables in launchd plists, no wrapper scripts.
    """
    configure_logging()
    env_var = KNOWN_KEYS.get(name.strip().lower(), "")
    if tenant:
        # NEVER the ambient environment when writing into somebody else's
        # workspace. 'ensure_env' hydrates the host and global files into
        # this process on every invocation, so the fallback below would
        # silently copy the OPERATOR's key into a tenant's BYOK file and
        # report success — the write looks like it worked, the tenant is
        # pinned to a credential that is not theirs, and rotating the file
        # it came from no longer reaches them. Hit live on lobster.
        secret = value or typer.prompt(f"{name} key for tenant {tenant!r}", hide_input=True)
    else:
        secret = value or os.environ.get(env_var, "").strip()
        if not secret:
            secret = typer.prompt(f"{name} key", hide_input=True)

    choice = scope.strip().lower()
    if choice not in ("keychain", "host", "global", "workspace"):
        typer.echo(
            f"keys set failed: unknown --scope {scope!r}; "
            "use keychain, host, global, or workspace.",
            err=True,
        )
        raise typer.Exit(code=1)
    if tenant and choice != "workspace":
        typer.echo("keys set failed: --tenant only applies to --scope workspace.", err=True)
        raise typer.Exit(code=1)

    try:
        if choice == "keychain":
            stored = set_key(name, secret)
            typer.echo(f"Stored {stored} in the Keychain (account 'wingman').")
        elif choice == "host":
            path = store_host_key(name, secret)
            typer.echo(f"Stored {env_var} in {path} (0600).")
        elif choice == "global":
            path, mode, group_readable = store_global_key(name, secret)
            typer.echo(f"Stored {env_var} in {path} ({mode:04o}).")
            if not group_readable:
                # The silent-empty-tier failure, said out loud: this file is
                # root-owned and every other account reaches it by group.
                typer.echo(
                    f"WARNING: {mode:04o} has no group-read bit, so no other account on "
                    "this box can read this file — the tier will read as UNREADABLE for "
                    "the service account and every tenant funded from it.",
                    err=True,
                )
        else:
            data_dir = load_config().data_dir
            if tenant:
                from wingman.infrastructure.tenants import load_registry, tenant_registry_path

                registry = tenant_registry_path()
                match = [t for t in load_registry(registry) if t.slug == tenant]
                if not match:
                    typer.echo(f"keys set failed: no tenant {tenant!r} in {registry}.", err=True)
                    raise typer.Exit(code=1)
                data_dir = match[0].data_dir
            store_workspace_key(data_dir, name, secret)
            typer.echo(f"Stored {env_var} in {data_dir / 'keys.env'} (0600).")
    except KeyStoreError as exc:
        typer.echo(f"keys set failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # 'keys where' walks the single-account ladder, so pointing a tenant
    # write at it names the operator's host file as the winner — a file the
    # tenant never reads.
    typer.echo(
        f"Confirm which copy now wins with: wingman tenant keys --tenant {tenant}"
        if tenant
        else "Confirm which copy now wins with: wingman keys where"
    )


@keys_app.command("list")
def keys_list() -> None:
    """Where each known key comes from right now — never the values."""
    configure_logging()
    for short_name, env_var, state in key_status():
        typer.echo(f"{short_name:10s} {env_var:20s} {state}")
    typer.echo(
        "Precedence: a workspace's own key wins (BYOK), then the environment, then the "
        "Keychain, the host file, and the box-wide global file."
    )
    typer.echo("Run 'wingman keys where' to see every tier, its path, and which copy is used.")


def _echo_key_locations(title: str, rows_by_key: dict[str, list[KeyLocation]]) -> bool:
    """Print one workspace's tier table. Returns True if any drift was found."""
    typer.echo(f"\n{title}")
    drifted = False
    for short_name, rows in rows_by_key.items():
        env_var = KNOWN_KEYS[short_name]
        winner = next((r for r in rows if r.winner), None)
        blind = [r for r in rows if not r.readable]
        if winner:
            headline = winner.tier
        elif blind:
            headline = "CANNOT TELL - a tier could not be read"
        else:
            headline = "NOT SET ANYWHERE"
        typer.echo(f"  {short_name:11s} {env_var:22s} -> {headline}")
        others = [r.fingerprint for r in rows if r.present and not r.winner]
        if winner and any(f != winner.fingerprint for f in others):
            drifted = True
        for row in rows:
            if not row.readable:
                typer.echo(
                    f"    ????  {row.tier:42s} UNREADABLE (permission denied)"
                    "  <-- run as the owning account"
                )
                continue
            if not row.present:
                continue
            mark = "USED " if row.winner else "     "
            note = ""
            if winner and not row.winner and row.fingerprint != winner.fingerprint:
                note = "   <-- DIFFERENT KEY (ignored)"
            typer.echo(f"    {mark}{row.tier:42s} {row.fingerprint}{note}")
    return drifted


_TENANT_FLAGS_MOVED = (
    "{cmd} answers for ONE ACCOUNT, on the single-account ladder: workspace, "
    "environment, Keychain, host file, global file.\n"
    "A tenant resolves on the strict RFC-048 ladder instead — their own workspace "
    "file, then the global file only if funded — and never reads this account's host "
    "file or this process's environment. Reporting one ladder for the other named a "
    "file no tenant ever consults, so the tenant flags moved rather than staying "
    "quietly wrong:\n\n  {replacement}\n"
)


@keys_app.command("where")
def keys_where(
    tenant: str = typer.Option("", "--tenant", hidden=True),
    all_tenants: bool = typer.Option(False, "--all-tenants", hidden=True),
) -> None:
    """Every place a key could live, which copy is actually used, and whether
    the copies disagree — by fingerprint, never by value.

    'wingman keys list' can only ever say "environment", because startup
    hydration has already copied the winning tier into it. This shows the
    tiers themselves, so an expired key can be found in the one file that
    still holds it.
    """
    if tenant or all_tenants:
        replacement = (
            f"wingman tenant keys --tenant {tenant}" if tenant else "wingman tenant keys --all"
        )
        typer.echo(
            _TENANT_FLAGS_MOVED.format(cmd="'keys where'", replacement=replacement), err=True
        )
        raise typer.Exit(code=2)
    configure_logging()
    config = load_config()

    typer.echo("Resolution order (first one present wins):")
    typer.echo("  1. workspace file   <workspace>/keys.env      (BYOK - this person's own key)")
    typer.echo("  2. environment      exported variable")
    typer.echo("  3. keychain         macOS only, account 'wingman'")
    typer.echo(f"  4. host file        {host_keys_path()}")
    typer.echo("  5. global file      /etc/wingman/global-secrets.env  (whole box)")
    typer.echo("\nTiers 2-5 are the box's; tier 1 is per person. A fingerprint is")
    typer.echo("prefix...#digest (len N) - same digest means the same key.")

    drifted = False
    rows = describe_key_locations(_pre_hydration_env, data_dir=config.data_dir)
    drifted |= _echo_key_locations(f"this workspace  ({config.data_dir})", rows)

    if drifted:
        typer.echo(
            "\nSome tiers hold a DIFFERENT key from the one being used. That is how an "
            "expired key hides: fix or remove the stale copy, or it will win somewhere else."
        )


@keys_app.command("test")
def keys_test() -> None:
    """Actually call each provider to confirm its key works — not just that
    it's set. Costs one cheap, no-completion-tokens call per configured key
    (Anthropic: list models; Voyage: one one-word embed). Keys that resolve
    to nothing are reported as 'not set' with no network call at all.
    """
    configure_logging()
    failed = False
    for short_name, env_var, worked, message in test_keys():
        status = "ok" if worked else "FAIL"
        typer.echo(f"[{status}] {short_name:10s} {env_var:20s} {message}")
        if not worked and message != "not set":
            failed = True
    if failed:
        raise typer.Exit(code=1)


def _echo_validation(title: str, rows: list[KeyValidation]) -> bool:
    """Print one workspace's validation table. Returns True if anything failed."""
    typer.echo(f"\n{title}")
    failed = False
    for row in rows:
        if row.tier == "not set":
            marker = "  ??" if row.unreadable_tiers else "  --"
            typer.echo(f"  [{marker}] {row.short_name:11s} {row.env_var:22s} {row.message}")
            if row.unreadable_tiers:
                failed = True
            continue
        status = "  ok" if row.ok else "FAIL"
        typer.echo(f"  [{status}] {row.short_name:11s} {row.env_var:22s} {row.message}")
        typer.echo(f"           from {row.tier} - {row.fingerprint}")
        if row.unreadable_tiers:
            typer.echo(f"           unreadable below it: {', '.join(row.unreadable_tiers)}")
        if not row.ok:
            failed = True
    return failed


@keys_app.command("validate")
def keys_validate(
    tenant: str = typer.Option("", "--tenant", hidden=True),
    all_tenants: bool = typer.Option(False, "--all-tenants", hidden=True),
) -> None:
    """Take the key that is actually in use and call the provider with it.

    Differs from 'keys test' in the thing that matters: it resolves each
    key through the real ladder first — a workspace's own key, then the
    environment, Keychain, host file, global file — and tests THAT value,
    naming the tier it came from. 'keys test' only ever looks at the
    environment and the Keychain, so it can report a healthy key while
    every real call is spending an expired one out of a file.

    Costs one cheap, no-completion-tokens call per configured key
    (Anthropic: list models; Voyage: a one-word embed; GitHub/OpenRouter:
    an auth-status GET). Exits non-zero if any key fails.
    """
    if tenant or all_tenants:
        replacement = (
            f"wingman tenant validate --tenant {tenant}"
            if tenant
            else "wingman tenant validate --all"
        )
        typer.echo(
            _TENANT_FLAGS_MOVED.format(cmd="'keys validate'", replacement=replacement), err=True
        )
        raise typer.Exit(code=2)
    configure_logging()
    config = load_config()
    failed = False

    rows = validate_keys(_pre_hydration_env, data_dir=config.data_dir)
    failed |= _echo_validation(f"this workspace  ({config.data_dir})", rows)

    if failed:
        typer.echo(
            "\nA key that fails here is the one being spent. Replace it in the tier "
            "named above with: wingman keys set <name> --scope <tier>",
            err=True,
        )
        raise typer.Exit(code=1)


@keys_app.command("unset")
def keys_unset(
    name: str = typer.Argument(..., help="Which key to remove from the Keychain."),
) -> None:
    """Remove a stored key from the Keychain (environment variables are untouched)."""
    configure_logging()
    try:
        removed = unset_key(name)
    except KeyStoreError as exc:
        typer.echo(f"keys unset failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Removed." if removed else "Nothing was stored under that name.")


@examples_app.command("list")
def examples_list(
    kind: str = typer.Option("", "--kind", help="Only this kind of document."),
    verdict: str = typer.Option("", "--verdict", help="Only 'good' or only 'bad'."),
    contains: str = typer.Option("", "--contains", help="Only ones matching this text."),
) -> None:
    """Saved examples, newest first — a preview of each, not the whole document.

    Saving happens in conversation ("save this as a good example"); this is
    for looking over what has accumulated.
    """
    configure_logging()
    from wingman.application.examples import list_examples, render_examples

    config = load_config()
    _require_workspace(config, "examples")
    with Storage(config.db_path) as storage:
        typer.echo(
            render_examples(list_examples(storage, kind=kind, verdict=verdict, contains=contains))
        )


@examples_app.command("show")
def examples_show(
    example_id: str = typer.Argument(..., help="Id, or the first few characters of one."),
) -> None:
    """One example in full — the whole document, its verdict and its reason."""
    configure_logging()
    from wingman.application.examples import find_example, render_example

    config = load_config()
    _require_workspace(config, "examples")
    try:
        with Storage(config.db_path) as storage:
            typer.echo(render_example(find_example(example_id, storage)))
    except IngestError as exc:
        typer.echo(f"examples show failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@examples_app.command("kinds")
def examples_kinds() -> None:
    """Which kinds of document have examples, commonest first.

    Kinds are free text, so this is how the vocabulary stays visible:
    near-duplicates here mean it is drifting.
    """
    configure_logging()
    from wingman.application.examples import example_kinds, render_kinds

    config = load_config()
    _require_workspace(config, "examples")
    with Storage(config.db_path) as storage:
        typer.echo(render_kinds(example_kinds(storage)))


@examples_app.command("remove")
def examples_remove(
    example_id: str = typer.Argument(..., help="Id, or the first few characters of one."),
) -> None:
    """Forget one example."""
    configure_logging()
    from wingman.application.examples import remove_example

    config = load_config()
    _require_workspace(config, "examples")
    try:
        with Storage(config.db_path) as storage:
            removed = remove_example(example_id, storage)
    except IngestError as exc:
        typer.echo(f"examples remove failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Removed the {removed.verdict} example of '{removed.kind}'.")


@drive_app.command("auth")
def drive_auth_cmd() -> None:
    """Authorize wingman's Drive push with your own Google account (RFC-053, #205).

    Google's device-code flow — no local browser needed, so this works on a
    headless server: run this once to get a short code and a URL, open the
    URL on any device (phone, laptop) and approve, then run this exact same
    command again to finish. 'drive.file' scope only — wingman can see/write
    only the files/folders it creates itself, nothing else in your Drive.
    """
    configure_logging()
    try:
        result = run_drive_auth()
    except GDriveAuthError as exc:
        typer.echo(f"drive auth failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(result.detail)
    if result.status in ("pending", "expired", "denied"):
        raise typer.Exit(code=1)


def _overnight_tally(report: OvernightReport) -> str:
    """The first line a reader sees, with the two states kept apart.

    "N with failures" lumped a company awaiting setup together with a target
    that genuinely broke, and printed the same number every night for a week
    (#473). Attention is only mentioned when there is some, so a clean run
    still reads as one short sentence.
    """
    tally = f"{report.processed} targets, {report.failed} failed"
    if report.needs_attention:
        tally += f", {report.needs_attention} need attention"
    return f"{tally}, {len(report.actions)} actions."


@app.command()
def overnight(
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Where the digest lands (default: the workspace's reports/digests/). "
        "Point it somewhere you actually look — Desktop, a synced folder.",
    ),
    drive: bool = typer.Option(
        True,
        "--drive/--no-drive",
        help="Push the finished digest to Drive once authorized ('wingman drive auth'). "
        "A no-op before authorization; a push failure never affects the local digest.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit non-zero if any target had a failed step. Off by default: a run that "
        "wrote its digest completed, and the failures are recorded in it.",
    ),
) -> None:
    """Deep-refresh every followed company and person; write the dated digest (RFC-018).

    The explicit spend-the-tokens command — deliberately expensive: research
    diffs, feed fetches, news queries (each enrolled name+company goes to the
    news provider), embeddings, fresh POV cards, company themes, briefs, and
    exports for everything on the 'overnight' watchlist. Ends in a digest
    under reports/digests/ — what changed, what failed, what to consider
    following next. Schedule it yourself (launchd/cron — see README);
    wingman runs no daemon. The digest itself pushes to Drive once you've
    run 'wingman drive auth' (RFC-053, #205) — that adds one upload of a
    file that is already written, and changes nothing about the provider
    traffic listed above. Wingman still never sends anything ON YOUR
    BEHALF: no message, no application, no external write except this
    upload of your own artifact (RFC-006).

    A target whose fetch, news query or export fails is marked and carried
    into the digest; the command still exits 0, because the run itself
    completed. Pass --strict to exit non-zero on any such failure.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "refreshed")
    try:
        with Storage(config.db_path) as storage:
            report = overnight_run(config, storage, out_dir=out)
    except IngestError as exc:
        typer.echo(f"overnight failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for target in report.targets:
        marker = target_mark(target.status)
        typer.echo(f"{marker} {target.name} ({target.kind})")
    typer.echo(_overnight_tally(report))
    typer.echo(f"Digest: {report.digest_path}")
    pretty = Path(report.digest_path).with_suffix(".html")
    if pretty.exists():
        typer.echo(f"Pretty: {pretty}  (wingman digest --open)")
    typer.echo("Read it any time with: wingman digest")
    if drive:
        typer.echo(push_digest(Path(report.digest_path)).detail)
    if report.failed:
        # A failed feed fetch, news query or export is one target's bad step,
        # not a failed run: the digest is written and every other target is
        # done. Exiting non-zero here is what put the systemd unit into
        # 'failed' on 2026-08-05 after a run that produced everything it
        # promised — which teaches the reader to ignore the one signal that
        # should mean "nothing came back". Say what did not happen, and let
        # --strict restore the old status for callers that want it.
        typer.echo(
            "Run completed; the failures above are recorded in the digest. "
            "(--strict exits non-zero on them instead.)"
        )
        if strict:
            raise typer.Exit(code=1)


@app.command()
def digest(
    path_only: bool = typer.Option(False, "--path", help="Print only the path."),
    open_it: bool = typer.Option(False, "--open", help="Open it with the system viewer."),
) -> None:
    """Show the newest overnight digest — the morning read, one word away."""
    configure_logging()
    config = load_config()
    newest = latest_digest(config)
    if newest is None:
        typer.echo(
            "No digests yet — 'wingman overnight' writes one per run "
            "(enroll targets first with 'wingman company follow').",
            err=True,
        )
        raise typer.Exit(code=1)
    if path_only:
        typer.echo(str(newest))
        return
    if open_it:
        # Prefer the styled HTML twin when the run wrote one — it opens in
        # the browser; the markdown remains the canonical artifact.
        pretty = newest.with_suffix(".html")
        target = pretty if pretty.exists() else newest
        typer.launch(str(target))
        typer.echo(f"Opened {target}")
        return
    typer.echo(newest.read_text(encoding="utf-8"))


@app.command()
def today() -> None:
    """What's new in wingman itself — recent user-facing changes, dated newest
    first (RFC-038, #145). Describes the tool, not your workspace — needs no
    initialized workspace to answer "what's new in wingman?"."""
    configure_logging()
    from datetime import UTC, datetime

    from wingman.domain.changelog import render_changelog

    typer.echo(render_changelog(datetime.now(UTC).date()))


@app.command()
def pack(
    query: str = typer.Argument(..., help="Part of the assessed role's title, e.g. 'staff mle'."),
    company: str = typer.Option(
        "", "--company", help="Attach this company's intelligence (default: inferred from title)."
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Destination folder (default: the workspace's reports/packs/)."
    ),
) -> None:
    """Compose the application pack for an assessed role (RFC-024).

    Deterministic composition, no model call: the cited fit summary,
    cover-letter fodder quoting your own evidence verbatim (compose it in
    your voice — Wingman never sends), and the company intelligence the
    workspace already validated: themes, people you know there, research
    sources. Renders with npx md-to-pdf like every export.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "packed")
    try:
        with Storage(config.db_path) as storage:
            report = build_application_pack(
                query, config, storage, company=company or None, out_dir=out
            )
    except IngestError as exc:
        typer.echo(f"pack failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Pack: {report.title}" + (f" @ {report.company}" if report.company else ""))
    typer.echo(_render_hint(Path(report.path)))


@company_app.command("pov")
def company_pov_cmd(
    name: str = typer.Argument(..., help="Company to synthesize themes for."),
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild the themes (a model call) even if stored."
    ),
) -> None:
    """Synthesized company themes from its people's writing (RFC-016).

    A model call (synthesize_balanced) over the company's document pool —
    writing by watched people there plus org-attributed feeds, with author
    attribution on every document. Each theme survives only if its quote
    appears verbatim in a stored document. A stored card is shown without
    any model call; --refresh rebuilds. The dossier renders the stored card.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "synthesized")
    with Storage(config.db_path) as storage:
        if not refresh:
            stored = storage.get_pov_card(company_card_id(company_key(name)))
            if stored is not None:
                typer.echo(render_pov_card(stored))
                typer.echo("\n(stored card — rebuild with --refresh)")
                return
        try:
            provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
            report = build_company_pov(name, storage, provider)
        except (IngestError, ModelConfigError, ProviderError) as exc:
            typer.echo(f"company pov failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ProposalParseError as exc:
            typer.echo(
                f"company pov failed: {exc}. Nothing was stored; re-run to retry.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
    typer.echo(render_pov_card(report.card))
    for rejected in report.rejected:
        typer.echo(f"  rejected theme {rejected.statement!r}: {rejected.reason}")


@company_app.command("add-source")
def company_add_source(
    name: str = typer.Argument(..., help="Company the source belongs to."),
    url: str = typer.Argument(..., help="https:// page to watch (careers page, newsroom)."),
    label: str = typer.Option("", "--label", help="Optional short label, e.g. 'careers'."),
    retain: bool | None = typer.Option(
        None,
        "--retain/--no-retain",
        help="Also keep the page's text as a citable document for this company (RFC-060).",
    ),
) -> None:
    """Approve one research URL for a company — adding it IS the approval (RFC-015).

    'wingman company research' will fetch exactly the pages approved here,
    nothing else.

    --retain additionally KEEPS the fetched page's prose as a document
    attributed to the company, so 'company pov' and 'evidence' can quote it.
    Off by default: it suits a values or about page that changes twice a
    year, not a careers page that would re-store on every run. It changes
    nothing about the fetch — same page, same one GET. Re-run with
    --retain/--no-retain on an already-approved source to change your mind.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "added")
    try:
        with Storage(config.db_path) as storage:
            source, created = add_company_source(
                name, url, storage, label=label or None, retain=retain
            )
    except IngestError as exc:
        typer.echo(f"add-source failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    kept = " (text retained as a document)" if source.retain else ""
    if created:
        typer.echo(f"Approved for {source.company_name}: {source.url}{kept}")
        typer.echo(f'Fetch it with: wingman company research "{source.company_name}"')
    else:
        typer.echo(f"Already approved for {source.company_name}: {source.url}{kept}")


@company_app.command("remove-source")
def company_remove_source(
    name: str = typer.Argument(..., help="Company the source belongs to."),
    url: str = typer.Argument(..., help="The approved URL to withdraw."),
) -> None:
    """Withdraw an approved research source (its stored snapshot goes too)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "removed")
    try:
        with Storage(config.db_path) as storage:
            removed = remove_company_source(name, url, storage)
    except IngestError as exc:
        typer.echo(f"remove-source failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if removed:
        typer.echo(f"Withdrawn: {url}")
    else:
        typer.echo(f"{url} was not an approved source for {name!r}; nothing was removed.")


@company_app.command("sources")
def company_sources_cmd(
    name: str = typer.Argument(..., help="Company whose approved sources to list."),
) -> None:
    """List the approved research sources for a company."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    try:
        with Storage(config.db_path) as storage:
            sources = list_company_sources(name, storage)
            snapshots = {
                source.url: storage.get_research_snapshot(source.company_key, source.url)
                for source in sources
            }
    except IngestError as exc:
        typer.echo(f"sources failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not sources:
        typer.echo(
            f"No approved research sources for {name!r}. Approve one with "
            f"'wingman company add-source \"{name}\" <https-url>'."
        )
        return
    typer.echo(f"Approved sources for {name} ({len(sources)}):")
    for source in sources:
        label = f" ({source.label})" if source.label else ""
        snapshot = snapshots[source.url]
        state = (
            f"snapshot {snapshot.fetched_at.date().isoformat()}, {len(snapshot.links)} links"
            if snapshot
            else "no snapshot yet"
        )
        kept = ", text retained" if source.retain else ""
        typer.echo(f"- {source.url}{label} — {state}{kept}")


@company_app.command("add-feed")
def company_add_feed(
    name: str = typer.Argument(..., help="Company the feed belongs to."),
    url: str = typer.Argument(..., help="https:// feed URL (RSS/Atom), or a blog index page."),
    index: bool = typer.Option(
        False, "--index", help="The URL is a blog index page, not a feed (RFC-011)."
    ),
) -> None:
    """Attach a company blog/newsroom feed — no person required (RFC-029).

    Posts are organization-attributed to the company and flow into its
    dossier, themes, news, and search exactly like a watched person's
    writing. Find the feed URL first with 'wingman people discover-feed'
    style discovery if you only know the blog page.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "attached")
    try:
        with Storage(config.db_path) as storage:
            anchor, source = attach_company_feed(name, url, storage, index_page=index)
    except IngestError as exc:
        typer.echo(f"add-feed failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Attached to {anchor.company}: {source.url} ({source.kind.value})")
    typer.echo(f'Fetch it with: wingman company fetch "{anchor.company}"')


@company_app.command("feeds")
def company_feeds_list(
    name: str = typer.Argument(..., help="Company whose feeds to list."),
) -> None:
    """List the feeds attached directly to a company (RFC-029)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        feeds = list_company_feeds(name, storage)
    if not feeds:
        typer.echo(f"No company feeds for {name.strip()!r} — 'wingman company add-feed' adds one.")
        return
    for feed in feeds:
        typer.echo(f"{feed.url} ({feed.kind.value})")


@company_app.command("remove-feed")
def company_remove_feed(
    name: str = typer.Argument(..., help="Company the feed belongs to."),
    url: str = typer.Argument(..., help="Feed URL to remove."),
) -> None:
    """Detach a company feed (already-fetched posts are kept)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "removed")
    with Storage(config.db_path) as storage:
        removed = remove_company_feed(name, url, storage)
    if removed:
        typer.echo(f"Removed {url}")
    else:
        typer.echo(f"No such feed on {name.strip()!r}: {url}", err=True)
        raise typer.Exit(code=1)


@company_app.command("fetch")
def company_fetch(
    name: str = typer.Argument(..., help="Company whose feeds to fetch."),
) -> None:
    """Fetch a company's attached feeds now (RFC-009: explicit, enumerable)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "fetched")
    try:
        with Storage(config.db_path) as storage:
            report = fetch_company_feeds(name, config, storage)
    except IngestError as exc:
        typer.echo(f"fetch failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"{report.items} item(s) seen, {report.added} added, "
        f"{report.skipped_duplicates} duplicate(s) skipped."
    )
    for title in report.titles:
        typer.echo(f"  + {title}")
    for failure in report.failed_sources:
        typer.echo(f"  ! {failure}", err=True)


@company_app.command("research")
def company_research_cmd(
    name: str = typer.Argument(..., help="Company to research across its approved sources."),
) -> None:
    """Fetch every approved source once and report what changed (RFC-015).

    One read-only HTTPS GET per approved URL — the pages you named, nothing
    else. Findings are deterministic diffs against the previous snapshot:
    new links (the hiring/announcement signal) and changed page text. A
    failed source keeps its previous snapshot and is reported, never fatal.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "researched")
    try:
        with Storage(config.db_path) as storage:
            report = research_company(name, config, storage)
    except IngestError as exc:
        typer.echo(f"research failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_research_report(report))
    if report.failed and not report.fetched:
        raise typer.Exit(code=1)


@company_app.command("score-board")
def company_score_board_cmd(
    name: str = typer.Argument(..., help="Company whose full current job board to score."),
) -> None:
    """Score EVERY current jobish link on a company's watched pages (#488).

    Unlike overnight scoring, which only ever judges links NEW since the
    last research diff, this sweeps every link a watched page currently
    shows — including postings older than when scoring existed, or older
    than when you started watching this company — against job-criteria.md
    (RFC-035). A deliberate, explicit, more-expensive pass: it is not
    subject to overnight's per-run fetch/judge budgets, only to its own
    sweep cap (reported if it fires). Requires job-criteria.md; seed it
    via 'wingman criteria review' or the job_criteria tool first.
    """
    configure_logging()
    from wingman.application.job_scoring import render_opening_scores, score_full_board

    config = load_config()
    _require_workspace(config, "scored")
    try:
        provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        with Storage(config.db_path) as storage:
            outcome = score_full_board(name, config, storage, provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"score-board failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_opening_scores(name, outcome))


@company_app.command("like")
def company_like(
    names: list[str] = typer.Argument(..., help="Two or more companies, e.g. 'Supersimple' 'Hex'."),
    limit: int = typer.Option(10, "--limit", help="How many companies to show."),
) -> None:
    """'If these companies interest you, look at…' — centroid of the named ones."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "compared")
    try:
        with Storage(config.db_path) as storage:
            report = companies_like(storage, names=names, limit=limit)
    except IngestError as exc:
        typer.echo(f"company like failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not report.companies:
        typer.echo(_NO_COMPANY_SIGNALS)
        return
    typer.echo(f"Closest to {report.reference}:")
    for number, entry in enumerate(report.companies, start=1):
        typer.echo(
            f"{number}. {entry.name}  score {entry.score:.3f}  "
            f"[{entry.people} people, {entry.documents} docs]"
        )


@company_app.command("rename")
def company_rename(
    current: str = typer.Argument(..., help="The company's current name."),
    new_name: str = typer.Argument(..., help="Its corrected name."),
) -> None:
    """Re-key a company's sources, research snapshots, POV card, and watchlist
    memberships to a new name."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "renamed")
    try:
        with Storage(config.db_path) as storage:
            moved, people_moved = rename_company(current, new_name, storage)
    except IngestError as exc:
        typer.echo(f"company rename failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Renamed {current!r} to {new_name!r} "
        f"({moved} source(s), {people_moved} person/people moved)"
    )


@company_app.command("delete")
def company_delete(
    name: str = typer.Argument(..., help="Company to delete."),
) -> None:
    """Delete a company's approved sources, research snapshots, POV card, and
    watchlist memberships. Generated dossier files are untouched — see 'delete-dossier'.
    Not reversible."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "deleted")
    with Storage(config.db_path) as storage:
        removed, cleared = delete_company(name, storage)
    if not removed:
        typer.echo(f"Nothing found for {name!r}.")
        return
    typer.echo(f"Deleted {name!r} (sources, research, POV card, watchlist memberships).")
    if cleared:
        typer.echo(f"Company cleared on: {', '.join(cleared)}")


@company_app.command("delete-dossier")
def company_delete_dossier(
    name: str = typer.Argument(..., help="Company whose generated dossier files to remove."),
) -> None:
    """Delete every generated dossier report for a company. Only removes the rendered
    Markdown under reports/companies/ — sources and research are untouched."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "deleted")
    removed = delete_dossier_reports(name, config)
    if not removed:
        typer.echo(f"No dossier files found for {name!r}.")
        return
    typer.echo(f"Deleted {len(removed)} dossier file(s):")
    for path in removed:
        typer.echo(f"  {path}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Words to find anywhere the workspace knows about."),
    limit: int = typer.Option(12, "--limit", help="Maximum hits across all stores."),
) -> None:
    """Search everything: corpus, people's writing, POV stances, news, research, briefs.

    One ranked list; every hit says what it is, whose it is, when, and where
    it came from. Keyword matching plus a semantic pass that finds documents
    matching by meaning (RFC-022). Egress, stated plainly: with a remote
    embeddings provider configured (voyage), the query text is sent to it;
    with the local 'hashed' provider or no embeddings, nothing leaves the
    machine and the semantic pass says it was skipped.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "searched")
    try:
        with Storage(config.db_path) as storage:
            report = search_workspace(query, storage, config, limit=limit)
    except IngestError as exc:
        typer.echo(f"search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_search_report(report))


@app.command()
def evidence(
    query: str = typer.Argument(..., help="Words or a quoted phrase to search for."),
    limit: int = typer.Option(10, "--limit", help="Maximum number of excerpts."),
) -> None:
    """Search the corpus: 'you have this evidence, here'."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "searched")
    try:
        with Storage(config.db_path) as storage:
            hits = find_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        typer.echo(f"evidence search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not hits:
        typer.echo(f"No corpus evidence found for {query!r}.")
        return
    for number, hit in enumerate(hits, start=1):
        typer.echo(f"{number}. {hit.document.title} [{hit.document.source_type}]")
        typer.echo(f"   {hit.snippet}")
        typer.echo(f"   source: {hit.source_locator} (doc {hit.document.doc_id})")


@feature_app.command("repo")
def feature_repo_cmd(
    repo: str = typer.Argument("", help="owner/name to file feature requests into; omit to show."),
) -> None:
    """Set (or show) the repo that 'wingman feature request' files issues into."""
    configure_logging()
    config = load_config()
    if not repo:
        current = get_feature_repo(config)
        typer.echo(current or "No feature-request repo set — 'wingman feature repo <owner/name>'.")
        return
    try:
        stored = set_feature_repo(config, repo)
    except IngestError as exc:
        typer.echo(f"feature repo failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Feature requests will be filed to {stored}.")


@feature_app.command("request")
def feature_request_cmd(
    title: str = typer.Argument(..., help="One-line title for the issue."),
    body: str = typer.Option("", "--body", help="Issue body (markdown)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """File a feature request as a GitHub issue — previewed and confirmed first.

    Wingman's one gated external write (RFC-025): the exact issue is shown,
    nothing leaves the machine until you confirm, and filing uses your own
    'gh' CLI and auth against the repo set via 'wingman feature repo'.
    """
    configure_logging()
    config = load_config()
    body = stamp_operator(body, config=config)
    typer.echo(render_preview(get_feature_repo(config), title, body))
    if not yes and not typer.confirm("File this issue?", default=False):
        typer.echo("Nothing was filed.")
        raise typer.Exit(code=1)
    try:
        filed = file_feature_request(config, title, body)
    except IngestError as exc:
        typer.echo(f"feature request failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Filed: {filed.url}")


@telemetry_app.command("on")
def telemetry_on() -> None:
    """Turn the local usage journal on (a conscious opt-in; default is off).

    Records CLI invocations, MCP tool calls with arguments and results, and
    harvested transcripts — into the workspace database, nowhere else.
    Wingman ships no transmitter for it (RFC-023).
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "enabled")
    telemetry_set_enabled(config, True)
    typer.echo("Telemetry is ON — local journal in the workspace database only.")


@telemetry_app.command("off")
def telemetry_off() -> None:
    """Turn the usage journal off (recorded events are kept until you delete them)."""
    configure_logging()
    config = load_config()
    telemetry_set_enabled(config, False)
    typer.echo("Telemetry is OFF.")


@telemetry_app.command("status")
def telemetry_status() -> None:
    """Whether the journal is recording, and how much it holds."""
    configure_logging()
    config = load_config()
    state = "ON" if telemetry_is_enabled(config) else "OFF"
    typer.echo(f"Telemetry: {state}  events: {telemetry_count(config)}")


@telemetry_app.command("show")
def telemetry_show(
    limit: int = typer.Option(20, "--limit", help="How many recent events to show."),
) -> None:
    """The most recent events, newest first."""
    configure_logging()
    config = load_config()
    events = telemetry_list(config, limit=limit)
    if not events:
        typer.echo("No telemetry events recorded.")
        return
    for event in events:
        duration = f"  {event['duration_ms']}ms" if event["duration_ms"] is not None else ""
        typer.echo(
            f"{event['ts']}  [{event['surface']}] {event['name']} ({event['outcome']}){duration}"
        )
        payload = json.dumps(event["payload"], ensure_ascii=False)
        typer.echo(f"   {payload[:200]}{'…' if len(payload) > 200 else ''}")


@telemetry_app.command("export")
def telemetry_export(
    out: Path | None = typer.Option(
        None, "--out", help="Destination file (default: reports/telemetry-export.jsonl)."
    ),
) -> None:
    """Dump every event as JSONL for analysis. Local file; nothing is sent."""
    configure_logging()
    config = load_config()
    destination = (out or (config.reports_dir / "telemetry-export.jsonl")).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for event in telemetry_iter(config):
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            count += 1
    typer.echo(f"Exported {count} events to {destination}")


@telemetry_app.command("harvest-transcript")
def telemetry_harvest(
    transcript: Path = typer.Argument(
        ...,
        help="A Claude Code session .jsonl (see ~/.claude/projects/<project>/).",
    ),
) -> None:
    """Import a Claude Code session transcript into the journal (RFC-023).

    Harvests conversation text (user and assistant messages), every Bash
    invocation mentioning wingman, and every wingman MCP tool call — with
    their original timestamps. Requires telemetry to be on.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "harvested")
    try:
        report = harvest_transcript(transcript, config)
    except IngestError as exc:
        typer.echo(f"harvest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Harvested {report.imported} events from {report.source}")
    for kind, count in sorted(report.by_kind.items()):
        typer.echo(f"  {kind}: {count}")
    if report.skipped_lines:
        typer.echo(f"  (skipped {report.skipped_lines} unparseable lines)")


@telemetry_app.command("summary")
def telemetry_summary(
    gap_minutes: float = typer.Option(
        DEFAULT_GAP_MINUTES,
        "--gap-minutes",
        help="Quiet gap, in minutes, that starts a new session.",
    ),
    top: int = typer.Option(
        DEFAULT_TOP_N, "--top", help="How many top commands / dead ends to show."
    ),
) -> None:
    """Sessions, most-frequent commands, and dead ends — from THIS account's own
    local journal only (issue #223). No cross-account reads: see #215.

    A "dead end" is the last command/tool invoked in a session before a long
    quiet gap — a reasonable first proxy for where usage trails off.
    """
    configure_logging()
    config = load_config()
    try:
        summary = summarize_telemetry(config, gap_minutes=gap_minutes, top_n=top)
    except ValueError as exc:
        typer.echo(f"telemetry summary failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_summary(summary))


@mcp_app.command("status")
def mcp_status(
    port: int = typer.Option(8787, help="Port to check for orphaned servers."),
) -> None:
    """Whether the HTTP MCP server ('wingman-mcp --http') is running.

    Also flags an orphaned server holding the port without a pidfile
    (killed before cleanup, or an older build) — the case where restarts
    fail with 'address already in use' while this says not running.
    """
    configure_logging()
    config = load_config()
    typer.echo(server_status(config, port=port))


@mcp_app.command("stop")
def mcp_stop(
    port: int = typer.Option(8787, help="Port whose orphaned servers to stop too."),
) -> None:
    """Stop the HTTP MCP server — and ONLY it (RFC-032).

    Deterministic via the server's pidfile, with one narrow exception: an
    orphaned 'wingman-mcp --http' holding this port without a pidfile is
    stopped too (it blocks every restart and is invisible to status).
    Stdio servers spawned by Claude Desktop / CLI sessions are never
    touched — those belong to their clients.
    """
    configure_logging()
    config = load_config()
    stopped, detail = stop_server(config, port=port)
    typer.echo(detail)
    if not stopped:
        raise typer.Exit(code=1)


def _checked_connector_name(value: str | None) -> str | None:
    """Typer callback for every --connector-name flag.

    The name is rendered into a 'claude mcp add' line whose whole purpose
    is to be pasted into a shell, so a metacharacter in it is a second
    command waiting to run. Rejecting at the flag turns that into an
    ordinary usage error instead of a traceback.
    """
    from wingman.mcp_server import ConnectorNameError, validate_connector_name

    if value is None or value == "":  # '' is the documented "suppress it" value
        return value
    try:
        return validate_connector_name(value)
    except ConnectorNameError as exc:
        raise typer.BadParameter(str(exc)) from exc


@mcp_app.command("url")
def mcp_url(
    host: str = typer.Option("127.0.0.1", help="Bind address the server was started with."),
    port: int = typer.Option(8787, help="Port the server was started with."),
    prefix: str = typer.Option("", help="Native --prefix the server was started with, if any."),
    allowed_host: list[str] = typer.Option(  # noqa: B008 — typer's documented pattern
        [], "--allowed-host", help="Extra --allowed-host flag(s) the server was started with."
    ),
    tunnel_port: int | None = typer.Option(
        None,
        "--tunnel-port",
        help="External port the tunnel front uses, if not the implicit 443 (e.g. a second "
        "instance sharing this Tailscale hostname on its own funnel port). Falls back to "
        "WINGMAN_TUNNEL_PORT.",
    ),
    connector_name: str = typer.Option(
        "wingman",
        "--connector-name",
        callback=_checked_connector_name,
        help="Name for the ready-to-paste 'claude mcp add' command printed alongside each MCP "
        "url (issue #253). Pass '' to suppress it and print bare urls only, as before.",
    ),
) -> None:
    """Print the ready-to-paste MCP connector and web UI URLs (#94, #122).

    Computed straight from the token file and Tailscale auto-detection —
    no need to grep the startup banner out of journalctl, and it works
    whether or not the server is currently running. If the server was
    started with non-default --host/--port/--prefix/--allowed-host/
    --tunnel-port, pass the same flags here so the printed URLs match.
    """
    configure_logging()
    from wingman.mcp_server import _extra_allowed_hosts, _http_token, _tunnel_port, render_urls

    config = load_config()
    token = _http_token(config)
    extra_hosts = _extra_allowed_hosts(allowed_host or None)
    for line in render_urls(
        token,
        extra_hosts,
        host=host,
        port=port,
        prefix=prefix,
        tunnel_port=_tunnel_port(tunnel_port),
        connector_name=connector_name,
    ):
        typer.echo(line)
    if not extra_hosts:
        typer.echo(
            "(no tunnel hostname detected — is Tailscale up? or pass --allowed-host explicitly)"
        )


def _load_registry_or_exit(registry: Path | None) -> tuple[list[Tenant], Path]:
    """Shared registry load for the 'tenant' commands below: resolves the
    registry (explicit --registry, else WINGMAN_TENANT_REGISTRY, else the
    RFC-047-style default) and exits(1) with a clear message if it's
    malformed — never a bare traceback for an operator-facing command.

    A registry this account cannot READ is reported in its own words
    (#411), not as "malformed": these commands belong to the operator, and
    an operator told the file is malformed goes to edit a file that will
    not open for them either.
    """
    from wingman.infrastructure.tenants import (
        TenantRegistryError,
        TenantRegistryUnreadable,
        load_registry,
        tenant_registry_path,
    )

    registry_path = registry or tenant_registry_path()
    try:
        tenants = load_registry(registry_path)
    except TenantRegistryUnreadable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except TenantRegistryError as exc:
        typer.echo(f"tenant registry {registry_path} is malformed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return tenants, registry_path


def _load_tenant_or_exit(slug: str, registry: Path | None) -> tuple[Tenant, Path]:
    """A single named tenant, resolved via '_load_registry_or_exit' above,
    exiting(1) if the slug isn't in the registry."""
    tenants, registry_path = _load_registry_or_exit(registry)
    tenant = next((t for t in tenants if t.slug == slug), None)
    if tenant is None:
        typer.echo(f"No tenant {slug!r} in the registry ({registry_path}).", err=True)
        raise typer.Exit(code=1)
    return tenant, registry_path


@tenant_app.command("url")
def tenant_url_cmd(
    slug: str = typer.Argument(..., help="The tenant's slug in the registry."),
    registry: Path | None = typer.Option(
        None,
        "--registry",
        help="Tenant registry path (default: WINGMAN_TENANT_REGISTRY host setting).",
    ),
    host: str = typer.Option("127.0.0.1", help="Bind address the shared server was started with."),
    port: int = typer.Option(8787, help="Port the shared server was started with."),
    allowed_host: list[str] = typer.Option(  # noqa: B008 — typer's documented pattern
        [],
        "--allowed-host",
        help="Extra --allowed-host flag(s) the shared server was started with.",
    ),
    tunnel_port: int | None = typer.Option(
        None, "--tunnel-port", help="External tunnel port, if not the implicit 443."
    ),
    tunnel_prefix: str = typer.Option(
        "",
        "--tunnel-prefix",
        help="Path prefix a STRIPPING tunnel front mounts this process under (e.g. /shared, "
        "matching WINGMAN_SHARED_TAILSCALE_PATH and wingman-provision-shared.sh's "
        "'tailscale funnel --set-path'). Only changes the printed tunnel URLs -- the shared "
        "process itself runs with no --prefix, so omitting this when the tunnel needs it "
        "prints a URL that 404s at the tunnel, not at wingman.",
    ),
    connector_name: str | None = typer.Option(
        None,
        "--connector-name",
        callback=_checked_connector_name,
        help="Name for the ready-to-paste 'claude mcp add' command printed alongside the MCP "
        "url (issue #253). Default: 'wingman-<slug>'. Pass '' to suppress it and print bare "
        "urls only, as before.",
    ),
) -> None:
    """Print a registered tenant's connector URLs, by slug (#209).

    Operator-assisted URL recovery: no self-service flow, no new
    credential — just a lookup over the tenant registry and that
    tenant's own token file, for whoever already has access to run this.
    See also 'wingman tenant urls' (plural) for every tenant at once.
    """
    configure_logging()
    from wingman.mcp_server import _extra_allowed_hosts, _tunnel_port, render_tenant_urls

    tenant, registry_path = _load_tenant_or_exit(slug, registry)
    extra_hosts = _extra_allowed_hosts(allowed_host or None)
    lines, ok = render_tenant_urls(
        [tenant],
        slug,
        registry_path,
        extra_hosts,
        host=host,
        port=port,
        tunnel_port=_tunnel_port(tunnel_port),
        tunnel_prefix=tunnel_prefix,
        connector_name=f"wingman-{slug}" if connector_name is None else connector_name,
    )
    for line in lines:
        typer.echo(line, err=not ok)
    if not extra_hosts:
        typer.echo(
            "(no tunnel hostname detected — is Tailscale up? or pass --allowed-host explicitly)"
        )
    if not ok:
        raise typer.Exit(code=1)


@tenant_app.command("urls")
def tenant_urls_cmd(
    slug: str | None = typer.Argument(
        None,
        help="A single tenant's slug (same as 'tenant url'), or omit to list every tenant.",
    ),
    registry: Path | None = typer.Option(
        None,
        "--registry",
        help="Tenant registry path (default: WINGMAN_TENANT_REGISTRY host setting).",
    ),
    host: str = typer.Option("127.0.0.1", help="Bind address the shared server was started with."),
    port: int = typer.Option(8787, help="Port the shared server was started with."),
    allowed_host: list[str] = typer.Option(  # noqa: B008 — typer's documented pattern
        [],
        "--allowed-host",
        help="Extra --allowed-host flag(s) the shared server was started with.",
    ),
    tunnel_port: int | None = typer.Option(
        None, "--tunnel-port", help="External tunnel port, if not the implicit 443."
    ),
    tunnel_prefix: str = typer.Option(
        "",
        "--tunnel-prefix",
        help="Path prefix a STRIPPING tunnel front mounts this process under (e.g. /shared, "
        "matching WINGMAN_SHARED_TAILSCALE_PATH and wingman-provision-shared.sh's "
        "'tailscale funnel --set-path'). Only changes the printed tunnel URLs -- the shared "
        "process itself runs with no --prefix, so omitting this when the tunnel needs it "
        "prints a URL that 404s at the tunnel, not at wingman.",
    ),
    connector_name: str | None = typer.Option(
        None,
        "--connector-name",
        callback=_checked_connector_name,
        help="Name for the ready-to-paste 'claude mcp add' command printed alongside each MCP "
        "url (issue #253). Default: 'wingman-<slug>' for one tenant, auto-derived per tenant "
        "for the full roster. Pass '' to suppress it and print bare urls only, as before.",
    ),
) -> None:
    """Print connector URLs for one tenant, or every tenant in the
    registry at once (#209; the "all people" roster view added for
    #235's carve-off follow-up).

    A slug behaves exactly like 'wingman tenant url <slug>' (same URLs,
    same failure and exit code for an unknown slug or a tenant with no
    token minted yet) -- in fact it delegates to the same underlying
    lookup/render logic rather than duplicating it. Omitting the slug
    loads the WHOLE tenant registry and prints every tenant's block,
    labeled by slug, in turn; a tenant with no token minted yet is listed
    as "<slug>: not yet connected (no token minted)" rather than erroring
    or being silently skipped -- the roster view's entire point is a
    complete picture of who's onboarded, not an all-or-nothing lookup.
    """
    configure_logging()
    from wingman.mcp_server import _extra_allowed_hosts, _tunnel_port, render_tenant_urls

    if slug is not None:
        tenant, registry_path = _load_tenant_or_exit(slug, registry)
        tenants = [tenant]
    else:
        tenants, registry_path = _load_registry_or_exit(registry)
    extra_hosts = _extra_allowed_hosts(allowed_host or None)
    if connector_name is None:
        effective_name = f"wingman-{slug}" if slug is not None else "wingman"
    else:
        effective_name = connector_name
    lines, ok = render_tenant_urls(
        tenants,
        slug,
        registry_path,
        extra_hosts,
        host=host,
        port=port,
        tunnel_port=_tunnel_port(tunnel_port),
        tunnel_prefix=tunnel_prefix,
        connector_name=effective_name,
    )
    for line in lines:
        typer.echo(line, err=not ok)
    if not extra_hosts:
        typer.echo(
            "(no tunnel hostname detected — is Tailscale up? or pass --allowed-host explicitly)"
        )
    if not ok:
        raise typer.Exit(code=1)


@tenant_app.command("rotate-token")
def tenant_rotate_token_cmd(
    slug: str = typer.Argument(..., help="The tenant's slug in the registry."),
    registry: Path | None = typer.Option(
        None,
        "--registry",
        help="Tenant registry path (default: WINGMAN_TENANT_REGISTRY host setting).",
    ),
    host: str = typer.Option("127.0.0.1", help="Bind address the shared server was started with."),
    port: int = typer.Option(8787, help="Port the shared server was started with."),
    tunnel_port: int | None = typer.Option(
        None, "--tunnel-port", help="External tunnel port, if not the implicit 443."
    ),
    tunnel_prefix: str = typer.Option(
        "",
        "--tunnel-prefix",
        help="Path prefix a STRIPPING tunnel front mounts this process under (e.g. /shared, "
        "matching WINGMAN_SHARED_TAILSCALE_PATH). Only changes the printed tunnel URLs.",
    ),
    connector_name: str | None = typer.Option(
        None,
        "--connector-name",
        callback=_checked_connector_name,
        help="Name for the ready-to-paste 'claude mcp add' command printed alongside the MCP "
        "url (issue #253). Default: 'wingman-<slug>'. Pass '' to suppress it and print bare "
        "urls only, as before.",
    ),
) -> None:
    """Rotate one tenant's capability token — invalidate and reissue in a
    single step (#210).

    Writing a fresh token to the tenant's own file, by itself,
    invalidates the old one (only the file's CURRENT content is ever
    indexed) and mints the new one — there is no separate "invalidate"
    step. A SIGHUP to the running shared process reloads its in-memory
    index so this takes effect immediately: no restart (which would drop
    every OTHER tenant's session too), and no draining, since the index
    is only ever consulted once per request, at the start.
    """
    configure_logging()
    import secrets as secrets_module

    from wingman.infrastructure.tenant_process import (
        TenantProcessSignalError,
        signal_reload,
    )
    from wingman.mcp_server import _extra_allowed_hosts, _tunnel_port, render_urls

    tenant, registry_path = _load_tenant_or_exit(slug, registry)
    token_path = tenant.token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    new_token = secrets_module.token_urlsafe(24)
    token_path.write_text(new_token + "\n", encoding="utf-8")
    token_path.chmod(0o600)

    try:
        signaled_pid = signal_reload(registry_path)
    except TenantProcessSignalError as exc:
        # The token is already on disk, so this is a partial success and has
        # to read as one — not as a traceback over a command that did most of
        # its job (#329).
        typer.echo(str(exc), err=True)
        signaled_pid = None
    if signaled_pid is not None:
        typer.echo(f"Signaled the running shared process (pid {signaled_pid}) to reload.")
    else:
        typer.echo(
            "No running shared process found for this registry — the new token is written, "
            "but won't take effect until the process starts (or is otherwise reloaded).",
            err=True,
        )

    typer.echo(f"New token for {slug!r}:")
    extra_hosts = _extra_allowed_hosts(None)
    for line in render_urls(
        new_token,
        extra_hosts,
        host=host,
        port=port,
        tunnel_port=_tunnel_port(tunnel_port),
        tunnel_prefix=tunnel_prefix,
        connector_name=f"wingman-{slug}" if connector_name is None else connector_name,
    ):
        typer.echo(line)


@tenant_app.command("form")
def tenant_form_cmd(
    slug: str = typer.Argument(..., help="Tenant slug the form is for, e.g. 'jason'."),
    out: Path = typer.Option(
        Path("."), "--out", help="Directory to write the script and manifest into."
    ),
    print_script: bool = typer.Option(
        False,
        "--print",
        help="Write the script to stdout instead of a file, to copy straight out "
        "of the terminal — the artefact is generated on the server and pasted on "
        "a laptop.",
    ),
) -> None:
    """Emit a Google Form for the interview questions, for one tenant (#287).

    wingman does NOT create the form: it writes an Apps Script you paste
    into script.google.com and run once, so the form is built by YOUR
    Google session and wingman never holds a Google credential.

    Then: send the published URL to the person, and ingest their responses
    when they say they're done.
    """
    from wingman.application.interview_form import (
        form_questions,
        render_apps_script,
        render_manifest,
    )

    configure_logging()
    label = slug.strip()
    if not label:
        typer.echo("tenant form failed: a tenant slug is required.", err=True)
        raise typer.Exit(code=1)
    if print_script:
        # Nothing but the script: this is meant to be piped or selected, and a
        # stray banner line pasted into the editor is a syntax error on line 1.
        typer.echo(render_apps_script(label))
        return
    try:
        out.mkdir(parents=True, exist_ok=True)
        script_path = out / f"{label}-interview-form.gs"
        manifest_path = out / f"{label}-interview-form.json"
        script_path.write_text(render_apps_script(label), encoding="utf-8")
        manifest_path.write_text(render_manifest(label), encoding="utf-8")
    except OSError as exc:
        typer.echo(f"tenant form failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {script_path}")
    typer.echo(f"Wrote {manifest_path}  (routing table for ingest; not needed by the form)")
    typer.echo("")
    typer.echo(f"{len(form_questions())} questions. Next:")
    typer.echo("  1. Get the script onto the machine you'll paste from:")
    typer.echo(f"       scp <this-host>:{script_path} ~/Downloads/")
    typer.echo("     or re-run with --print and copy it out of the terminal.")
    typer.echo("  2. Open https://script.google.com, New project")
    typer.echo("  3. In Code.gs: select ALL, paste over it, save")
    typer.echo("       the myFunction stub must be gone, or it won't save")
    typer.echo("  4. Choose createWingmanInterviewForm in the dropdown (not myFunction)")
    typer.echo("  5. Run, approve the permission prompt (a form in YOUR Drive)")
    typer.echo("  6. Execution log prints the published URL — send that to them")
    typer.echo("  7. When they say they're done, download the responses as CSV and:")
    typer.echo(f"       wingman tenant ingest-form {label} <responses.csv>")
    typer.echo("     which previews, and writes only when you add --apply.")
    typer.echo("")
    typer.echo(f"Paste the .gs file, NOT {Path(__file__).name} or any wingman source.")


@tenant_app.command("ingest-form")
def tenant_ingest_form_cmd(
    slug: str = typer.Argument(..., help="The tenant whose workspace these answers go into."),
    responses: Path = typer.Argument(
        ..., help="The form's response export, downloaded from Google Forms (.csv or .json)."
    ),
    registry: Path | None = typer.Option(
        None,
        "--registry",
        help="Tenant registry path (default: WINGMAN_TENANT_REGISTRY host setting).",
    ),
    manifest: Path | None = typer.Option(
        None,
        "--manifest",
        help="The routing table 'wingman tenant form' wrote, for a form built from an "
        "older or edited question set. Default: this build's own questions.",
    ),
    submission: int | None = typer.Option(
        None,
        "--submission",
        help="Which submission in the file to ingest, 1 (oldest) upwards. Default: the "
        "most recent, which is the person's current answer.",
    ),
    apply_changes: bool = typer.Option(
        False,
        "--apply",
        help="Actually write. Without it this previews and writes nothing.",
    ),
    replace_criteria: bool = typer.Option(
        False,
        "--replace-criteria",
        help="Overwrite an existing job-criteria.md (the current text is kept alongside "
        "it first). Without it, an existing document is left alone.",
    ),
) -> None:
    """Ingest a completed interview form into ONE named tenant's workspace (#287).

    The other half of 'wingman tenant form': you sent somebody the form,
    they filled it in, you downloaded the responses. This routes each
    answer where that question's answer belongs — the five RFC-035 areas
    into one job-criteria.md, nominations into interview captures, anything
    the manifest routes to the answer bank into the bank — through the same
    functions the conversational path uses, so the validation and evidence
    rules are identical. Only the provenance differs: every capture records
    that it arrived in a form you ingested, and says so wherever it is
    shown.

    It PREVIEWS by default and writes only on --apply (or a yes at the
    prompt). You are writing into somebody else's career record, and the
    tenant is named on the command line rather than read out of the file:
    a form link is a bearer URL, so the file cannot say whose answers these
    are — only you can.

    Deliberately a CLI command and not an MCP tool, like 'tenant urls' and
    'tenant answers': the gate is shell access to this box. Run it as the
    account that owns the workspace (e.g. 'sudo -u jason -H wingman tenant
    ingest-form jason ...') so the files it writes belong to them.
    """
    from wingman.application.form_ingest import (
        apply_plan,
        parse_responses,
        plan_ingest,
        questions_from_manifest,
        render_outcome,
        render_plan,
    )
    from wingman.application.job_scoring import criteria_path

    configure_logging()
    tenant, _registry_path = _load_tenant_or_exit(slug, registry)
    config = tenant.config()
    if not config.db_path.exists():
        typer.echo(
            f"tenant ingest-form failed: {slug} has no workspace yet ({config.db_path} "
            "missing). Nothing was read.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        questions = (
            questions_from_manifest(manifest.read_text(encoding="utf-8"))
            if manifest is not None
            else None
        )
        parsed = parse_responses(responses)
        plan = plan_ingest(
            slug,
            parsed,
            source=str(responses),
            questions=questions,
            submission=submission,
        )
    except (IngestError, OSError) as exc:
        typer.echo(f"tenant ingest-form failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_plan(plan, config.data_dir, criteria_path(config).exists()))
    if not plan.writes_anything:
        typer.echo("")
        typer.echo("Nothing in this file routes anywhere — nothing was written.")
        return
    typer.echo("")
    if apply_changes:
        write = True
    elif sys.stdin.isatty():
        # The interactive equivalent of --apply, never a default: the
        # question is whether to write into somebody else's workspace, and
        # a bare Enter must mean no.
        write = typer.confirm(f"Write all of this into {slug}'s workspace?", default=False)
    else:
        write = False
    if not write:
        typer.echo(f"Nothing was written. Re-run with --apply to write into {slug}'s workspace.")
        return
    with Storage(config.db_path) as storage:
        outcome = apply_plan(plan, config, storage, replace_criteria=replace_criteria)
    typer.echo(render_outcome(outcome, slug))
    typer.echo("")
    typer.echo(f"Tell {slug} their answers are in, and that the conversation can pick up there.")


@tenant_app.command("answers")
def tenant_answers_cmd(
    slug: str | None = typer.Argument(
        None, help="One tenant's slug, or omit for every tenant in the registry."
    ),
    registry: Path | None = typer.Option(
        None,
        "--registry",
        help="Tenant registry path (default: WINGMAN_TENANT_REGISTRY host setting).",
    ),
    question_id: str = typer.Option(
        "", "--id", help="Only answers to this question id (as passed to 'wingman qotd set')."
    ),
) -> None:
    """Read back what people answered to the question of the day (#224).

    This is the fan-in, and it is deliberately an OPERATOR act performed
    with operator access — the same access that reads the registry and runs
    'tenant urls' — rather than a wingman feature that hands answers over.
    Each answer was written into that tenant's own workspace and stays
    there; this command opens each database read-only and prints what it
    finds. Nothing is group-writable, no account can read another's
    answers, and RFC-048's share-nothing posture is untouched: the fan-in
    is one operator reading N workspaces, not N accounts writing to one
    place.

    Every person who answered was told, before they answered, that whoever
    runs this machine can read it. That disclosure is what makes running
    this honest; the banner above the output repeats whose words these are.
    """
    from wingman.application.qotd import list_operator_answers, render_answers

    configure_logging()
    if slug is not None:
        tenant, _registry_path = _load_tenant_or_exit(slug, registry)
        tenants = [tenant]
    else:
        tenants, _registry_path = _load_registry_or_exit(registry)
    if not tenants:
        typer.echo(f"no tenants in the registry ({_registry_path}) — nobody to read.")
        return
    wanted = question_id.strip()
    total = 0
    read = 0
    unreadable = 0
    for tenant in tenants:
        # The WHOLE per-tenant body is guarded, not just the query.
        # Tenant.config() reads that tenant's own keys file, and rendering
        # validates rows that tenant's workspace wrote — a failure in
        # either is still one tenant's problem, and aborting here would
        # hide every tenant AFTER it in the registry, silently, from the
        # only command that can read them at all.
        try:
            config = tenant.config()
            if not config.db_path.exists():
                typer.echo(f"{tenant.slug}: no workspace yet ({config.db_path} missing).")
                continue
            with Storage(config.db_path) as storage:
                answers = list_operator_answers(storage)
            if wanted:
                answers = [record for record in answers if record.question_id == wanted]
            rendered = render_answers(answers, who=tenant.slug)
        except Exception as exc:  # noqa: BLE001 — one bad workspace, not the whole roster
            typer.echo(f"{tenant.slug}: could not be read — {exc}", err=True)
            unreadable += 1
            continue
        total += len(answers)
        read += 1
        typer.echo(rendered)
        typer.echo("")
    # Counted over what was actually READ. "across N tenants" where N
    # includes the ones that failed would report a complete picture of a
    # roster this command only partly saw — the same reason 'tenant
    # overnight' prints a completed-cleanly fraction rather than a total.
    typer.echo(
        f"{total} answer{'' if total == 1 else 's'} across {read} of {len(tenants)} tenants."
    )
    if unreadable:
        typer.echo(f"{unreadable} workspace(s) could not be read — see the errors above.")
        raise typer.Exit(code=1)


@tenant_app.command("overnight")
def tenant_overnight_cmd(
    registry: Path | None = typer.Option(
        None,
        "--registry",
        help="Tenant registry path (default: WINGMAN_TENANT_REGISTRY host setting).",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit non-zero if any tenant's run had a failed target. Off by default: a "
        "tenant whose run completed got their night's work, however thin.",
    ),
) -> None:
    """Deep-refresh every tenant in the registry, one after another
    (RFC-048's overnight-loop gap — tenants under the shared process have
    no per-account systemd timer to hang RFC-018's 'wingman overnight' off
    of, unlike shape B).

    Calls the exact same 'overnight_run' the single-workspace 'wingman
    overnight' command does, once per tenant, each with that TENANT's own
    strict Config (Tenant.config() — resolves Anthropic/Voyage keys only
    from that tenant's own workspace file, never falls through to a
    shared tier) — never a shell-out with WINGMAN_DATA_DIR set, which
    would use the full ladder and risk one tenant's run spending a key
    that isn't theirs. One tenant's failure (nothing enrolled, a fetch
    error, a corrupt or locked database, a bad registry entry, whatever)
    is reported by slug and never blocks the rest — mirrors
    'upgrade_all.py's same per-user isolation, and catches as broadly as
    that does, because a roster this command abandons half way through is
    a night's work silently missing for everyone after the break.

    Exits non-zero when a tenant got NOTHING, not when a tenant's run
    completed with some failed targets — that tenant has their digest, and
    it says which parts are thin. Pass --strict to exit non-zero on those
    too.
    """
    configure_logging()
    from wingman.application.focus import overnight_run
    from wingman.infrastructure.storage import Storage

    # The same load, refusal and exit as every other 'tenant' command —
    # shared rather than repeated, so an unreadable registry is diagnosed
    # here too (#411) instead of only where the helper is already called.
    tenants, registry_path = _load_registry_or_exit(registry)
    if not tenants:
        typer.echo(f"no tenants in the registry ({registry_path}) — nothing to do.")
        return

    # Two different things, deliberately counted apart. A tenant whose run
    # could not happen got nothing, and that is this command's failure. A
    # tenant whose run completed with some bad targets got their night's
    # work and a digest naming what is thin. Summing them is what made a
    # good night exit non-zero (#469), the same conflation fixed in
    # 'wingman overnight'.
    unrunnable = 0
    degraded = 0
    for tenant in tenants:
        # Everything from config resolution onwards is guarded, not just the
        # run itself (#387). The isolation this docstring promises is only
        # worth having if it covers the failures it names: a corrupt or
        # locked SQLite file raises sqlite3.DatabaseError, a bad registry
        # entry fails in Tenant.config(), a malformed row raises
        # ValidationError — and NONE of them is an IngestError, which is all
        # this used to catch. Tenants are processed in registry order, so an
        # escaping exception silently cost every tenant positioned after the
        # broken one their whole night's work.
        try:
            config = tenant.config()
            if not config.db_path.exists():
                typer.echo(f"{tenant.slug}: no workspace yet ({config.db_path} missing) — skipped.")
                unrunnable += 1
                continue
            with Storage(config.db_path) as storage:
                report = overnight_run(config, storage)
        except Exception as exc:  # noqa: BLE001 — must never abort the whole roster
            # Reported by slug with the reason, never swallowed: this runs
            # unattended on other people's behalf, so a tenant who got
            # nothing has to be nameable in the morning.
            typer.echo(f"{tenant.slug}: failed — {type(exc).__name__}: {exc}")
            unrunnable += 1
            continue
        typer.echo(
            f"{tenant.slug}: {report.processed} targets, {report.failed} failed, "
            f"{len(report.actions)} actions. Digest: {report.digest_path}"
        )
        if report.failed:
            degraded += 1

    # "completed", not "completed cleanly": a tenant carrying failed targets
    # did complete, and the next line is where that gets said. Claiming
    # cleanliness for a roster with seven bad targets in it is the polished
    # fiction this codebase keeps choosing against.
    typer.echo(f"{len(tenants) - unrunnable}/{len(tenants)} tenants completed.")
    if degraded:
        typer.echo(f"{degraded} of them had failed targets, recorded in that tenant's own digest.")
    if unrunnable or (degraded and strict):
        raise typer.Exit(code=1)


def _echo_tenant_key_locations(
    slug: str, data_dir: Path, funded: bool, rows_by_key: dict[str, list[KeyLocation]]
) -> bool:
    """Print one tenant's strict-ladder table. Returns True if drift was found."""
    typer.echo(f"\ntenant {slug!r}   {'funded' if funded else 'not funded'}")
    typer.echo(f"  workspace: {data_dir}")
    drifted = False
    for short_name, rows in rows_by_key.items():
        winner = next((r for r in rows if r.winner), None)
        blind = [r for r in rows if not r.readable]
        if blind:
            # An unreadable tier outranks or ties everything reported below
            # it, so no winner may be asserted — the failure 'keys where'
            # still has, where a readable lower tier gets announced as USED
            # while the tier above it was never read.
            headline = "CANNOT TELL - a tier could not be read"
        elif winner:
            headline = winner.tier
        elif funded:
            headline = "NO KEY - workspace unset and global unset"
        else:
            headline = "NO KEY - workspace unset, and not funded"
        typer.echo(f"  {short_name:11s} {KNOWN_KEYS[short_name]:22s} -> {headline}")
        others = [r.fingerprint for r in rows if r.present and not r.winner]
        if winner and any(f != winner.fingerprint for f in others):
            drifted = True
        for row in rows:
            if not row.readable:
                typer.echo(
                    f"    ????  {row.tier:28s} UNREADABLE (permission denied)"
                    "  <-- run as the account owning the workspace"
                )
                continue
            if not row.present:
                continue
            mark = "USED " if row.winner else "     "
            note = "" if row.winner else "   <-- unused"
            typer.echo(f"    {mark}{row.tier:28s} {row.fingerprint}{note}")
    return drifted


@tenant_app.command("keys")
def tenant_keys_cmd(
    tenant: str = typer.Option("", "--tenant", help="One tenant by slug. Default: every tenant."),
    all_tenants: bool = typer.Option(
        False, "--all", help="Every tenant in the registry (the default when --tenant is omitted)."
    ),
) -> None:
    """Which key each person is actually using — by fingerprint, never by value.

    Reports the ladder a TENANT's own process really walks
    (strict_provider_keys, RFC-048): their workspace 'keys.env', then the
    box-wide global file only if they are funded. 'wingman keys where'
    reports the single-account ladder instead, which names the operator's
    host file — a file no tenant ever reads.

    Tenant workspaces are readable only by the account that owns them, so
    run this AS that account or the rows come back UNREADABLE:

      sudo -iu wingman-shared bash -c 'export PATH="$HOME/.local/bin:$PATH"; wingman tenant keys'
    """
    configure_logging()
    from wingman.infrastructure.tenants import load_registry, tenant_registry_path

    registry = tenant_registry_path()
    tenants = load_registry(registry)
    if not tenants:
        typer.echo(f"No tenants in {registry}.", err=True)
        raise typer.Exit(code=1)
    if tenant and all_tenants:
        typer.echo("--tenant and --all contradict each other; pass one.", err=True)
        raise typer.Exit(code=2)
    if tenant:
        tenants = [t for t in tenants if t.slug == tenant]
        if not tenants:
            typer.echo(f"No tenant named {tenant!r} in {registry}.", err=True)
            raise typer.Exit(code=1)

    typer.echo("Strict tenant ladder (RFC-048) - first one present wins:")
    typer.echo("  1. workspace file   <workspace>/keys.env   (this person's own key)")
    typer.echo("  2. global file      /etc/wingman/global-secrets.env   (only if funded)")
    typer.echo("\nThe operator host file and process environment are never consulted for a")
    typer.echo("tenant. A fingerprint is prefix...#digest (len N) - same digest, same key.")

    drifted = False
    for entry in tenants:
        rows = describe_tenant_key_locations(entry.data_dir, entry.funded)
        drifted |= _echo_tenant_key_locations(entry.slug, entry.data_dir, entry.funded, rows)

    if drifted:
        typer.echo(
            "\nA tier holds a DIFFERENT key from the one being used. That is how an expired "
            "key hides: fix or remove the stale copy, or it will win somewhere else."
        )


@tenant_app.command("validate")
def tenant_validate_cmd(
    tenant: str = typer.Option("", "--tenant", help="One tenant by slug. Default: every tenant."),
    all_tenants: bool = typer.Option(False, "--all", help="Every tenant in the registry."),
) -> None:
    """Call the provider with the key each person's calls actually spend.

    Differs from 'wingman keys validate' in the ladder it resolves through:
    the strict RFC-048 one. 'keys validate' falls through to this account's
    host file when a tenant has no workspace key, and reports a healthy key
    that tenant never touches — a green check on somebody else's
    credential, which is worse than a red one.

    Costs one cheap, no-completion-tokens call per configured key. Exits
    non-zero if any key fails.
    """
    configure_logging()
    from wingman.infrastructure.tenants import load_registry, tenant_registry_path

    if tenant and all_tenants:
        typer.echo("--tenant and --all contradict each other; pass one.", err=True)
        raise typer.Exit(code=2)
    registry = tenant_registry_path()
    tenants = load_registry(registry)
    if not tenants:
        typer.echo(f"No tenants in {registry}.", err=True)
        raise typer.Exit(code=1)
    if tenant:
        tenants = [t for t in tenants if t.slug == tenant]
        if not tenants:
            typer.echo(f"No tenant named {tenant!r} in {registry}.", err=True)
            raise typer.Exit(code=1)

    failed = False
    for entry in tenants:
        rows = validate_tenant_keys(entry.data_dir, entry.funded)
        label = f"tenant '{entry.slug}'  ({'funded' if entry.funded else 'not funded'})"
        failed |= _echo_validation(label, rows)

    if failed:
        typer.echo(
            "\nA key that fails here is the one that tenant is spending. Replace it with: "
            "wingman keys set <name> --scope workspace --tenant <slug> --value <key>",
            err=True,
        )
        raise typer.Exit(code=1)


@admin_app.command("url")
def admin_url(
    host: str = typer.Option("127.0.0.1", help="Bind address the server was started with."),
    port: int = typer.Option(8787, help="Port the server was started with."),
    allowed_host: list[str] = typer.Option(  # noqa: B008 — typer's documented pattern
        [], "--allowed-host", help="Extra --allowed-host flag(s) the server was started with."
    ),
    tunnel_port: int | None = typer.Option(
        None,
        "--tunnel-port",
        help="External port the tunnel front uses, if not the implicit 443. Falls back to "
        "WINGMAN_TUNNEL_PORT.",
    ),
) -> None:
    """Print the installations page URL (#130).

    The admin token is generated on first use, separate from any instance's
    own mcp-http-token (RFC-017) — add instances to
    '<workspace>/installations.toml' for the page to list anything.
    """
    configure_logging()
    from wingman.admin import admin_token
    from wingman.mcp_server import _extra_allowed_hosts, _tunnel_port

    config = load_config()
    token = admin_token(config)
    extra_hosts = _extra_allowed_hosts(allowed_host or None)
    resolved_tunnel_port = _tunnel_port(tunnel_port)
    typer.echo(f"Installations page: http://{host}:{port}/admin/{token}/installations")
    for tunnel_host in extra_hosts:
        authority = (
            tunnel_host if resolved_tunnel_port is None else f"{tunnel_host}:{resolved_tunnel_port}"
        )
        typer.echo(f"Tunnel installations page: https://{authority}/admin/{token}/installations")
    if not extra_hosts:
        typer.echo(
            "(no tunnel hostname detected — is Tailscale up? or pass --allowed-host explicitly)"
        )


@app.command("qa")
def qa_note(
    question: str = typer.Argument(..., help="The clarifying question, as asked."),
    answer: str = typer.Argument(..., help="Your answer — stored verbatim as evidence."),
    kind: str = typer.Option(
        "achievement", help="Profile item kind: achievement, skill, role, or testimonial."
    ),
) -> None:
    """Save a clarifying Q&A as citable profile evidence (#96, RFC-036).

    The answer becomes a profile item future 'wingman assess' runs can
    cite; re-answering the same question supersedes the old answer.
    """
    configure_logging()
    from wingman.application.qa_capture import capture_qa

    config = load_config()
    try:
        with Storage(config.db_path) as storage:
            report = capture_qa(question, answer, config, storage, kind=kind)
    except IngestError as exc:
        typer.echo(f"qa capture failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{report.outcome}: [{report.kind}] {report.question}")
    typer.echo(f"evidence file: {report.source_path}")


@app.command("interview")
def interview_react(
    subtype: str = typer.Argument(
        ...,
        help="Interview category. Reactions (fetch a target, agree/disagree): "
        "alignment_of_perspective_agree, alignment_of_perspective_disagree. "
        "Nominations (name a target, no fetch): values_pro, values_con "
        "(three people, living or dead, you'd have dinner with / be horrified "
        "to see your name in print alongside — excluding Hitler), "
        "values_fallback_pro, values_fallback_con (companies whose products/"
        "services you're proud to buy / would never buy — use these if "
        "people-naming struggles), mission_alignment_pro, mission_alignment_con "
        "(companies, clubs, or any group aligned for a purpose that you'd be "
        "proud/horrified to be associated with — requires --primary-purpose), "
        "network_admired (a LinkedIn profile URL of someone you admire, "
        "preferably a first-degree connection — pro-only, no con).",
    ),
    target: str = typer.Argument(
        ...,
        help="For a reaction subtype: an https:// URL, or a local PDF/DOCX/MD/TXT file. "
        "For a nomination subtype: the person's or organization's name, or (for "
        "network_admired) their LinkedIn profile URL.",
    ),
    why: str = typer.Argument(..., help="Your reasoning — stored verbatim as evidence."),
    primary_purpose: str = typer.Option(
        "",
        "--primary-purpose",
        help="Required for mission_alignment subtypes only: what you understand this "
        "organization's primary purpose to be, e.g. 'Pepsi sells cola'. Stored as "
        "context alongside the capture, never as evidence.",
    ),
    value_statement: str = typer.Option(
        "",
        "--value-statement",
        help="For values_pro/con, values_fallback_pro/con, and "
        "mission_alignment_pro/con: what this nomination tells you that YOU value, "
        "in your own words — asked after 'why' and before --intensity. Stored "
        "verbatim alongside the capture; a condemnation becomes a positive value "
        "statement. Not asked for alignment_of_perspective or network_admired.",
    ),
    intensity: str = typer.Option(
        "",
        "--intensity",
        help="Required for values_pro/con, values_fallback_pro/con, and "
        "mission_alignment_pro/con: how strongly you feel about this, on top of "
        "pro/con — mild, moderate, or strong. Not accepted for "
        "alignment_of_perspective or network_admired.",
    ),
    company_reason: str = typer.Option(
        "",
        "--company-reason",
        help="Required for values_fallback_pro/con and mission_alignment_pro/con "
        "only: is this about the company itself, its product, or its industry — "
        "company, product, or industry. Stored as a structured field alongside "
        "'why', never replacing it. Not asked for people-naming subtypes.",
    ),
    persona: str = typer.Option(
        "",
        "--persona",
        help="Capture for this persona instead of the active one (or your own work, if "
        "none is active) for just this one call — see 'wingman coach-persona'.",
    ),
    persona_authored: bool = typer.Option(
        False,
        "--persona-authored",
        help="The persona answered this themselves (a verified statement) rather than "
        "you speculating on their behalf (the default, stored as your own inference).",
    ),
) -> None:
    """Capture one interview reaction or nomination as citable profile evidence (docs/PROFILE-BOOTSTRAP-DESIGN.md).

    The target is fetched (reactions) or just named (nominations) only for
    provenance — 'why' is the only thing that becomes evidence, never the
    target's own words. Capturing the same target again under the same
    subtype supersedes the earlier answer. See 'wingman profile list' to
    review what's been captured so far.

    Values' and Mission alignment's protocol (issue #242) — four separate
    steps, never a combined name-and-why pass: name all three pro
    nominees (no why yet), name all three con nominees (no why yet), ask
    why for each con nominee in #2/#1/#3 order, then ask why for each pro
    nominee in the same #2/#1/#3 order (ends on a high note). This is the
    interview module's own protocol — conduct it in that order; it isn't
    enforced by this command itself.

    The value statement (RFC-057, issue #343): for every Values/Mission-
    alignment subtype, ask "what does that tell us you value?" right after
    'why' and before --intensity, and pass the answer verbatim as
    --value-statement. A nomination on its own records a verdict about
    somebody else; this records the positive value behind it, in the
    person's own words, so a later value-axis pass reads a statement of
    the value rather than inferring it from who they condemned. If the
    answer just restates the 'why', re-ask once — pointed at them, not the
    nominee — and never a third time.

    Sentiment intensity and company reason (RFC-049, issue #240 v1): for
    every Values/Mission-alignment subtype, ask --intensity right after
    the value statement — how strongly they feel, on top of the pro/con
    the subtype already carries (mild/moderate/strong; a con nominee is never
    "positive," only how strongly negative). For the company-naming
    subtypes specifically (values_fallback_pro/con, mission_alignment_pro/
    con), also ask --company-reason — is this about the company itself,
    its product, or its industry (company/product/industry) — a
    structured field alongside 'why', never replacing its free text.
    Neither is asked for alignment_of_perspective or network_admired.
    Neither flag is required by this command itself — same
    protocol-not-code-enforcement status as the con-then-pro ordering
    above — but a value that IS given must match the enum, or the capture
    is rejected.

    Coaching mode (docs/COACHING-MODE-DESIGN.md): with an active persona
    ('wingman coach-persona set <name>'), this is scoped to them
    automatically — --persona overrides for just this one call.
    """
    configure_logging()
    from wingman.application.coaching import render_acting_as, resolve_persona
    from wingman.application.interview import capture_interview_reaction, render_interview_reaction

    config = load_config()
    try:
        with Storage(config.db_path) as storage:
            active_persona = resolve_persona(persona, storage, config)
            report = capture_interview_reaction(
                subtype,
                target,
                why,
                config,
                storage,
                primary_purpose=primary_purpose,
                intensity=intensity,
                company_reason=company_reason,
                value_statement=value_statement,
                persona_id=active_persona.persona_id if active_persona is not None else None,
                persona_authored=persona_authored,
            )
    except IngestError as exc:
        typer.echo(f"interview capture failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(render_acting_as(active_persona))
    typer.echo(render_interview_reaction(report))


@app.command()
def perspectives() -> None:
    """Perspectives: the onboarding entry point for a new profile (docs/PROFILE-BOOTSTRAP-DESIGN.md).

    Branches once, up front: existing writing goes to the corpus (same as
    'wingman corpus add'); no writing yet starts a short interview instead
    (same mechanic as 'wingman interview', tier 1 — Alignment of
    perspective). Neither path is required before the other — do one, the
    other, or both, in any order.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "used")
    from wingman.application.interview import capture_interview_reaction, render_interview_reaction

    typer.echo(
        "Perspectives builds your profile two ways: share writing you've already "
        "published, or answer a few quick interview questions instead — either, or "
        "both, in any order."
    )
    choice = typer.prompt(
        "Do you have existing writing to share, or would you rather do a quick interview?",
        type=click.Choice(["content", "interview"], case_sensitive=False),
    )
    with Storage(config.db_path) as storage:
        if choice == "content":
            path_str = typer.prompt("Path to a file, directory, or zip export")
            source_type = typer.prompt("Source type", default="writing")
            try:
                corpus_report = add_to_corpus(Path(path_str), source_type, config, storage)
            except IngestError as exc:
                typer.echo(f"corpus add failed: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            typer.echo(
                f"Added: {corpus_report.added}  Duplicates skipped: {corpus_report.skipped_duplicates}  "
                f"Unsupported: {len(corpus_report.skipped_unsupported)}  "
                f"Failures: {len(corpus_report.failures)}"
            )
            for title in corpus_report.titles:
                typer.echo(f"  + {title}")
            return
        typer.echo(
            "Let's start light: something you already agree or disagree with — a link "
            "or a file, not your own writing — and why."
        )
        while True:
            agree = typer.confirm("Do you agree with it? (no = disagree)")
            subtype = (
                "alignment_of_perspective_agree" if agree else "alignment_of_perspective_disagree"
            )
            target = typer.prompt("A URL or a local file")
            why = typer.prompt("Why, in your own words")
            try:
                interview_report = capture_interview_reaction(subtype, target, why, config, storage)
            except IngestError as exc:
                typer.echo(f"capture failed: {exc}", err=True)
            else:
                typer.echo(render_interview_reaction(interview_report))
            if not typer.confirm("Capture another reaction?", default=True):
                break
        typer.echo(
            "That's tier 1. When you're ready for Values and Mission alignment (naming "
            "people or organizations you align with or against), see 'wingman interview --help'."
        )


@app.command("log")
def log_interaction_cmd(
    person: str = typer.Argument(..., help="Who this happened with."),
    note: str = typer.Argument(..., help="What happened — stored verbatim as evidence."),
) -> None:
    """Record an interaction with a watched person (RFC-037): 'coffee with
    R., discussed the eval harness role'. Deterministic, zero model calls
    — the note becomes citable evidence for future briefs and objective
    reviews. See 'wingman people log <person>' to list past entries.
    """
    configure_logging()
    from wingman.application.relationship import log_interaction

    config = load_config()
    _require_workspace(config, "logged")
    try:
        with Storage(config.db_path) as storage:
            report = log_interaction(person, note, config, storage)
    except IngestError as exc:
        typer.echo(f"log failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Logged for {report.person}: {report.entry.note}")
    typer.echo(f"evidence file: {report.source_path}")


@criteria_app.command("show")
def criteria_show() -> None:
    """Print job-criteria.md — the document overnight scoring judges against."""
    configure_logging()
    from wingman.application.job_scoring import CRITERIA_FILENAME, criteria_path, load_criteria

    config = load_config()
    current = load_criteria(config)
    if current is None:
        typer.echo(
            f"No {CRITERIA_FILENAME} yet — new openings appear unscored. Seed it via the "
            "job_criteria tool's interview in a connected client, or: "
            "wingman criteria save <file>"
        )
        raise typer.Exit(code=1)
    typer.echo(f"# {criteria_path(config)}")
    typer.echo(current)


@criteria_app.command("review")
def criteria_review() -> None:
    """Print the interview packet: current criteria plus the five question areas.

    The same loop seeds a first document and reviews a stale one; the
    digest nudges this when job-criteria.md is a month old (snooze the
    'criteria-review' action key to set your own cadence).
    """
    configure_logging()
    from wingman.application.job_scoring import render_interview

    typer.echo(render_interview(load_config()))


@criteria_app.command("save")
def criteria_save(
    source: Path = typer.Argument(..., help="Markdown file to install as job-criteria.md."),
) -> None:
    """Replace job-criteria.md with a file's contents (takes effect next run)."""
    configure_logging()
    from wingman.application.job_scoring import save_criteria

    config = load_config()
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        typer.echo(f"could not read {source}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        path = save_criteria(config, text)
    except IngestError as exc:
        typer.echo(f"criteria save failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved {path} — overnight runs now score new openings against it.")


@objective_app.command("show")
def objective_show(
    person: str = typer.Argument(..., help="The person the objective is for."),
) -> None:
    """Print the relationship objective for one person: goal, thesis, next move."""
    configure_logging()
    from wingman.application.relationship import load_objective, render_objective

    config = load_config()
    _require_workspace(config, "shown")
    try:
        with Storage(config.db_path) as storage:
            who, objective = load_objective(person, storage)
    except IngestError as exc:
        typer.echo(f"objective show failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if objective is None:
        typer.echo(
            f"No relationship objective for {who.name} yet. Seed it via the "
            "relationship_objective tool's interview in a connected client, or: "
            f'wingman objective save "{who.name}" --goal ... --thesis ... --next-move ...'
        )
        raise typer.Exit(code=1)
    typer.echo(render_objective(who, objective))


@objective_app.command("review")
def objective_review(
    person: str = typer.Argument(..., help="The person to seed or revise an objective for."),
) -> None:
    """Print the interview packet: current objective (if any) plus the three areas.

    The same loop seeds a first objective and revises an existing one.
    """
    configure_logging()
    from wingman.application.relationship import render_interview

    config = load_config()
    _require_workspace(config, "reviewed")
    try:
        with Storage(config.db_path) as storage:
            typer.echo(render_interview(person, storage))
    except IngestError as exc:
        typer.echo(f"objective review failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@objective_app.command("save")
def objective_save(
    person: str = typer.Argument(..., help="The person the objective is for."),
    goal: str = typer.Option(..., help="Why you're investing in this relationship."),
    thesis: str = typer.Option(..., help="What you believe about the path from here."),
    next_move: str = typer.Option(
        ..., "--next-move", help="The next intended action, concrete enough to act on."
    ),
) -> None:
    """Replace the relationship objective for one person — your confirmed words."""
    configure_logging()
    from wingman.application.relationship import render_objective, save_objective

    config = load_config()
    _require_workspace(config, "saved")
    try:
        with Storage(config.db_path) as storage:
            objective = save_objective(person, goal, thesis, next_move, storage)
            who = storage.get_person(objective.person_id)
    except IngestError as exc:
        typer.echo(f"objective save failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved.\n{render_objective(who, objective) if who else ''}")


@heap_app.command("add")
def heap_add(
    items: list[str] = typer.Argument(..., help="One or more URLs or short references to capture."),
    heat: str = typer.Option(
        "warm", "--heat", help="How alive this lead is right now: hot, warm, or cold."
    ),
    note: str = typer.Option("", "--note", help="An optional note to attach to every item."),
) -> None:
    """Capture leads into the heap, unconditionally — no fetching, no sorting yet (#113)."""
    configure_logging()
    from wingman.application.heap import add_to_heap

    config = load_config()
    _require_workspace(config, "captured")
    try:
        with Storage(config.db_path) as storage:
            saved = add_to_heap(items, storage, heat=heat, note=note, config=config)
    except IngestError as exc:
        typer.echo(f"heap add failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Captured {len(saved)} item(s) at heat={heat}.")


@heap_app.command("show")
def heap_show() -> None:
    """List everything in the heap, hottest first — nothing sorted or clustered yet."""
    configure_logging()
    from wingman.application.heap import list_heap, render_heap

    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        typer.echo(render_heap(list_heap(storage)))


@heap_app.command("sort")
def heap_sort_cmd() -> None:
    """Work out what is in the heap and where it should go — hottest first,
    clustered by company, routed only on your confirmation (#113).

    Explicitly invoked, never automatic. It reads the heap, classifies each
    drop by URL shape (deterministic — AGENTS.md forbids a model for
    routing, and "is this a LinkedIn profile or a careers page" has a
    correct answer a model can only get wrong more expensively), groups the
    drops that share a company, and prints what it would do.

    It writes nothing. Every proposal names the command it would run and
    the evidence it keyed on, so the report is something you can disagree
    with rather than a decision already taken. Near-namesake LinkedIn slugs
    are flagged and neither is routed — merging two people is the one
    mistake here that corrupts a record instead of mislabelling it.

    Screenshots are listed, not read. Reading an image belongs to a client
    that has vision — this terminal does not — so the report names them and
    an MCP session fetches them with 'heap_read'. Said plainly rather than
    pretended away: this is one capability the CLI genuinely lacks.
    """
    configure_logging()
    from wingman.application.heap_sort import render_sort, sort_heap

    config = load_config()
    _require_workspace(config, "sorted")
    with Storage(config.db_path) as storage:
        typer.echo(render_sort(sort_heap(storage)))


@heap_app.command("remove")
def heap_remove(
    item_id: str = typer.Argument(..., help="Item id (or a prefix), shown by 'wingman heap show'."),
) -> None:
    """Remove one item from the heap."""
    configure_logging()
    from wingman.application.heap import remove_from_heap

    config = load_config()
    _require_workspace(config, "removed")
    try:
        with Storage(config.db_path) as storage:
            removed = remove_from_heap(item_id, storage)
    except IngestError as exc:
        typer.echo(f"heap remove failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Removed: {removed.item}")


@commentary_app.command("save")
def commentary_save(
    text: str = typer.Argument(..., help="The reading itself — the assistant's words, verbatim."),
    topic: str = typer.Option("", "--topic", help="A short label for listings."),
    model: str = typer.Option(
        "", "--model", help="The model that wrote it (blank records 'unnamed model')."
    ),
    prompt_version: str = typer.Option(
        "",
        "--prompt-version",
        help="The versioned prompt behind it; blank records 'none' (a conversation, or a human).",
    ),
    drawn_from: list[str] = typer.Option(
        [],
        "--from",
        help="Id (or prefix) of a capture the reading was drawn from; repeatable. "
        "An id matching nothing is refused.",
    ),
) -> None:
    """Save a reading OF your material — attributed to its author, never stored as your words.

    Kept out of POV cards, outreach briefs, fit assessments, 'wingman evidence',
    workspace search and the profile pages by construction: its own store (#339).
    """
    configure_logging()
    from wingman.application.commentary import save_commentary

    config = load_config()
    _require_workspace(config, "saved")
    try:
        with Storage(config.db_path) as storage:
            entry = save_commentary(
                text,
                storage,
                topic=topic,
                model=model,
                prompt_version=prompt_version,
                drawn_from=list(drawn_from),
            )
    except IngestError as exc:
        typer.echo(f"commentary save failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved commentary [{entry.entry_id[:8]}] — {entry.attribution()}.")


@commentary_app.command("list")
def commentary_list(
    full: bool = typer.Option(False, "--full", help="Print each reading in full, not clipped."),
) -> None:
    """Every saved reading, newest first, under its authorship."""
    configure_logging()
    from wingman.application.commentary import list_commentary, render_commentary

    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        typer.echo(render_commentary(list_commentary(storage), storage, full=full))


@commentary_app.command("find")
def commentary_find(
    query: str = typer.Argument(..., help="Plain words; every one must appear."),
    limit: int = typer.Option(10, "--limit", help="Maximum entries to return."),
) -> None:
    """Search the commentary store — its own retrieval, since workspace search excludes it."""
    configure_logging()
    from wingman.application.commentary import find_commentary, render_commentary

    config = load_config()
    _require_workspace(config, "searched")
    try:
        with Storage(config.db_path) as storage:
            typer.echo(
                render_commentary(find_commentary(query, storage, limit=limit), storage, full=True)
            )
    except IngestError as exc:
        typer.echo(f"commentary find failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@commentary_app.command("show")
def commentary_show(
    entry_id: str = typer.Argument(..., help="Entry id (or a prefix), from 'commentary list'."),
) -> None:
    """One reading in full, with the material it was drawn from."""
    configure_logging()
    from wingman.application.commentary import get_commentary, render_commentary_entry

    config = load_config()
    _require_workspace(config, "shown")
    try:
        with Storage(config.db_path) as storage:
            typer.echo(render_commentary_entry(get_commentary(entry_id, storage), storage))
    except IngestError as exc:
        typer.echo(f"commentary show failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@commentary_app.command("remove")
def commentary_remove(
    entry_id: str = typer.Argument(..., help="Entry id (or a prefix), from 'commentary list'."),
) -> None:
    """Delete one reading."""
    configure_logging()
    from wingman.application.commentary import remove_commentary

    config = load_config()
    _require_workspace(config, "removed")
    try:
        with Storage(config.db_path) as storage:
            removed = remove_commentary(entry_id, storage)
    except IngestError as exc:
        typer.echo(f"commentary remove failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Removed commentary [{removed.entry_id[:8]}].")


@actions_app.command("mute")
def actions_mute(
    key: str = typer.Argument(..., help="Action key (shown under each digest action)."),
) -> None:
    """Never show this action again — for the recurring items you'll never act on."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "muted")
    try:
        with Storage(config.db_path) as storage:
            mute_action(key, storage)
    except IngestError as exc:
        typer.echo(f"mute failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Muted {key} — it will not appear in future digests.")


@actions_app.command("snooze")
def actions_snooze(
    key: str = typer.Argument(..., help="Action key (shown under each digest action)."),
    days: int = typer.Option(7, "--days", help="Hide it for this many days."),
) -> None:
    """Hide this action for a while — for 'not now', not 'never'."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "snoozed")
    try:
        with Storage(config.db_path) as storage:
            until = snooze_action(key, storage, days=days)
    except IngestError as exc:
        typer.echo(f"snooze failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Snoozed {key} until {until}.")


@actions_app.command("unmute")
def actions_unmute(
    key: str = typer.Argument(..., help="Action key to reinstate."),
) -> None:
    """Clear a mute or snooze — the action reaches digests again."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "unmuted")
    with Storage(config.db_path) as storage:
        cleared = unmute_action(key, storage)
    if cleared:
        typer.echo(f"Unmuted {key}.")
    else:
        typer.echo(f"No verdict recorded for {key}.", err=True)
        raise typer.Exit(code=1)


@actions_app.command("list")
def actions_list() -> None:
    """Every standing triage verdict (expired snoozes clear themselves)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        typer.echo(render_verdicts(storage))


@answers_app.command("save")
def answers_save(
    question: str = typer.Argument(..., help="The question, as asked."),
    answer: str = typer.Argument(..., help="The refined answer (iterate first; save the result)."),
    company: str = typer.Option("", "--company", help="Application context: company."),
    role: str = typer.Option("", "--role", help="Application context: role title."),
    date: str = typer.Option("", "--date", help="Application context: date asked."),
    answer_id: str = typer.Option("", "--id", help="Revise this existing entry (id prefix)."),
) -> None:
    """Bank a refined answer with its application context (RFC-030)."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "saved")
    try:
        with Storage(config.db_path) as storage:
            record, created = save_answer(
                question,
                answer,
                storage,
                company=company,
                role_title=role,
                asked_on=date,
                answer_id=answer_id,
            )
    except IngestError as exc:
        typer.echo(f"answers save failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{'Saved' if created else 'Revised'} [{record.answer_id[:8]}] — {record.context}")


@answers_app.command("find")
def answers_find(
    question: str = typer.Argument(..., help="A question to match against banked answers."),
) -> None:
    """Surface previously refined answers similar to a question — the recall step."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "searched")
    with Storage(config.db_path) as storage:
        hits = find_similar(question, storage)
    if not hits:
        typer.echo("No similar answers banked yet.")
        return
    for record, snippet in hits:
        typer.echo(render_answer(record))
        typer.echo(f"  match: {snippet}")
        typer.echo("")


@answers_app.command("list")
def answers_list() -> None:
    """Every banked answer with its id and context."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        typer.echo(render_answer_listing(storage.list_answers()))


@answers_app.command("show")
def answers_show(
    answer_id: str = typer.Argument(..., help="Answer id (any unambiguous prefix)."),
) -> None:
    """One banked answer in full."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "shown")
    try:
        with Storage(config.db_path) as storage:
            typer.echo(render_answer(find_answer(answer_id, storage)))
    except IngestError as exc:
        typer.echo(f"answers show failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@answers_app.command("rm")
def answers_rm(
    answer_id: str = typer.Argument(..., help="Answer id (any unambiguous prefix) to delete."),
) -> None:
    """Delete one banked answer."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "removed")
    try:
        with Storage(config.db_path) as storage:
            record = remove_answer(answer_id, storage)
    except IngestError as exc:
        typer.echo(f"answers rm failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Removed [{record.answer_id[:8]}] {record.question[:60]!r}")


@profile_app.command("list")
def profile_list() -> None:
    """Every profile item with its id: active by kind, then unresolved conflicts."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "listed")
    with Storage(config.db_path) as storage:
        typer.echo(render_profile_listing(storage.list_profile_items()))


@profile_app.command("rm")
def profile_rm(
    item_id: str = typer.Argument(..., help="Item id (any unambiguous prefix) to delete."),
) -> None:
    """Delete one item — active or conflict — and re-render career.md/json."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "removed")
    try:
        with Storage(config.db_path) as storage:
            item = remove_item(item_id, config, storage)
    except IngestError as exc:
        typer.echo(f"profile rm failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Removed {item.kind.value} {item.name!r} ({item.item_id[:8]}).")


@profile_app.command("resolve")
def profile_resolve(
    item_id: str = typer.Argument(
        ..., help="Id (any unambiguous prefix) of the item to KEEP; its rivals are dropped."
    ),
) -> None:
    """Settle a conflict: keep this item, drop every rival with the same name."""
    configure_logging()
    config = load_config()
    _require_workspace(config, "resolved")
    try:
        with Storage(config.db_path) as storage:
            winner, rivals = resolve_item(item_id, config, storage)
    except IngestError as exc:
        typer.echo(f"profile resolve failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Kept {winner.kind.value} {winner.name!r} ({winner.item_id[:8]}).")
    for rival in rivals:
        typer.echo(f"  dropped {rival.item_id[:8]} — {rival.detail or rival.name}")
    if not rivals:
        typer.echo("  (nothing conflicted with it)")


@profile_app.command("rekind")
def profile_rekind(
    item_id: str = typer.Argument(..., help="Item id (any unambiguous prefix) to move."),
    kind: str = typer.Argument(..., help="achievement, skill, role, or testimonial."),
) -> None:
    """Move an item to a different kind, keeping its id, evidence and source.

    For something captured under the wrong heading — a screening answer
    filed as a skill, an employment history filed as an achievement. The
    claim and its evidence are fine; only the label is wrong, and
    delete-and-recapture would throw away the lineage that makes the item
    citable in the first place.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "re-kinded")
    try:
        with Storage(config.db_path) as storage:
            moved, was = rekind_item(item_id, kind, config, storage)
    except IngestError as exc:
        typer.echo(f"profile rekind failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Moved {moved.name!r} ({moved.item_id[:8]}) from {was.value} to {moved.kind.value}."
    )


@profile_app.command("rename")
def profile_rename(
    item_id: str = typer.Argument(..., help="Item id (any unambiguous prefix) to rename."),
    name: str = typer.Argument(..., help="The new name."),
) -> None:
    """Rename an item, keeping its id, kind, evidence and source record.

    For a claim captured under the wrong name — qa_capture stores the
    question, so a real achievement can be called 'Have you shipped an
    AI/LLM product?'. The claim and its evidence are fine; only the label
    is wrong, and delete-and-recapture would throw away the lineage.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "renamed")
    try:
        with Storage(config.db_path) as storage:
            renamed, was = rename_item(item_id, name, config, storage)
    except IngestError as exc:
        typer.echo(f"profile rename failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Renamed {was!r} to {renamed.name!r} ({renamed.item_id[:8]}).")


@profile_app.command("amend")
def profile_amend(
    item_id: str = typer.Argument(..., help="Interview capture id (any unambiguous prefix)."),
    why: str = typer.Option("", "--why", help="The revised answer, in your own words."),
    intensity: str = typer.Option(
        "", "--intensity", help="mild, moderate or strong (RFC-050 sentiment scale)."
    ),
    company_reason: str = typer.Option(
        "", "--company-reason", help="company, product or industry."
    ),
    value_statement: str = typer.Option(
        "", "--value-statement", help="What this nomination tells you that you value."
    ),
) -> None:
    """Revise an interview capture's own answer, keeping its id and lineage.

    For "that came out wrong": the person IS the source of an interview
    answer, so revising it is theirs to do — and for adding the intensity
    a capture ingested from the interview form never collected, without
    which it carries no weight in 'wingman values'. The previous answer is
    kept as a revision rather than erased, the item id, source record and
    provenance are untouched, and the item then reads '(revised)' in
    'wingman profile list'.

    Only interview captures. An achievement, skill, role or testimonial
    quotes a document somebody wrote — fix the document and re-ingest it
    under the same filename, which supersedes the old claim by itself.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "amended")
    try:
        with Storage(config.db_path) as storage:
            amended, before = amend_item(
                item_id,
                config,
                storage,
                why=why,
                intensity=intensity,
                company_reason=company_reason,
                value_statement=value_statement,
            )
    except IngestError as exc:
        typer.echo(f"profile amend failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Amended {amended.name!r} ({amended.item_id[:8]}).")
    typer.echo(f"  {describe_amendment(amended, before)}")
    typer.echo(
        f"  The previous answer is kept as revision {len(amended.revisions)}; the item id, "
        "source record and provenance are unchanged."
    )
    typer.echo("  Re-run 'wingman values --refresh' if this changes your value profile.")


@profile_app.command("correct")
def profile_correct(
    item_id: str = typer.Argument(
        ..., help="Achievement/skill/role/testimonial id (any unambiguous prefix)."
    ),
    old_text: str = typer.Argument(..., help="The exact evidence text as currently stored."),
    new_text: str = typer.Argument(..., help="The corrected text."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Fix a transcription/mishearing error in quoted evidence, in place (#487).

    The gap 'amend' deliberately leaves for achievements, skills, roles and
    testimonials: their evidence is quoted verbatim from a document, so
    amend refuses them outright rather than let anyone edit a quote no
    document makes. correct is for the narrower case where the evidence WAS
    captured correctly but arrived with a transcription or voice-dictation
    error — a misheard name is the common case.

    old_text must match the stored evidence verbatim; run 'wingman profile
    list' first if you're not sure of the exact wording. The previous
    wording is kept as a revision, the item id and provenance are
    unchanged, and the item then reads '(corrected)'.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "corrected")
    try:
        with Storage(config.db_path) as storage:
            preview = preview_correction(item_id, old_text, new_text, storage)
            if not yes:
                typer.echo(preview)
                typer.confirm("Apply this correction?", abort=True)
            corrected, before = correct_item(item_id, old_text, new_text, config, storage)
    except IngestError as exc:
        typer.echo(f"profile correct failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Corrected {corrected.name!r} ({corrected.item_id[:8]}).")
    typer.echo(f"  {describe_correction(corrected, before)}")
    typer.echo(
        f"  The previous wording is kept as revision {len(corrected.revisions)}; the item id "
        "and provenance are unchanged."
    )


@profile_app.command("clear")
def profile_clear(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Delete EVERY profile item, for a clean re-ingest of your source of truth.

    Reversible only via 'wingman restore' from a backup — take one first.
    Stored job assessments cite item ids that stop existing; re-run
    'wingman assess' for anything that still matters.
    """
    configure_logging()
    config = load_config()
    _require_workspace(config, "cleared")
    with Storage(config.db_path) as storage:
        count = storage.count_profile_items()
        if count == 0:
            typer.echo("The profile is already empty.")
            return
        if not yes:
            typer.confirm(
                f"Delete all {count} profile items? ('wingman backup' first is wise)",
                abort=True,
            )
        removed = clear_profile(config, storage)
    typer.echo(f"Removed {removed} profile items. Re-ingest with 'wingman ingest <resume>'.")


def run() -> None:
    """Console-script entry: the CLI, wrapped in the opt-in usage journal."""
    argv = sys.argv[1:]
    started = time.monotonic()
    code = 0
    try:
        app()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        raise
    except Exception:
        code = 1
        raise
    finally:
        try:
            record_event(
                load_config(),
                "cli",
                argv[0] if argv else "(no-command)",
                {"argv": redact_argv(argv)},
                outcome="ok" if code == 0 else f"exit-{code}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:  # noqa: BLE001, S110 — the journal never breaks the CLI
            pass


if __name__ == "__main__":
    run()
