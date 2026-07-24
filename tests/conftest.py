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

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
