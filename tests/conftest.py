"""Session-wide test isolation: never touch a real host's secrets.

`ensure_env` (wingman.infrastructure.keys) reads the macOS Keychain and, as
of #157, the canonical host key file (`~/.config/keys.env`) on EVERY CLI
invocation — `_bootstrap`, a Typer `@app.callback()` that runs before every
command, calls it unconditionally. That means every integration test that
drives the CLI via `CliRunner`, in any test file, silently hydrates
whatever real keys exist at the real `Path.home()` into that test
process's environment — on any machine that actually has one configured
(every shape-B box, by #157's own design), not just the couple of test
files that happen to exercise key resolution directly and already guard
against it with a restore fixture.

Pinning HOME to an isolated directory for every test closes this at the
root for the whole suite, rather than requiring every test file that
happens to invoke the CLI to know to defend against it individually.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wingman.infrastructure import broadcast, keys, tenants


@pytest.fixture(autouse=True)
def _isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))


@pytest.fixture(autouse=True)
def _isolated_box_wide_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same protection as `_isolated_home`, for the tiers it cannot reach.

    RFC-047's box-wide files live at ABSOLUTE paths under `/etc/wingman`, so
    pinning HOME does nothing about them — that is the entire bug. Any test
    that resolves a key, a broadcast or a tenant without passing an explicit
    path reads the operator's real state, and every CLI-driving test does so
    via `_bootstrap`'s unconditional `ensure_env`, exactly as #157 described
    for the host tier.

    It stayed invisible for as long as the box-wide files held nothing a test
    asserted about. Two separate failures made it visible, and they are the
    same failure: `global-secrets.env` held only `GITHUB_SHARED_ISSUES_KEY`
    until an operator added ANTHROPIC_API_KEY and VOYAGE_API_KEY for #514's
    funded tenants, at which point six tests asserting "no key is configured"
    began failing; and a real `motd.json` broadcast outranks every computed
    action by RFC-065's design, so four tests asserting what comes first in
    `next_actions` failed for as long as that box had a message set.

    Both only ever failed on a box that actually uses RFC-047 — which is the
    box the code is developed on. CI has no `/etc/wingman` at all, so it
    agreed with the tests and kept agreeing: a green pipeline that proved
    nothing about the machine the code actually runs on. That is the property
    worth naming, because it means the suite was least trustworthy exactly
    where it was most needed.

    Pointing each module global at a path that does not exist is enough:
    every reader resolves them at call time, and the tests that mean to
    exercise these tiers pass an explicit path and are untouched.
    """
    absent = tmp_path_factory.mktemp("box-wide")
    monkeypatch.setattr(keys, "GLOBAL_KEYS_PATH", absent / "global-secrets.env")
    monkeypatch.setattr(broadcast, "OPERATOR_MESSAGE_PATH", absent / "motd.json")
    monkeypatch.setattr(broadcast, "OPERATOR_QUESTION_PATH", absent / "qotd.json")
    monkeypatch.setattr(tenants, "DEFAULT_REGISTRY_PATH", absent / "tenants.toml")


@pytest.fixture(scope="session")
def _tailscale_down(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory whose 'tailscale' always fails, to shadow any real one."""
    directory = tmp_path_factory.mktemp("tailscale-down")
    shim = directory / "tailscale"
    shim.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    shim.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def _no_ambient_tunnel(_tailscale_down: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tunnel detection say "no tunnel" everywhere, on every host (#290).

    `_extra_allowed_hosts` auto-detects this machine's Tailscale name, so any
    test touching a front-door URL got a different answer on a box with
    Tailscale up than on one without. `test_mcp_url_command_hints_when_no_
    tunnel_detected` asserted the *absence* of a tunnel while building no
    absence, so it passed in CI and failed on every deployed host — where the
    command was behaving exactly as designed.

    Shadowing the binary rather than patching a symbol, because both
    `_tailscale_dns_name` and `_extra_allowed_hosts` bind their injection
    points as default arguments at import time: monkeypatching the module
    attribute afterwards changes nothing. A failing `tailscale` is also a
    state the code explicitly supports ("daemon down" → None), so this
    exercises the real path rather than a stubbed shortcut.

    PATH is prepended, not replaced, so git/uv and friends still resolve. A
    test that wants a front door passes `--allowed-host` explicitly, which is
    already the established pattern.
    """
    monkeypatch.setenv("PATH", f"{_tailscale_down}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("WINGMAN_ALLOWED_HOSTS", raising=False)
