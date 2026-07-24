"""API keys in the macOS Keychain, hydrated into the environment (RFC-019).

'wingman keys set anthropic' stores a key with the system 'security' CLI —
no plaintext config files, no wrapper scripts. At startup (CLI and MCP
server alike) every known key that is absent from the environment is
hydrated from the Keychain; an environment variable that is already set
always wins, so shell exports and launchd EnvironmentVariables behave
exactly as before. On systems without the 'security' binary (Linux, CI)
hydration is a silent no-op and 'wingman keys' says why.

Secrets are never logged and never printed back by any command here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
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


def workspace_keys_path(data_dir: Path) -> Path:
    return data_dir / KEYS_FILENAME


def read_workspace_keys(data_dir: Path) -> dict[str, str]:
    """NAME=value lines from the workspace key file; unknown names ignored."""
    path = workspace_keys_path(data_dir)
    if not path.exists():
        return {}
    known = set(KNOWN_KEYS.values())
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in known and value.strip():
            values[name.strip()] = value.strip()
    return values


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


def ensure_env(runner: Runner | None = None, data_dir: Path | None = None) -> list[str]:
    """Hydrate absent env vars from the Keychain, then the workspace key file.

    Resolution order (RFC-019/034): a set environment variable always wins;
    the macOS Keychain fills gaps; the workspace 'keys.env' (written by the
    web UI's validated key form) fills what remains. Returns the hydrated
    variable names. Safe to call anywhere, any number of times.
    """
    hydrated: list[str] = []
    if keychain_available():
        for short_name, env_var in KNOWN_KEYS.items():
            if os.environ.get(env_var, "").strip():
                continue
            value = get_key(short_name, runner=runner)
            if value is not None:
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
    raise AssertionError(f"no live test defined for {key!r}")  # unreachable — _require_name guards


def test_keys(runner: Runner | None = None) -> list[tuple[str, str, bool, str]]:
    """(short name, env var, worked, message) per known key — live-tested."""
    return [
        (short_name, KNOWN_KEYS[short_name], *test_key(short_name, runner=runner))
        for short_name in KNOWN_KEYS
    ]
