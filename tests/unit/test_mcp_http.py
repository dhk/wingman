"""Remote MCP transport (RFC-017): loopback HTTP behind a capability path."""

from pathlib import Path

import pytest

from wingman.infrastructure.config import load_config
from wingman.mcp_server import _http_token, main, server


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(data_dir))
    return data_dir


class RunRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


def test_token_is_persistent_secret_and_rotatable(workspace: Path) -> None:
    config = load_config()
    token = _http_token(config)
    assert len(token) >= 24
    token_path = workspace / "mcp-http-token"
    assert token_path.exists()
    assert (token_path.stat().st_mode & 0o777) == 0o600  # owner-only
    assert _http_token(config) == token  # stable across runs
    rotated = _http_token(config, rotate=True)
    assert rotated != token  # rotation is revocation


def test_default_invocation_stays_stdio(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RunRecorder()
    monkeypatch.setattr(server, "run", recorder)
    main([])
    assert recorder.calls == [((), {})]  # no transport argument: stdio default
    assert not (workspace / "mcp-http-token").exists()  # no token unless HTTP is asked for


def test_http_invocation_configures_capability_path(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = RunRecorder()
    monkeypatch.setattr(server, "run", recorder)
    main(["--http", "--port", "9911"])
    assert recorder.calls == [((), {"transport": "streamable-http"})]
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 9911
    token = _http_token(load_config())
    assert server.settings.streamable_http_path == f"/mcp/{token}"
    out = capsys.readouterr()
    assert f"http://127.0.0.1:9911/mcp/{token}" in out.out
    assert "rotate-token" in out.out
    assert "WARNING" not in out.err  # loopback bind warns about nothing


def test_non_loopback_bind_warns(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server, "run", RunRecorder())
    main(["--http", "--host", "0.0.0.0", "--port", "9912"])
    assert "WARNING" in capsys.readouterr().err


def test_rotate_token_requires_http(workspace: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--rotate-token"])


def test_prefix_serves_natively_on_the_folder(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--prefix /trent: the server itself owns the path segment (no proxy strip needed)."""
    monkeypatch.setattr(server, "run", RunRecorder())
    main(["--http", "--port", "9913", "--prefix", "trent/"])  # normalized either way
    token = _http_token(load_config())
    assert server.settings.streamable_http_path == f"/trent/mcp/{token}"
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:9913/trent/mcp/{token}" in out
    assert f"http://127.0.0.1:9913/trent/ui/{token}" in out
