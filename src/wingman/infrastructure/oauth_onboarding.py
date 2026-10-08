"""Invite-only admission state for the shared OAuth resource server.

Authentication is not admission.  A verified identity that is not already
mapped to a tenant may be recorded here for an operator to inspect, but it
receives no workspace and no data until an operator consumes a reserved slug.
The file contains opaque provider subjects only; access tokens and email
addresses are never persisted.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import pwd
import re
import secrets
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wingman.infrastructure.logs import get_logger

if TYPE_CHECKING:
    from wingman.infrastructure.operator_notify import TodoistNotifier

_logger = get_logger("oauth_onboarding")

DEFAULT_PENDING_LIMIT = 128
_APPROVAL_TTL_SECONDS = 3600.0
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class PendingResult(Enum):
    """What recording a verified identity did. Truthy when it is in the queue."""

    ADDED = "added"
    SEEN_AGAIN = "seen-again"
    QUEUE_FULL = "queue-full"
    ALREADY_BOUND = "already-bound"

    def __bool__(self) -> bool:
        return self in (PendingResult.ADDED, PendingResult.SEEN_AGAIN)


class OAuthOnboardingError(RuntimeError):
    """Invite or pending-admission state could not be changed safely."""


@dataclass(frozen=True)
class PendingIdentity:
    issuer: str
    subject: str
    first_seen: float
    last_seen: float
    sign_in_count: int


@dataclass(frozen=True)
class ReservedInvite:
    slug: str
    created_at: float


def pending_reference(issuer: str, subject: str) -> str:
    """A short code a waiting person can quote and an operator can find.

    A stable 40-bit fingerprint of the caller's own (iss, sub). It does not
    disclose the subject directly and says nothing about other identities or
    tenants. It could confirm a guess only if subjects came from a small,
    guessable namespace, which WorkOS's opaque high-entropy subjects do not.
    Shown on the refusal they receive and on the matching `oauth-pending` row.
    """
    digest = hashlib.sha256(f"{issuer}\n{subject}".encode()).digest()
    code = base64.b32encode(digest).decode("ascii")[:8]
    return f"{code[:4]}-{code[4:]}"


def unapproved_message(issuer: str, subject: str) -> str:
    """What a verified but unapproved person is told, on every surface."""
    return (
        "Signed in, but this account is not approved on this Wingman server yet. "
        f"Send reference {pending_reference(issuer, subject)} to the person who invited you. "
        "After they approve it, reconnect."
    )


def onboarding_path_for(identity_path: Path) -> Path:
    """Keep admission state beside, but not inside, the live identity map."""
    return identity_path.with_name(f"{identity_path.stem}-onboarding.json")


class OAuthOnboardingStore:
    """A bounded, atomically replaced invite and pending-identity queue."""

    def __init__(self, path: Path, *, pending_limit: int = DEFAULT_PENDING_LIMIT) -> None:
        if pending_limit < 1:
            raise ValueError("pending_limit must be positive")
        self.path = path
        self.pending_limit = pending_limit

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        fd: int | None = None
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = self._read()
            yield state
            self._write(state)
        except OAuthOnboardingError:
            raise
        except OSError as exc:
            raise OAuthOnboardingError(
                f"OAuth onboarding state {self.path} could not be locked or written: {exc}"
            ) from exc
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "invites": [], "pending": []}
        except (OSError, json.JSONDecodeError) as exc:
            raise OAuthOnboardingError(
                f"OAuth onboarding state {self.path} is unreadable or malformed: {exc}"
            ) from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise OAuthOnboardingError(
                f"OAuth onboarding state {self.path} has an unsupported format"
            )
        invites = raw.get("invites")
        pending = raw.get("pending")
        if not isinstance(invites, list) or not isinstance(pending, list):
            raise OAuthOnboardingError(f"OAuth onboarding state {self.path} has malformed queues")
        self._validate_rows(invites, pending)
        return raw

    def _validate_rows(self, invites: list[Any], pending: list[Any]) -> None:
        """Reject malformed durable state before any caller indexes into it."""

        def number(value: object) -> bool:
            return (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )

        invite_slugs: set[str] = set()
        for position, item in enumerate(invites, start=1):
            slug = item.get("slug") if isinstance(item, dict) else None
            created_at = item.get("created_at") if isinstance(item, dict) else None
            if not isinstance(slug, str) or _SLUG.fullmatch(slug) is None or not number(created_at):
                raise OAuthOnboardingError(
                    f"OAuth onboarding state {self.path} has malformed invite entry {position}"
                )
            if slug in invite_slugs:
                raise OAuthOnboardingError(
                    f"OAuth onboarding state {self.path} repeats invite slug {slug!r}"
                )
            invite_slugs.add(slug)
            claim = item.get("claim")
            if claim is not None and (
                not isinstance(claim, dict)
                or not all(
                    isinstance(claim.get(field), str) and bool(claim.get(field))
                    for field in ("issuer", "subject", "token")
                )
                or not number(claim.get("expires_at"))
            ):
                raise OAuthOnboardingError(
                    f"OAuth onboarding state {self.path} has malformed approval claim "
                    f"for invite {slug!r}"
                )

        if len(pending) > self.pending_limit:
            raise OAuthOnboardingError(
                f"OAuth onboarding state {self.path} exceeds the pending queue limit "
                f"of {self.pending_limit}"
            )
        pending_keys: set[tuple[str, str]] = set()
        for position, item in enumerate(pending, start=1):
            issuer = item.get("issuer") if isinstance(item, dict) else None
            subject = item.get("subject") if isinstance(item, dict) else None
            first_seen = item.get("first_seen") if isinstance(item, dict) else None
            last_seen = item.get("last_seen") if isinstance(item, dict) else None
            sign_in_count = item.get("sign_in_count") if isinstance(item, dict) else None
            if (
                not isinstance(issuer, str)
                or not issuer
                or not isinstance(subject, str)
                or not subject
                or not number(first_seen)
                or not number(last_seen)
                or not isinstance(sign_in_count, int)
                or isinstance(sign_in_count, bool)
                or sign_in_count < 1
            ):
                raise OAuthOnboardingError(
                    f"OAuth onboarding state {self.path} has malformed pending entry {position}"
                )
            key = (issuer, subject)
            if key in pending_keys:
                raise OAuthOnboardingError(
                    f"OAuth onboarding state {self.path} repeats pending identity "
                    f"({issuer!r}, {subject!r})"
                )
            pending_keys.add(key)

    def _write(self, state: dict[str, Any]) -> None:
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
            raise OAuthOnboardingError(
                f"OAuth onboarding state {self.path} could not be replaced; "
                f"the previous state was preserved: {exc}"
            ) from exc

    @staticmethod
    def _expire_claim(invite: dict[str, Any], now: float) -> None:
        claim = invite.get("claim")
        if isinstance(claim, dict) and claim.get("expires_at", 0) <= now:
            invite.pop("claim", None)

    def reserve_invite(
        self, slug: str, *, existing_slugs: set[str], now: float | None = None
    ) -> None:
        if _SLUG.fullmatch(slug) is None:
            raise OAuthOnboardingError(
                "invite slug must use lowercase letters, numbers and single hyphens "
                "(1-63 characters)"
            )
        if slug in existing_slugs:
            raise OAuthOnboardingError(f"tenant slug {slug!r} already exists")
        created_at = time.time() if now is None else now
        with self._locked() as state:
            if any(item.get("slug") == slug for item in state["invites"]):
                raise OAuthOnboardingError(f"invite slug {slug!r} is already reserved")
            state["invites"].append({"slug": slug, "created_at": created_at})

    def record_pending(
        self, issuer: str, subject: str, *, now: float | None = None
    ) -> PendingResult:
        """Record a verified unprovisioned identity. ADDED only the first time."""
        if not issuer or not subject:
            raise OAuthOnboardingError("pending identity issuer and subject must be non-empty")
        seen_at = time.time() if now is None else now
        with self._locked() as state:
            for item in state["pending"]:
                if item.get("issuer") == issuer and item.get("subject") == subject:
                    item["last_seen"] = seen_at
                    item["sign_in_count"] = int(item.get("sign_in_count", 1)) + 1
                    return PendingResult.SEEN_AGAIN
            if len(state["pending"]) >= self.pending_limit:
                return PendingResult.QUEUE_FULL
            state["pending"].append(
                {
                    "issuer": issuer,
                    "subject": subject,
                    "first_seen": seen_at,
                    "last_seen": seen_at,
                    "sign_in_count": 1,
                }
            )
            return PendingResult.ADDED

    def discard_pending(self, issuer: str, subject: str) -> bool:
        """Drop one exact identity from the queue; False means it was not there.

        For identities bound by 'tenant oauth-bind' rather than consumed by an
        approval: once bound they are no longer awaiting anything. A host that
        never queued anyone has no state file, and this does not create one.
        """
        try:
            if not self.path.exists():
                return False
        except OSError as exc:
            raise OAuthOnboardingError(
                f"OAuth onboarding state {self.path} could not be checked: {exc}"
            ) from exc
        with self._locked() as state:
            kept = [
                item
                for item in state["pending"]
                if not (item.get("issuer") == issuer and item.get("subject") == subject)
            ]
            removed = len(kept) != len(state["pending"])
            state["pending"] = kept
            return removed

    def pending(self) -> list[PendingIdentity]:
        with self._locked() as state:
            return [
                PendingIdentity(
                    issuer=str(item["issuer"]),
                    subject=str(item["subject"]),
                    first_seen=float(item["first_seen"]),
                    last_seen=float(item["last_seen"]),
                    sign_in_count=int(item["sign_in_count"]),
                )
                for item in state["pending"]
            ]

    def invites(self) -> list[ReservedInvite]:
        with self._locked() as state:
            return [
                ReservedInvite(slug=str(item["slug"]), created_at=float(item["created_at"]))
                for item in state["invites"]
            ]

    def begin_approval(
        self, slug: str, issuer: str, subject: str, *, now: float | None = None
    ) -> str:
        current = time.time() if now is None else now
        with self._locked() as state:
            invite = next((item for item in state["invites"] if item.get("slug") == slug), None)
            if invite is None:
                raise OAuthOnboardingError(f"invite slug {slug!r} is not reserved")
            self._expire_claim(invite, current)
            if "claim" in invite:
                raise OAuthOnboardingError(f"invite slug {slug!r} is already being approved")
            match = next(
                (
                    item
                    for item in state["pending"]
                    if item.get("issuer") == issuer and item.get("subject") == subject
                ),
                None,
            )
            if match is None:
                raise OAuthOnboardingError(
                    f"identity ({issuer!r}, {subject!r}) is not in the verified pending queue"
                )
            token = secrets.token_urlsafe(24)
            invite["claim"] = {
                "issuer": issuer,
                "subject": subject,
                "token": token,
                "expires_at": current + _APPROVAL_TTL_SECONDS,
            }
            return token

    def finish_approval(self, slug: str, token: str) -> None:
        with self._locked() as state:
            invite = next((item for item in state["invites"] if item.get("slug") == slug), None)
            claim = invite.get("claim") if isinstance(invite, dict) else None
            if not isinstance(claim, dict) or not secrets.compare_digest(
                str(claim.get("token", "")), token
            ):
                raise OAuthOnboardingError("OAuth approval claim is absent, expired, or changed")
            issuer = claim.get("issuer")
            subject = claim.get("subject")
            state["invites"] = [item for item in state["invites"] if item is not invite]
            state["pending"] = [
                item
                for item in state["pending"]
                if not (item.get("issuer") == issuer and item.get("subject") == subject)
            ]

    def cancel_approval(self, slug: str, token: str) -> None:
        with self._locked() as state:
            invite = next((item for item in state["invites"] if item.get("slug") == slug), None)
            if invite is None:
                return
            claim = invite.get("claim")
            if isinstance(claim, dict) and secrets.compare_digest(
                str(claim.get("token", "")), token
            ):
                invite.pop("claim", None)


def record_verified_pending(
    identity_path: Path,
    store: OAuthOnboardingStore,
    issuer: str,
    subject: str,
) -> PendingResult:
    """Queue an identity only if the current on-disk map is still unbound.

    The live server map reloads asynchronously after approval.  Holding the
    same writer lock used by binding closes the interval in which that stale
    in-memory map could re-add an identity that approval just removed.
    """
    from wingman.infrastructure.oauth_bearer import _identity_map_lock, _identity_map_or_empty

    with _identity_map_lock(identity_path):
        if _identity_map_or_empty(identity_path).slug_for(issuer, subject) is not None:
            return PendingResult.ALREADY_BOUND
        return store.record_pending(issuer, subject)


def pending_recorder(
    identity_path: Path,
    store: OAuthOnboardingStore,
    notifier: TodoistNotifier | None = None,
) -> Callable[[str, str], PendingResult]:
    """The recorder the MCP and browser refusals call.

    Tells the operator only when an identity is first queued: repeat sign-ins
    and restarts find the row already there. Notification is best effort and
    cannot change what was recorded or what the caller is told.
    """
    from wingman.infrastructure.operator_notify import PendingNotice

    def record(issuer: str, subject: str) -> PendingResult:
        result = record_verified_pending(identity_path, store, issuer, subject)
        if result is PendingResult.ADDED and notifier is not None:
            try:
                notifier.notify(
                    PendingNotice(
                        issuer=issuer,
                        reference=pending_reference(issuer, subject),
                        first_seen=time.time(),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — notification never blocks admission
                _logger.warning("operator notification could not be started: %s", exc)
        return result

    return record


@contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    """Serialize registry replacement between concurrent operator approvals."""
    lock_path = path.with_name(f".{path.name}.onboarding.lock")
    fd: int | None = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise OAuthOnboardingError(f"tenant registry {path} could not be locked: {exc}") from exc
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass


def _register_unprivileged_tenant(path: Path, slug: str, data_dir: Path) -> bool:
    """Append one exact fail-closed tenant entry by atomic replacement."""
    from wingman.infrastructure.tenants import TenantRegistryError, load_registry

    with _registry_lock(path):
        try:
            tenants = load_registry(path)
        except TenantRegistryError as exc:
            raise OAuthOnboardingError(str(exc)) from exc
        existing = next((tenant for tenant in tenants if tenant.slug == slug), None)
        if existing is not None:
            if existing.data_dir == data_dir and not existing.privileged and not existing.funded:
                return False
            raise OAuthOnboardingError(
                f"tenant slug {slug!r} already exists with different settings; nothing changed"
            )
        try:
            old = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            old = ""
        except OSError as exc:
            raise OAuthOnboardingError(f"tenant registry {path} could not be read: {exc}") from exc
        addition = (
            ("" if not old or old.endswith("\n\n") else "\n")
            + "[[tenant]]\n"
            + f"slug = {json.dumps(slug)}\n"
            + f"data_dir = {json.dumps(str(data_dir))}\n"
        )
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(old)
                handle.write(addition)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                Path(temporary).chmod(path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        except OSError as exc:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
            raise OAuthOnboardingError(
                f"tenant registry {path} could not be replaced; the previous registry "
                f"was preserved: {exc}"
            ) from exc
        return True


@contextmanager
def _as_service_user(username: str) -> Iterator[None]:
    """Run service-owned writes as the account that serves the tenant.

    The approval command also has to replace the root-owned tenant registry,
    so it runs as root and drops effective uid/gid only around onboarding,
    identity-map, and workspace writes.  That keeps those mode-0600 files
    readable by the long-running service instead of accidentally making them
    root-owned during approval.
    """
    try:
        account = pwd.getpwnam(username)
    except KeyError as exc:
        raise OAuthOnboardingError(f"service account {username!r} does not exist") from exc
    original_uid = os.geteuid()
    original_gid = os.getegid()
    if original_uid not in (0, account.pw_uid):
        raise OAuthOnboardingError(
            f"oauth-approve must run as root or as service account {username!r}"
        )
    changed = original_uid == 0 and account.pw_uid != 0
    try:
        if changed:
            os.setegid(account.pw_gid)
            os.seteuid(account.pw_uid)
        yield
    except OSError as exc:
        raise OAuthOnboardingError(
            f"service account {username!r} could not write its OAuth or workspace state: {exc}"
        ) from exc
    finally:
        if changed:
            os.seteuid(original_uid)
            os.setegid(original_gid)


def approve_pending_tenant(
    *,
    identity_path: Path,
    registry_path: Path,
    data_root: Path,
    slug: str,
    issuer: str,
    subject: str,
    service_user: str,
) -> Path:
    """Consume one invite and create its isolated, unfunded tenant.

    The onboarding and identity claims serialize competing approvals.  Each
    durable step is idempotent, so interruption leaves a safe inaccessible
    partial tenant that the same command can finish; it never grants a
    different identity or silently changes privilege/funding.
    """
    from wingman.infrastructure.config import Config
    from wingman.infrastructure.oauth_bearer import (
        IdentityMapError,
        bind_trusted_identity,
        release_trusted_identity_reservation,
        reserve_trusted_identity,
    )
    from wingman.infrastructure.storage import Storage
    from wingman.providers.router import DEFAULT_MODELS_TOML

    store = OAuthOnboardingStore(onboarding_path_for(identity_path))
    with _as_service_user(service_user):
        approval = store.begin_approval(slug, issuer, subject)
    reservation: str | None = None
    completed = False
    try:
        data_dir = data_root / slug
        marker = data_dir / ".oauth-approval.json"
        with _as_service_user(service_user):
            reservation = reserve_trusted_identity(identity_path, issuer, subject, slug)
            if data_dir.exists():
                try:
                    marker_data = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise OAuthOnboardingError(
                        f"tenant directory {data_dir} already exists without a readable matching "
                        f"OAuth approval marker; nothing was overwritten: {exc}"
                    ) from exc
                if marker_data != {"issuer": issuer, "slug": slug, "subject": subject}:
                    raise OAuthOnboardingError(
                        f"tenant directory {data_dir} belongs to a different approval; "
                        "nothing was overwritten"
                    )
            else:
                data_dir.mkdir(parents=True)
                marker.write_text(
                    json.dumps({"issuer": issuer, "slug": slug, "subject": subject}, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                marker.chmod(0o600)
            config = Config(data_dir=data_dir, data_dir_source="OAuth invite approval")
            config.inbox_dir.mkdir(parents=True, exist_ok=True)
            config.reports_dir.mkdir(parents=True, exist_ok=True)
            Storage(config.db_path).close()
            if not config.models_config_path.exists():
                config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
        _register_unprivileged_tenant(registry_path, slug, data_dir)
        with _as_service_user(service_user):
            bind_trusted_identity(identity_path, issuer, subject, slug, reservation=reservation)
            reservation = None
            store.finish_approval(slug, approval)
            marker.unlink(missing_ok=True)
        completed = True
        return data_dir
    except IdentityMapError as exc:
        raise OAuthOnboardingError(str(exc)) from exc
    finally:
        if reservation is not None:
            try:
                with _as_service_user(service_user):
                    release_trusted_identity_reservation(
                        identity_path, issuer, subject, reservation
                    )
            except IdentityMapError:
                pass
        if not completed:
            with _as_service_user(service_user):
                store.cancel_approval(slug, approval)
