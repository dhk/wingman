"""The last terminal answer a model provider gave this workspace (#528).

'status' and 'completeness' used to answer "can this workspace call a
model?" by asking whether a key resolves. A key that resolves and is out of
quota passed that check, so a month-long outage of every model-backed tool
was reported as seven healthy counts and nothing else. The honest signal is
the last thing the provider actually said, so that is what gets kept here:
one small JSON file per workspace, written when a call is refused for a
reason that will not clear on retry, and removed by the next call that
succeeds.

Deliberately NOT a probe. Asking the provider from 'status' would spend
money to find out whether the workspace can spend money, and would turn a
read-only tool into a metered one.

What counts as terminal is narrow on purpose. A 429, a 5xx, an overload or
a network blip says nothing about tomorrow, and latching a banner on one
would cry wolf until the next success happened to clear it. Only a refusal
that names the account — bad or revoked credentials, a spend limit, an
empty balance — is recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

FILENAME = "model-health.json"

# A 400 is usually the request's own fault (too long, malformed) and says
# nothing about the next call. These two are the account's fault: the
# wording is Anthropic's for a workspace spend limit and an empty balance.
_ACCOUNT_REFUSALS = re.compile(r"usage limits|credit balance", re.IGNORECASE)
_RESET = re.compile(r"regain access on (\d{4}-\d{2}-\d{2}) at (\d{2}:\d{2}) UTC")
# The provider's own words are shown back, but never at unbounded length.
_REASON_LIMIT = 300


class ModelRejection(BaseModel):
    """One terminal refusal: who refused, why in their words, and until when."""

    provider: str
    status_code: int
    reason: str
    recorded_at: datetime
    # When the provider said access returns, if it said. After this the
    # record no longer claims anything: the next call is the evidence.
    resets_at: datetime | None = None
    # Which credential was refused — a truncated digest, never the key. A
    # workspace that swaps in a new key has not been refused yet, and must
    # not be told it has.
    key_fingerprint: str | None = None


def health_path(data_dir: Path) -> Path:
    return data_dir / FILENAME


def key_fingerprint(api_key: str | None) -> str | None:
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def is_terminal(status_code: int, message: str) -> bool:
    """Would retrying this call later, unchanged, fail the same way?"""
    if status_code in (401, 402, 403):
        return True
    return status_code == 400 and bool(_ACCOUNT_REFUSALS.search(message))


def parse_reset(message: str) -> datetime | None:
    match = _RESET.search(message)
    if match is None:
        return None
    try:
        return datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}:00+00:00")
    except ValueError:
        return None


def record_rejection(
    path: Path,
    *,
    provider: str,
    status_code: int,
    message: str,
    api_key: str | None,
    now: datetime | None = None,
) -> None:
    """Keep the refusal. Never raises: the call has already failed with its
    own error, and a bookkeeping failure must not replace that message."""
    rejection = ModelRejection(
        provider=provider,
        status_code=status_code,
        reason=" ".join(message.split())[:_REASON_LIMIT],
        recorded_at=now or datetime.now(UTC),
        resets_at=parse_reset(message),
        key_fingerprint=key_fingerprint(api_key),
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(rejection.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return


def clear_rejection(path: Path) -> None:
    """A call just succeeded, so whatever was recorded is no longer true."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def current_rejection(
    path: Path,
    provider: str,
    api_key: str | None,
    now: datetime | None = None,
) -> ModelRejection | None:
    """The recorded refusal, if it still describes this workspace.

    It does not once the provider's own reset time has passed, or once the
    key it was recorded against is no longer the one this workspace would
    spend. An unreadable or malformed file reports nothing rather than
    inventing an outage.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        rejection = ModelRejection.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return None
    if rejection.provider != provider:
        return None
    if rejection.key_fingerprint != key_fingerprint(api_key):
        return None
    if rejection.resets_at is not None and (now or datetime.now(UTC)) >= rejection.resets_at:
        return None
    return rejection


def describe(rejection: ModelRejection) -> str:
    """One clause for a status line: when, why, and when it ends."""
    when = rejection.recorded_at.strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f"the provider refused the last model call at {when} "
        f"({rejection.status_code}: {rejection.reason})"
    )
    if rejection.resets_at is not None:
        text += f"; it says access returns {rejection.resets_at.strftime('%Y-%m-%d %H:%M UTC')}"
    return text
