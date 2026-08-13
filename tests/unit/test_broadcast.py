"""The operator's box-wide broadcast, delivered as an action (issue #224).

Three properties carry the feature, and each is here as a test that fails
without the change:

- an operator action reaches `completeness.next_actions`, leads it, and is
  distinguishable from a computed one at every surface that renders it;
- an account sees a message once and never again until the id changes;
- a missing, unreadable or malformed shared file costs everyone the
  message and nobody their status.

A fourth, added by #382: what was delivered stays readable afterwards. The
seen-marker records an id, the shared file is the operator's to replace, so
without a copy in this account's own workspace "what was today's message?"
is unanswerable exactly when it is asked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wingman.application.completeness import (
    CompletenessReport,
    compute_completeness,
    next_actions,
)
from wingman.domain.delivered_message import DeliveredMessage
from wingman.infrastructure.broadcast import (
    OperatorMessage,
    acknowledge_delivery,
    last_seen_id,
    pending_operator_message,
    read_operator_message,
    write_operator_message,
)
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.completeness import render_completeness_markdown
from wingman.reporting.completeness_html import render_completeness_html

# Mode bits mean nothing to root, so the two permission-degradation tests
# below cannot express what they are about when the suite runs as root
# (some CI containers do). Skipped rather than quietly asserting nothing.
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
def motd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The shared /etc/wingman/motd.json, relocated for the test.

    Monkeypatched module constant rather than an environment variable, for
    the same reason production has no env override: this is a property of
    the box, not of a process.
    """
    path = tmp_path / "etc" / "motd.json"
    monkeypatch.setattr("wingman.infrastructure.broadcast.OPERATOR_MESSAGE_PATH", path)
    return path


def _broadcast(path: Path, **fields: str) -> None:
    payload = {"id": "m1", "action": "Re-ingest your CV", **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _report(config: Config) -> CompletenessReport:
    with Storage(config.db_path) as storage:
        return compute_completeness(storage, config)


# --------------------------------------------------------------------------
# It arrives as an ACTION, and it leads.
# --------------------------------------------------------------------------


def test_an_operator_message_becomes_a_next_action(workspace: Path, motd: Path) -> None:
    """The reframe that made this worth building: a banner gets skimmed, an
    entry in next_actions gets acted on, because the completeness tool's own
    protocol forbids answering "what should I do next" from anywhere else."""
    _broadcast(motd, why="The parser changed.", how="say: here's my resume")

    actions = next_actions(_report(load_config()))

    assert [a.title for a in actions].count("Re-ingest your CV") == 1
    action = next(a for a in actions if a.title == "Re-ingest your CV")
    assert action.why == "The parser changed."
    assert action.how == "say: here's my resume"


def test_an_operator_action_outranks_the_most_unblocking_computed_one(
    workspace: Path, motd: Path
) -> None:
    """The written rule (issue #224's first open question), not an accident
    of insertion order. Job criteria otherwise leads everything, and this
    empty workspace has no criteria — the operator still goes first,
    because their action is shown once and would otherwise be missed
    permanently, while 'set your job criteria' is still there tomorrow."""
    _broadcast(motd)

    actions = next_actions(_report(load_config()))

    assert actions[0].title == "Re-ingest your CV"
    assert actions[0].origin == "operator"
    assert "Set your job criteria" in [a.title for a in actions]
    assert actions[1].title == "Set your job criteria"
    assert actions[1].origin == "workspace"


def test_a_computed_action_still_leads_when_there_is_no_broadcast(
    workspace: Path, motd: Path
) -> None:
    """The rule above must not have quietly cost job criteria its lead in
    the ordinary case, which is every box that never sets a message."""
    actions = next_actions(_report(load_config()))

    assert actions[0].title == "Set your job criteria"
    assert all(action.origin == "workspace" for action in actions)


# --------------------------------------------------------------------------
# Its origin is visible — an instruction must never read as a measurement.
# --------------------------------------------------------------------------


def test_the_operator_action_is_distinguishable_from_a_computed_one(
    workspace: Path, motd: Path
) -> None:
    """Rendering them identically would let somebody's instruction pass as a
    fact derived from the workspace — the failure RFC-058 exists to prevent
    one layer up. The distinction is carried in the model, so no renderer
    can lose it by accident."""
    _broadcast(motd)

    actions = next_actions(_report(load_config()))

    operator = actions[0]
    computed = actions[1]
    assert operator.origin == "operator" and operator.attribution
    assert computed.origin == "workspace" and computed.attribution == ""
    assert "instruction" in operator.attribution
    assert "not something wingman measured" in operator.attribution


@pytest.mark.parametrize("surface", ["markdown", "html", "setup_guide"])
def test_every_surface_that_renders_the_list_shows_where_it_came_from(
    workspace: Path, motd: Path, surface: str
) -> None:
    """All three render the same list, which is the whole reason this
    mechanism reuses next_actions — so all three owe the same attribution."""
    from wingman.application.completeness import OPERATOR_ATTRIBUTION
    from wingman.application.setup_guide import render_setup_guide

    _broadcast(motd)
    config = load_config()
    report = _report(config)

    rendered = {
        "markdown": lambda: render_completeness_markdown(report),
        "html": lambda: render_completeness_html(report),
        "setup_guide": lambda: render_setup_guide(config, report),
    }[surface]()

    assert "Re-ingest your CV" in rendered
    assert OPERATOR_ATTRIBUTION in rendered


def test_the_html_surface_labels_it_before_it_is_read(workspace: Path, motd: Path) -> None:
    """A page is scanned, not read top to bottom, so the label goes in front
    of the title rather than only in a line underneath it."""
    _broadcast(motd)

    html = render_completeness_html(_report(load_config()))

    assert "[from the operator of this machine]" in html
    assert html.index("[from the operator of this machine]") < html.index("Re-ingest your CV")


def test_a_message_with_no_reason_does_not_get_one_invented(workspace: Path, motd: Path) -> None:
    """Wingman filling in the operator's missing rationale would be making
    up somebody else's reasoning — invariant 9, partial truth over polished
    fiction."""
    _broadcast(motd)

    action = next_actions(_report(load_config()))[0]

    assert action.why == "No reason was given beyond the request itself."
    assert "ask whoever runs this machine" in action.how


# --------------------------------------------------------------------------
# Seen once, and not again until the id changes.
# --------------------------------------------------------------------------


def test_a_message_is_pending_once_then_never_again(workspace: Path, motd: Path) -> None:
    _broadcast(motd)
    config = load_config()

    first = pending_operator_message(config)
    assert first is not None and first.id == "m1"

    acknowledge_delivery(config, first)

    assert pending_operator_message(config) is None
    assert pending_operator_message(config) is None


def test_a_new_id_re_delivers_to_an_account_that_saw_the_old_one(
    workspace: Path, motd: Path
) -> None:
    """The id is what makes 'once' mean anything: the operator changes it to
    say something new, and every account that already read the last one gets
    the next one exactly once too."""
    _broadcast(motd)
    config = load_config()
    acknowledge_delivery(config, pending_operator_message(config))
    assert pending_operator_message(config) is None

    _broadcast(motd, id="m2", action="The box moves on Friday")

    pending = pending_operator_message(config)
    assert pending is not None and pending.action == "The box moves on Friday"


def test_editing_the_text_without_changing_the_id_does_not_re_deliver(
    workspace: Path, motd: Path
) -> None:
    """A fixed typo is not a new instruction. Re-delivering on any content
    change would make every correction cost everybody another interruption,
    so the operator says 'this is new' explicitly, by changing the id."""
    _broadcast(motd)
    config = load_config()
    acknowledge_delivery(config, pending_operator_message(config))

    _broadcast(motd, action="Re-ingest your CV, please")

    assert pending_operator_message(config) is None


def test_the_seen_marker_lives_in_this_accounts_own_data_dir(workspace: Path, motd: Path) -> None:
    """A pure broadcast needs no write-back: the shared file stays
    root-owned and read-only to everybody, and each account's 'I have seen
    it' is its own business, in its own directory. Nothing in this half of
    the feature requires a group-writable anything."""
    _broadcast(motd)
    config = load_config()

    acknowledge_delivery(config, pending_operator_message(config))

    marker = config.data_dir / "operator-message-seen"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == "m1"
    assert last_seen_id(config) == "m1"
    assert motd.read_text(encoding="utf-8") == json.dumps(
        {"id": "m1", "action": "Re-ingest your CV"}
    ), "the shared file is never written back to by a reader"


def test_computing_completeness_does_not_consume_the_message(workspace: Path, motd: Path) -> None:
    """compute_completeness also runs for the profile page's progress band
    and the profile HTML export, neither of which renders the next-actions
    list. Consuming the message there would burn a once-only broadcast with
    nobody on the other end, so delivery is the caller's explicit act."""
    _broadcast(motd)
    config = load_config()

    for _ in range(3):
        report = _report(config)
        assert report.operator_action is not None

    assert not (config.data_dir / "operator-message-seen").exists()


def test_the_completeness_tool_delivers_it_exactly_once(workspace: Path, motd: Path) -> None:
    """End to end through the surface the reframe was built around."""
    from wingman import mcp_server

    _broadcast(motd, why="The parser changed.")

    first = mcp_server.completeness()
    second = mcp_server.completeness()

    assert "Re-ingest your CV" in first
    assert "The parser changed." in first
    assert "Re-ingest your CV" not in second


def test_the_setup_guide_tool_delivers_it_exactly_once(workspace: Path, motd: Path) -> None:
    """setup_guide renders the same list, so it is a delivery point too —
    otherwise a new person's first question would consume nothing and the
    message would still be waiting after they had already read it."""
    from wingman import mcp_server

    _broadcast(motd)

    first = mcp_server.setup_guide()
    second = mcp_server.setup_guide()

    assert "Re-ingest your CV" in first
    assert "Re-ingest your CV" not in second


def test_the_progress_page_delivers_it_exactly_once(workspace: Path, motd: Path) -> None:
    """The third surface that renders the list. The profile page's own
    progress band also computes a report but never renders the list, so it
    must NOT consume the message — proven here rather than assumed, because
    a page that silently spends somebody's one delivery is invisible until
    the message is already gone."""
    from starlette.testclient import TestClient

    from wingman.mcp_server import _http_token, server
    from wingman.webui import register_ui

    _broadcast(motd)
    config = load_config()
    token = _http_token(config)
    register_ui(server)
    http = TestClient(server.streamable_http_app())

    assert http.get(f"/ui/{token}/profile").status_code == 200
    assert pending_operator_message(config) is not None, "the profile band must not consume it"

    first = http.get(f"/ui/{token}/completeness").text
    second = http.get(f"/ui/{token}/completeness").text

    assert "Re-ingest your CV" in first
    assert "Re-ingest your CV" not in second


# --------------------------------------------------------------------------
# A broken shared file costs the message, never the status.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("not json at all", id="not-json"),
        pytest.param('["a", "list"]', id="json-but-not-an-object"),
        pytest.param('{"action": "Do the thing"}', id="no-id"),
        pytest.param('{"id": "m1"}', id="no-action"),
        pytest.param('{"id": "m1", "action": ""}', id="blank-action"),
        pytest.param('{"id": "m1", "action": 42}', id="action-not-a-string"),
        pytest.param('{"id": "m1", "action": "x", "why": null}', id="null-field"),
    ],
)
def test_a_malformed_shared_file_degrades_to_no_message(
    workspace: Path, motd: Path, content: str
) -> None:
    motd.parent.mkdir(parents=True, exist_ok=True)
    motd.write_text(content, encoding="utf-8")

    assert read_operator_message() is None
    assert pending_operator_message(load_config()) is None


def test_an_absent_shared_file_is_the_ordinary_case_not_an_error(
    workspace: Path, motd: Path
) -> None:
    assert not motd.exists()
    assert read_operator_message() is None
    assert pending_operator_message(load_config()) is None


def test_an_oversized_file_is_malformed_rather_than_truncated(workspace: Path, motd: Path) -> None:
    """A truncated instruction is worse than no instruction — half a
    sentence telling somebody to do something is exactly the kind of
    confident fragment invariant 9 refuses."""
    motd.parent.mkdir(parents=True, exist_ok=True)
    motd.write_text(json.dumps({"id": "m1", "action": "x" * 200_000}), encoding="utf-8")

    assert read_operator_message() is None


@not_root
def test_an_unreadable_shared_file_never_breaks_status_for_everyone(
    workspace: Path, motd: Path
) -> None:
    """The exact production hazard: a root-owned file at mode 600 on a box
    where the accounts are not root. The keys ladder already treats an
    unreadable global file as empty (RFC-047) and this must too — every
    account's completeness, setup guide and Progress page sit downstream of
    it, so raising here would take out the whole workspace for a
    permissions mistake in a file nobody needed."""
    from wingman import mcp_server

    motd.parent.mkdir(parents=True, exist_ok=True)
    motd.write_text(json.dumps({"id": "m1", "action": "Do the thing"}), encoding="utf-8")
    motd.chmod(0o000)

    try:
        config = load_config()
        assert read_operator_message() is None
        assert pending_operator_message(config) is None
        report = _report(config)
        assert report.operator_action is None
        assert "Set your job criteria" in render_completeness_markdown(report)
        assert "Wingman:" in mcp_server.status()
        assert "Do the thing" not in mcp_server.completeness()
    finally:
        motd.chmod(0o600)


@not_root
def test_a_read_only_data_dir_shows_the_message_again_rather_than_failing(
    workspace: Path, motd: Path
) -> None:
    """Delivery is best-effort by design. If the marker cannot be written,
    the honest degradation is repeating yourself, not crashing the tool that
    just successfully told somebody something."""
    _broadcast(motd)
    config = load_config()
    message = pending_operator_message(config)
    assert message is not None

    config.data_dir.chmod(0o555)
    try:
        acknowledge_delivery(config, message)  # must not raise
        assert pending_operator_message(config) is not None
    finally:
        config.data_dir.chmod(0o755)


def test_acknowledging_nothing_is_a_no_op(workspace: Path, motd: Path) -> None:
    """So every delivery point can pass report.operator_action
    unconditionally, which is the version of this call that cannot be got
    wrong."""
    config = load_config()
    acknowledge_delivery(config, None)
    assert not (config.data_dir / "operator-message-seen").exists()


# --------------------------------------------------------------------------
# What was delivered is recoverable afterwards (#382).
# --------------------------------------------------------------------------


def _delivered(config: Config) -> list[DeliveredMessage]:
    with Storage(config.db_path) as storage:
        return storage.list_delivered_messages()


def test_a_delivered_message_survives_the_operator_replacing_the_file(
    workspace: Path, motd: Path
) -> None:
    """The whole of #382. A message is shown once, acknowledgement means
    *shown* and not *read*, and the text lives in a file the operator
    replaces the moment they have something newer to say — so 'what was
    today's message?' has to be answerable from a copy this account kept,
    or it is not answerable at all on the day it is asked."""
    from wingman.application.motd import recent_messages, render_messages

    _broadcast(motd, why="The parser changed.", how="say: here's my resume")
    config = load_config()

    acknowledge_delivery(config, pending_operator_message(config))

    _broadcast(motd, id="m2", action="The box moves on Friday")
    assert read_operator_message() is not None
    assert read_operator_message().action == "The box moves on Friday"  # type: ignore[union-attr]

    with Storage(config.db_path) as storage:
        kept = recent_messages(storage)
    assert [record.action for record in kept] == ["Re-ingest your CV"]
    assert kept[0].why == "The parser changed."
    assert kept[0].how == "say: here's my resume"
    assert kept[0].message_id == "m1"
    assert "Re-ingest your CV" in render_messages(kept)


def test_the_copy_is_kept_even_when_the_shared_file_is_deleted(workspace: Path, motd: Path) -> None:
    """Deleting the file is how an operator stops saying anything (RFC-065),
    and it must not also erase what everybody was already told."""
    _broadcast(motd)
    config = load_config()

    acknowledge_delivery(config, pending_operator_message(config))
    motd.unlink()

    assert read_operator_message() is None
    assert [record.action for record in _delivered(config)] == ["Re-ingest your CV"]


def test_the_same_message_delivered_twice_is_recorded_once(workspace: Path, motd: Path) -> None:
    """Delivery is best-effort, so it genuinely happens twice: a seen-marker
    that could not be written means the message is shown again. One row per
    message, keyed by the operator's id, and the FIRST delivery time is
    kept — an upsert would quietly re-date "when were you told"."""
    _broadcast(motd)
    config = load_config()
    message = pending_operator_message(config)

    acknowledge_delivery(config, message)
    first = _delivered(config)[0].delivered_at
    acknowledge_delivery(config, message)

    kept = _delivered(config)
    assert len(kept) == 1
    assert kept[0].delivered_at == first


def test_a_new_message_is_a_second_row_most_recent_first(workspace: Path, motd: Path) -> None:
    """Recent messages, most recent first — the id decides novelty here
    exactly as it decides delivery, so the two can never disagree about how
    many distinct things the operator has said."""
    _broadcast(motd)
    config = load_config()
    acknowledge_delivery(config, pending_operator_message(config))

    _broadcast(motd, id="m2", action="The box moves on Friday")
    acknowledge_delivery(config, pending_operator_message(config))

    assert [record.message_id for record in _delivered(config)] == ["m2", "m1"]


def test_a_failed_history_write_does_not_break_the_surface_that_showed_it(
    workspace: Path, motd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same posture as the seen-marker write, and for the same reason one
    step further on: by the time this runs the person has ALREADY been told,
    so a locked or unwritable database must cost the copy and never the tool
    that just successfully delivered the message. The marker must still be
    written too — otherwise a broken history store would turn a once-only
    message into one that repeats forever."""
    from wingman import mcp_server

    def explode(self: Storage, message: DeliveredMessage) -> bool:
        raise RuntimeError("no room left on device")

    monkeypatch.setattr(Storage, "record_delivered_message", explode)
    _broadcast(motd, why="The parser changed.")
    config = load_config()

    rendered = mcp_server.completeness()

    assert "Re-ingest your CV" in rendered
    assert "The parser changed." in rendered
    assert last_seen_id(config) == "m1"
    assert _delivered(config) == []


def test_no_workspace_means_no_history_and_no_workspace_is_created(
    workspace: Path, motd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Showing a message must never CREATE a workspace: writing a schema
    into an empty data dir leaves a half-initialized one that every later
    `db_path.exists()` check reads as real. The message is still delivered
    and still marked seen."""
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "empty"))
    _broadcast(motd)
    config = load_config()
    config.data_dir.mkdir(parents=True)

    acknowledge_delivery(config, pending_operator_message(config))

    assert not config.db_path.exists()
    assert last_seen_id(config) == "m1"


def test_the_profile_page_neither_delivers_nor_records(workspace: Path, motd: Path) -> None:
    """The fourth surface that computes a report and does NOT render the
    next-actions list. Adding a second write at acknowledge time must not
    have changed WHICH surfaces acknowledge — the copy is written inside
    `acknowledge_delivery`, so remembering and delivering cannot drift
    apart, and a page that renders nothing does neither."""
    from starlette.testclient import TestClient

    from wingman.mcp_server import _http_token, server
    from wingman.webui import register_ui

    _broadcast(motd)
    config = load_config()
    token = _http_token(config)
    register_ui(server)
    http = TestClient(server.streamable_http_app())

    assert http.get(f"/ui/{token}/profile").status_code == 200
    assert pending_operator_message(config) is not None
    assert _delivered(config) == []

    assert http.get(f"/ui/{token}/completeness").status_code == 200
    assert [record.message_id for record in _delivered(config)] == ["m1"]


def test_the_history_tool_reads_it_back_without_delivering_anything(
    workspace: Path, motd: Path
) -> None:
    """The tool a person's 'what was today's message?' reaches. It reports a
    waiting message as waiting and never quotes it: quoting would make this
    a fifth delivery surface that does not acknowledge, and the message
    would then arrive again later — 'shown once' quietly stops being true."""
    from wingman import mcp_server

    _broadcast(motd)
    config = load_config()
    mcp_server.completeness()
    _broadcast(motd, id="m2", action="The box moves on Friday")

    rendered = mcp_server.motd()

    assert "Re-ingest your CV" in rendered
    assert "The box moves on Friday" not in rendered
    assert "not been shown yet" in rendered
    assert pending_operator_message(config) is not None, "reading history delivers nothing"


def test_motd_history_prints_what_this_account_was_told(workspace: Path, motd: Path) -> None:
    """The CLI half of the same question, for whoever is on the box."""
    from typer.testing import CliRunner

    from wingman.cli.main import app

    _broadcast(motd, why="The parser changed.")
    config = load_config()
    acknowledge_delivery(config, pending_operator_message(config))
    motd.unlink()

    result = CliRunner().invoke(app, ["motd", "history"])

    assert result.exit_code == 0
    assert "Re-ingest your CV" in result.output
    assert "The parser changed." in result.output


def test_an_account_with_no_history_is_told_so_rather_than_shown_nothing(
    workspace: Path, motd: Path
) -> None:
    """An empty answer to 'what was today's message?' must say that nothing
    has been delivered — a blank reply reads like a failure."""
    from wingman import mcp_server

    rendered = mcp_server.motd()

    assert "has not sent you a message yet" in rendered


# --------------------------------------------------------------------------
# The operator's own side.
# --------------------------------------------------------------------------


def test_the_writer_produces_a_file_the_reader_accepts(workspace: Path, motd: Path) -> None:
    """The point of having a writer at all: a hand-typed comma costs every
    account its message with no error anywhere, so there is a way to produce
    the JSON that is guaranteed to parse."""
    written = write_operator_message(
        OperatorMessage(id="m9", action="Rotate your key", why="It expires Friday.")
    )

    assert written == motd
    round_tripped = read_operator_message()
    assert round_tripped is not None
    assert round_tripped.id == "m9"
    assert round_tripped.action == "Rotate your key"
    assert round_tripped.why == "It expires Friday."
    assert round_tripped.how == ""


def test_motd_show_reports_silence_rather_than_hiding_it(
    workspace: Path, motd: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Everywhere else a malformed file degrades to silence on purpose. That
    is only safe if the person who caused it has one place to see it."""
    from wingman.cli.main import motd_show

    motd.parent.mkdir(parents=True, exist_ok=True)
    motd.write_text("{ oops", encoding="utf-8")

    motd_show()

    assert "could not be read as a message" in capsys.readouterr().out


def test_motd_show_does_not_spend_the_operators_own_copy(
    workspace: Path, motd: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator checking what they just wrote must not thereby consume
    their own delivery of it."""
    from wingman.cli.main import motd_show

    _broadcast(motd)
    config = load_config()

    motd_show()

    assert "not yet shown" in capsys.readouterr().out
    assert pending_operator_message(config) is not None


# --------------------------------------------------------------------------
# A permissions problem is a diagnosis, not a traceback (#401)
# --------------------------------------------------------------------------


def test_an_unreadable_file_is_diagnosed_rather_than_raising(tmp_path: Path) -> None:
    """Path.exists() RAISES on EACCES rather than returning False, so the
    operator-facing 'show' commands turned a permissions problem into a
    stack trace naming nothing actionable."""
    from wingman.infrastructure.broadcast import permission_problem

    directory = tmp_path / "etc"
    directory.mkdir()
    path = directory / "motd.json"
    path.write_text("{}", encoding="utf-8")
    directory.chmod(0o000)
    try:
        problem = permission_problem(path)
    finally:
        directory.chmod(0o755)

    assert problem is not None
    assert "permission denied" in problem


def test_the_diagnosis_names_the_session_predates_the_group_case(tmp_path: Path) -> None:
    """The cause that actually costs an hour: supplementary groups are fixed
    at login, so an account added to the group keeps failing in every
    session that predates the change."""
    from wingman.infrastructure.broadcast import permission_problem

    directory = tmp_path / "etc"
    directory.mkdir()
    path = directory / "motd.json"
    path.write_text("{}", encoding="utf-8")
    directory.chmod(0o000)
    try:
        problem = permission_problem(path) or ""
    finally:
        directory.chmod(0o755)

    assert "predates" in problem
    assert "log out and back in" in problem.lower()
    assert "id -nG" in problem


def test_a_missing_file_is_not_a_permissions_problem(tmp_path: Path) -> None:
    """Absent and unreadable are different problems with different fixes.
    Collapsing them sends an operator to fix the wrong one."""
    from wingman.infrastructure.broadcast import permission_problem

    assert permission_problem(tmp_path / "nothing-here.json") is None


def test_a_readable_file_reports_no_problem(tmp_path: Path) -> None:
    from wingman.infrastructure.broadcast import permission_problem

    path = tmp_path / "motd.json"
    path.write_text("{}", encoding="utf-8")

    assert permission_problem(path) is None


def test_delivery_still_degrades_to_silence_rather_than_failing(tmp_path: Path) -> None:
    """The delivery path never had this bug and must not acquire it: a
    tenant who cannot read the file gets their status, minus the message."""
    from wingman.infrastructure.broadcast import read_operator_message

    directory = tmp_path / "etc"
    directory.mkdir()
    path = directory / "motd.json"
    path.write_text('{"id": "x", "action": "do the thing"}', encoding="utf-8")
    directory.chmod(0o000)
    try:
        assert read_operator_message(path) is None
    finally:
        directory.chmod(0o755)
