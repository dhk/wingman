"""HTTP MCP server process management (RFC-032): pidfile, status, stop."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wingman.infrastructure import mcp_process as mcp_process_module
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.mcp_process import (
    clear_pidfile,
    orphan_http_pids,
    pidfile_path,
    read_server_pid,
    server_status,
    stop_server,
    write_pidfile,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    return config


def _as_wingman(pid: int) -> str:
    return f"python /Users/x/.local/bin/wingman-mcp --http (pid {pid})"


def _no_processes() -> list[tuple[int, str]]:
    # Tests must never scan the real process table: on a dev machine with a
    # live server, stop_server would kill it mid-test-run.
    return []


def test_pidfile_roundtrip(workspace: Config) -> None:
    path = write_pidfile(workspace)
    assert path == pidfile_path(workspace)
    assert int(path.read_text().strip()) == os.getpid()
    # this process is alive but is pytest, not wingman-mcp: identity check rejects it
    assert read_server_pid(workspace, command_of=lambda pid: "pytest") is None
    assert not path.exists()  # rejected pidfile is removed as stale


def test_status_and_verified_read(workspace: Config) -> None:
    assert "not running" in server_status(
        workspace, command_of=_as_wingman, processes=_no_processes
    )
    write_pidfile(workspace)
    assert read_server_pid(workspace, command_of=_as_wingman) == os.getpid()
    assert f"running (pid {os.getpid()})" in server_status(
        workspace, command_of=_as_wingman, processes=_no_processes
    )
    clear_pidfile(workspace)
    assert "not running" in server_status(
        workspace, command_of=_as_wingman, processes=_no_processes
    )


def test_dead_pid_is_stale(workspace: Config) -> None:
    # spawn and reap a process so its pid is definitely dead
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    pidfile_path(workspace).write_text(f"{child.pid}\n", encoding="utf-8")
    assert read_server_pid(workspace, command_of=_as_wingman) is None
    assert not pidfile_path(workspace).exists()


def test_garbage_pidfile_is_stale(workspace: Config) -> None:
    pidfile_path(workspace).write_text("not-a-pid\n", encoding="utf-8")
    assert read_server_pid(workspace, command_of=_as_wingman) is None
    assert not pidfile_path(workspace).exists()


def test_stop_terminates_only_the_recorded_server(workspace: Config) -> None:
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pidfile_path(workspace).write_text(f"{victim.pid}\n", encoding="utf-8")
        stopped, detail = stop_server(workspace, command_of=_as_wingman, processes=_no_processes)
        assert stopped and f"pid {victim.pid}" in detail
        assert victim.wait(timeout=5) != 0  # terminated
        assert bystander.poll() is None  # untouched
        assert not pidfile_path(workspace).exists()
        # second stop: nothing to do, honest message
        stopped, detail = stop_server(workspace, command_of=_as_wingman, processes=_no_processes)
        assert not stopped and "not running" in detail
    finally:
        for proc in (victim, bystander):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_stop_escalates_to_kill(workspace: Config) -> None:
    # a child that ignores SIGTERM: only SIGKILL ends it
    stubborn = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    try:
        time.sleep(0.3)  # let the handler install before we signal
        pidfile_path(workspace).write_text(f"{stubborn.pid}\n", encoding="utf-8")
        stopped, detail = stop_server(
            workspace, command_of=_as_wingman, processes=_no_processes, grace_seconds=0.5
        )
        assert stopped and "forced" in detail
        assert stubborn.wait(timeout=5) is not None
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait()


def _owned_by_us(_pid: int) -> int:
    return os.getuid()


def test_orphan_detection_is_port_and_transport_scoped(workspace: Config) -> None:
    """Only 'wingman-mcp … --http' on OUR port, owned by US, counts: stdio
    servers, other instances' ports, and other accounts' processes
    (multi-instance design, #138) must never match."""

    def table() -> list[tuple[int, str]]:
        return [
            (101, "python /x/bin/wingman-mcp --http"),  # default port -> orphan
            (102, "python /x/bin/wingman-mcp --http --port 8788"),  # Trent's -> not ours
            (103, "python /x/bin/wingman-mcp"),  # stdio -> never
            # a shell whose script TEXT quotes the command: kill this and you
            # kill an innocent terminal — must never match
            (104, "bash -c echo see wingman-mcp --http --port 8787 docs"),
            (105, "python /x/bin/wingman-mcp --http --port=8787"),  # ours, = form
            (106, "uv run wingman-mcp --http"),  # wrapper of a real server -> orphan
        ]

    orphans = orphan_http_pids(
        workspace, port=8787, command_of=_as_wingman, processes=table, owner_of=_owned_by_us
    )
    assert orphans == [101, 105, 106]
    assert orphan_http_pids(
        workspace, port=8788, command_of=_as_wingman, processes=table, owner_of=_owned_by_us
    ) == [102]
    # the pidfile-managed server is not an orphan of itself
    managed = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pidfile_path(workspace).write_text(f"{managed.pid}\n", encoding="utf-8")

        def managed_table() -> list[tuple[int, str]]:
            return [(managed.pid, "python /x/bin/wingman-mcp --http")]

        assert (
            orphan_http_pids(
                workspace,
                port=8787,
                command_of=lambda pid: "python /x/bin/wingman-mcp --http",
                processes=managed_table,
                owner_of=_owned_by_us,
            )
            == []
        )
    finally:
        managed.kill()
        managed.wait()


def test_orphan_detection_excludes_other_accounts_processes(workspace: Config) -> None:
    """Regression: a caller who forgets --port falls back to DEFAULT_PORT, which
    may be a DIFFERENT account's real port on a shared host (lobster, shape B).
    That process must never be treated as our orphan, even though the command
    line matches — ownership is unknown or foreign, so it's excluded entirely."""

    def table() -> list[tuple[int, str]]:
        return [
            (201, "python /x/bin/wingman-mcp --http"),  # command matches, but...
            (202, "python /x/bin/wingman-mcp --http"),
        ]

    def owner_of(pid: int) -> int | None:
        return {201: os.getuid() + 1, 202: None}[pid]  # a different uid; an unknown uid

    orphans = orphan_http_pids(
        workspace, port=8787, command_of=_as_wingman, processes=table, owner_of=owner_of
    )
    assert orphans == []


def test_status_names_the_orphan(workspace: Config) -> None:
    def table() -> list[tuple[int, str]]:
        return [(4242, "python /x/bin/wingman-mcp --http")]

    text = server_status(workspace, command_of=_as_wingman, processes=table, owner_of=_owned_by_us)
    assert "not running" in text  # the pidfile view, unchanged
    assert "Orphaned" in text and "4242" in text and "wingman mcp stop" in text


def test_status_never_names_another_accounts_process(workspace: Config) -> None:
    def table() -> list[tuple[int, str]]:
        return [(4243, "python /x/bin/wingman-mcp --http")]

    text = server_status(
        workspace, command_of=_as_wingman, processes=table, owner_of=lambda _pid: os.getuid() + 1
    )
    assert "Orphaned" not in text and "4243" not in text


def test_stop_clears_the_orphan_too(workspace: Config) -> None:
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:

        def table() -> list[tuple[int, str]]:
            return [(orphan.pid, "python /x/bin/wingman-mcp --http")]

        stopped, detail = stop_server(workspace, command_of=_as_wingman, processes=table)
        assert stopped and "orphaned" in detail and str(orphan.pid) in detail
        assert orphan.wait(timeout=5) is not None
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


def test_stop_reports_permission_denied_without_crashing(
    workspace: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a same-port process that reaches _terminate (e.g. ownership
    was undeterminable at scan time, so it wasn't pre-filtered) must be
    reported, never crash 'wingman mcp stop' with an unhandled PermissionError
    — this is exactly what happened live on a shared multi-account host."""

    def fake_kill(_pid: int, _sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(mcp_process_module.os, "kill", fake_kill)

    def table() -> list[tuple[int, str]]:
        return [(9001, "python /x/bin/wingman-mcp --http")]

    # owner_of says "ours" so it survives the pre-filter, simulating a race
    # or an owner_of implementation that can't always tell in advance —
    # _terminate's own PermissionError handling is the last line of defense.
    stopped, detail = stop_server(
        workspace, command_of=_as_wingman, processes=table, owner_of=_owned_by_us
    )
    assert not stopped
    assert "9001" in detail
    assert "different account" in detail
    assert "left it running" in detail
