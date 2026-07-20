"""HTTP MCP server process management (RFC-032): pidfile, status, stop."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.mcp_process import (
    clear_pidfile,
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


def test_pidfile_roundtrip(workspace: Config) -> None:
    path = write_pidfile(workspace)
    assert path == pidfile_path(workspace)
    assert int(path.read_text().strip()) == os.getpid()
    # this process is alive but is pytest, not wingman-mcp: identity check rejects it
    assert read_server_pid(workspace, command_of=lambda pid: "pytest") is None
    assert not path.exists()  # rejected pidfile is removed as stale


def test_status_and_verified_read(workspace: Config) -> None:
    assert "not running" in server_status(workspace, command_of=_as_wingman)
    write_pidfile(workspace)
    assert read_server_pid(workspace, command_of=_as_wingman) == os.getpid()
    assert f"running (pid {os.getpid()})" in server_status(workspace, command_of=_as_wingman)
    clear_pidfile(workspace)
    assert "not running" in server_status(workspace, command_of=_as_wingman)


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
        stopped, detail = stop_server(workspace, command_of=_as_wingman)
        assert stopped and f"pid {victim.pid}" in detail
        assert victim.wait(timeout=5) != 0  # terminated
        assert bystander.poll() is None  # untouched
        assert not pidfile_path(workspace).exists()
        # second stop: nothing to do, honest message
        stopped, detail = stop_server(workspace, command_of=_as_wingman)
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
        stopped, detail = stop_server(workspace, command_of=_as_wingman, grace_seconds=0.5)
        assert stopped and "forced" in detail
        assert stubborn.wait(timeout=5) is not None
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait()
