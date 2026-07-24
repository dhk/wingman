"""Cross-account port-owner detection (#137, #138)."""

from wingman.infrastructure.portcheck import PortOwner, find_port_owner


def _runner(responses: dict[tuple[str, ...], tuple[int, str]]):
    def run(argv: list[str]) -> tuple[int, str]:
        for prefix, result in responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return 1, ""

    return run


def test_ss_listener_found_and_identity_resolved(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    run = _runner(
        {
            ("ss",): (
                0,
                'LISTEN 0 4096 0.0.0.0:8787 0.0.0.0:* users:(("wingman-mcp",pid=4242,fd=7))\n',
            ),
            ("ps",): (0, "dave wingman-mcp --http --port 8787\n"),
        }
    )
    owner = find_port_owner(8787, run=run)
    assert owner == PortOwner(pid=4242, command="wingman-mcp --http --port 8787", user="dave")
    assert owner.readable_identity
    assert "4242" in owner.describe()


def test_nothing_listening_returns_none(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    run = _runner({("ss",): (0, "")})
    assert find_port_owner(8787, run=run) is None


def test_falls_back_to_lsof_when_ss_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None if name == "ss" else f"/usr/bin/{name}")
    run = _runner(
        {
            ("lsof",): (
                0,
                "COMMAND   PID USER   FD   TYPE\nwingman-  555 trent   7u  IPv4\n",
            ),
            ("ps",): (0, "trent wingman-mcp --http --port 8788\n"),
        }
    )
    owner = find_port_owner(8788, run=run)
    assert owner is not None
    assert owner.pid == 555
    assert owner.user == "trent"


def test_cross_account_owner_with_unreadable_command(monkeypatch) -> None:
    """The exact #138 scenario: a foreign account's process holds the port,
    and 'ps' can't see into it — still reported as *present*, not silently
    dropped."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    run = _runner(
        {
            ("ss",): (0, 'LISTEN 0 4096 *:8788 *:* users:(("wingman-mcp",pid=999,fd=7))\n'),
            ("ps",): (1, ""),  # permission denied / hidepid — command unreadable
        }
    )
    owner = find_port_owner(8788, run=run)
    assert owner is not None
    assert owner.pid == 999
    assert not owner.readable_identity
    assert "different Unix account" in owner.describe()


def test_never_calls_lsof_without_dns_suppressing_flags(monkeypatch) -> None:
    """Guards against reintroducing the incident: any lsof invocation here
    must always carry -n (and -P), the flags whose absence caused the hang."""
    monkeypatch.setattr("shutil.which", lambda name: None if name == "ss" else f"/usr/bin/{name}")
    seen: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        seen.append(argv)
        return 1, ""

    find_port_owner(8787, run=run)
    lsof_calls = [argv for argv in seen if argv[0] == "lsof"]
    assert lsof_calls, "expected a lsof fallback call"
    for argv in lsof_calls:
        assert "-n" in argv
        assert "-P" in argv
