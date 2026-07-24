"""Root-run: keep every shape-B user's wingman install current (#125)."""

from wingman.infrastructure.upgrade_all import (
    UpgradeTarget,
    _parse_usernames,
    main,
    target_for,
    upgrade_all,
    upgrade_one,
)


def _homes(mapping: dict[str, str]):
    return lambda username: mapping.get(username)


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


def test_target_for_known_user() -> None:
    target = target_for("trent", resolve_home=_homes({"trent": "/home/trent"}))
    assert target == UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")


def test_target_for_unknown_user_is_none() -> None:
    assert target_for("ghost", resolve_home=_homes({})) is None


def test_parse_usernames_accepts_commas_and_whitespace() -> None:
    assert _parse_usernames("dhk, trent   pat") == ["dhk", "trent", "pat"]
    assert _parse_usernames("") == []


# --- upgrade_one: the happy path and each failure point -----------------


def test_full_upgrade_pulls_reinstalls_and_restarts() -> None:
    target = UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")
    run = _scripted_runner(
        {
            ("sudo", "-u", "trent", "env", "XDG_RUNTIME_DIR=/run/user/1001", "git"): (0, ""),
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
    assert result.steps == ["pulled latest", "reinstalled", "restarted wingman-mcp.service"]
    # every call ran as the target user, with XDG_RUNTIME_DIR set
    for call in run.calls:  # type: ignore[attr-defined]
        assert call[:2] == ["sudo", "-u"]
        assert "XDG_RUNTIME_DIR=/run/user/1001" in call


def test_pull_failure_stops_before_reinstall() -> None:
    target = UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")
    run = _scripted_runner({("sudo", "-u", "trent"): (1, "local changes present")})
    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "pull failed, nothing was touched" in result.steps[0]
    assert "local changes present" in result.steps[0]
    assert len(result.steps) == 1  # never reached reinstall


def test_reinstall_failure_stops_before_restart() -> None:
    target = UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")
    calls = {"n": 0}

    def run(argv: list[str]) -> tuple[int, str]:
        calls["n"] += 1
        if "git" in argv:
            return 0, ""
        if "uv" in argv:
            return 1, "disk full"
        raise AssertionError(f"unexpected call after reinstall failure: {argv}")

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "reinstall failed, nothing was restarted" in result.steps[1]
    assert "disk full" in result.steps[1]


def test_non_systemd_managed_user_is_reported_ok_without_restart() -> None:
    target = UpgradeTarget(user="dhk", repo_path="/home/dhk/src/wingman")

    def run(argv: list[str]) -> tuple[int, str]:
        if "git" in argv or "uv" in argv:
            return 0, ""
        if "is-active" in argv:
            return 3, "inactive\n"
        raise AssertionError(f"restart should never be attempted: {argv}")

    result = upgrade_one(target, run=run, resolve_uid=_uids({"dhk": 1000}))
    assert result.ok  # the build IS updated — just nothing to restart
    assert "not active" in result.steps[-1]


def test_restart_failure_is_reported() -> None:
    target = UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")

    def run(argv: list[str]) -> tuple[int, str]:
        if "restart" in argv:
            return 1, "Failed to restart wingman-mcp.service: Unit not found."
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""  # git pull / uv reinstall

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "restart failed" in result.steps[-1]


# --- upgrade_all: isolation across users ---------------------------------


def test_one_users_failure_never_blocks_another() -> None:
    good = UpgradeTarget(user="dhk", repo_path="/home/dhk/src/wingman")
    bad = UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")

    def run(argv: list[str]) -> tuple[int, str]:
        if "-u" in argv and "trent" in argv:
            return 1, "pull failed for trent"
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    results = upgrade_all([bad, good], run=run, resolve_uid=_uids({"dhk": 1000, "trent": 1001}))
    by_user = {result.user: result for result in results}
    assert not by_user["trent"].ok
    assert by_user["dhk"].ok  # unaffected by trent's failure


def test_a_runner_that_raises_is_caught_and_reported() -> None:
    target = UpgradeTarget(user="trent", repo_path="/home/trent/src/wingman")

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
