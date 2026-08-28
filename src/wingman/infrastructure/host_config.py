"""Non-secret host settings, and the one-time migration off the old flat
canonical host file (#122's `~/.config/keys.env`, RFC-040) onto the split
layout `~/.config/wingman/{wingman.env,secrets.env}` (RFC-046).

Mirrors `dhk/alexandria`#31's `alexandria.env`/`secrets.env` split as
closely as wingman's existing conventions allow: `wingman.env` holds
non-secret host/path settings (today, just `WINGMAN_REPO` — the setting
`scripts/wingman-ctl` needs to find this checkout from a context that
never exported it, e.g. cron or a fresh SSH session before `.bashrc`
runs); `secrets.env` holds exactly what `keys.py`'s `KNOWN_KEYS` ladder
already recognized (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`). Secrets never
touch this module — `keys.py` still owns `secrets.env`'s steady-state path
and reader; this module only writes it once, during migration, from
values already destined for exactly the same file `keys.py` would read.

This is a firm cutover, not a permanent fallback (owner directive): once
migrated, nothing in this codebase reads the old flat file again except
this migration itself. The one-time move is triggered automatically and
safely — `migrate_legacy_host_file` is idempotent and cheap (a few
`Path.exists()` checks once already migrated), so every process entrypoint
calls it unconditionally before it does anything else that depends on the
new layout, and the CLI/MCP entrypoints print its result so a migration on
someone's production box is never a silent background action.
"""

from __future__ import annotations

import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from wingman.infrastructure.keys import KNOWN_KEYS, host_keys_path
from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.host_config")

WINGMAN_ENV_FILENAME = "wingman.env"

# Non-secret settings this codebase actually reads from wingman.env today.
# Table-driven and easy to extend (mirrors KNOWN_KEYS's shape). WINGMAN_REPO
# is scripts/wingman-ctl's host-file fallback; WINGMAN_OPERATOR_NAME is the
# per-account label (e.g. "Trent") that feature_request.py stamps into a
# filed issue's body when the same GitHub credential is shared across
# accounts (issue #205's follow-up) — attribution the token itself can no
# longer provide once it's shared, so the app supplies it in the body
# instead. WINGMAN_DATA_DIR and friends stay environment/systemd-
# Environment=-only for now: nothing currently reads them from a host
# file, and adding that ladder without a driving need would be exactly the
# gold-plating the design brief warned against — the frozenset below is
# where a future one gets added, one name at a time. WINGMAN_TENANT_REGISTRY
# (RFC-048, infrastructure.tenants.tenant_registry_path) is the newest
# resident: the shared multi-tenant process's registry path, set once per
# box so 'wingman-mcp --http --tenant-registry' and every 'wingman tenant
# ...' command agree on the same file without an explicit flag each time.
# WINGMAN_GDRIVE_CLIENT_ID / WINGMAN_GDRIVE_CLIENT_SECRET (RFC-053, #205)
# identify wingman's own OAuth client to Google's device-authorization
# flow — one shared client for every account on a box, the same value on
# dhk's and trent's Drive pushes alike, not a per-account secret (the same
# reasoning `gh`/`rclone` ship their own public client id/secret: it
# names the app, not any one server or user). That makes it a host
# SETTING, not a KNOWN_KEYS secret — it belongs here rather than in
# secrets.env's per-account ladder. infrastructure.gdrive_auth reads it
# with its own extra fallback beneath this file: an environment variable
# wins over a wingman.env line, which wins over the placeholder default
# compiled into the module (no real Google Cloud project exists yet).
HOST_SETTINGS = frozenset(
    {
        "WINGMAN_REPO",
        "WINGMAN_FEATURE_REPO",
        "WINGMAN_OPERATOR_NAME",
        "WINGMAN_TENANT_REGISTRY",
        "WINGMAN_GDRIVE_CLIENT_ID",
        "WINGMAN_GDRIVE_CLIENT_SECRET",
        "WINGMAN_WOVEN_NETWORKS",
    }
)


def operator_name(home: Path | None = None) -> str | None:
    """The WINGMAN_OPERATOR_NAME setting, or None when unset — the label
    feature_request.py stamps into a filed issue's body to say who actually
    submitted it, since a shared GITHUB_API_ISSUES_KEY can no longer let
    GitHub's own 'opened by' field answer that question."""
    return read_host_settings(home).get("WINGMAN_OPERATOR_NAME")


def woven_networks(home: Path | None = None) -> tuple[str, ...]:
    """The named network groups this box may reason about (WINGMAN_WOVEN_NETWORKS).

    A "network group" is a working group whose members pooled their
    LinkedIn exports into one Woven graph — Dave's and Trent's connections
    in one snapshot, say. Woven itself has no notion of this: one running
    Woven serves exactly one `WOVEN_GRAPH_PATH`, its nodes carry no group
    label, and a warm path it returns says nothing about whose pooled data
    produced it. That is fine while a single graph exists and everyone in
    it is a trusted handful. It stops being fine the moment there are two.

    So this is NOT connection configuration — wingman does not dial Woven
    (RFC-043's server-to-server bridge is retired; the agent is the
    integration point). It is a provenance and consent declaration: the
    groups whose pooled data this workspace is entitled to reason about,
    named, so an answer can be attributed to one of them.

    The rule this exists to make possible:

    - **no names declared** — warm-path material is unattributed. Usable in
      conversation, but nothing may record it as evidence, because there is
      no answer to "whose network said so".
    - **exactly one name** — attribution is unambiguous and automatic.
      Today's state, and nothing changes for it.
    - **two or more names** — an answer must say which group it came from.
      Silence is not a default here: two working groups are two different
      sets of people who consented to two different pools, and quietly
      merging them is the privacy failure this flag is built to prevent.

    Comma-separated, order preserved, blanks dropped, duplicates removed.
    This file's parser is shlex-strict, so an unquoted value may not contain
    spaces — both of these are accepted, and a stray unquoted space gets the
    usual file:line error rather than being silently misread:

        WINGMAN_WOVEN_NETWORKS=personal,sanderson
        WINGMAN_WOVEN_NETWORKS="personal, sanderson"

    Returns an empty tuple when unset — inert, never an error, the same
    posture every other optional host setting takes.
    """
    raw = read_host_settings(home).get("WINGMAN_WOVEN_NETWORKS", "")
    seen: dict[str, None] = {}
    for part in raw.split(","):
        name = part.strip()
        if name:
            seen.setdefault(name, None)
    return tuple(seen)


def woven_attribution(home: Path | None = None) -> str | None:
    """The one group a warm-path answer belongs to, or None when the caller
    has to say which — the two ambiguous cases (none declared, several
    declared) are deliberately the same answer here, because both mean
    "wingman cannot name the source on its own"."""
    names = woven_networks(home)
    return names[0] if len(names) == 1 else None


# The pre-#122-split flat file this migration retires: '~/.config/keys.env'.
_LEGACY_HOST_KEYS_FILENAME = "keys.env"


class HostEnvironmentError(RuntimeError):
    """The 'wingman.env' file is present but malformed."""


def wingman_env_path(home: Path | None = None) -> Path:
    """The one canonical non-secret host settings file, 0600."""
    return (
        (home if home is not None else Path.home()) / ".config" / "wingman" / WINGMAN_ENV_FILENAME
    )


def legacy_host_keys_path(home: Path | None = None) -> Path:
    """The old flat file this migration moves off of: '~/.config/keys.env'."""
    return (home if home is not None else Path.home()) / ".config" / _LEGACY_HOST_KEYS_FILENAME


def _parse_quoted_value(value: str, *, path: Path, line_number: int) -> str:
    try:
        parsed = shlex.split(value, comments=False, posix=True)
    except ValueError as exc:
        raise HostEnvironmentError(f"{path}:{line_number}: invalid quoted value: {exc}") from exc
    if len(parsed) != 1:
        raise HostEnvironmentError(
            f"{path}:{line_number}: values containing whitespace must be quoted"
        )
    return parsed[0]


def read_host_settings(home: Path | None = None) -> dict[str, str]:
    """Recognized NAME=value entries from wingman.env; unrecognized names ignored.

    Quote-aware (shlex), unlike the secrets ladder's lenient partition-on-'='
    parser, and raises HostEnvironmentError with file:line context on a
    malformed line — this file is hand-edited far more often than
    secrets.env (an operator setting WINGMAN_REPO once per box), so a typo
    should say exactly where it is rather than being silently ignored or
    silently misread. Mirrors alexandria's `read_host_environment`
    (`alexandria`#31).
    """
    path = wingman_env_path(home)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise HostEnvironmentError(f"{path}:{line_number}: expected NAME=value")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name not in HOST_SETTINGS:
            continue
        value = _parse_quoted_value(raw_value.strip(), path=path, line_number=line_number)
        if not value:
            raise HostEnvironmentError(f"{path}:{line_number}: {name} may not be empty")
        values[name] = value
    return values


def _environment_line(name: str, value: str) -> str:
    if not name or not name.isascii() or not name.replace("_", "A").isalnum() or name[0].isdigit():
        raise HostEnvironmentError(f"invalid environment variable name: {name!r}")
    if "\n" in value or "\r" in value:
        raise HostEnvironmentError(f"{name} contains a newline and cannot be written safely")
    return f"{name}={shlex.quote(value)}"


def _atomic_write(path: Path, content: str) -> None:
    """Write 0600, atomically (temp file + rename) — never a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _parse_legacy_line(raw_line: str) -> tuple[str, str] | None:
    """The same lenient partition-on-'=' the legacy file has always been
    read with (`keys.py`'s `_parse_known_keys_file`) — never shlex. A real
    production file (lobster's) must never fail *this* migration just
    because the migration got pickier than the code that originally wrote
    the file. Returns None for a blank line, a comment, or anything that
    doesn't parse as NAME=value.
    """
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    name, _, value = line.partition("=")
    name = name.strip()
    value = value.strip()
    if not name or not value:
        return None
    return name, value


@dataclass(frozen=True)
class MigrationResult:
    """What 'migrate_legacy_host_file' did, or why it did nothing."""

    migrated: bool
    secret_names: list[str] = field(default_factory=list)
    setting_names: list[str] = field(default_factory=list)
    legacy_backup: Path | None = None
    detail: str = ""


def migrate_legacy_host_file(home: Path | None = None) -> MigrationResult:
    """One-time, automatic move off '~/.config/keys.env' onto the split
    layout '~/.config/wingman/{wingman.env,secrets.env}' (RFC-046).

    Never loses a value: every recognized secret (KNOWN_KEYS) goes to
    secrets.env; every other non-blank, non-comment line — a recognized
    setting like WINGMAN_REPO, or anything else a human or systemd put in
    the old file (RFC-040's file also doubled as a plain systemd
    EnvironmentFile, so it may carry lines this codebase itself never
    parsed, e.g. WINGMAN_ALLOWED_HOSTS) — goes to wingman.env verbatim, so
    nothing that used to work via that file's systemd EnvironmentFile= role
    stops working. Comments and blank lines are preserved in wingman.env
    (not re-attached to whichever value they may have annotated — a human
    can move a stray comment by hand; the requirement is never losing a
    VALUE).

    The old file is never deleted or overwritten — only renamed to a
    timestamped '.migrated-YYYYMMDD' backup, and only after both new files
    have been written successfully, so a failure partway through this
    function leaves the legacy file exactly as it was for the next attempt.

    Idempotent: a no-op whenever EITHER new file already exists (even if
    the legacy file is still sitting there too — that combination means a
    human already started or completed a manual move, and this function
    must never overwrite whatever they set up by hand), or the legacy file
    is absent. Safe to call from every process entrypoint, every time.
    """
    legacy = legacy_host_keys_path(home)
    new_secrets = host_keys_path(home)
    new_settings = wingman_env_path(home)

    if new_secrets.exists() or new_settings.exists():
        return MigrationResult(migrated=False, detail="already on the new host-config layout")
    if not legacy.is_file():
        return MigrationResult(
            migrated=False, detail="no legacy host file (~/.config/keys.env) to migrate"
        )

    known_secret_names = set(KNOWN_KEYS.values())
    secrets: dict[str, str] = {}
    settings: dict[str, str] = {}
    passthrough: list[str] = []  # comments / blanks, preserved verbatim in wingman.env

    for raw_line in legacy.read_text(encoding="utf-8").splitlines():
        parsed = _parse_legacy_line(raw_line)
        if parsed is None:
            if raw_line.strip():
                passthrough.append(raw_line.rstrip())
            continue
        name, value = parsed
        if name in known_secret_names:
            secrets[name] = value
        else:
            settings[name] = value

    secrets_content = "".join(
        f"{name}={shlex.quote(value)}\n" for name, value in sorted(secrets.items())
    )
    settings_lines = [*passthrough, *(_environment_line(n, v) for n, v in sorted(settings.items()))]
    settings_content = ("\n".join(settings_lines) + "\n") if settings_lines else ""

    _atomic_write(new_secrets, secrets_content)
    _atomic_write(new_settings, settings_content)

    timestamp = time.strftime("%Y%m%d", time.gmtime())
    backup = legacy.with_name(f"{legacy.name}.migrated-{timestamp}")
    suffix = 2
    while backup.exists():
        backup = legacy.with_name(f"{legacy.name}.migrated-{timestamp}-{suffix}")
        suffix += 1
    legacy.rename(backup)

    detail = (
        f"migrated {legacy} into {new_secrets.parent} "
        f"({len(secrets)} secret(s): {', '.join(sorted(secrets)) or 'none'}; "
        f"{len(settings)} setting(s): {', '.join(sorted(settings)) or 'none'}); "
        f"old file preserved as {backup}"
    )
    _logger.info(
        "host config migrated legacy=%s secrets=%s settings=%s backup=%s",
        legacy,
        sorted(secrets),
        sorted(settings),
        backup,
    )
    return MigrationResult(
        migrated=True,
        secret_names=sorted(secrets),
        setting_names=sorted(settings),
        legacy_backup=backup,
        detail=detail,
    )
