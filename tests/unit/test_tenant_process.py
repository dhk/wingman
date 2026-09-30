"""The shared multi-tenant process's pidfile + SIGHUP reload (RFC-048, #210)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wingman.infrastructure.oauth_bearer import IdentityMap
from wingman.infrastructure.tenant_process import (
    clear_tenant_pidfile,
    read_tenant_process_pid,
    register_reload_handler,
    signal_reload,
    tenant_pidfile_path,
    write_tenant_pidfile,
)
from wingman.infrastructure.tenants import Tenant, TenantIndex


def _fake_command_of(alive: dict[int, str]):
    return lambda pid: alive.get(pid, "")


def test_pidfile_lives_under_tempdir_not_alongside_the_registry(tmp_path: Path) -> None:
    """The registry conventionally lives under a root-owned, non-group-
    writable directory (RFC-047's /etc/wingman/) — the account actually
    running the shared process usually cannot write there at all, even
    as a 'wingman' group member (read+traverse only, never write)."""
    import tempfile

    registry = tmp_path / "tenants.toml"
    path = tenant_pidfile_path(registry)
    assert path.parent == Path(tempfile.gettempdir())
    assert path != tmp_path / "tenants.toml.pid"


def test_pidfile_path_is_a_pure_function_of_the_resolved_registry_path(tmp_path: Path) -> None:
    """Same registry, spelled two different ways (plain vs. through a
    '.' segment), must resolve to the same pidfile — the whole point of
    hashing the RESOLVED path rather than the raw string."""
    registry = tmp_path / "tenants.toml"
    spelled_differently = tmp_path / "." / "tenants.toml"
    assert tenant_pidfile_path(registry) == tenant_pidfile_path(spelled_differently)


def test_different_registries_get_different_pidfiles(tmp_path: Path) -> None:
    a = tmp_path / "a" / "tenants.toml"
    b = tmp_path / "b" / "tenants.toml"
    assert tenant_pidfile_path(a) != tenant_pidfile_path(b)


def test_write_read_clear_roundtrip(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    write_tenant_pidfile(registry)
    command_of = _fake_command_of({os.getpid(): "wingman-mcp --http --tenant-registry x"})
    assert read_tenant_process_pid(registry, command_of=command_of) == os.getpid()
    clear_tenant_pidfile(registry)
    assert read_tenant_process_pid(registry, command_of=command_of) is None


def test_stale_pidfile_is_removed_on_sight(tmp_path: Path) -> None:
    """A pid that's dead, or alive but not a wingman-mcp process, must
    never be trusted — mirrors mcp_process.read_server_pid's rigor."""
    registry = tmp_path / "tenants.toml"
    path = tenant_pidfile_path(registry)
    path.write_text("999999\n", encoding="utf-8")  # almost certainly not a live pid
    assert read_tenant_process_pid(registry) is None
    assert not path.exists()  # cleaned up


def test_stale_pidfile_removed_when_pid_is_alive_but_not_wingman(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    write_tenant_pidfile(registry)  # records our own real, alive pid
    command_of = _fake_command_of({os.getpid(): "some other unrelated process"})
    assert read_tenant_process_pid(registry, command_of=command_of) is None
    assert not tenant_pidfile_path(registry).exists()


def test_signal_reload_returns_none_when_nothing_is_running(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    assert signal_reload(registry, command_of=lambda pid: "") is None


def test_signal_reload_sends_sighup_to_the_verified_pid(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    write_tenant_pidfile(registry)
    command_of = _fake_command_of({os.getpid(): "wingman-mcp --http --tenant-registry x"})
    received: list[int] = []

    def handler(signum: int, frame: object) -> None:
        received.append(signum)

    import signal

    old = signal.signal(signal.SIGHUP, handler)
    try:
        signaled_pid = signal_reload(registry, command_of=command_of)
        assert signaled_pid == os.getpid()
        assert received == [signal.SIGHUP]
    finally:
        signal.signal(signal.SIGHUP, old)
        clear_tenant_pidfile(registry)


def test_register_reload_handler_reloads_the_index_on_sighup(tmp_path: Path) -> None:
    import signal

    jason_dir = tmp_path / "jason"
    jason_dir.mkdir()
    (jason_dir / "mcp-http-token").write_text("tok-old", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n', encoding="utf-8")
    index = TenantIndex.from_registry_path(registry)
    assert index.resolve("tok-old") is not None

    old_handler = signal.signal(signal.SIGHUP, signal.SIG_DFL)
    try:
        register_reload_handler(index, registry)
        (jason_dir / "mcp-http-token").write_text("tok-new", encoding="utf-8")
        os.kill(os.getpid(), signal.SIGHUP)
        assert index.resolve("tok-old") is None
        assert index.resolve("tok-new") is not None
    finally:
        signal.signal(signal.SIGHUP, old_handler)


def test_sighup_reloads_registry_and_oauth_map_together(tmp_path: Path) -> None:
    import signal

    jason_dir = tmp_path / "jason"
    jason_dir.mkdir()
    (jason_dir / "mcp-http-token").write_text("tok-old", encoding="utf-8")
    taylor_dir = tmp_path / "taylor"
    taylor_dir.mkdir()
    (taylor_dir / "mcp-http-token").write_text("tok-taylor", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n', encoding="utf-8")
    identity_path = tmp_path / "identities.toml"
    identity_path.write_text(
        '[[identity]]\niss = "https://issuer.example"\nsub = "old"\nslug = "jason"\n',
        encoding="utf-8",
    )
    index = TenantIndex.from_registry_path(registry)
    identities = IdentityMap.from_toml(identity_path)
    old_handler = signal.signal(signal.SIGHUP, signal.SIG_DFL)
    try:
        register_reload_handler(index, registry, identities, identity_path)
        registry.write_text(
            f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n\n'
            f'[[tenant]]\nslug = "taylor"\ndata_dir = "{taylor_dir}"\n',
            encoding="utf-8",
        )
        (jason_dir / "mcp-http-token").write_text("tok-new", encoding="utf-8")
        identity_path.write_text(
            '[[identity]]\niss = "https://issuer.example"\nsub = "new"\nslug = "jason"\n\n'
            '[[identity]]\niss = "https://issuer.example"\nsub = "taylor"\nslug = "taylor"\n',
            encoding="utf-8",
        )
        os.kill(os.getpid(), signal.SIGHUP)
        assert index.resolve("tok-old") is None
        assert index.resolve("tok-new").slug == "jason"  # type: ignore[union-attr]
        assert index.resolve("tok-taylor").slug == "taylor"  # type: ignore[union-attr]
        assert identities.slug_for("https://issuer.example", "old") is None
        assert identities.slug_for("https://issuer.example", "new") == "jason"
        assert identities.slug_for("https://issuer.example", "taylor") == "taylor"
    finally:
        signal.signal(signal.SIGHUP, old_handler)


def test_failed_oauth_reload_keeps_both_previous_snapshots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import signal

    jason_dir = tmp_path / "jason"
    jason_dir.mkdir()
    (jason_dir / "mcp-http-token").write_text("tok-old", encoding="utf-8")
    taylor_dir = tmp_path / "taylor"
    taylor_dir.mkdir()
    (taylor_dir / "mcp-http-token").write_text("tok-taylor", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n', encoding="utf-8")
    identity_path = tmp_path / "identities.toml"
    identity_path.write_text(
        '[[identity]]\niss = "https://issuer.example"\nsub = "old"\nslug = "jason"\n',
        encoding="utf-8",
    )
    index = TenantIndex.from_registry_path(registry)
    identities = IdentityMap.from_toml(identity_path)
    old_handler = signal.signal(signal.SIGHUP, signal.SIG_DFL)
    try:
        register_reload_handler(index, registry, identities, identity_path)
        registry.write_text(
            f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n\n'
            f'[[tenant]]\nslug = "taylor"\ndata_dir = "{taylor_dir}"\n',
            encoding="utf-8",
        )
        (jason_dir / "mcp-http-token").write_text("tok-new", encoding="utf-8")
        identity_path.write_text("[[identity]\n", encoding="utf-8")
        os.kill(os.getpid(), signal.SIGHUP)
        assert index.resolve("tok-old").slug == "jason"  # type: ignore[union-attr]
        assert index.resolve("tok-new") is None
        assert index.by_slug("taylor") is None
        assert identities.slug_for("https://issuer.example", "old") == "jason"
        assert len(identities) == 1
        assert "retaining the previous tenant registry" in caplog.text
        assert "and OAuth identity map" in caplog.text
        assert str(registry) in caplog.text
        assert str(identity_path) in caplog.text
    finally:
        signal.signal(signal.SIGHUP, old_handler)


def test_tenant_index_still_isolated_after_reload(tmp_path: Path) -> None:
    """Sanity check that reload doesn't accidentally merge tenants."""
    jason = Tenant(slug="jason", data_dir=tmp_path / "jason")
    (tmp_path / "jason").mkdir()
    (tmp_path / "jason" / "mcp-http-token").write_text("tok-jason", encoding="utf-8")
    index = TenantIndex([jason])
    assert index.resolve("tok-jason") is not None
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{tmp_path / "jason"}"\n', encoding="utf-8"
    )
    index.reload(registry)
    assert index.resolve("tok-jason").slug == "jason"  # type: ignore[union-attr]


def test_sighup_reloads_the_oauth_identity_map_too(tmp_path: Path) -> None:
    import signal

    data_dir = tmp_path / "jason"
    data_dir.mkdir()
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "jason"\ndata_dir = "{data_dir}"\n', encoding="utf-8")
    identity_path = tmp_path / "oauth-identities.toml"
    identity_path.write_text("", encoding="utf-8")
    index = TenantIndex.from_registry_path(registry)
    identities = IdentityMap.from_toml(identity_path)

    old_handler = signal.signal(signal.SIGHUP, signal.SIG_DFL)
    try:
        register_reload_handler(index, registry, identities, identity_path)
        identity_path.write_text(
            '[[identity]]\niss = "https://issuer.example"\nsub = "user_123"\nslug = "jason"\n',
            encoding="utf-8",
        )
        os.kill(os.getpid(), signal.SIGHUP)
        assert identities.slug_for("https://issuer.example", "user_123") == "jason"
    finally:
        signal.signal(signal.SIGHUP, old_handler)


def test_a_malformed_registry_does_not_take_the_shared_process_down(
    tmp_path: Path,
) -> None:
    """#328 — the reload runs inside a signal handler, so an exception there
    propagates into whatever the main thread was doing and kills a process
    serving every tenant.

    The trigger is ordinary operator work: one typo while adding somebody,
    then any 'tenant rotate-token', which sends the SIGHUP by design.
    """
    import signal

    jason_dir = tmp_path / "jason"
    jason_dir.mkdir()
    (jason_dir / "mcp-http-token").write_text("tok-jason", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n', encoding="utf-8")
    index = TenantIndex.from_registry_path(registry)
    assert index.resolve("tok-jason") is not None

    # Somebody adds a tenant and fats-fingers the key.
    registry.write_text(
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n\n'
        '[[tenant]]\nslugg = "taylor"\ndata_dir = "/tmp/taylor"\n',
        encoding="utf-8",
    )

    old = signal.signal(signal.SIGHUP, signal.SIG_DFL)
    try:
        register_reload_handler(index, registry)
        os.kill(os.getpid(), signal.SIGHUP)  # would have raised out of the handler
    finally:
        signal.signal(signal.SIGHUP, old)

    # Still serving, from the registry that was already loaded.
    assert index.resolve("tok-jason") is not None


def test_a_process_that_exits_before_the_signal_is_simply_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#329 — os.kill was unguarded, so a pid that vanished between the read
    and the signal escaped as a traceback out of 'tenant rotate-token', after
    the new token had already been written."""
    registry = tmp_path / "tenants.toml"
    write_tenant_pidfile(registry)
    command_of = _fake_command_of({os.getpid(): "wingman-mcp --http --tenant-registry x"})

    import signal as signal_module

    # Only the SIGHUP raises. _alive() probes with os.kill(pid, 0), so a
    # blanket fake makes the liveness check fail and signal_reload returns
    # None before ever reaching the signal — passing for the wrong reason.
    real_kill = os.kill
    attempted: list[int] = []

    def vanished(pid: int, sig: int) -> None:
        if sig == signal_module.SIGHUP:
            attempted.append(sig)
            raise ProcessLookupError
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", vanished)
    try:
        assert signal_reload(registry, command_of=command_of) is None
        assert attempted == [signal_module.SIGHUP], "the signal was never attempted"
    finally:
        clear_tenant_pidfile(registry)


def test_a_process_we_may_not_signal_says_so_instead_of_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different situation from 'not found', and the operator's next step
    differs: the process is alive and healthy, this account just may not
    signal it. Flattening it into "no process" sends them hunting for
    something that is running fine."""
    from wingman.infrastructure.tenant_process import TenantProcessSignalError

    registry = tmp_path / "tenants.toml"
    write_tenant_pidfile(registry)
    command_of = _fake_command_of({os.getpid(): "wingman-mcp --http --tenant-registry x"})

    import signal as signal_module

    real_kill = os.kill

    def not_permitted(pid: int, sig: int) -> None:
        if sig == signal_module.SIGHUP:
            raise PermissionError
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", not_permitted)
    try:
        with pytest.raises(TenantProcessSignalError, match="may not signal it"):
            signal_reload(registry, command_of=command_of)
    finally:
        clear_tenant_pidfile(registry)
