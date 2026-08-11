"""The operator's one box-wide message, read as an ACTION (issue #224).

Whoever runs a shared box sometimes knows something the workspace cannot:
a parser changed and everyone's CV needs re-ingesting, a migration is
coming, the machine moves on Friday. There was no way to say it. This is
that way, and it is deliberately the smallest one that works.

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

**Storage follows RFC-047, it does not invent a mechanism.** The message
is one root-provisioned file under `/etc/wingman/` — the same box-wide,
group-readable tier `global-secrets.env` already established, at the same
mode 640 root:wingman. Nothing here ever writes to that directory as part
of a normal read; the per-account "I have seen this" marker lives in that
account's OWN data dir, so a pure broadcast needs no write-back and no
group-writable state at all. (A question of the day WOULD need one; it is
not built here — see RFC-065.)

**Unreadable degrades to silent, exactly as the keys ladder does.**
`keys.read_global_keys` treats a missing or unreadable global file as an
empty dict rather than an error, because a file owned by root that this
account may not be able to read must never be the reason someone's
workspace stops answering. The same rule holds here, and matters more:
this file is on the path of `completeness`, `setup_guide` and the Progress
page, so a stray comma in the operator's JSON must cost everyone their
message, never their status. Every failure mode — absent, unreadable,
oversized, not JSON, JSON of the wrong shape, missing a required field —
returns None and logs. `wingman motd show` exists precisely so that
silence is checkable by the person who caused it.
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

# The per-account "last delivered id", in the account's own data dir.
SEEN_FILENAME = "operator-message-seen"

# A broadcast that must fit in a next-action line. The cap exists so a
# runaway or hostile file cannot be read into memory or pasted wholesale
# into every account's status output; anything larger is treated as
# malformed rather than truncated, since a truncated instruction is worse
# than no instruction.
MAX_MESSAGE_BYTES = 8 * 1024
MAX_FIELD_CHARS = 500


class OperatorMessage(BaseModel):
    """One pending instruction from whoever runs this box.

    `id` is what makes "seen once" mean anything: an account records the
    id it was shown, and sees nothing again until the operator changes it.
    Deliberately opaque — a date, a slug, a counter — because the operator
    owns the file and this code only compares it for equality.

    `action` is the imperative ("Re-ingest your CV"), because it lands in
    a list of things to do. `why` and `how` mirror `NextAction`'s own two
    fields so an operator action is not structurally poorer than a
    computed one; both are optional, and `application.completeness`
    supplies an honest fallback rather than inventing a reason.
    """

    id: str
    action: str
    why: str = ""
    how: str = ""


def _clean(value: Any, *, field: str, path: Path) -> str | None:
    """One field, or None if it is not a usable string."""
    if not isinstance(value, str):
        _logger.warning("operator message field not a string path=%s field=%s", path, field)
        return None
    text = " ".join(value.split())
    if len(text) > MAX_FIELD_CHARS:
        _logger.warning("operator message field too long path=%s field=%s", path, field)
        return None
    return text


def read_operator_message(path: Path | None = None) -> OperatorMessage | None:
    """The current broadcast, or None when there isn't a usable one.

    Never raises. 'path' is injectable for tests; production callers always
    get the real /etc/wingman/motd.json.
    """
    source = path if path is not None else OPERATOR_MESSAGE_PATH
    try:
        if not source.is_file():
            return None
        raw = source.read_bytes()
    except OSError as exc:
        # Unreadable is the expected case on a misconfigured box (mode 600,
        # or an account not in the wingman group). Silence, not a stack
        # trace in someone's status output.
        _logger.warning("operator message unreadable path=%s error=%s", source, exc)
        return None
    if len(raw) > MAX_MESSAGE_BYTES:
        _logger.warning("operator message too large path=%s bytes=%d", source, len(raw))
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _logger.warning("operator message unparseable path=%s error=%s", source, exc)
        return None
    if not isinstance(payload, dict):
        _logger.warning("operator message is not a JSON object path=%s", source)
        return None

    fields: dict[str, str] = {}
    for field, required in (("id", True), ("action", True), ("why", False), ("how", False)):
        value = payload.get(field, "")
        cleaned = _clean(value, field=field, path=source)
        if cleaned is None:
            return None
        if required and not cleaned:
            _logger.warning("operator message missing %s path=%s", field, source)
            return None
        fields[field] = cleaned
    return OperatorMessage(**fields)


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
    """The broadcast this account has not been shown yet, if any.

    Read-only on purpose. Marking it seen is a separate, explicit act
    (`acknowledge_delivery`) performed by whoever actually hands it to a
    person — because `compute_completeness` is also called by paths with
    no human on the other end (the profile page's progress band, the
    profile HTML export), and consuming a one-shot message there would
    burn it without anyone reading it.
    """
    message = read_operator_message(path)
    if message is None:
        return None
    if message.id == last_seen_id(config):
        return None
    return message


def acknowledge_delivery(config: Config, message: OperatorMessage | None) -> None:
    """Record that this account has now been shown 'message'.

    A no-op for None, so a caller can pass 'report.operator_action'
    unconditionally. Failure to write is logged and swallowed: a read-only
    data dir must degrade to showing the message again, never to breaking
    the tool that just showed it.
    """
    if message is None:
        return
    path = seen_marker_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{message.id}\n", encoding="utf-8")
    except OSError as exc:
        _logger.warning("could not record operator message delivery path=%s error=%s", path, exc)


def write_operator_message(message: OperatorMessage, path: Path | None = None) -> Path:
    """Write the shared broadcast file (operator-side; needs write access).

    Exists so `wingman motd set` can produce a file this module's reader is
    guaranteed to accept. Hand-editing the JSON is still fine — but a typo
    there costs every account on the box its message silently, and a
    validated writer plus `wingman motd show` is how that stops being an
    invisible failure.
    """
    target = path if path is not None else OPERATOR_MESSAGE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(message.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # 644, not 640: this is an announcement, not a credential, and what
    # actually gates it is the enclosing directory (750 root:wingman,
    # RFC-047). Tightening the file to 640 root:root would lock out every
    # account it is written FOR, which is a failure this reader would then
    # correctly report as silence — the worst of both.
    target.chmod(0o644)
    return target


__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_MESSAGE_BYTES",
    "OPERATOR_MESSAGE_PATH",
    "OperatorMessage",
    "acknowledge_delivery",
    "last_seen_id",
    "pending_operator_message",
    "read_operator_message",
    "seen_marker_path",
    "write_operator_message",
]
