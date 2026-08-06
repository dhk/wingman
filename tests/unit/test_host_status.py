"""Host-wide instance status (#263).

Every test drives injected runners and an injected /health fetcher: no
real sockets, no real 'ss', no real 'systemctl', no real git.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from wingman.infrastructure import host_status
from wingman.infrastructure.host_status import (
    InstanceRow,
    build_state,
    collect,
    commit_of,
    is_dirty_build,
    is_wingman_health,
    read_installation_names,
    render,
    service_column,
    unit_state,
)

NOW = datetime(2026, 8, 5, 21, 0, tzinfo=UTC)


def _health(payloads: dict[int, dict[str, object]]):
    def fetch(host: str, port: int) -> dict[str, object] | None:
        return payloads.get(port)

    return fetch


def _runner(responses: dict[tuple[str, ...], tuple[int, str]], *, default=(1, "")):
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        for prefix, response in responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return response
        return default

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_wingman_health_is_told_apart_from_a_neighbours() -> None:
    assert is_wingman_health({"version": "0.4.1", "started_at": "2026-08-05T19:28:11+00:00"})
    assert is_wingman_health({"service": "wingman", "version": "0.4.1", "started_at": "x"})
    # Alexandria shares this box and this port neighbourhood.
    assert not is_wingman_health({"service": "alexandria", "version": "0.1.0", "started_at": "x"})
    assert not is_wingman_health({"nothing": "useful"})


def test_commit_and_dirtiness_are_read_out_of_the_version() -> None:
    assert commit_of("0.4.1.dev116+gc5fcdc275") == "c5fcdc275"
    assert commit_of("0.4.1.dev116+gc5fcdc275.d20260805") == "c5fcdc275"
    assert commit_of("0.4.1") is None
    assert is_dirty_build("0.4.1.dev116+gc5fcdc275.d20260805")
    assert not is_dirty_build("0.4.1.dev116+gc5fcdc275")


def test_build_state_counts_commits_behind_head(tmp_path: Path) -> None:
    run = _runner({("git",): (0, "3\n")})
    assert build_state("0.4.1.dev113+ga654f1816", tmp_path, run) == "3 behind"
    assert run.calls[0][:4] == ["git", "-C", str(tmp_path), "rev-list"]  # type: ignore[attr-defined]


def test_build_state_says_current_at_zero(tmp_path: Path) -> None:
    assert (
        build_state("0.4.1.dev116+gc5fcdc275", tmp_path, _runner({("git",): (0, "0\n")}))
        == "current"
    )


def test_build_state_admits_an_unknown_sha_rather_than_guessing(tmp_path: Path) -> None:
    """A sha this checkout has never seen must not read as 'current'."""
    assert build_state("0.4.1+gdeadbee", tmp_path, _runner({("git",): (128, "")})) == "unknown"


def test_build_state_without_a_repo_is_a_question_mark() -> None:
    assert build_state("0.4.1+gc5fcdc275", None, _runner({})) == "?"


def _fake_passwd(monkeypatch, accounts: dict[str, int]) -> None:
    """Stand in for the host's passwd database.

    These tests used to pass real names and real uids, which only worked
    because dhk happens to be 1000 on the box they were written on — the
    same host-dependence that made two of minority-report's tests
    unrunnable anywhere else.
    """
    import pwd as pwd_module

    def getpwnam(name: str) -> object:
        if name not in accounts:
            raise KeyError(f"getpwnam(): name not found: {name}")
        uid = accounts[name]
        return pwd_module.struct_passwd((name, "x", uid, uid, "", f"/home/{name}", "/bin/bash"))

    monkeypatch.setattr(host_status.pwd, "getpwnam", getpwnam)


def test_unit_state_asks_directly_when_running_as_that_account(monkeypatch) -> None:
    _fake_passwd(monkeypatch, {"dhk": 1000})
    run = _runner({("systemctl",): (3, "failed\n")})
    assert unit_state("dhk", run=run, euid=1000) == "failed"
    assert run.calls[0] == [  # type: ignore[attr-defined]
        "systemctl",
        "--user",
        "is-active",
        "wingman-mcp.service",
    ]


def test_unit_state_for_another_account_is_unknown_without_root(monkeypatch) -> None:
    _fake_passwd(monkeypatch, {"dhk": 1000, "trent": 1001})
    run = _runner({("systemctl",): (0, "active\n")})
    assert unit_state("trent", run=run, euid=1000) is None
    assert run.calls == []  # type: ignore[attr-defined]


def test_unit_state_for_another_account_carries_xdg_runtime_dir_as_root(monkeypatch) -> None:
    """Without XDG_RUNTIME_DIR the call cannot reach that account's session
    bus at all — the gotcha upgrade_all.py carries on every sudo call."""
    _fake_passwd(monkeypatch, {"trent": 1001})
    run = _runner({("sudo",): (0, "active\n")})
    assert unit_state("trent", run=run, euid=0) == "active"
    assert run.calls[0][:5] == [  # type: ignore[attr-defined]
        "sudo",
        "-u",
        "trent",
        "env",
        "XDG_RUNTIME_DIR=/run/user/1001",
    ]


def test_unit_state_uses_sudo_for_your_own_account_when_running_as_root(monkeypatch) -> None:
    """Root is not dhk, even when asking about dhk's unit.

    Deciding by NAME took the 'that's me, ask directly' branch here and
    queried root's own session bus, reporting the wrong account's state.
    Deciding by uid cannot (#265).
    """
    _fake_passwd(monkeypatch, {"dhk": 1000})
    run = _runner({("sudo",): (0, "inactive\n")})
    assert unit_state("dhk", run=run, euid=0) == "inactive"
    assert run.calls[0][0] == "sudo"  # type: ignore[attr-defined]


def test_unit_state_gives_up_on_an_unresolvable_account(monkeypatch) -> None:
    """A truncated name ('wingman+') resolves to nothing, and must not be
    reported as a state — the exact failure that made SERVICE read '?' for
    wingman-shared even as root (#265)."""
    _fake_passwd(monkeypatch, {"wingman-shared": 1002})
    run = _runner({("sudo",): (0, "active\n")})
    assert unit_state("wingman+", run=run, euid=0) is None
    assert run.calls == []  # type: ignore[attr-defined]
    # The untruncated name, same call, does resolve.
    assert unit_state("wingman-shared", run=run, euid=0) == "active"


def test_invoking_identity_prefers_the_sudo_caller(monkeypatch) -> None:
    """Under sudo, config must come from the human, not from root."""
    _fake_passwd(monkeypatch, {"dhk": 1000})
    name, home = host_status.invoking_identity({"SUDO_USER": "dhk"})
    assert name == "dhk"
    assert home == Path("/home/dhk")


def test_invoking_identity_falls_back_when_sudo_user_is_absent_or_bogus(monkeypatch) -> None:
    _fake_passwd(monkeypatch, {})
    monkeypatch.setattr(host_status.os, "geteuid", lambda: 4242)
    monkeypatch.setattr(
        host_status.pwd,
        "getpwuid",
        lambda uid: __import__("pwd").struct_passwd(
            ("someone", "x", uid, uid, "", "/home/someone", "/bin/bash")
        ),
    )
    assert host_status.invoking_identity({})[0] == "someone"
    assert host_status.invoking_identity({"SUDO_USER": "ghost"})[0] == "someone"


def _fake_which(monkeypatch) -> None:
    """portcheck asks shutil.which('ss') before shelling out.

    Left real, these tests silently depend on iproute2 being installed on
    whatever runs them — the same class of environmental assumption that
    made two of this file's tests pass locally and fail in CI (#265).
    """
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")


def _ss_listening(port: int, pid: int = 9001, user: str = "wingman-shared") -> dict:
    return {
        ("ss", "-H", "-tlnp", f"sport = :{port}"): (
            0,
            f'LISTEN 0 2048 127.0.0.1:{port} 0.0.0.0:* users:(("wingman-mcp",pid={pid},fd=7))',
        ),
        ("ps",): (0, f"{user} /home/{user}/.local/bin/wingman-mcp --http\n"),
    }


def test_a_restarting_instance_is_shown_not_dropped(tmp_path: Path, monkeypatch) -> None:
    """Bound but silent is a restart, not an absence (#267).

    The first version dropped the row entirely, so the only live instance
    on a consolidated box vanished for the three seconds after every
    deploy — and the output supported the most alarming reading, that the
    box had no wingman on it at all.
    """
    _fake_which(monkeypatch)
    _fake_passwd(monkeypatch, {"wingman-shared": 1002})
    waited: list[float] = []
    rows = collect(
        [8789],
        {},
        repo=tmp_path,
        health=_health({}),  # never answers: mid-restart
        run=_runner(_ss_listening(8789)),
        euid=1000,
        sleep=waited.append,
    )

    assert len(rows) == 1
    assert rows[0].service == "starting"
    assert rows[0].note == ""  # not a finding to act on; it resolves itself
    assert waited, "a bound-but-silent port must be retried before being judged"
    # A row saying 'stopped' under VERSION and 'starting' under SERVICE
    # contradicts itself.
    assert "stopped" not in render([rows[0]], now=NOW, repo=None, euid=0)


def test_a_restarting_instance_is_seen_without_privilege(tmp_path: Path, monkeypatch) -> None:
    """The reported case was an UNPRIVILEGED run right after a deploy.

    Ownership needs a pid, and 'ss -tlnp' hides pids for other accounts'
    sockets — so keying "is it restarting" off find_port_owner fixed this
    only for root, which is not where it was reported. Boundness is the
    question that needs no privilege (#267).
    """
    _fake_which(monkeypatch)
    rows = collect(
        [8789],
        {},
        repo=tmp_path,
        health=_health({}),
        # 'ss -H -tln' answers; the '-tlnp' form yields no pid, as for any
        # socket owned by another account.
        run=_runner(
            {("ss", "-H", "-tln", "sport = :8789"): (0, "LISTEN 0 2048 127.0.0.1:8789 0.0.0.0:*")}
        ),
        euid=1000,
        sleep=lambda _seconds: None,
    )

    assert len(rows) == 1
    assert rows[0].service == "starting"
    assert rows[0].name == "?"  # who it belongs to is genuinely unknowable here


def test_a_restarting_instance_that_comes_back_reads_as_running(
    tmp_path: Path, monkeypatch
) -> None:
    """The retry is the point: one ordinary restart should not show as down."""
    _fake_which(monkeypatch)
    _fake_passwd(monkeypatch, {"wingman-shared": 1002})
    answers = [None, {"version": "0.4.1+g36b879f0d", "started_at": "2026-08-06T03:39:50+00:00"}]

    def flaky(host: str, port: int) -> dict | None:
        return answers.pop(0)

    rows = collect(
        [8789],
        {},
        repo=tmp_path,
        health=flaky,
        run=_runner({**_ss_listening(8789), ("sudo",): (0, "active\n"), ("git",): (0, "0\n")}),
        euid=0,
        sleep=lambda _seconds: None,
    )

    assert len(rows) == 1
    assert rows[0].running is True
    assert rows[0].service == "active"
    assert rows[0].build == "current"


def test_an_empty_port_is_still_omitted(tmp_path: Path) -> None:
    """The retry must not resurrect ports that genuinely have nothing."""
    waited: list[float] = []
    rows = collect(
        [8790],
        {},
        repo=tmp_path,
        health=_health({}),
        run=_runner({}),  # ss finds no listener
        euid=1000,
        sleep=waited.append,
    )
    assert rows == []
    assert waited == [], "nothing bound: nothing to wait for"


def test_listening_without_an_active_unit_is_called_unmanaged() -> None:
    service, note = service_column(True, "failed")
    assert service == "UNMANAGED"
    assert "upgrade-all will not restart" in note


def test_listening_under_systemd_is_quiet() -> None:
    assert service_column(True, "active") == ("active", "")


def test_listening_with_unknowable_state_is_a_question_mark_not_a_verdict() -> None:
    assert service_column(True, None) == ("?", "")


def test_stopped_instance_reports_its_unit_state() -> None:
    assert service_column(False, "inactive") == ("inactive", "")


def test_installation_names_are_read_without_touching_tokens(tmp_path: Path) -> None:
    path = tmp_path / "installations.toml"
    path.write_text(
        '[[instance]]\nname = "dhk"\nport = 8787\ntoken = "secret-value"\n'
        '[[instance]]\nname = "trent"\nport = 8788\ntoken = "another-secret"\n',
        encoding="utf-8",
    )
    assert read_installation_names(path) == {8787: "dhk", 8788: "trent"}


def test_unparseable_installations_file_does_not_break_status(tmp_path: Path) -> None:
    path = tmp_path / "installations.toml"
    path.write_text("this is not toml [[[", encoding="utf-8")
    assert read_installation_names(path) == {}
    assert read_installation_names(tmp_path / "absent.toml") == {}


def test_collect_flags_the_unmanaged_instance_and_leaves_the_others_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole point, end to end: 8787 listening but its unit failed,
    8788 healthy under systemd, 8789 healthy but a build behind.

    The injected passwd lookup is load-bearing, not decoration: without it
    this resolves 'dhk' against the real host database, which passes on the
    box it was written on and fails in CI where no such account exists.
    """
    _fake_which(monkeypatch)
    _fake_passwd(monkeypatch, {"dhk": 1000})
    payloads = {
        8787: {"version": "0.4.1.dev116+gc5fcdc275", "started_at": "2026-08-05T19:28:11+00:00"},
        8788: {"version": "0.4.1.dev116+gc5fcdc275", "started_at": "2026-08-05T19:44:38+00:00"},
        8789: {"version": "0.4.1.dev113+ga654f1816", "started_at": "2026-08-05T04:30:12+00:00"},
    }
    run = _runner(
        {
            ("ss", "-H", "-tlnp", "sport = :8787"): (
                0,
                'LISTEN 0 2048 127.0.0.1:8787 0.0.0.0:* users:(("wingman-mcp",pid=302633,fd=7))',
            ),
            ("ps",): (0, "dhk /home/dhk/.local/bin/wingman-mcp --http\n"),
            ("systemctl",): (3, "failed\n"),
            ("git", "-C", str(tmp_path), "rev-list", "--count", "c5fcdc275..HEAD"): (0, "0\n"),
            ("git", "-C", str(tmp_path), "rev-list", "--count", "a654f1816..HEAD"): (0, "3\n"),
        }
    )
    rows = collect(
        [8787, 8788, 8789],
        {8787: "dhk", 8788: "trent", 8789: "wingman-shared"},
        repo=tmp_path,
        health=_health(payloads),
        run=run,
        euid=1000,
    )

    assert [row.port for row in rows] == [8787, 8788, 8789]
    assert rows[0].service == "UNMANAGED"
    assert "upgrade-all will not restart" in rows[0].note
    assert rows[0].build == "current"
    # Other accounts: honestly unknown at this privilege, never guessed.
    assert rows[1].service == "?" and rows[1].note == ""
    assert rows[2].build == "3 behind"


def test_collect_skips_a_neighbours_service_on_a_scanned_port(tmp_path: Path) -> None:
    payloads = {8797: {"service": "alexandria", "version": "0.1.0", "started_at": "x"}}
    rows = collect([8797], {}, repo=tmp_path, health=_health(payloads), run=_runner({}))
    assert rows == []


def test_collect_keeps_a_configured_instance_that_answers_nothing(tmp_path: Path) -> None:
    """A registered instance that vanished is a finding, not an absence."""
    rows = collect(
        [8788],
        {8788: "trent"},
        repo=tmp_path,
        health=_health({}),
        run=_runner({}),
        euid=1000,
    )
    assert len(rows) == 1
    assert rows[0].running is False
    assert rows[0].name == "trent"


def test_collect_ignores_an_unconfigured_port_that_answers_nothing(tmp_path: Path) -> None:
    rows = collect([8790], {}, repo=tmp_path, health=_health({}), run=_runner({}))
    assert rows == []


def test_render_puts_the_unmanaged_row_in_a_needs_attention_block() -> None:
    rows = [
        InstanceRow(
            port=8787,
            name="dhk",
            running=True,
            version="0.4.1.dev116+gc5fcdc275.d20260805",
            started_at="2026-08-05T19:28:11+00:00",
            build="current",
            service="UNMANAGED",
            note="unit is 'failed' — upgrade-all will not restart this instance",
        ),
        InstanceRow(
            port=8788,
            name="trent",
            running=True,
            version="0.4.1.dev116+gc5fcdc275",
            started_at="2026-08-05T19:44:38+00:00",
            build="current",
            service="?",
            note="",
        ),
    ]
    output = render(rows, now=NOW, repo=Path("/home/dhk/src/wingman"), euid=1000)

    assert "PORT" in output.splitlines()[0]
    assert "19:28 (1h ago)" in output
    assert "Needs attention:" in output
    assert "8787: unit is 'failed'" in output
    assert "dirty working tree" in output
    # The hint must name a command that can actually be run. 'sudo wg hosts'
    # cannot — 'wg' is a shell alias sudo never inherits (#265).
    assert "rerun as:" in output
    assert "sudo env PYTHONDONTWRITEBYTECODE=1 /" in output
    assert "sudo wg" not in output
    # Without it, running as root writes root-owned .pyc into the invoking
    # user's uv tool store, and their next 'uv tool install --reinstall'
    # fails with EACCES — a hint that breaks the next deploy (#276).
    assert "PYTHONDONTWRITEBYTECODE=1" in output
    # 'env', not a bare VAR=value: sudo's default policy can refuse
    # caller-set environment variables outright.
    assert "sudo PYTHONDONTWRITEBYTECODE" not in output


def test_runnable_path_prefers_the_name_on_your_path(tmp_path: Path) -> None:
    """uv installs entry points into its tool store and symlinks them onto
    PATH. sys.argv[0] resolves to the store, which works but is nobody's
    muscle memory — prefer the name a human would actually type (#267)."""
    real = tmp_path / "store" / "wingman-host-status"
    real.parent.mkdir()
    real.touch()
    link = tmp_path / "bin" / "wingman-host-status"
    link.parent.mkdir()
    link.symlink_to(real)

    assert host_status._runnable_path(str(real), which=lambda _n: str(link)) == str(link)


def test_runnable_path_falls_back_when_path_names_a_different_program(tmp_path: Path) -> None:
    """A hint that names the WRONG binary is worse than an ugly one."""
    real = tmp_path / "store" / "wingman-host-status"
    real.parent.mkdir()
    real.touch()
    impostor = tmp_path / "elsewhere" / "wingman-host-status"
    impostor.parent.mkdir()
    impostor.touch()

    assert host_status._runnable_path(str(real), which=lambda _n: str(impostor)) == str(real)
    assert host_status._runnable_path(str(real), which=lambda _n: None) == str(real)


def test_render_drops_the_root_hint_when_already_root() -> None:
    """Telling root to rerun as root is noise that reads as a real finding."""
    rows = [
        InstanceRow(
            port=8789,
            name="wingman-shared",
            running=True,
            version="0.4.1.dev117+g16072b689",
            started_at="2026-08-06T03:10:00+00:00",
            build="current",
            service="?",
            note="",
        )
    ]
    assert "rerun as" not in render(rows, now=NOW, repo=None, euid=0)


def test_render_says_so_when_there_is_nothing_to_report() -> None:
    assert render([], now=NOW, repo=None) == "No wingman instances found on this box."


def test_render_shows_a_stopped_instance_as_stopped() -> None:
    rows = [
        InstanceRow(
            port=8788,
            name="trent",
            running=False,
            version=None,
            started_at=None,
            build="-",
            service="inactive",
            note="",
        )
    ]
    output = render(rows, now=NOW, repo=None)
    assert "stopped" in output
    assert "Needs attention" not in output
