"""Root-run: keep every shape-B user's wingman install current (#125).

Every user gets the identical git-free upgrade: 'uv tool install
--reinstall <source>' (default git+https://github.com/dhk/wingman.git),
then a restart if wingman-mcp.service was already active.
"""

from wingman.infrastructure.upgrade_all import (
    DEFAULT_INSTALL_SOURCE,
    UpgradeTarget,
    _parse_usernames,
    main,
    target_for,
    upgrade_all,
    upgrade_one,
)


def _uids(mapping: dict[str, int]):
    return lambda username: mapping.get(username)


def _scripted_runner(responses: dict[tuple[str, ...], tuple[int, str]]):
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        for prefix, result in responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return 1, f"unscripted call: {argv}"

    run.calls = calls  # type: ignore[attr-defined]
    return run


# --- target resolution -----------------------------------------------


def test_target_for_known_user_uses_the_default_source() -> None:
    target = target_for("trent", resolve_uid=_uids({"trent": 1001}))
    assert target == UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)


def test_target_for_honors_an_explicit_source_override() -> None:
    target = target_for(
        "trent", resolve_uid=_uids({"trent": 1001}), install_source="git+https://x/fork.git"
    )
    assert target == UpgradeTarget(user="trent", install_source="git+https://x/fork.git")


def test_target_for_unknown_user_is_none() -> None:
    assert target_for("ghost", resolve_uid=_uids({})) is None


def test_parse_usernames_accepts_commas_and_whitespace() -> None:
    assert _parse_usernames("dhk, trent   pat") == ["dhk", "trent", "pat"]
    assert _parse_usernames("") == []


# --- upgrade_one: the happy path and each failure point -----------------


def test_full_upgrade_reinstalls_and_restarts_with_no_local_checkout() -> None:
    target = UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)
    run = _scripted_runner(
        {
            ("sudo", "-u", "trent", "env", "XDG_RUNTIME_DIR=/run/user/1001", "uv"): (0, ""),
            (
                "sudo",
                "-u",
                "trent",
                "env",
                "XDG_RUNTIME_DIR=/run/user/1001",
                "systemctl",
                "--user",
                "is-active",
            ): (0, "active\n"),
            (
                "sudo",
                "-u",
                "trent",
                "env",
                "XDG_RUNTIME_DIR=/run/user/1001",
                "systemctl",
                "--user",
                "restart",
            ): (0, ""),
        }
    )
    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert result.ok
    assert result.steps == ["reinstalled", "restarted wingman-mcp.service"]
    # every call ran as the target user, with XDG_RUNTIME_DIR set, and none was 'git'
    for call in run.calls:  # type: ignore[attr-defined]
        assert call[:2] == ["sudo", "-u"]
        assert "XDG_RUNTIME_DIR=/run/user/1001" in call
        assert "git" not in call


def test_install_uses_the_configured_source() -> None:
    target = UpgradeTarget(user="trent", install_source="git+https://x/fork.git@main")

    def run(argv: list[str]) -> tuple[int, str]:
        if "uv" in argv:
            assert argv[-1] == "git+https://x/fork.git@main"
            return 0, ""
        if "is-active" in argv:
            return 0, "active\n"
        if "restart" in argv:
            return 0, ""
        raise AssertionError(f"unexpected call: {argv}")

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert result.ok


def test_reinstall_failure_stops_before_restart() -> None:
    target = UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)

    def run(argv: list[str]) -> tuple[int, str]:
        if "uv" in argv:
            return 1, "disk full"
        raise AssertionError(f"unexpected call after reinstall failure: {argv}")

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "reinstall failed, nothing was restarted" in result.steps[0]
    assert "disk full" in result.steps[0]


def test_non_systemd_managed_user_is_reported_ok_without_restart() -> None:
    target = UpgradeTarget(user="dhk", install_source=DEFAULT_INSTALL_SOURCE)

    def run(argv: list[str]) -> tuple[int, str]:
        if "uv" in argv:
            return 0, ""
        if "is-active" in argv:
            return 3, "inactive\n"
        raise AssertionError(f"restart should never be attempted: {argv}")

    result = upgrade_one(target, run=run, resolve_uid=_uids({"dhk": 1000}))
    assert result.ok  # the build IS updated — just nothing to restart
    assert "not active" in result.steps[-1]


def test_restart_failure_is_reported() -> None:
    target = UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)

    def run(argv: list[str]) -> tuple[int, str]:
        if "restart" in argv:
            return 1, "Failed to restart wingman-mcp.service: Unit not found."
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""  # uv reinstall

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "restart failed" in result.steps[-1]


# --- upgrade_all: isolation across users -------------------


def test_one_users_failure_never_blocks_another() -> None:
    good = UpgradeTarget(user="dhk", install_source=DEFAULT_INSTALL_SOURCE)
    bad = UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)

    def run(argv: list[str]) -> tuple[int, str]:
        if "-u" in argv and "trent" in argv:
            return 1, "reinstall failed for trent"
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    results = upgrade_all([bad, good], run=run, resolve_uid=_uids({"dhk": 1000, "trent": 1001}))
    by_user = {result.user: result for result in results}
    assert not by_user["trent"].ok
    assert by_user["dhk"].ok  # unaffected by trent's failure


def test_targets_run_in_the_order_given() -> None:
    """No shape-based reordering anymore — nothing depends on another
    target's install, so input order is preserved."""
    trent = UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)
    dhk = UpgradeTarget(user="dhk", install_source=DEFAULT_INSTALL_SOURCE)
    order: list[str] = []

    def run(argv: list[str]) -> tuple[int, str]:
        user = argv[2]  # sudo -u <user> ...
        if user not in order:
            order.append(user)
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    upgrade_all([trent, dhk], run=run, resolve_uid=_uids({"dhk": 1000, "trent": 1001}))
    assert order == ["trent", "dhk"]


def test_a_runner_that_raises_is_caught_and_reported() -> None:
    target = UpgradeTarget(user="trent", install_source=DEFAULT_INSTALL_SOURCE)

    def exploding_run(argv: list[str]) -> tuple[int, str]:
        raise RuntimeError("boom")

    results = upgrade_all([target], run=exploding_run)
    assert len(results) == 1
    assert not results[0].ok
    assert "unexpected error" in results[0].steps[0]


# --- main(): the CLI entry point ------------------------------------------


def test_main_exits_nonzero_with_no_users_configured(capsys) -> None:
    try:
        main(["--users", ""])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected SystemExit")
    assert "nothing to do" in capsys.readouterr().err


def test_main_installs_every_configured_user_from_git_with_no_checkout(monkeypatch, capsys) -> None:
    import wingman.infrastructure.upgrade_all as upgrade_all_module

    monkeypatch.setattr(upgrade_all_module, "_default_uid", lambda u: {"trent": 1001}.get(u))

    def run(argv: list[str]) -> tuple[int, str]:
        assert "git" not in argv
        if "uv" in argv:
            assert argv[-1] == upgrade_all_module.DEFAULT_INSTALL_SOURCE
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    monkeypatch.setattr(upgrade_all_module, "_default_runner", run)

    main(["--users", "trent"])
    out = capsys.readouterr().out
    assert "[ok] trent:" in out
    assert "reinstalled" in out


def test_main_source_override_applies_to_every_user(monkeypatch, capsys) -> None:
    import wingman.infrastructure.upgrade_all as upgrade_all_module

    monkeypatch.setattr(
        upgrade_all_module, "_default_uid", lambda u: {"dhk": 1000, "trent": 1001}.get(u)
    )

    def run(argv: list[str]) -> tuple[int, str]:
        if "uv" in argv:
            assert argv[-1] == "git+https://x/fork.git@testing"
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    monkeypatch.setattr(upgrade_all_module, "_default_runner", run)

    main(["--users", "dhk,trent", "--source", "git+https://x/fork.git@testing"])
    out = capsys.readouterr().out
    assert "[ok] dhk:" in out
    assert "[ok] trent:" in out
