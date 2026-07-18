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


def ensure_env(runner: Runner | None = None) -> list[str]:
    """Hydrate absent env vars from the Keychain; env always wins when set.

    Returns the names of the variables that were hydrated. Safe to call
    anywhere, any number of times; a no-op without a keychain.
    """
    if not keychain_available():
        return []
    hydrated: list[str] = []
    for short_name, env_var in KNOWN_KEYS.items():
        if os.environ.get(env_var, "").strip():
            continue
        value = get_key(short_name, runner=runner)
        if value is not None:
            os.environ[env_var] = value
            hydrated.append(env_var)
    if hydrated:
        _logger.info("keychain hydrated %s", ",".join(hydrated))
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
