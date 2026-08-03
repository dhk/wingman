"""The shared multi-tenant process's pidfile + SIGHUP reload (RFC-048, #210)."""

from __future__ import annotations

import os
from pathlib import Path

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
    registry.write_text(
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\n', encoding="utf-8"
    )
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
