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
environment variable that is already set always wins over all three, so
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
    "github": "GITHUB_API_ISSUES_KEY",
}
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


def _parse_known_keys_file(path: Path) -> dict[str, str]:
    """NAME=value lines from a keys.env-shaped file; unknown names ignored."""
    if not path.exists():
        return {}
    known = set(KNOWN_KEYS.values())
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in known and value.strip():
            values[name.strip()] = value.strip()
    return values


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
# GITHUB_API_ISSUES_KEY is the same fine-grained PAT for every account that
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
    """Store one key in the workspace file (0600) and hydrate it if env is unset.

    Returns True when the key is live in this process now; False when an
    already-set environment variable shadows it (env always wins, RFC-019).
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
    if os.environ.get(env_var, "").strip():
        return False
    os.environ[env_var] = value.strip()
    return True


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
        if env_var in workspace_values:
            candidates.append((_SOURCE_LABELS[4], workspace_values[env_var]))
        if not candidates:
            results.append(KeySource(short_name, env_var, "not set"))
            continue
        winning_source, winning_value = candidates[0]
        shadowed_by = [source for source, value in candidates[1:] if value != winning_value]
        results.append(KeySource(short_name, env_var, winning_source, shadowed_by))
    return results


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
    raise AssertionError(f"no live test defined for {key!r}")  # unreachable — _require_name guards


def test_keys(runner: Runner | None = None) -> list[tuple[str, str, bool, str]]:
    """(short name, env var, worked, message) per known key — live-tested."""
    return [
        (short_name, KNOWN_KEYS[short_name], *test_key(short_name, runner=runner))
        for short_name in KNOWN_KEYS
    ]
