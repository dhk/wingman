"""The guided diagnostic ladder (#137, #138): 'wingman doctor --deep'."""

from pathlib import Path

import pytest

from wingman.infrastructure import doctor_deep, portcheck
from wingman.infrastructure.config import ENV_DATA_DIR, load_config


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    return config


# --- cursor state -----------------------------------------------------


def test_cursor_defaults_to_one(workspace) -> None:
    assert doctor_deep.read_cursor(workspace) == 1


def test_cursor_roundtrip_and_reset(workspace) -> None:
    doctor_deep.write_cursor(workspace, 3)
    assert doctor_deep.read_cursor(workspace) == 3
    doctor_deep.reset_cursor(workspace)
    assert doctor_deep.read_cursor(workspace) == 1


def test_corrupt_state_file_falls_back_to_one(workspace) -> None:
    (workspace.data_dir / doctor_deep.STATE_FILENAME).write_text("not json", encoding="utf-8")
    assert doctor_deep.read_cursor(workspace) == 1


def test_out_of_range_cursor_falls_back_to_one(workspace) -> None:
    doctor_deep.write_cursor(workspace, 999)
    assert doctor_deep.read_cursor(workspace) == 1


# --- step 1: pidfile & port binding -------------------------------------


def test_step1_passes_when_pid_matches_port_owner(workspace, monkeypatch) -> None:
    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: 4242)
    monkeypatch.setattr(
        portcheck,
        "find_port_owner",
        lambda port, run=None: portcheck.PortOwner(
            pid=4242, command="wingman-mcp --http", user="dave"
        ),
    )
    result = doctor_deep.run_step(workspace, 1, 8787)
    assert result.ok
    assert "4242" in result.summary


def test_step1_flags_cross_account_port_squatting(workspace, monkeypatch) -> None:
    """The exact #138 incident: our pidfile is empty/absent, but the port is
    held by a process this account can't identify."""
    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: None)
    monkeypatch.setattr(
        portcheck,
        "find_port_owner",
        lambda port, run=None: portcheck.PortOwner(pid=999, command=None, user=None),
    )
    result = doctor_deep.run_step(workspace, 1, 8788)
    assert not result.ok
    assert "999" in result.summary
    assert result.next_action is not None
    assert "different account" in result.next_action


def test_step1_flags_pidfile_alive_but_not_bound(workspace, monkeypatch) -> None:
    """The exact #137 gap: pidfile-verified alive+named-right is not enough
    — the process must actually be bound to the port."""
    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: 4242)
    monkeypatch.setattr(portcheck, "find_port_owner", lambda port, run=None: None)
    result = doctor_deep.run_step(workspace, 1, 8787)
    assert not result.ok
    assert "not bound" in result.summary


def test_step1_nothing_running_at_all(workspace, monkeypatch) -> None:
    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: None)
    monkeypatch.setattr(portcheck, "find_port_owner", lambda port, run=None: None)
    result = doctor_deep.run_step(workspace, 1, 8787)
    assert not result.ok
    assert "wingman-ctl start" in result.next_action


# --- step 2: loopback health ---------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_step2_passes_when_health_answers(workspace, monkeypatch) -> None:
    monkeypatch.setattr(
        doctor_deep.httpx,
        "get",
        lambda url, timeout=None: _FakeResponse({"version": "1.2.3", "started_at": "now"}),
    )
    result = doctor_deep.run_step(workspace, 2, 8787)
    assert result.ok
    assert "1.2.3" in result.summary


def test_step2_fails_when_unreachable(workspace, monkeypatch) -> None:
    import httpx as real_httpx

    def raise_connect_error(url, timeout=None):
        raise real_httpx.ConnectError("refused")

    monkeypatch.setattr(doctor_deep.httpx, "get", raise_connect_error)
    result = doctor_deep.run_step(workspace, 2, 8787)
    assert not result.ok
    assert result.next_action is not None


# --- step 3: tunnel health -------------------------------------------------


def test_step3_skips_when_no_tunnel_detected(workspace, monkeypatch) -> None:
    import wingman.mcp_server as mcp_server_mod

    monkeypatch.setattr(mcp_server_mod, "_extra_allowed_hosts", lambda cli: [])
    result = doctor_deep.run_step(workspace, 3, 8787)
    assert result.ok
    assert "no tunnel" in result.summary


# --- step 4: process state & logs -----------------------------------------


def test_step4_nothing_to_inspect(workspace, monkeypatch) -> None:
    monkeypatch.setattr(portcheck, "find_port_owner", lambda port, run=None: None)
    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: None)
    result = doctor_deep.run_step(workspace, 4, 8787)
    assert not result.ok


def test_step4_tails_the_log_file(workspace, monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "wingman-mcp.log"
    log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
    monkeypatch.setenv("WINGMAN_LOG", str(log_path))
    monkeypatch.setattr(
        portcheck,
        "find_port_owner",
        lambda port, run=None: portcheck.PortOwner(
            pid=4242, command="wingman-mcp --http", user="dave"
        ),
    )
    monkeypatch.setattr(doctor_deep.mcp_process, "read_server_pid", lambda config: 4242)
    monkeypatch.setattr(doctor_deep.systemd_check, "unit_exists", lambda unit: False)
    result = doctor_deep.run_step(workspace, 4, 8787)
    assert result.ok
    assert "line3" in result.summary


# --- step 5: systemd rate limit -------------------------------------------


def test_step5_skips_when_systemd_unavailable(workspace, monkeypatch) -> None:
    monkeypatch.setattr(doctor_deep.systemd_check, "systemd_available", lambda: False)
    result = doctor_deep.run_step(workspace, 5, 8787)
    assert result.ok
    assert "not present" in result.summary


def test_step5_flags_rate_limited_unit(workspace, monkeypatch) -> None:
    monkeypatch.setattr(doctor_deep.systemd_check, "systemd_available", lambda: True)
    monkeypatch.setattr(doctor_deep.systemd_check, "unit_exists", lambda unit: True)
    monkeypatch.setattr(doctor_deep.systemd_check, "is_rate_limited", lambda unit: True)
    result = doctor_deep.run_step(workspace, 5, 8787)
    assert not result.ok
    assert "reset-failed" in result.next_action


def test_total_steps_matches_step_names() -> None:
    assert doctor_deep.total_steps() == len(doctor_deep.STEP_NAMES) == 5
