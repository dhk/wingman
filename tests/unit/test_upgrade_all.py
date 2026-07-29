"""Root-run: keep every shape-B user's wingman install current (#125, #167)."""

from wingman.infrastructure.upgrade_all import (
    UpgradeTarget,
    _parse_usernames,
    _source_override_env_var,
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


def test_target_for_known_user_is_checkout_shape_by_default() -> None:
    target = target_for("trent", resolve_home=_homes({"trent": "/home/trent"}))
    assert target == UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )


def test_target_for_with_install_source_is_local_path_shape() -> None:
    """#167: a user with no checkout of their own (e.g. Trent) installs from
    someone else's already-updated checkout, and has nothing to pull."""
    target = target_for(
        "trent",
        resolve_home=_homes({"trent": "/home/trent"}),
        install_source="/home/dhk/src/wingman",
    )
    assert target == UpgradeTarget(
        user="trent", install_source="/home/dhk/src/wingman", own_checkout=None
    )


def test_target_for_unknown_user_is_none() -> None:
    assert target_for("ghost", resolve_home=_homes({})) is None


def test_parse_usernames_accepts_commas_and_whitespace() -> None:
    assert _parse_usernames("dhk, trent   pat") == ["dhk", "trent", "pat"]
    assert _parse_usernames("") == []


def test_source_override_env_var_name() -> None:
    assert _source_override_env_var("trent") == "WINGMAN_UPGRADE_SOURCE_trent"


# --- upgrade_one: the happy path and each failure point -----------------


def test_full_upgrade_pulls_reinstalls_and_restarts() -> None:
    target = UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )
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


def test_local_path_shape_skips_pull_entirely() -> None:
    """#167: Trent's install has nothing of his own to 'git pull' — the
    upgrade must never attempt it, only reinstall from the source path."""
    target = UpgradeTarget(user="trent", install_source="/home/dhk/src/wingman", own_checkout=None)

    def run(argv: list[str]) -> tuple[int, str]:
        assert "git" not in argv, f"local-path shape must never pull: {argv}"
        if "uv" in argv:
            assert argv[-1] == "/home/dhk/src/wingman"  # installs from the SOURCE, not trent's home
            return 0, ""
        if "is-active" in argv:
            return 0, "active\n"
        if "restart" in argv:
            return 0, ""
        raise AssertionError(f"unexpected call: {argv}")

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert result.ok
    assert result.steps == ["reinstalled", "restarted wingman-mcp.service"]  # no "pulled latest"


def test_pull_failure_stops_before_reinstall() -> None:
    target = UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )
    run = _scripted_runner({("sudo", "-u", "trent"): (1, "local changes present")})
    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "pull failed, nothing was touched" in result.steps[0]
    assert "local changes present" in result.steps[0]
    assert len(result.steps) == 1  # never reached reinstall


def test_reinstall_failure_stops_before_restart() -> None:
    target = UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )

    def run(argv: list[str]) -> tuple[int, str]:
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
    target = UpgradeTarget(
        user="dhk", install_source="/home/dhk/src/wingman", own_checkout="/home/dhk/src/wingman"
    )

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
    target = UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )

    def run(argv: list[str]) -> tuple[int, str]:
        if "restart" in argv:
            return 1, "Failed to restart wingman-mcp.service: Unit not found."
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""  # git pull / uv reinstall

    result = upgrade_one(target, run=run, resolve_uid=_uids({"trent": 1001}))
    assert not result.ok
    assert "restart failed" in result.steps[-1]


# --- upgrade_all: isolation and sequencing across users -------------------


def test_one_users_failure_never_blocks_another() -> None:
    good = UpgradeTarget(
        user="dhk", install_source="/home/dhk/src/wingman", own_checkout="/home/dhk/src/wingman"
    )
    bad = UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )

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


def test_checkout_shape_users_run_before_local_path_shape_users() -> None:
    """#167: a local-path target may install from a checkout-shape target's
    directory, so the checkout must be pulled first regardless of input
    order — Trent listed before dhk must still upgrade dhk first."""
    trent_local_path = UpgradeTarget(
        user="trent", install_source="/home/dhk/src/wingman", own_checkout=None
    )
    dhk_checkout = UpgradeTarget(
        user="dhk", install_source="/home/dhk/src/wingman", own_checkout="/home/dhk/src/wingman"
    )
    order: list[str] = []

    def run(argv: list[str]) -> tuple[int, str]:
        user = argv[2]  # sudo -u <user> ...
        if user not in order:
            order.append(user)
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    upgrade_all(
        [trent_local_path, dhk_checkout], run=run, resolve_uid=_uids({"dhk": 1000, "trent": 1001})
    )
    assert order == ["dhk", "trent"]  # checkout-shape first, despite input order


def test_a_runner_that_raises_is_caught_and_reported() -> None:
    target = UpgradeTarget(
        user="trent",
        install_source="/home/trent/src/wingman",
        own_checkout="/home/trent/src/wingman",
    )

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


def test_main_reads_per_user_source_override_from_env(monkeypatch, capsys) -> None:
    """#167: WINGMAN_UPGRADE_SOURCE_<username> routes that user through the
    local-path shape end to end via the real CLI entry point."""
    import wingman.infrastructure.upgrade_all as upgrade_all_module

    monkeypatch.setenv("WINGMAN_UPGRADE_SOURCE_trent", "/home/dhk/src/wingman")
    monkeypatch.setattr(
        upgrade_all_module, "_default_home", lambda u: {"trent": "/home/trent"}.get(u)
    )
    monkeypatch.setattr(upgrade_all_module, "_default_uid", lambda u: {"trent": 1001}.get(u))

    def run(argv: list[str]) -> tuple[int, str]:
        assert "git" not in argv
        if "uv" in argv:
            assert argv[-1] == "/home/dhk/src/wingman"
        if "is-active" in argv:
            return 0, "active\n"
        return 0, ""

    monkeypatch.setattr(upgrade_all_module, "_default_runner", run)

    main(["--users", "trent"])
    out = capsys.readouterr().out
    assert "[ok] trent:" in out
    assert "reinstalled" in out
