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


def test_unit_state_reads_your_own_account_directly() -> None:
    run = _runner({("systemctl",): (3, "failed\n")})
    assert unit_state("dhk", self_user="dhk", run=run, euid=1000) == "failed"
    assert run.calls[0] == [  # type: ignore[attr-defined]
        "systemctl",
        "--user",
        "is-active",
        "wingman-mcp.service",
    ]


def test_unit_state_for_another_account_is_unknown_without_root() -> None:
    run = _runner({("systemctl",): (0, "active\n")})
    assert unit_state("trent", self_user="dhk", run=run, euid=1000) is None
    assert run.calls == []  # type: ignore[attr-defined]


def test_unit_state_for_another_account_carries_xdg_runtime_dir_as_root(monkeypatch) -> None:
    """Without XDG_RUNTIME_DIR the call cannot reach that account's session
    bus at all — the gotcha upgrade_all.py carries on every sudo call."""
    import pwd as pwd_module

    monkeypatch.setattr(
        host_status.pwd,
        "getpwnam",
        lambda name: pwd_module.struct_passwd(
            (name, "x", 1001, 1001, "", "/home/" + name, "/bin/bash")
        ),
    )
    run = _runner({("sudo",): (0, "active\n")})
    assert unit_state("trent", self_user="dhk", run=run, euid=0) == "active"
    assert run.calls[0][:5] == [  # type: ignore[attr-defined]
        "sudo",
        "-u",
        "trent",
        "env",
        "XDG_RUNTIME_DIR=/run/user/1001",
    ]


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


def test_collect_flags_the_unmanaged_instance_and_leaves_the_others_alone(tmp_path: Path) -> None:
    """The whole point, end to end: 8787 listening but its unit failed,
    8788 healthy under systemd, 8789 healthy but a build behind."""
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
        self_user="dhk",
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
    rows = collect(
        [8797], {}, self_user="dhk", repo=tmp_path, health=_health(payloads), run=_runner({})
    )
    assert rows == []


def test_collect_keeps_a_configured_instance_that_answers_nothing(tmp_path: Path) -> None:
    """A registered instance that vanished is a finding, not an absence."""
    rows = collect(
        [8788],
        {8788: "trent"},
        self_user="dhk",
        repo=tmp_path,
        health=_health({}),
        run=_runner({}),
        euid=1000,
    )
    assert len(rows) == 1
    assert rows[0].running is False
    assert rows[0].name == "trent"


def test_collect_ignores_an_unconfigured_port_that_answers_nothing(tmp_path: Path) -> None:
    rows = collect([8790], {}, self_user="dhk", repo=tmp_path, health=_health({}), run=_runner({}))
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
    output = render(rows, now=NOW, repo=Path("/home/dhk/src/wingman"))

    assert "PORT" in output.splitlines()[0]
    assert "19:28 (1h ago)" in output
    assert "Needs attention:" in output
    assert "8787: unit is 'failed'" in output
    assert "rerun as root" in output
    assert "dirty working tree" in output


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
