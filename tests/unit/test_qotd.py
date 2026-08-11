"""The operator's question of the day, answered in the tenant's own
workspace (issue #224's second half, RFC-067).

Five properties carry the feature, and each is here as a test that fails
without the change:

- a question reaches people the way a message does — as an entry in
  `completeness.next_actions`, on every surface that renders one;
- addressing works, and fails CLOSED: a question addressed to one slug
  does not reach another tenant, or an account with no slug at all;
- an answer lands in the answering tenant's own workspace and nowhere
  else — not in the other tenant's database, and not in shared space;
- the answer is what makes the question answered, so it stands until then
  and stops afterwards;
- an unreadable or malformed question file costs everybody the question
  and nobody their status (the precedent RFC-065 set).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wingman.application.completeness import (
    OPERATOR_QUESTION_ATTRIBUTION,
    compute_completeness,
    next_actions,
)
from wingman.application.ingest import IngestError
from wingman.application.qotd import (
    list_operator_answers,
    pending_question,
    save_operator_answer,
)
from wingman.infrastructure.broadcast import (
    account_slug,
    is_addressed_to,
    read_operator_question,
)
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.completeness import render_completeness_markdown
from wingman.reporting.completeness_html import render_completeness_html

not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="file modes do not restrict root",
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    return tmp_path


@pytest.fixture
def qotd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The shared /etc/wingman/qotd.json, relocated for the test.

    A monkeypatched module constant rather than an environment variable,
    for the same reason production has no env override: what the operator
    asked is a property of the box, not of a process.
    """
    path = tmp_path / "etc" / "qotd.json"
    monkeypatch.setattr("wingman.infrastructure.broadcast.OPERATOR_QUESTION_PATH", path)
    return path


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty tenant registry, relocated. Individual tests fill it in."""
    path = tmp_path / "etc" / "tenants.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr("wingman.infrastructure.tenants.DEFAULT_REGISTRY_PATH", path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return path


def _ask(path: Path, **fields: str) -> None:
    payload = {"id": "q1", "question": "What is slowing you down this week?", **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _register(path: Path, tenants: dict[str, Path]) -> None:
    blocks = [f'[[tenant]]\nslug = "{slug}"\ndata_dir = "{d}"\n' for slug, d in tenants.items()]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _tenant_workspace(root: Path, slug: str) -> Config:
    """A second, fully separate workspace on the same box."""
    data_dir = root / slug
    config = Config(data_dir=data_dir, data_dir_source=f"test ({slug})")
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    Storage(config.db_path).close()
    return config


def _report(config: Config):  # type: ignore[no-untyped-def]
    with Storage(config.db_path) as storage:
        return compute_completeness(storage, config)


# --------------------------------------------------------------------------
# It arrives the way the message does, and it says who can read the answer.
# --------------------------------------------------------------------------


def test_a_question_becomes_a_next_action(workspace: Path, qotd: Path) -> None:
    """The delivery argument RFC-065 made for the message holds for the
    question: a surface nobody visits is a question nobody answers, and
    `next_actions` is the one list the completeness tool's own protocol
    obliges the assistant to answer from."""
    _ask(qotd, why="Deciding what to fix next.")

    actions = next_actions(_report(load_config()))

    asked = next(a for a in actions if a.origin == "operator_question")
    assert asked.title == "What is slowing you down this week?"
    assert asked.why == "Deciding what to fix next."
    assert "question of the day" in asked.how


def test_a_question_outranks_every_computed_action(workspace: Path, qotd: Path) -> None:
    """Same reasoning as the message's own lead: an operator knows something
    the workspace cannot, and a question buried under six computed steps is
    a question that goes unanswered."""
    _ask(qotd)

    actions = next_actions(_report(load_config()))

    assert actions[0].origin == "operator_question"
    assert actions[1].title == "Set your job criteria"


def test_a_message_still_leads_a_question(workspace: Path, qotd: Path, tmp_path: Path) -> None:
    """A message asks somebody to act and is shown ONCE; a question only asks
    them to speak and stands until answered. Ranking the question first
    would spend the message's single delivery on the less urgent of the
    two."""
    from wingman.infrastructure.broadcast import OPERATOR_MESSAGE_PATH, write_operator_message
    from wingman.infrastructure.broadcast import OperatorMessage as Message

    motd = tmp_path / "etc" / "motd.json"
    write_operator_message(Message(id="m1", action="Re-ingest your CV"), motd)
    assert OPERATOR_MESSAGE_PATH != motd  # the real one is untouched
    _ask(qotd)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("wingman.infrastructure.broadcast.OPERATOR_MESSAGE_PATH", motd)
        actions = next_actions(_report(load_config()))

    assert [a.origin for a in actions[:2]] == ["operator", "operator_question"]


@pytest.mark.parametrize("surface", ["markdown", "html", "setup_guide"])
def test_every_surface_says_the_operator_can_read_the_answer(
    workspace: Path, qotd: Path, surface: str
) -> None:
    """The disclosure decision, enforced rather than documented: somebody who
    does not know who reads their answer is answering a different question
    than the one they were asked, and consent that arrives after the words
    are stored is not consent. It is one constant, shown by all three
    renderers, so it cannot say something slightly different in one of
    them."""
    from wingman.application.setup_guide import render_setup_guide

    _ask(qotd)
    config = load_config()
    report = _report(config)

    rendered = {
        "markdown": lambda: render_completeness_markdown(report),
        "html": lambda: render_completeness_html(report),
        "setup_guide": lambda: render_setup_guide(config, report),
    }[surface]()

    assert "What is slowing you down this week?" in rendered
    assert "they can read it" in rendered
    assert OPERATOR_QUESTION_ATTRIBUTION in rendered


def test_the_html_surface_labels_it_a_question_before_it_is_read(
    workspace: Path, qotd: Path
) -> None:
    """A page is scanned, not read top to bottom, so 'this is a question from
    a person' goes in front of the text rather than only underneath it."""
    _ask(qotd)

    html = render_completeness_html(_report(load_config()))

    badge = "[a question from the operator of this machine]"
    assert badge in html
    assert html.index(badge) < html.index("What is slowing you down this week?")


def test_a_question_with_no_reason_does_not_get_one_invented(workspace: Path, qotd: Path) -> None:
    """Invariant 9: wingman does not compose somebody else's reasoning."""
    _ask(qotd)

    asked = next_actions(_report(load_config()))[0]

    assert asked.why == "No reason was given beyond the question itself."


# --------------------------------------------------------------------------
# Addressing: one slug, or all — and it fails closed.
# --------------------------------------------------------------------------


def test_a_question_for_one_slug_does_not_reach_another_tenant(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path
) -> None:
    """The addressing requirement, stated as the failure it prevents: asking
    one person something and having everybody on the box read it is a
    privacy breach, not a delivery bug."""
    alice = _tenant_workspace(tmp_path / "tenants", "alice")
    bob = _tenant_workspace(tmp_path / "tenants", "bob")
    _register(registry, {"alice": alice.data_dir, "bob": bob.data_dir})
    _ask(qotd, to="alice")

    with Storage(alice.db_path) as storage:
        assert pending_question(alice, storage) is not None
    with Storage(bob.db_path) as storage:
        assert pending_question(bob, storage) is None

    assert [a.origin for a in next_actions(_report(bob))].count("operator_question") == 0


def test_an_account_with_no_slug_gets_nothing_addressed_to_a_slug(
    workspace: Path, qotd: Path, registry: Path
) -> None:
    """Fails CLOSED. An operator can re-send a question that reached nobody;
    they cannot unsend one that reached the wrong person. So an account this
    box cannot identify — no registry, a malformed one, a solo install —
    receives what is addressed to everybody and nothing addressed to a
    slug."""
    _ask(qotd, to="alice")
    config = load_config()

    assert account_slug(config) is None
    assert is_addressed_to("alice", config) is False
    assert is_addressed_to("all", config) is True
    with Storage(config.db_path) as storage:
        assert pending_question(config, storage) is None


def test_a_malformed_registry_does_not_broaden_delivery(
    workspace: Path, qotd: Path, registry: Path
) -> None:
    """The most dangerous plausible bug: treating an unreadable registry as
    'cannot tell, so show everyone'."""
    registry.write_text("this is not toml [[[", encoding="utf-8")
    _ask(qotd, to="alice")
    config = load_config()

    with Storage(config.db_path) as storage:
        assert pending_question(config, storage) is None


def test_two_slugs_differing_only_in_case_are_two_different_people(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path
) -> None:
    """The one way addressing could fail OPEN. The registry's own duplicate
    check is case-sensitive, so 'alice' and 'Alice' can be two accounts;
    matching case-insensitively would hand one person's question to both."""
    lower = _tenant_workspace(tmp_path / "tenants", "alice")
    upper = _tenant_workspace(tmp_path / "tenants", "Alice")
    _register(registry, {"alice": lower.data_dir, "Alice": upper.data_dir})
    _ask(qotd, to="alice")

    assert is_addressed_to("alice", lower) is True
    assert is_addressed_to("alice", upper) is False
    with Storage(upper.db_path) as storage:
        assert pending_question(upper, storage) is None


def test_a_registry_entry_with_no_home_directory_costs_the_question_not_the_status(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path
) -> None:
    """A decommissioned account left in the registry as '~olduser/...' makes
    expanduser() raise RuntimeError — which is neither OSError nor
    TenantRegistryError, so a narrower catch let it escape all the way out
    of `completeness` and take out every account's status for a stale line
    in somebody else's file. It degrades to no-slug instead: the targeted
    question reaches nobody (fail closed), and everything else still
    works."""
    alice = _tenant_workspace(tmp_path / "tenants", "alice")
    registry.write_text(
        '[[tenant]]\nslug = "ghost"\ndata_dir = "~nosuchuser12345/w"\n\n'
        f'[[tenant]]\nslug = "alice"\ndata_dir = "{alice.data_dir}"\n',
        encoding="utf-8",
    )
    _ask(qotd, to="alice")

    assert account_slug(alice) is None  # must not raise
    with Storage(alice.db_path) as storage:
        assert pending_question(alice, storage) is None
    assert "Set your job criteria" in render_completeness_markdown(_report(alice))


def test_an_unaddressed_question_reaches_everybody(
    workspace: Path, qotd: Path, registry: Path
) -> None:
    """A file written before addressing existed has no 'to', and must keep
    working: omitting the addressee means everybody, which is the only
    reading that does not silently stop delivering."""
    _ask(qotd)
    config = load_config()

    question = read_operator_question()
    assert question is not None and question.to == "all"
    with Storage(config.db_path) as storage:
        assert pending_question(config, storage) is not None


def test_addressing_is_checked_before_the_message_seen_marker(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path
) -> None:
    """A message for somebody else must not be recorded as seen here — the
    account would then miss it if the operator re-addressed it to
    everybody."""
    from wingman.infrastructure.broadcast import (
        OperatorMessage,
        last_seen_id,
        pending_operator_message,
        write_operator_message,
    )

    motd = tmp_path / "etc" / "motd.json"
    write_operator_message(OperatorMessage(id="m1", action="Rotate your key", to="alice"), motd)
    config = load_config()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("wingman.infrastructure.broadcast.OPERATOR_MESSAGE_PATH", motd)
        assert pending_operator_message(config) is None
        assert last_seen_id(config) == ""


# --------------------------------------------------------------------------
# The answer lands in the answering workspace, and nowhere else.
# --------------------------------------------------------------------------


def test_an_answer_lands_in_the_answering_tenants_workspace_and_nowhere_else(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path
) -> None:
    """The correction that unblocked this feature: no group-writable
    directory, no shared write, no first-of-its-kind shared state. The
    answer is in the answerer's own database; the other tenant's database
    and the shared question file are untouched."""
    alice = _tenant_workspace(tmp_path / "tenants", "alice")
    bob = _tenant_workspace(tmp_path / "tenants", "bob")
    _register(registry, {"alice": alice.data_dir, "bob": bob.data_dir})
    _ask(qotd)
    before = qotd.read_text(encoding="utf-8")

    with Storage(alice.db_path) as storage:
        saved = save_operator_answer("Interviews I cannot schedule.", alice, storage)

    with Storage(alice.db_path) as storage:
        mine = list_operator_answers(storage)
    with Storage(bob.db_path) as storage:
        theirs = list_operator_answers(storage)

    assert [record.answer for record in mine] == ["Interviews I cannot schedule."]
    assert theirs == []
    assert saved.question == "What is slowing you down this week?"
    assert qotd.read_text(encoding="utf-8") == before, "the shared file is never written back to"
    assert not (qotd.parent / "qotd-answers").exists(), "no shared answer directory, ever"


def test_the_answer_is_stored_verbatim(workspace: Path, qotd: Path) -> None:
    """BP-06's whole point: a paraphrase filed under somebody's name is worse
    here than anywhere else, because a third party reads the result."""
    _ask(qotd)
    config = load_config()
    words = "Honestly?  The  admin.   Not the interviews."

    with Storage(config.db_path) as storage:
        record = save_operator_answer(words, config, storage)

    assert record.answer == words.strip()


def test_an_answer_carries_the_question_that_produced_it(workspace: Path, qotd: Path) -> None:
    """The operator's file will have moved on to the next question long
    before anybody reads this answer, and an answer without its question is
    a sentence with its meaning removed."""
    _ask(qotd)
    config = load_config()

    with Storage(config.db_path) as storage:
        record = save_operator_answer("The admin.", config, storage)

    _ask(qotd, id="q2", question="Something else entirely?")
    with Storage(config.db_path) as storage:
        stored = list_operator_answers(storage)[0]

    assert stored.question == "What is slowing you down this week?"
    assert stored.question_id == record.question_id == "q1"


def test_answering_a_question_addressed_to_somebody_else_is_refused(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path
) -> None:
    alice = _tenant_workspace(tmp_path / "tenants", "alice")
    bob = _tenant_workspace(tmp_path / "tenants", "bob")
    _register(registry, {"alice": alice.data_dir, "bob": bob.data_dir})
    _ask(qotd, to="alice")

    with Storage(bob.db_path) as storage, pytest.raises(IngestError, match="somebody else"):
        save_operator_answer("Not my question.", bob, storage)


def test_answering_when_nothing_was_asked_is_refused(workspace: Path, qotd: Path) -> None:
    """An answer filed against no question is a record nobody can read."""
    config = load_config()

    with Storage(config.db_path) as storage, pytest.raises(IngestError, match="no question"):
        save_operator_answer("Anything.", config, storage)


def test_an_empty_answer_saves_nothing(workspace: Path, qotd: Path) -> None:
    _ask(qotd)
    config = load_config()

    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            save_operator_answer("   ", config, storage)
        assert list_operator_answers(storage) == []


# --------------------------------------------------------------------------
# The answer IS the acknowledgement.
# --------------------------------------------------------------------------


def test_a_question_stands_until_it_is_answered(workspace: Path, qotd: Path) -> None:
    """Unlike a message, which is shown once. A question asked into the void
    is worse than one asked twice, and deriving 'answered' from the stored
    answer means there is no marker file and no fourth delivery call site to
    forget to write."""
    _ask(qotd)
    config = load_config()

    for _ in range(3):
        assert next_actions(_report(config))[0].origin == "operator_question"

    with Storage(config.db_path) as storage:
        save_operator_answer("The admin.", config, storage)

    assert all(a.origin != "operator_question" for a in next_actions(_report(config)))
    assert not (config.data_dir / "operator-question-seen").exists()


def test_a_new_id_asks_an_account_that_answered_the_last_one(workspace: Path, qotd: Path) -> None:
    """The id is what makes a question NEW — the same rule the message uses,
    so an operator fixing a typo does not re-ask everybody."""
    _ask(qotd)
    config = load_config()
    with Storage(config.db_path) as storage:
        save_operator_answer("The admin.", config, storage)

    _ask(qotd, question="What is slowing you down this week??")
    with Storage(config.db_path) as storage:
        assert pending_question(config, storage) is None

    _ask(qotd, id="q2", question="What would you drop if you could?")
    with Storage(config.db_path) as storage:
        assert pending_question(config, storage) is not None


def test_a_second_answer_supersedes_nothing(workspace: Path, qotd: Path) -> None:
    """An edited answer and a changed mind are different things, and only the
    person who wrote them can say which this is — so both are kept, in
    order, and nothing is deleted on their behalf."""
    _ask(qotd)
    config = load_config()

    with Storage(config.db_path) as storage:
        save_operator_answer("The admin.", config, storage)
        save_operator_answer("Actually: the waiting.", config, storage)
        answers = list_operator_answers(storage)

    assert [record.answer for record in answers] == ["The admin.", "Actually: the waiting."]


# --------------------------------------------------------------------------
# An answer is not evidence.
# --------------------------------------------------------------------------


def test_an_answer_is_not_reachable_from_any_evidence_path(workspace: Path, qotd: Path) -> None:
    """The sharpest call in the ticket, enforced structurally rather than by
    every existing filter remembering a new kind: the words are the person's
    own, but the question was authored by whoever will read the reply, so a
    leading question would otherwise be a way of writing sentences into
    somebody else's career record. Code that does not name `operator_answers`
    cannot read from it."""
    from wingman.application.search import search_workspace

    _ask(qotd, question="What are you proudest of shipping?")
    config = load_config()

    with Storage(config.db_path) as storage:
        save_operator_answer("I rebuilt the billing pipeline single-handed.", config, storage)

        assert storage.list_profile_items() == []
        assert storage.list_answers() == []
        assert storage.list_corpus_documents() == []
        assert search_workspace("billing pipeline", storage, config).hits == []
        tables = {
            row[0]
            for row in storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "operator_answers" in tables
        assert not any(name.startswith("operator_answers_fts") for name in tables)


# --------------------------------------------------------------------------
# A broken shared file costs the question, never the status.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("not json at all", id="not-json"),
        pytest.param('["a", "list"]', id="json-but-not-an-object"),
        pytest.param('{"question": "Why?"}', id="no-id"),
        pytest.param('{"id": "q1"}', id="no-question"),
        pytest.param('{"id": "q1", "question": ""}', id="blank-question"),
        pytest.param('{"id": "q1", "question": 42}', id="question-not-a-string"),
        pytest.param('{"id": "q1", "question": "Why?", "to": null}', id="null-addressee"),
    ],
)
def test_a_malformed_question_file_degrades_to_no_question(
    workspace: Path, qotd: Path, content: str
) -> None:
    qotd.parent.mkdir(parents=True, exist_ok=True)
    qotd.write_text(content, encoding="utf-8")
    config = load_config()

    assert read_operator_question() is None
    with Storage(config.db_path) as storage:
        assert pending_question(config, storage) is None
    assert "Set your job criteria" in render_completeness_markdown(_report(config))


def test_an_oversized_question_is_malformed_rather_than_truncated(
    workspace: Path, qotd: Path
) -> None:
    """Half a question is worse than no question — it reads as a whole one."""
    qotd.parent.mkdir(parents=True, exist_ok=True)
    qotd.write_text(json.dumps({"id": "q1", "question": "x" * 200_000}), encoding="utf-8")

    assert read_operator_question() is None


@not_root
def test_an_unreadable_question_file_never_breaks_status_for_everyone(
    workspace: Path, qotd: Path
) -> None:
    """The precedent RFC-065 set, followed rather than re-argued: this file
    sits on the path of every account's completeness, setup guide and
    Progress page, so a permissions mistake in a file nobody needed must
    cost the question and nothing else."""
    from wingman import mcp_server

    qotd.parent.mkdir(parents=True, exist_ok=True)
    qotd.write_text(json.dumps({"id": "q1", "question": "Why?"}), encoding="utf-8")
    qotd.chmod(0o000)

    try:
        config = load_config()
        assert read_operator_question() is None
        report = _report(config)
        assert report.operator_question is None
        assert "Set your job criteria" in render_completeness_markdown(report)
        assert "Wingman:" in mcp_server.status()
        assert "Why?" not in mcp_server.completeness()
    finally:
        qotd.chmod(0o600)


# --------------------------------------------------------------------------
# The two ends: the tool the tenant answers with, the command the operator
# reads back with.
# --------------------------------------------------------------------------


def test_the_qotd_tool_shows_answers_and_says_who_can_read_them(
    workspace: Path, qotd: Path
) -> None:
    from wingman import mcp_server

    assert "Nobody has asked" in mcp_server.qotd()

    _ask(qotd, why="Planning next month.")
    shown = mcp_server.qotd()
    assert "What is slowing you down this week?" in shown
    assert "they can read it" in shown

    saved = mcp_server.qotd(action="answer", answer="The admin.")
    assert "word for word" in saved
    assert "not evidence" in saved
    assert "The admin." in mcp_server.qotd(action="list")
    assert "already answered" in mcp_server.qotd()

    # A refusal is a sentence back, not a traceback in somebody's session.
    assert "failed" in mcp_server.qotd(action="answer", answer="  ")
    assert "unknown action" in mcp_server.qotd(action="delete")


def test_the_cli_shows_the_exact_words_and_saves_only_on_confirmation(
    workspace: Path, qotd: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BP-06 on the terminal side: the words that will be stored are shown
    first, the audience is named, and declining stores nothing."""
    import typer

    from wingman.cli.main import qotd_answer

    _ask(qotd)
    config = load_config()
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)

    with pytest.raises(typer.Exit):
        qotd_answer("The admin.", False)

    out = capsys.readouterr().out
    assert "The admin." in out
    assert "can read your answer" in out
    with Storage(config.db_path) as storage:
        assert list_operator_answers(storage) == []

    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    qotd_answer("The admin.", False)
    with Storage(config.db_path) as storage:
        assert [record.answer for record in list_operator_answers(storage)] == ["The admin."]


def test_the_operator_reads_answers_across_workspaces(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fan-in: one operator reading N workspaces with the access they
    already have, rather than N accounts writing to one place."""
    from wingman.cli.main import tenant_answers_cmd

    alice = _tenant_workspace(tmp_path / "tenants", "alice")
    bob = _tenant_workspace(tmp_path / "tenants", "bob")
    _register(registry, {"alice": alice.data_dir, "bob": bob.data_dir})
    _ask(qotd)
    with Storage(alice.db_path) as storage:
        save_operator_answer("Interviews I cannot schedule.", alice, storage)

    tenant_answers_cmd(None, registry, "")

    out = capsys.readouterr().out
    assert "Interviews I cannot schedule." in out
    assert "alice" in out and "bob" in out
    assert "each person's own words" in out
    assert "1 answer across 2 of 2 tenants" in out


def test_one_unreadable_workspace_does_not_hide_the_rest(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """This is the operator's ONLY way to read answers, so aborting the sweep
    on one corrupt database would silently hide every tenant after it in the
    registry — the same per-tenant isolation 'tenant overnight' keeps."""
    import typer

    from wingman.cli.main import tenant_answers_cmd

    alice = _tenant_workspace(tmp_path / "tenants", "alice")
    broken = _tenant_workspace(tmp_path / "tenants", "broken")
    # Registered first, so a sweep that aborts on it never reaches alice.
    _register(registry, {"broken": broken.data_dir, "alice": alice.data_dir})
    _ask(qotd)
    with Storage(alice.db_path) as storage:
        save_operator_answer("The admin.", alice, storage)
    broken.db_path.write_bytes(b"this is not a database")

    with pytest.raises(typer.Exit):
        tenant_answers_cmd(None, registry, "")

    captured = capsys.readouterr()
    assert "broken: could not be read" in captured.err
    assert "The admin." in captured.out
    # Counted over what was actually read: claiming "across 2 tenants" would
    # report a complete picture of a roster this run only partly saw.
    assert "1 answer across 1 of 2 tenants" in captured.out


def test_setting_a_question_survives_a_registry_that_cannot_be_parsed(
    workspace: Path, qotd: Path, registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The write side must tolerate exactly what the read side tolerates. A
    '~olduser' entry raises RuntimeError out of the registry parser, and an
    operator unable to send anything because somebody else's line is stale
    is the same failure in a different chair."""
    from wingman.cli.main import qotd_set

    registry.write_text('[[tenant]]\nslug = "ghost"\ndata_dir = "~nosuchuser12345/w"\n', "utf-8")

    qotd_set("Anyone there?", "alice", "q9", "", qotd)

    out = capsys.readouterr().out
    assert "could not read the tenant registry" in out
    assert "nobody will receive it" in out
    assert read_operator_question() is not None


def test_show_tells_a_broken_registry_apart_from_an_unregistered_account(
    workspace: Path, qotd: Path, registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both cases resolve to 'no slug' for delivery, deliberately — but one
    is an ordinary unregistered account and the other has silenced every
    slug-addressed broadcast on the box. Telling them apart is the whole
    job of a command that exists to make silence visible."""
    from wingman.cli.main import qotd_show

    _ask(qotd, to="alice")

    qotd_show()
    assert "no slug in the registry" in capsys.readouterr().out

    registry.write_text("this is not toml [[[", encoding="utf-8")
    qotd_show()
    out = capsys.readouterr().out
    assert "could not be read" in out
    assert "NOBODY on this box" in out


def test_showing_the_question_never_creates_a_workspace(
    workspace: Path, qotd: Path, registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only command that writes a database leaves a half-initialized
    data dir which every later db_path.exists() check reads as a real
    workspace."""
    from wingman.cli.main import qotd_show

    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "empty"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    _ask(qotd)

    qotd_show()

    assert not config.db_path.exists()
    assert list(config.data_dir.iterdir()) == []
