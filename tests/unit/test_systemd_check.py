"""systemd --user unit state: detecting the restart rate limit (#138)."""

from wingman.infrastructure.systemd_check import (
    is_rate_limited,
    systemd_available,
    unit_exists,
    unit_result,
)


def _runner(result: tuple[int, str]):
    def run(argv: list[str]) -> tuple[int, str]:
        return result

    return run


def test_no_systemctl_binary_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert systemd_available() is False
    assert unit_exists("wingman-mcp.service") is False
    assert unit_result("wingman-mcp.service") is None
    assert is_rate_limited("wingman-mcp.service") is False


def test_unit_not_found(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    run = _runner((0, "LoadState=not-found\n"))
    assert unit_exists("wingman-mcp.service", run=run) is False


def test_unit_exists_and_result(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    run = _runner((0, "LoadState=loaded\n"))
    assert unit_exists("wingman-mcp.service", run=run) is True


def test_rate_limited_state_detected(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    run = _runner((0, "Result=start-limit-hit\n"))
    assert unit_result("wingman-mcp.service", run=run) == "start-limit-hit"
    assert is_rate_limited("wingman-mcp.service", run=run) is True


def test_healthy_unit_is_not_rate_limited(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    run = _runner((0, "Result=success\n"))
    assert is_rate_limited("wingman-mcp.service", run=run) is False
