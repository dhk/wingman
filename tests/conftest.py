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


@pytest.fixture(autouse=True)
def _isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))


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
