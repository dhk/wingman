"""The operator's box-wide broadcasts: a message to act on, a question to
answer, and who each one is for (issue #224).

Whoever runs a shared box sometimes knows something the workspace cannot:
a parser changed and everyone's CV needs re-ingesting, a migration is
coming, the machine moves on Friday. There was no way to say it. This is
that way, and it is deliberately the smallest one that works.

**Two broadcasts, one shape.** A message (`motd.json`) asks somebody to DO
something; a question (`qotd.json`) asks them to SAY something. They are
separate files rather than two fields of one, because they are separate
acts and an operator should never have to choose between asking and
telling. Everything else is shared: the same reader, the same degrade-to-
silence rule, the same addressing.

**Addressing (the QOTD half's only real addition to the file shape).** A
broadcast carries `to`: a tenant slug, or `"all"`. An account's own slug is
resolved from the tenant registry by matching its data dir — the registry
is already root-provisioned and readable, and no account has to be told
who it is. Addressing FAILS CLOSED: an account whose slug cannot be
resolved (no registry, a malformed one, a solo install) receives broadcasts
addressed to everybody and nothing addressed to a slug. A message reaching
nobody is a message the operator can re-send; a message reaching the wrong
person cannot be unsent.

**Why this is not a banner.** The owner's phrasing was "a way to inject a
'do this at your earliest convenience' prompt into people's sessions" —
which is an action, not an announcement. A banner gets skimmed. An entry
in `completeness.next_actions` gets acted on, because the `completeness`
tool's own protocol tells the assistant to answer "what's my status" and
"what should I do next" FROM that list and never to invent a step outside
it. So an operator's action inherits an already-enforced delivery path
instead of needing a new one, and appears in the web UI's Progress page
and in `setup_guide` (RFC-062) for free, since all three render the same
list. This module is therefore a reader, not a renderer: it answers "is
there something pending for this account", and `application.completeness`
decides what that means.

**Storage follows RFC-047, it does not invent a mechanism.** Each
broadcast is one root-provisioned file under `/etc/wingman/` — the same
box-wide, group-readable tier `global-secrets.env` already established.
Nothing here ever writes to that directory as part of a normal read; the
per-account "I have seen this" marker lives in that account's OWN data
dir, and so does the copy of the message it was shown (`delivered_messages`,
RFC-070 — the marker says which id, the copy says what it SAID, because the
operator's file has moved on by the time anybody asks). **Nor does the
answer half need shared state**: a tenant's answer is
stored in that tenant's own SQLite database like every other capture, and
the operator reads answers back with the operator access they already have
(the same access that reads the registry and runs `tenant urls`). The
fan-in is one operator reading N workspaces, not N accounts writing to one
place, so RFC-048's share-nothing posture is left intact rather than
needing an answer. RFC-065 predicted a group-writable
`/etc/wingman/qotd-answers/`; that is not the design and never was needed.

**Unreadable degrades to silent, exactly as the keys ladder does.**
`keys.read_global_keys` treats a missing or unreadable global file as an
empty dict rather than an error, because a file owned by root that this
account may not be able to read must never be the reason someone's
workspace stops answering. The same rule holds here, and matters more:
this file is on the path of `completeness`, `setup_guide` and the Progress
page, so a stray comma in the operator's JSON must cost everyone their
message, never their status. Every failure mode — absent, unreadable,
oversized, not JSON, JSON of the wrong shape, missing a required field —
returns None and logs. `wingman motd show` and `wingman qotd show` exist
precisely so that silence is checkable by the person who caused it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.broadcast")

# Root-provisioned, inside the box-wide directory RFC-047 already
# established for /etc/wingman/global-secrets.env — which is mode 750
# root:wingman, so the directory is what gates who can read this and the
# file itself needs no secrecy (it is an announcement, not a credential).
# A module constant rather than an environment variable, matching
# keys.GLOBAL_KEYS_PATH: this is a property of the box, not of a process,
# and an env var would let one account's shell decide what "the operator
# said" means for that account.
OPERATOR_MESSAGE_PATH = Path("/etc/wingman/motd.json")

# The question of the day. A second file rather than a second field of the
# first: asking and telling are different acts, and one file would make an
# operator choose between them for no gain.
OPERATOR_QUESTION_PATH = Path("/etc/wingman/qotd.json")

# The per-account "last delivered id", in the account's own data dir.
SEEN_FILENAME = "operator-message-seen"

# The 'to' value that means everybody. A tenant slug is anything else.
# Reserved, so a tenant literally named "all" cannot be addressed
# individually — they receive everything instead of nothing, which is the
# harmless direction, and no registry in practice uses the word.
ALL_TENANTS = "all"

# A broadcast that must fit in a next-action line. The cap exists so a
# runaway or hostile file cannot be read into memory or pasted wholesale
# into every account's status output; anything larger is treated as
# malformed rather than truncated, since a truncated instruction is worse
# than no instruction.
MAX_MESSAGE_BYTES = 8 * 1024
MAX_FIELD_CHARS = 500


class Broadcast(BaseModel):
    """What every broadcast carries, whether it tells or asks.

    `id` is what makes "seen once" mean anything: an account records the
    id it was shown (or, for a question, stores an answer against it), and
    sees nothing again until the operator changes it. Deliberately opaque —
    a date, a slug, a counter — because the operator owns the file and this
    code only compares it for equality.

    `to` is the addressee: one tenant slug, or `ALL_TENANTS`. Scalar rather
    than a list because the owner's shape is "one tenant, or all of them",
    and a list would immediately need a rule for what an empty one means.
    """

    id: str
    to: str = ALL_TENANTS


class OperatorMessage(Broadcast):
    """One pending instruction from whoever runs this box.

    `action` is the imperative ("Re-ingest your CV"), because it lands in
    a list of things to do. `why` and `how` mirror `NextAction`'s own two
    fields so an operator action is not structurally poorer than a
    computed one; both are optional, and `application.completeness`
    supplies an honest fallback rather than inventing a reason.
    """

    action: str
    why: str = ""
    how: str = ""


class OperatorQuestion(Broadcast):
    """One pending question from whoever runs this box.

    `question` is the operator's words, verbatim — it is copied onto the
    stored answer at save time so an answer is never read back without the
    question that produced it, even after the operator has moved on to the
    next one.

    There is no `how`: unlike a message, the way to answer a question is
    wingman's own (`qotd`), not something the operator gets to specify.
    """

    question: str
    why: str = ""


def _clean(value: Any, *, field: str, path: Path) -> str | None:
    """One field, or None if it is not a usable string."""
    if not isinstance(value, str):
        _logger.warning("operator broadcast field not a string path=%s field=%s", path, field)
        return None
    text = " ".join(value.split())
    if len(text) > MAX_FIELD_CHARS:
        _logger.warning("operator broadcast field too long path=%s field=%s", path, field)
        return None
    return text


def _read_broadcast_fields(
    source: Path, required: tuple[str, ...], optional: tuple[str, ...]
) -> dict[str, str] | None:
    """The cleaned string fields of one broadcast file, or None.

    Shared by both broadcasts so there is exactly one implementation of
    "what counts as readable" — two would drift, and the half that drifted
    would degrade to silence without anyone noticing (which is precisely
    the failure mode this function's own caution is built around).
    """
    try:
        if not source.is_file():
            return None
        raw = source.read_bytes()
    except OSError as exc:
        # Unreadable is the expected case on a misconfigured box (mode 600,
        # or an account not in the wingman group). Silence, not a stack
        # trace in someone's status output.
        _logger.warning("operator broadcast unreadable path=%s error=%s", source, exc)
        return None
    if len(raw) > MAX_MESSAGE_BYTES:
        _logger.warning("operator broadcast too large path=%s bytes=%d", source, len(raw))
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _logger.warning("operator broadcast unparseable path=%s error=%s", source, exc)
        return None
    if not isinstance(payload, dict):
        _logger.warning("operator broadcast is not a JSON object path=%s", source)
        return None

    fields: dict[str, str] = {}
    for field in (*required, *optional, "to"):
        value = payload.get(field, "")
        cleaned = _clean(value, field=field, path=source)
        if cleaned is None:
            return None
        if field in required and not cleaned:
            _logger.warning("operator broadcast missing %s path=%s", field, source)
            return None
        fields[field] = cleaned
    # An omitted addressee is everybody — the shape every file written
    # before addressing existed has, and the only reading of "no target"
    # that does not silently stop delivering messages that already work.
    fields["to"] = fields["to"] or ALL_TENANTS
    return fields


def permission_problem(path: Path) -> str | None:
    """Why an operator cannot examine a broadcast file, in words they can act on.

    None when the file can be examined — including when it simply is not
    there, which is a different problem with a different fix and must not
    be collapsed into this one.

    This exists because `Path.exists()` RAISES on EACCES rather than
    returning False, so the operator-facing 'show' commands turned a
    permissions problem into a traceback naming nothing actionable (#401).
    The delivery path never had the bug — `_read_broadcast_fields` catches
    OSError and degrades to silence — but silence is exactly wrong for the
    person who can fix the permissions.

    The second cause below is the one that actually costs people an hour:
    supplementary groups are fixed at login, so an account added to the
    group keeps failing in every session that predates the change, and
    `id -nG` and `id -nG <user>` disagree while it does.
    """
    try:
        path.stat()
    except PermissionError:
        return (
            f"Cannot read {path} — permission denied, so nothing here can say what is "
            "set.\n"
            "Two usual causes:\n"
            "  - this account is not in the group that owns /etc/wingman; or\n"
            "  - this SESSION predates being added to it. Supplementary groups are "
            "fixed at login, so 'id -nG' and 'id -nG <user>' disagree until you log "
            "out and back in.\n"
            "Check with: id -nG   and   id -nG $USER"
        )
    except OSError:
        # Missing, a broken symlink, anything else: not this function's
        # question. The caller reports absence in its own words.
        return None
    return None


def read_operator_message(path: Path | None = None) -> OperatorMessage | None:
    """The current message, or None when there isn't a usable one.

    Never raises. 'path' is injectable for tests; production callers always
    get the real /etc/wingman/motd.json.
    """
    source = path if path is not None else OPERATOR_MESSAGE_PATH
    fields = _read_broadcast_fields(source, ("id", "action"), ("why", "how"))
    return None if fields is None else OperatorMessage(**fields)


def read_operator_question(path: Path | None = None) -> OperatorQuestion | None:
    """The current question, or None when there isn't a usable one.

    Never raises, for the same reason its sibling doesn't: this sits on the
    path of every account's `completeness`, and a stray comma in the
    operator's JSON must cost everybody the question, never their status.
    """
    source = path if path is not None else OPERATOR_QUESTION_PATH
    fields = _read_broadcast_fields(source, ("id", "question"), ("why",))
    return None if fields is None else OperatorQuestion(**fields)


def _same_directory(left: Path, right: Path) -> bool:
    """Whether two paths name the same directory, symlinks resolved.

    A registry that spells a data dir with a symlink or a trailing slash
    still identifies the account it belongs to; comparing the raw strings
    would leave that account unable to receive anything addressed to it.
    """
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except (OSError, RuntimeError):
        # Defensive in both cases: resolve() on a hostile filesystem, and
        # the RuntimeError expanduser() raises for a '~someone' with no
        # home. Neither reaches here today — `load_registry` expands the
        # path first and raises before this is called, which is why
        # `account_slug`'s catch is the one that actually holds the line —
        # but this comparison must never be the thing that raises.
        return False


def account_slug(config: Config) -> str | None:
    """This account's tenant slug, or None when it has no registry identity.

    Resolved by matching the account's data dir against the tenant registry
    rather than stored anywhere: the registry is the one place that already
    knows who is who, and a second copy in each workspace could disagree
    with it — at which point a question addressed to one person is answered
    by another, silently. Never raises: a missing, unreadable or malformed
    registry means "this account has no slug", which fails closed.

    The catch-all is deliberate and is the one place in this module where a
    bare `Exception` is the right thing. This function sits under
    `completeness`, `status`, `setup_guide` and the Progress page for every
    account on the box, and it calls into registry parsing that this module
    does not own — one `~olduser` entry whose home has been deleted (which
    raises `RuntimeError` from `expanduser`, not `OSError`) would otherwise
    take out everybody's status for a stale line in somebody else's file.
    Whatever goes wrong reading the registry, the honest answer here is
    "this account has no slug", which fails closed. One unusable ENTRY
    therefore costs the whole registry its addressing, since parsing is
    all-or-nothing by design (`load_registry` raises rather than serve a
    partial roster) — every slug-addressed broadcast on that box goes
    silent until somebody fixes the line, which is visible in the log and
    in `wingman qotd show`, and is the safe direction.

    The registry read is `tenant_registry_path()` — the box's canonical
    one. A shared server started with an explicit `--tenant-registry` while
    `WINGMAN_TENANT_REGISTRY` is unset would serve from one file and
    address against another; `qotd set`/`motd set` print the path they
    validated against so that mismatch is visible where it is created.
    """
    from wingman.infrastructure.tenants import load_registry, tenant_registry_path

    try:
        tenants = load_registry(tenant_registry_path())
    except Exception as exc:  # noqa: BLE001 — see the docstring
        # The TYPE is in the line on purpose. A total catch that logs only
        # str(exc) turns a future AttributeError from a refactor of the
        # registry code into the same sentence as a stale '~olduser' entry,
        # and this is the only trace either one leaves.
        _logger.warning(
            "tenant registry unusable for broadcast addressing error=%s: %s",
            type(exc).__name__,
            exc,
        )
        return None
    for tenant in tenants:
        if _same_directory(tenant.data_dir, config.data_dir):
            return tenant.slug
    return None


def is_addressed_to(target: str, config: Config) -> bool:
    """Whether a broadcast addressed to 'target' is for THIS account.

    Fails closed on purpose. If the target is a slug and this account's own
    slug cannot be resolved, the answer is no: an operator can always
    re-send a message that reached nobody, and cannot unsend one that
    reached the wrong person. That asymmetry is the whole argument, and it
    is why the registry lookup's failure mode is silence rather than a
    fallback to "everyone".

    A slug is compared EXACTLY, while `"all"` is recognised in any case.
    The registry's own duplicate check is case-sensitive, so `alice` and
    `Alice` can legitimately be two different accounts; matching a slug
    case-insensitively would deliver a question addressed to one of them to
    both — the single way this could fail open. `qotd set`/`motd set`
    validate the slug against the registry as typed, so an operator who
    means `Alice` cannot get `alice` by accident either.
    """
    wanted = target.strip()
    if not wanted or wanted.lower() == ALL_TENANTS:
        return True
    return account_slug(config) == wanted


def seen_marker_path(config: Config) -> Path:
    """Where THIS account records the last broadcast it was shown."""
    return config.data_dir / SEEN_FILENAME


def last_seen_id(config: Config) -> str:
    """The id this account was last shown, or '' — never raises."""
    path = seen_marker_path(config)
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def pending_operator_message(config: Config, path: Path | None = None) -> OperatorMessage | None:
    """The message this account has not been shown yet, if any.

    Read-only on purpose. Marking it seen is a separate, explicit act
    (`acknowledge_delivery`) performed by whoever actually hands it to a
    person — because `compute_completeness` is also called by paths with
    no human on the other end (the profile page's progress band, the
    profile HTML export), and consuming a one-shot message there would
    burn it without anyone reading it.

    Addressing is checked BEFORE the seen marker, so an account that is not
    the addressee never records having seen somebody else's message — which
    would otherwise hide it if the operator later re-addressed it to
    everybody.
    """
    message = read_operator_message(path)
    if message is None:
        return None
    if not is_addressed_to(message.to, config):
        return None
    if message.id == last_seen_id(config):
        return None
    return message


def _keep_delivered_copy(config: Config, message: OperatorMessage) -> None:
    """Freeze the operator's words in this account's own database (#382).

    The seen-marker records an id; this records what the id MEANT. Without
    it a delivered message is unrecoverable the moment the operator edits
    the shared file, and "what was today's message?" has no answer on
    precisely the day it is asked.

    Never raises, for the same reason the marker write doesn't, and it runs
    BEFORE the marker on purpose: if only one of the two writes can succeed,
    the better failure is a message shown twice with a copy kept than a
    message shown once and lost.
    """
    from wingman.domain.delivered_message import DeliveredMessage
    from wingman.infrastructure.storage import Storage

    if not config.db_path.exists():
        # Reading or showing a message must never CREATE a workspace:
        # Storage() would write a full schema here and leave a
        # half-initialized data dir that every later db_path.exists() check
        # reads as a real workspace. No workspace, no history — the message
        # was still delivered, and the marker below still says so.
        _logger.warning("no workspace to keep the operator message in path=%s", config.db_path)
        return
    record = DeliveredMessage(
        message_id=message.id,
        action=message.action,
        why=message.why,
        how=message.how,
        to=message.to,
    )
    try:
        with Storage(config.db_path) as storage:
            storage.record_delivered_message(record)
    except Exception as exc:  # noqa: BLE001 — this runs AFTER the message was shown
        # Deliberately total. By the time this is reached the person has
        # already been told; a locked database, a read-only data dir (which
        # raises sqlite3.OperationalError, not OSError) or anything else
        # this store can throw must cost the copy, never the tool that just
        # successfully delivered the message. The TYPE is in the line so a
        # refactor's AttributeError does not read like a permissions
        # problem, matching `account_slug`'s catch above.
        _logger.warning(
            "could not keep a copy of the operator message id=%s error=%s: %s",
            message.id,
            type(exc).__name__,
            exc,
        )


def acknowledge_delivery(config: Config, message: OperatorMessage | None) -> None:
    """Record that this account has now been shown 'message'.

    A no-op for None, so a caller can pass 'report.operator_action'
    unconditionally. Failure to write is logged and swallowed: a read-only
    data dir must degrade to showing the message again, never to breaking
    the tool that just showed it.

    Two writes, both here and neither at the call sites (#382): the
    seen-marker that stops the message repeating, and a copy of the message
    itself so it can be read back later. Keeping the second write inside
    this function is the whole reason it is safe to add — the four surfaces
    that acknowledge are unchanged, so which surfaces deliver a message
    cannot drift from which surfaces remember one, and the profile page
    still does neither. RFC-065 named those four call sites as a drift
    hazard; adding a fifth thing to remember at each of them would have made
    it worse.
    """
    if message is None:
        return
    _keep_delivered_copy(config, message)
    path = seen_marker_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{message.id}\n", encoding="utf-8")
    except OSError as exc:
        _logger.warning("could not record operator message delivery path=%s error=%s", path, exc)


def _write_broadcast(broadcast: Broadcast, target: Path) -> Path:
    """Write one shared broadcast file (operator-side; needs write access).

    Exists so `wingman motd set` / `wingman qotd set` can produce a file
    this module's reader is guaranteed to accept. Hand-editing the JSON is
    still fine — but a typo there costs every account on the box its
    message silently, and a validated writer plus a `show` command is how
    that stops being an invisible failure.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(broadcast.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # 644, not 640: this is an announcement, not a credential, and what
    # actually gates it is the enclosing directory (750 root:wingman,
    # RFC-047). Tightening the file to 640 root:root would lock out every
    # account it is written FOR, which is a failure this reader would then
    # correctly report as silence — the worst of both.
    target.chmod(0o644)
    return target


def write_operator_message(message: OperatorMessage, path: Path | None = None) -> Path:
    """Write the shared message file (operator-side; needs write access)."""
    return _write_broadcast(message, path if path is not None else OPERATOR_MESSAGE_PATH)


def write_operator_question(question: OperatorQuestion, path: Path | None = None) -> Path:
    """Write the shared question file (operator-side; needs write access)."""
    return _write_broadcast(question, path if path is not None else OPERATOR_QUESTION_PATH)


__all__ = [
    "ALL_TENANTS",
    "MAX_FIELD_CHARS",
    "MAX_MESSAGE_BYTES",
    "OPERATOR_MESSAGE_PATH",
    "OPERATOR_QUESTION_PATH",
    "Broadcast",
    "OperatorMessage",
    "OperatorQuestion",
    "account_slug",
    "acknowledge_delivery",
    "is_addressed_to",
    "last_seen_id",
    "pending_operator_message",
    "permission_problem",
    "read_operator_message",
    "read_operator_question",
    "seen_marker_path",
    "write_operator_message",
    "write_operator_question",
]
