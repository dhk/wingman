"""Per-instance self-restart, gated by that instance's own token (#133)."""

from wingman.infrastructure.self_restart import (
    SYSTEMD_UNIT,
    systemd_manages_this_instance,
    trigger_restart,
)


def test_no_systemctl_binary_means_not_systemd_managed(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert systemd_manages_this_instance() is False


def test_active_unit_is_detected(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    assert systemd_manages_this_instance(run=lambda argv: (0, "active\n")) is True


def test_inactive_or_missing_unit_is_not_managed(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    assert systemd_manages_this_instance(run=lambda argv: (0, "inactive\n")) is False
    assert systemd_manages_this_instance(run=lambda argv: (3, "")) is False


def test_trigger_restart_calls_systemctl_restart_for_this_unit() -> None:
    calls: list[list[str]] = []
    trigger_restart(launch=calls.append)
    assert calls == [["systemctl", "--user", "restart", SYSTEMD_UNIT]]
