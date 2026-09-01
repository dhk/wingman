"""API keys: Keychain, host file, workspace file — one resolution ladder
(RFC-019/034/046, #122).

'wingman keys set anthropic' stores a key with the system 'security' CLI —
no plaintext config files, no wrapper scripts. At startup (CLI and MCP
server alike) every known key that is absent from the environment is
hydrated, in order: the Keychain, then the host's canonical secrets file
(`~/.config/wingman/secrets.env`, RFC-046 — one file every consumer on a
server host reads, instead of shell dotfile exports from one setup session
and a service EnvironmentFile from another silently drifting apart), then
the workspace key file (`keys.env` inside the workspace, RFC-034). An
workspace's own key wins over all of them (BYOK), so
shell exports, launchd EnvironmentVariables, and a systemd
`EnvironmentFile=` all behave exactly as before. On systems without the
'security' binary (Linux, CI) Keychain hydration is a silent no-op and
'wingman keys' says why.

RFC-046 split the old flat `~/.config/keys.env` (#122/RFC-040) into this
file (secrets only) plus `~/.config/wingman/wingman.env` (non-secret host
settings — `wingman.infrastructure.host_config`) — a firm cutover: this
module never reads the old location itself. 'ensure_env' runs the
one-time, idempotent migration off it first, so an existing deployment's
already-populated old file moves automatically and losslessly the first
time any command runs after upgrading, with nothing left to do by hand.

Secrets are never logged and never printed back by any command here.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.keys")


class KeyStoreError(Exception):
    """A key could not be stored, read, or removed."""


# Canonical short names -> the environment variable each key hydrates.
# The Keychain service name IS the environment variable name, stored under
# the account "wingman" so items are recognizable in Keychain Access.
KNOWN_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "voyage": "VOYAGE_API_KEY",
    "github": "GITHUB_SHARED_ISSUES_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# RFC-047's shared issues credential was GITHUB_API_ISSUES_KEY until #506.
# The old name says what the key is FOR but not that it is box-wide, which
# is the exact property the tenant-isolation change overlooked when it gave
# this credential the per-tenant treatment the three metered keys get. Every
# tier a running box already has still holds the old spelling, so read it as
# an alias rather than making a rename an outage: the canonical name wins
# wherever both are present, and nothing has to be rewritten in lockstep.
LEGACY_KEY_ALIASES = {"GITHUB_API_ISSUES_KEY": "GITHUB_SHARED_ISSUES_KEY"}


def canonical_env_var(name: str) -> str:
    """The current spelling of a key's env var, translating a legacy one."""
    return LEGACY_KEY_ALIASES.get(name, name)


def env_key(env_var: str) -> str | None:
    """A key's value from the process environment, accepting the legacy
    spelling when the canonical one is unset. Callers that must NOT read
    ambient process env at all (a shared multi-tenant process — see
    application.feature_request._resolve_github_key) should not call this."""
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    for legacy, canonical in LEGACY_KEY_ALIASES.items():
        if canonical == env_var:
            legacy_value = os.environ.get(legacy, "").strip()
            if legacy_value:
                return legacy_value
    return None


_ACCOUNT = "wingman"

# (argv) -> (returncode, stdout). Injectable so tests never touch a keychain.
Runner = Callable[[list[str]], tuple[int, str]]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603 — fixed binary, no shell
        argv, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout


def keychain_available() -> bool:
    return shutil.which("security") is not None


def _require_name(name: str) -> str:
    key = name.strip().lower()
    if key not in KNOWN_KEYS:
        known = ", ".join(sorted(KNOWN_KEYS))
        raise KeyStoreError(f"unknown key {name!r}; known keys: {known}.")
    return key


def set_key(name: str, value: str, runner: Runner | None = None) -> str:
    """Store (or replace) a key in the Keychain; returns the env var it backs."""
    key = _require_name(name)
    if not value.strip():
        raise KeyStoreError(f"the {key} key value is empty; nothing was stored.")
    if not keychain_available():
        raise KeyStoreError(
            "no 'security' binary on this system — Keychain-backed keys are macOS-only. "
            "Set the environment variable directly instead."
        )
    run = runner if runner is not None else _default_runner
    env_var = KNOWN_KEYS[key]
    code, _ = run(
        [
            "security",
            "add-generic-password",
            "-U",  # replace an existing item instead of failing
            "-s",
            env_var,
            "-a",
            _ACCOUNT,
            "-w",
            value,
        ]
    )
    if code != 0:
        raise KeyStoreError(f"the keychain refused to store {env_var} (security exit {code}).")
    _logger.info("keychain stored %s", env_var)
    return env_var


def get_key(name: str, runner: Runner | None = None) -> str | None:
    """The stored key value, or None when absent or no keychain exists."""
    key = _require_name(name)
    if not keychain_available():
        return None
    run = runner if runner is not None else _default_runner
    code, out = run(
        ["security", "find-generic-password", "-s", KNOWN_KEYS[key], "-a", _ACCOUNT, "-w"]
    )
    if code != 0:
        return None
    value = out.strip()
    return value or None


def unset_key(name: str, runner: Runner | None = None) -> bool:
    """Remove a stored key; False when there was nothing to remove."""
    key = _require_name(name)
    if not keychain_available():
        return False
    run = runner if runner is not None else _default_runner
    code, _ = run(["security", "delete-generic-password", "-s", KNOWN_KEYS[key], "-a", _ACCOUNT])
    if code == 0:
        _logger.info("keychain removed %s", KNOWN_KEYS[key])
    return code == 0


KEYS_FILENAME = "keys.env"
HOST_KEYS_FILENAME = "secrets.env"
HOST_KEYS_SUBDIR = os.path.join(".config", "wingman")


def _read_known_keys_file(path: Path) -> tuple[dict[str, str], bool]:
    """(values, denied) for a keys.env-shaped file; unknown names ignored.

    'denied' is True when the file is there but this account cannot read
    it — a different fact from "not there", and the one the diagnostics
    need. 'ensure_env' still treats both the same (see
    '_parse_known_keys_file'), because it walks every tier on every single
    wingman invocation and one inaccessible tier must never crash every
    command for that account. Python 3.12 tightened 'Path.exists()' to
    propagate PermissionError instead of swallowing it the way it used to
    — discovered as a live crash during RFC-048 Phase 3, migrating dhk's
    own account, of all things.
    """
    try:
        if not path.exists():
            return {}, False
        text = path.read_text(encoding="utf-8")
    except PermissionError:
        _logger.warning("cannot read %s (permission denied)", path)
        return {}, True
    known = set(KNOWN_KEYS.values())
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        # A file written before #506 spells the shared issues key the old
        # way; fold it onto the canonical name so every tier keeps working
        # through a rename. An explicit canonical line always wins.
        canonical = canonical_env_var(name)
        if canonical in known and value and not (canonical != name and canonical in values):
            values[canonical] = value
    return values, False


def _parse_known_keys_file(path: Path) -> dict[str, str]:
    """NAME=value lines from a keys.env-shaped file, forgiving an
    unreadable one exactly as before (RFC-047). Resolution path only:
    anything that REPORTS to a human should use '_read_known_keys_file'
    and say so, rather than call a key absent when it could not look."""
    return _read_known_keys_file(path)[0]


def workspace_keys_path(data_dir: Path) -> Path:
    return data_dir / KEYS_FILENAME


def read_workspace_keys(data_dir: Path) -> dict[str, str]:
    """NAME=value lines from the workspace key file; unknown names ignored."""
    return _parse_known_keys_file(workspace_keys_path(data_dir))


def host_keys_path(home: Path | None = None) -> Path:
    """The one canonical host secrets file (RFC-046): '~/.config/wingman/secrets.env',

    0600, read by the CLI, the MCP server, and the overnight timer alike —
    so a server host has a single place keys live, instead of shell
    dotfile exports from one setup session and a systemd EnvironmentFile
    from another silently drifting apart (the failure mode that stalled
    the 2026-07-22 lobster migration, #122/RFC-040). Non-secret host
    settings live alongside it in the sibling 'wingman.env'
    (`wingman.infrastructure.host_config`), not in this file.
    """
    return (home if home is not None else Path.home()) / HOST_KEYS_SUBDIR / HOST_KEYS_FILENAME


def read_host_keys(home: Path | None = None) -> dict[str, str]:
    """NAME=value lines from the host key file; unknown names ignored."""
    return _parse_known_keys_file(host_keys_path(home))


# One credential meant to be shared by every account on a box, not
# per-account (RFC-046 follow-up, issue #205's "global keys" gap):
# GITHUB_SHARED_ISSUES_KEY is the same fine-grained PAT for every account that
# should be able to file feature requests, so it needs a home outside any
# one account's ~/.config — /etc/wingman/global-secrets.env, root-owned,
# group-readable by whichever accounts need it. Sits below the per-account
# host file in the ladder: an account's own secrets.env always overrides
# the shared default, never the other way around.
GLOBAL_KEYS_PATH = Path("/etc/wingman/global-secrets.env")


def read_global_keys(path: Path | None = None) -> dict[str, str]:
    """NAME=value lines from the global (box-wide) secrets file; unknown
    names ignored. 'path' is injectable for tests — production callers
    always get the real /etc/wingman/global-secrets.env."""
    return _parse_known_keys_file(path if path is not None else GLOBAL_KEYS_PATH)


def store_workspace_key(data_dir: Path, name: str, value: str) -> bool:
    """Store one key in the workspace file (0600).

    Returns True: a stored workspace key is now the one that gets used,
    whatever the environment holds (BYOK — see 'resolve_provider_key').
    The return value is kept so callers need not change shape, and so the
    UI can keep saying whether the key is live.
    Deliberately does NOT touch 'os.environ' — under a single shared
    process serving multiple tenants (docs/RFC.md RFC-048), mutating the
    process environment here would make one tenant's just-submitted key
    silently become every other tenant's key for the rest of the process's
    life. Provider construction reads the workspace file fresh via
    'resolve_provider_key' instead of relying on this having hydrated env.
    """
    short = _require_name(name)
    env_var = KNOWN_KEYS[short]
    if not value.strip():
        raise KeyStoreError("the key value is empty; nothing was stored.")
    values = read_workspace_keys(data_dir)
    values[env_var] = value.strip()
    path = workspace_keys_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{name}={val}\n" for name, val in sorted(values.items())), encoding="utf-8"
    )
    path.chmod(0o600)
    _logger.info("workspace key stored var=%s", env_var)  # never the value
    # Always live now: the workspace key outranks the environment (BYOK).
    return True


def store_host_key(name: str, value: str, home: Path | None = None) -> Path:
    """Store one key in the host's canonical secrets file (0600), creating it.

    The tier a server host actually wants: read by the CLI, the MCP server
    and the overnight timer alike, so a key fixed here is fixed for every
    consumer on the box at once — unlike the Keychain (macOS only) or a
    single workspace's 'keys.env' (that tenant only). Other keys already
    in the file are preserved; only the named one is replaced.
    """
    short = _require_name(name)
    env_var = KNOWN_KEYS[short]
    if not value.strip():
        raise KeyStoreError("the key value is empty; nothing was stored.")
    values = read_host_keys(home)
    values[env_var] = value.strip()
    path = host_keys_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{key}={val}\n" for key, val in sorted(values.items())), encoding="utf-8"
    )
    path.chmod(0o600)
    _logger.info("host key stored var=%s", env_var)  # never the value
    return path


def resolve_provider_key(env_var: str, data_dir: Path | None = None) -> str | None:
    """The value a provider should use for one known env var, read-only.

    **The workspace's own key wins** — bring-your-own-key means the key
    you provided is the key that gets used. RFC-019 and RFC-034 originally
    put an exported environment variable first; that was right when one
    person ran one workspace on their own machine, and wrong the moment a
    shared process serves several people. An operator's stale export
    silently spending on a tenant's behalf is an attribution problem, not
    just the misleading UI it also produced, and a form that stores a key
    it will not use is a trap.

    RFC-048's strict mode had already made this exception for tenants
    (env skipped entirely); this generalises it rather than keeping two
    competing truths.

    The cost, accepted deliberately: an operator can no longer override a
    stale stored key by exporting the variable — a bad workspace key must
    be fixed where it lives ('wingman keys set', the web form, or deleting
    keys.env).

    Read-only counterpart to 'ensure_env': call it on every provider
    construction rather than relying on a prior mutation, so a key
    submitted through the web form takes effect on the very next call with
    no process-wide state change.
    """
    if data_dir is not None:
        workspace_value = read_workspace_keys(data_dir).get(env_var, "").strip()
        if workspace_value:
            return workspace_value
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    return None


def ensure_env(
    runner: Runner | None = None,
    data_dir: Path | None = None,
    home: Path | None = None,
    global_path: Path | None = None,
) -> list[str]:
    """Migrate the legacy host file if needed, then hydrate absent env vars:
    Keychain, then the host secrets file, then the global secrets file,
    then the workspace file.

    Resolution order (RFC-019/034/046, #205): a set environment variable
    always wins; the macOS Keychain fills gaps; the host's canonical
    secrets file ('~/.config/wingman/secrets.env') fills what remains, so
    the CLI and MCP server work on a fresh shell with zero exports; the
    box-wide global secrets file ('/etc/wingman/global-secrets.env')
    fills what's shared across every account rather than set per-account;
    the workspace 'keys.env' (written by the web UI's validated key form)
    fills whatever is still missing. Returns the hydrated variable names.

    Before any of that, runs the one-time move off the old flat
    '~/.config/keys.env' (RFC-046) — idempotent and cheap once already
    migrated, so it is safe (and intentional) to call unconditionally here
    on every invocation rather than requiring a separate migration step.
    The CLI and MCP server entrypoints call
    'host_config.migrate_legacy_host_file' themselves first specifically so
    they can print its result — this call is the automatic-and-silent
    safety net for any other/future caller, not the primary place the
    event is surfaced.
    """
    from wingman.infrastructure.host_config import migrate_legacy_host_file

    migrate_legacy_host_file(home)
    hydrated: list[str] = []
    if keychain_available():
        for short_name, env_var in KNOWN_KEYS.items():
            if os.environ.get(env_var, "").strip():
                continue
            value = get_key(short_name, runner=runner)
            if value is not None:
                os.environ[env_var] = value
                hydrated.append(env_var)
    for env_var, value in read_host_keys(home).items():
        if os.environ.get(env_var, "").strip():
            continue
        os.environ[env_var] = value
        hydrated.append(env_var)
    for env_var, value in read_global_keys(global_path).items():
        if os.environ.get(env_var, "").strip():
            continue
        os.environ[env_var] = value
        hydrated.append(env_var)
    if data_dir is not None:
        for env_var, value in read_workspace_keys(data_dir).items():
            if os.environ.get(env_var, "").strip():
                continue
            os.environ[env_var] = value
            hydrated.append(env_var)
    if hydrated:
        _logger.info("keys hydrated %s", ",".join(hydrated))
    return hydrated


_SOURCE_LABELS = (
    "environment",
    "keychain",
    "host file (~/.config/wingman/secrets.env)",
    "global file (/etc/wingman/global-secrets.env)",
    "workspace file",
)


@dataclass(frozen=True)
class KeySource:
    """Where one known key was found, and whether other sources disagree.

    'winning_source' names whichever source 'ensure_env' would actually
    use, by the same ladder order. 'shadowed_by' lists every OTHER source
    that also defines this key with a DIFFERENT value — a duplicate with
    the same value is not a conflict worth a warning; a duplicate with a
    different value is exactly the "which key file is canonical" confusion
    #122 exists to surface instead of leaving to be found by hand.
    """

    short_name: str
    env_var: str
    winning_source: str
    shadowed_by: list[str] = field(default_factory=list)


def resolve_key_sources(
    environ: Mapping[str, str],
    data_dir: Path | None = None,
    home: Path | None = None,
    runner: Runner | None = None,
    global_path: Path | None = None,
) -> list[KeySource]:
    """Per known key: which source wins, and which others disagree (#122).

    'environ' is the caller's snapshot of the process environment — pass
    one taken BEFORE 'ensure_env' hydrates it, or the reported "winning
    source" degenerates to always "environment" once hydration has copied
    a Keychain/file value into os.environ. Read-only: makes no writes,
    unlike 'ensure_env'.
    """
    host_values = read_host_keys(home)
    global_values = read_global_keys(global_path)
    workspace_values = read_workspace_keys(data_dir) if data_dir is not None else {}
    results: list[KeySource] = []
    for short_name, env_var in KNOWN_KEYS.items():
        candidates: list[tuple[str, str]] = []
        # Workspace first: the key its owner provided (BYOK). Everything
        # below is somebody else's default.
        if env_var in workspace_values:
            candidates.append((_SOURCE_LABELS[4], workspace_values[env_var]))
        env_value = environ.get(env_var, "").strip()
        if env_value:
            candidates.append((_SOURCE_LABELS[0], env_value))
        if keychain_available():
            keychain_value = get_key(short_name, runner=runner)
            if keychain_value:
                candidates.append((_SOURCE_LABELS[1], keychain_value))
        if env_var in host_values:
            candidates.append((_SOURCE_LABELS[2], host_values[env_var]))
        if env_var in global_values:
            candidates.append((_SOURCE_LABELS[3], global_values[env_var]))
        if not candidates:
            results.append(KeySource(short_name, env_var, "not set"))
            continue
        winning_source, winning_value = candidates[0]
        shadowed_by = [source for source, value in candidates[1:] if value != winning_value]
        results.append(KeySource(short_name, env_var, winning_source, shadowed_by))
    return results


#: How much of a key value may be shown. Eight characters is the provider's
#: own public prefix ('sk-ant-a', 'pa-4uc-S') and nothing more — enough to
#: tell Anthropic from Voyage and to eyeball a truncated paste, short of
#: the entropy that makes the key a secret. The digest, not the prefix, is
#: what makes two tiers comparable (AGENTS.md "redact sensitive data").
PREFIX_CHARS = 8


def fingerprint(value: str) -> str:
    """A key's identity without the key: public prefix, short digest, length.

    Two tiers holding the same fingerprint hold the same key; two tiers
    with different fingerprints have drifted, which is the whole reason
    'keys where' exists. The digest is sha256 truncated to 6 hex chars —
    collision-proof enough to compare four tiers of one key, useless for
    recovering the value.
    """
    text = value.strip()
    if not text:
        return "(empty)"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]
    return f"{text[:PREFIX_CHARS]}...#{digest} (len {len(text)})"


@dataclass(frozen=True)
class KeyLocation:
    """One tier's answer for one key: is it here, and is it the one used?"""

    tier: str
    #: Where the tier physically lives, when it is a file. None for the
    #: environment and the Keychain, which have no path to go look at.
    path: str | None
    present: bool
    #: None when absent; a 'fingerprint' string otherwise. Never the value.
    fingerprint: str | None
    winner: bool
    #: False when the file is there but this account cannot read it. Such a
    #: tier is NOT absent — reporting it as absent is how a live key looks
    #: missing and sends someone off to set one that already exists (#442).
    readable: bool = True


@dataclass(frozen=True)
class _FileTiers:
    """The three file-backed tiers, read once per command.

    Read lazily per key instead and a four-key walk opens each file four
    times over, logging any permission warning four times with it — which
    is exactly how the first cut of this looked in the field.
    """

    workspace: dict[str, str]
    workspace_denied: bool
    workspace_path: str | None
    host: dict[str, str]
    host_denied: bool
    host_path: str
    shared: dict[str, str]
    shared_denied: bool
    shared_path: str


def _read_file_tiers(
    data_dir: Path | None, home: Path | None, global_path: Path | None
) -> _FileTiers:
    workspace_values, workspace_denied = (
        _read_known_keys_file(workspace_keys_path(data_dir))
        if data_dir is not None
        else ({}, False)
    )
    host_values, host_denied = _read_known_keys_file(host_keys_path(home))
    resolved_global = global_path if global_path is not None else GLOBAL_KEYS_PATH
    global_values, global_denied = _read_known_keys_file(resolved_global)
    return _FileTiers(
        workspace=workspace_values,
        workspace_denied=workspace_denied,
        workspace_path=str(workspace_keys_path(data_dir)) if data_dir is not None else None,
        host=host_values,
        host_denied=host_denied,
        host_path=str(host_keys_path(home)),
        shared=global_values,
        shared_denied=global_denied,
        shared_path=str(resolved_global),
    )


def _key_tiers(
    short_name: str,
    env_var: str,
    environ: Mapping[str, str],
    files: _FileTiers,
    runner: Runner | None,
) -> list[tuple[str, str | None, str, bool]]:
    """(tier label, path, value, readable) for one key, in precedence order.

    The single definition of "which tier comes first", shared by the
    reporting and the validating paths so they can never disagree about
    which copy is the live one — the disagreement 'key_status' and
    'test_key' still have with the rest of this module.
    """
    keychain_value = get_key(short_name, runner=runner) if keychain_available() else None
    workspace_values, workspace_denied = files.workspace, files.workspace_denied
    host_values, host_denied = files.host, files.host_denied
    global_values, global_denied = files.shared, files.shared_denied
    tiers: list[tuple[str, str | None, str, bool]] = [
        (
            _SOURCE_LABELS[4],
            files.workspace_path,
            workspace_values.get(env_var, ""),
            not workspace_denied,
        ),
        (_SOURCE_LABELS[0], None, environ.get(env_var, ""), True),
        (_SOURCE_LABELS[1], None, keychain_value or "", True),
        (
            _SOURCE_LABELS[2],
            files.host_path,
            host_values.get(env_var, ""),
            not host_denied,
        ),
        (
            _SOURCE_LABELS[3],
            files.shared_path,
            global_values.get(env_var, ""),
            not global_denied,
        ),
    ]
    # The workspace tier only exists when a workspace was named.
    return tiers if files.workspace_path is not None else tiers[1:]


def describe_key_locations(
    environ: Mapping[str, str],
    data_dir: Path | None = None,
    home: Path | None = None,
    runner: Runner | None = None,
    global_path: Path | None = None,
) -> dict[str, list[KeyLocation]]:
    """Every tier for every known key, in the order they are consulted.

    The read-only, value-free answer to "where is this key, and which copy
    is actually being used?" — the question 'key_status' cannot answer,
    because by the time it runs 'ensure_env' has copied whichever tier won
    into the environment and every key looks like it came from there.

    Pass an 'environ' snapshot taken BEFORE 'ensure_env' for the same
    reason 'resolve_key_sources' does. Tier order here is the effective
    one: the workspace's own key wins (BYOK), then the environment, then
    Keychain, host file, and the box-wide global file.
    """
    files = _read_file_tiers(data_dir, home, global_path)
    out: dict[str, list[KeyLocation]] = {}
    for short_name, env_var in KNOWN_KEYS.items():
        won = False
        rows: list[KeyLocation] = []
        for tier, path, value, readable in _key_tiers(short_name, env_var, environ, files, runner):
            present = bool(value.strip())
            winner = present and not won
            won = won or winner
            rows.append(
                KeyLocation(
                    tier=tier,
                    path=path,
                    present=present,
                    fingerprint=fingerprint(value) if present else None,
                    winner=winner,
                    readable=readable,
                )
            )
        out[short_name] = rows
    return out


def key_status(runner: Runner | None = None) -> list[tuple[str, str, str]]:
    """(short name, env var, state) per known key — states name the source,
    never the value: 'environment', 'keychain', or 'not set'."""
    rows: list[tuple[str, str, str]] = []
    for short_name, env_var in KNOWN_KEYS.items():
        if os.environ.get(env_var, "").strip():
            state = "environment"
        elif get_key(short_name, runner=runner) is not None:
            state = "keychain"
        else:
            state = "not set"
        rows.append((short_name, env_var, state))
    return rows


def _test_anthropic(api_key: str) -> tuple[bool, str]:
    """One cheap authenticated call (list models) — no completion tokens spent."""
    import anthropic

    try:
        anthropic.Anthropic(api_key=api_key).models.list(limit=1)
    except anthropic.AuthenticationError:
        return False, "Anthropic rejected the key (invalid or revoked)"
    except anthropic.APIConnectionError as exc:
        return False, f"could not reach the Anthropic API ({exc})"
    except anthropic.APIStatusError as exc:
        return False, f"Anthropic API error ({exc.status_code})"
    except anthropic.APIError as exc:
        # Catches the rest of the SDK's error hierarchy (e.g. a malformed
        # response) so an unusual failure reports cleanly instead of an
        # unhandled traceback — this is a health check, not a hard call.
        return False, f"Anthropic API error ({exc})"
    return True, "working"


def _test_voyage(api_key: str) -> tuple[bool, str]:
    """One minimal embed call — a single short word, cheapest possible request."""
    from wingman.providers.embeddings import EmbeddingError, VoyageEmbeddingProvider

    try:
        VoyageEmbeddingProvider(model="voyage-4", api_key=api_key).embed(["ping"], "query")
    except EmbeddingError as exc:
        return False, str(exc)
    return True, "working"


def _test_github(api_key: str) -> tuple[bool, str]:
    """One cheap authenticated call (GET /user) — works for a fine-grained
    PAT regardless of which repo(s) it's scoped to, since account identity
    isn't a repo-scoped permission."""
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "GitHub rejected the key (invalid or revoked)"
        return False, f"GitHub API returned {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, f"could not reach the GitHub API ({exc})"
    return True, "working"


def _test_openrouter(api_key: str) -> tuple[bool, str]:
    """One cheap authenticated call (GET /api/v1/key) — auth/usage status
    only, never a completion call, so verification never spends on the
    websearch plugin this key exists to pay for (#222)."""
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "OpenRouter rejected the key (invalid or revoked)"
        return False, f"OpenRouter API returned {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, f"could not reach the OpenRouter API ({exc})"
    return True, "working"


#: short name -> the one cheap authenticated call that proves the key works.
_LIVE_TESTS: dict[str, Callable[[str], tuple[bool, str]]] = {
    "anthropic": _test_anthropic,
    "voyage": _test_voyage,
    "github": _test_github,
    "openrouter": _test_openrouter,
}


def test_key_value(short_name: str, value: str) -> tuple[bool, str]:
    """Live-test a key value the caller already resolved.

    Separate from 'test_key' because validation must test the key that is
    actually in use — which, since BYOK, may come from a workspace or host
    file that 'test_key' never looks at.
    """
    key = _require_name(short_name)
    if not value.strip():
        return False, "not set"
    return _LIVE_TESTS[key](value)


@dataclass(frozen=True)
class KeyValidation:
    """One key, live-tested where it actually resolves from."""

    short_name: str
    env_var: str
    #: The tier the tested value came from, or "not set" when nowhere.
    tier: str
    fingerprint: str | None
    ok: bool
    message: str
    #: Tiers that exist but this account could not read. A key reported
    #: "not set" while one of these is non-empty is an unanswered
    #: question, not a finding (#442).
    unreadable_tiers: list[str] = field(default_factory=list)


def validate_keys(
    environ: Mapping[str, str],
    data_dir: Path | None = None,
    home: Path | None = None,
    runner: Runner | None = None,
    global_path: Path | None = None,
) -> list[KeyValidation]:
    """Live-test the key each tier ladder actually resolves to, and name
    the tier it came from.

    'test_keys' answers "does the key in env or the Keychain work?".
    That is the wrong question once a workspace or host file can outrank
    both: it can report a healthy key while every real call spends an
    expired one from a file it never read. This asks the right question —
    the winning tier's value is the one put on the wire.

    Costs at most one cheap, no-completion-tokens call per configured key.
    A key that resolves nowhere is reported without any network call.
    """
    files = _read_file_tiers(data_dir, home, global_path)
    results: list[KeyValidation] = []
    for short_name, env_var in KNOWN_KEYS.items():
        tiers = _key_tiers(short_name, env_var, environ, files, runner)
        blind = [tier for tier, _path, _value, readable in tiers if not readable]
        winner = next(
            ((tier, value) for tier, _path, value, _readable in tiers if value.strip()), None
        )
        if winner is None:
            message = "not set"
            if blind:
                # Never call a key missing when a tier could not be read:
                # the honest answer is that the question is unanswered.
                message = f"cannot tell — unreadable: {', '.join(blind)}"
            results.append(
                KeyValidation(short_name, env_var, "not set", None, False, message, blind)
            )
            continue
        tier, value = winner
        ok, message = test_key_value(short_name, value)
        results.append(
            KeyValidation(short_name, env_var, tier, fingerprint(value), ok, message, blind)
        )
    return results


def test_key(short_name: str, runner: Runner | None = None) -> tuple[bool, str]:
    """Actually call the provider to confirm a key works, not just that it's

    set (RFC-034-adjacent: presence isn't validity). Resolution order matches
    every other entrypoint — an exported environment variable wins, the
    Keychain fills gaps. Returns (worked, message); the message never
    contains the key value. A key that resolves to nothing is reported as
    'not set' without making any network call.
    """
    key = _require_name(short_name)
    env_var = KNOWN_KEYS[key]
    value = os.environ.get(env_var, "").strip() or get_key(key, runner=runner)
    if not value:
        return False, "not set"
    if key == "anthropic":
        return _test_anthropic(value)
    if key == "voyage":
        return _test_voyage(value)
    if key == "github":
        return _test_github(value)
    if key == "openrouter":
        return _test_openrouter(value)
    raise AssertionError(f"no live test defined for {key!r}")  # unreachable — _require_name guards


def test_keys(runner: Runner | None = None) -> list[tuple[str, str, bool, str]]:
    """(short name, env var, worked, message) per known key — live-tested."""
    return [
        (short_name, KNOWN_KEYS[short_name], *test_key(short_name, runner=runner))
        for short_name in KNOWN_KEYS
    ]
