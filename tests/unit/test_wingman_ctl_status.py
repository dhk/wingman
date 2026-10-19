"""Status describes the configured shared host without exposing tenant details."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from wingman.infrastructure.tenants import load_registry

CTL = Path(__file__).resolve().parents[2] / "scripts" / "wingman-ctl"


@pytest.mark.parametrize("personal", [False, True])
def test_shared_status_without_personal_server(tmp_path: Path, personal: bool) -> None:
    etc = tmp_path / "etc" / "wingman"
    etc.mkdir(parents=True)
    (etc / "tenants.toml").write_text(
        '[[tenant]]\nslug="private-slug"\ndata_dir="/private/workspace"\n'
    )
    assert len(load_registry(etc / "tenants.toml")) == 1
    (etc / "oauth-config-path").write_text(str(etc / "oauth.env"))
    (etc / "oauth.env").write_text("WINGMAN_OAUTH_ARGS=--oauth-identities /absent/map.toml\n")
    secret = tmp_path / "shared/.config/wingman/secrets.env"
    secret.parent.mkdir(parents=True)
    secret.write_text("WINGMAN_OPERATOR_TODOIST_TOKEN=never-show-this\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "wingman": 'echo "The HTTP MCP server is not running"',
        "getent": f'echo "wingman-shared:x:1:1::{tmp_path}/shared:/bin/bash"',
        "systemctl": 'echo "ActiveState=active"; echo "ActiveEnterTimestamp=Fri 2026-10-16"',
        "curl": 'case "$*" in *well-known*) printf 200;; *) printf 401;; esac',
        "pgrep": "exit 1",
    }.items():
        path = bindir / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    if personal:
        unit = tmp_path / ".config/systemd/user/wingman-mcp.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n")
    result = subprocess.run(
        ["bash", str(CTL), "status"],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "WINGMAN_HOST_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Shared HTTP service" in result.stdout
    assert ("not running" in result.stdout) is personal
    assert "Tenants: 1" in result.stdout
    assert "Operator notifications: on" in result.stdout
    assert "never-show-this" not in result.stdout
    assert "private-slug" not in result.stdout
    assert "PRM: 200" in result.stdout
    assert "/mcp: 401" in result.stdout


def test_unreadable_shared_config_needs_sudo(tmp_path: Path) -> None:
    etc = tmp_path / "etc/wingman"
    etc.mkdir(parents=True)
    registry = etc / "tenants.toml"
    registry.write_text("[[tenant]]\nslug='hidden'\ndata_dir='/hidden'\n")
    registry.chmod(0)
    try:
        result = subprocess.run(
            ["python3", str(CTL.with_name("wingman-shared-status.py")), "--home", str(tmp_path)],
            env={**os.environ, "WINGMAN_HOST_ROOT": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        registry.chmod(0o600)
    assert result.returncode == 0
    assert "Tenants: (needs sudo)" in result.stdout
    assert "hidden" not in result.stdout


@pytest.mark.parametrize("proxy_environment", [False, True])
def test_health_probes_ignore_curl_credentials_and_proxies(tmp_path, proxy_environment):
    """Exercise real curl: operator curlrc and proxies cannot authenticate or reroute probes."""
    import shutil
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if shutil.which("curl") is None:
        pytest.skip("real curl is required for this integration probe")
    requests = []
    proxy_requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(401 if self.path == "/mcp" else 200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    class ProxyHandler(Handler):
        def do_GET(self):
            proxy_requests.append(self.path)
            self.send_response(599)
            self.end_headers()

    target = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (target, proxy)
    ]
    for thread in threads:
        thread.start()
    try:
        home = tmp_path / "operator"
        home.mkdir()
        (home / ".curlrc").write_text('header = "Authorization: Bearer synthetic-test-only"\n')
        unit = home / ".config/systemd/user/wingman-mcp.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(f"[Service]\nExecStart=/synthetic/wingman --port {target.server_port}\n")
        etc = tmp_path / "etc/wingman"
        etc.mkdir(parents=True)
        (etc / "tenants.toml").write_text("")
        env = {
            key: value
            for key, value in os.environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
        }
        env.update(
            HOME=str(home),
            CURL_HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            WINGMAN_HOST_ROOT=str(tmp_path),
        )
        if proxy_environment:
            env.update(http_proxy=f"http://127.0.0.1:{proxy.server_port}", no_proxy="", NO_PROXY="")
        result = subprocess.run(
            [sys.executable, str(CTL.with_name("wingman-shared-status.py")), "--home", str(home)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert "PRM: 200" in result.stdout and "/mcp: 401" in result.stdout
        assert requests == [("/.well-known/oauth-protected-resource/mcp", None), ("/mcp", None)]
        assert proxy_requests == []
        assert "synthetic-test-only" not in result.stdout + result.stderr
    finally:
        for server in (target, proxy):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_shared_detection_keeps_eacces_when_exists_suppresses_errors(tmp_path, monkeypatch, capsys):
    """Python 3.14 exists() hides EACCES; stat/read must preserve the distinction."""
    import errno
    import runpy
    import sys

    helper = CTL.with_name("wingman-shared-status.py")
    namespace = runpy.run_path(str(helper))
    home = tmp_path / "shared"
    etc = tmp_path / "etc/wingman"
    original_stat = Path.stat
    original_read = Path.read_text

    def blocked(path):
        return path.is_relative_to(etc) or path.is_relative_to(home)

    def stat(path, *args, **kwargs):
        if blocked(path):
            raise PermissionError(errno.EACCES, "synthetic untraversable directory")
        return original_stat(path, *args, **kwargs)

    def read_text(path, *args, **kwargs):
        if blocked(path):
            raise PermissionError(errno.EACCES, "synthetic unreadable configuration")
        return original_read(path, *args, **kwargs)

    def python314_exists(path, *args, **kwargs):
        try:
            path.stat(*args, **kwargs)
        except OSError:
            return False
        return True

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "exists", python314_exists)
    monkeypatch.setenv("WINGMAN_HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [str(helper), "--home", str(home)])
    namespace["main"].__globals__["run"] = lambda *args: "unavailable"
    assert namespace["main"]() == 0
    output = capsys.readouterr().out
    assert "Shared HTTP service:" in output
    assert "Tenants: (needs sudo)" in output
    assert "not running" not in output


def test_shared_detection_distinguishes_absent_and_untraversable_directories(tmp_path):
    """Run the helper under the test interpreter with real directory permissions."""
    import sys

    helper = CTL.with_name("wingman-shared-status.py")
    home = tmp_path / "shared"
    home.mkdir()
    etc = tmp_path / "etc/wingman"
    etc.mkdir(parents=True)
    (etc / "tenants.toml").write_text("")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("systemctl", "curl"):
        command = bindir / name
        command.write_text("#!/bin/sh\nexit 1\n")
        command.chmod(0o700)
    env = {
        **os.environ,
        "WINGMAN_HOST_ROOT": str(tmp_path),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    home.chmod(0)
    etc.chmod(0)
    try:
        result = subprocess.run(
            [sys.executable, str(helper), "--home", str(home)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        home.chmod(0o700)
        etc.chmod(0o700)
    assert result.returncode == 0, result.stderr
    assert "Shared HTTP service:" in result.stdout
    assert "Tenants: (needs sudo)" in result.stdout
    (etc / "tenants.toml").unlink()
    absent = subprocess.run(
        [sys.executable, str(helper), "--home", str(home)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert absent.returncode == 3
    assert absent.stdout == ""
