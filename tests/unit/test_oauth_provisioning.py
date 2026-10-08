from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.oauth_bearer import (
    IdentityMap,
    IdentityMapError,
    bind_trusted_identity,
    release_trusted_identity_reservation,
    renew_trusted_identity_reservation,
    reserve_trusted_identity,
)
from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingError,
    OAuthOnboardingStore,
    onboarding_path_for,
)

cli = CliRunner()
ISSUER = "https://example.authkit.app"


def _registry(tmp_path: Path, *slugs: str) -> Path:
    path = tmp_path / "tenants.toml"
    rows: list[str] = []
    for slug in slugs:
        data_dir = tmp_path / slug
        data_dir.mkdir()
        rows.extend(("[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""))
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def test_operator_binds_a_trusted_identity_to_an_existing_tenant(tmp_path: Path) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "jason",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 0, result.output
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_123") == "jason"
    assert identities.stat().st_mode & 0o777 == 0o600
    assert "trusted OAuth identity" in result.output


def test_operator_cannot_bind_one_identity_to_two_tenants(tmp_path: Path) -> None:
    registry = _registry(tmp_path, "jason", "taylor")
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text(
        f'[[identity]]\niss = "{ISSUER}"\nsub = "user_123"\nslug = "jason"\n',
        encoding="utf-8",
    )

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "taylor",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 1
    assert "already bound to 'jason'" in result.output
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_123") == "jason"


def test_operator_cannot_bind_to_an_unknown_tenant(tmp_path: Path) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "nobody",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 1
    assert "No tenant 'nobody'" in result.output
    assert identities.read_text(encoding="utf-8") == ""


def _bind_args(slug: str, subject: str, identities: Path, registry: Path) -> list[str]:
    return [
        "tenant",
        "oauth-bind",
        slug,
        "--issuer",
        ISSUER,
        "--subject",
        subject,
        "--identities",
        str(identities),
        "--registry",
        str(registry),
    ]


def test_binding_an_existing_tenant_clears_only_that_pending_row(tmp_path: Path) -> None:
    # The existing-tenant path (runbook section 3) never goes through
    # oauth-approve, so before this the bound identity stayed listed as
    # "awaiting approval" forever, inviting an operator to approve it into
    # a second, empty workspace.
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    assert store.record_pending(ISSUER, "user_123", now=1.0)
    assert store.record_pending(ISSUER, "someone_else", now=2.0)

    result = cli.invoke(app, _bind_args("jason", "user_123", identities, registry))

    assert result.exit_code == 0, result.output
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_123") == "jason"
    assert [item.subject for item in store.pending()] == ["someone_else"]
    assert "Removed it from the pending approval queue" in result.output


def test_rebinding_an_exact_binding_clears_a_stale_pending_row(tmp_path: Path) -> None:
    # The state a deployment is left in by a bind that predates this fix:
    # bound on disk, still listed as pending. Re-running the bind is the fix.
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    bind_trusted_identity(identities, ISSUER, "user_123", "jason")
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    assert store.record_pending(ISSUER, "user_123", now=1.0)

    result = cli.invoke(app, _bind_args("jason", "user_123", identities, registry))

    assert result.exit_code == 0, result.output
    assert "already existed" in result.output
    assert store.pending() == []


def test_preflight_leaves_the_pending_queue_alone(tmp_path: Path) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    assert store.record_pending(ISSUER, "user_123", now=1.0)

    result = cli.invoke(
        app, [*_bind_args("jason", "user_123", identities, registry), "--preflight"]
    )

    assert result.exit_code == 0, result.output
    assert [item.subject for item in store.pending()] == ["user_123"]


def test_a_stuck_pending_queue_does_not_undo_a_written_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"

    def unwritable(self: OAuthOnboardingStore, issuer: str, subject: str) -> bool:
        raise OAuthOnboardingError("onboarding state could not be replaced")

    monkeypatch.setattr(OAuthOnboardingStore, "discard_pending", unwritable)

    result = cli.invoke(app, _bind_args("jason", "user_123", identities, registry))

    assert result.exit_code == 0, result.output
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_123") == "jason"
    assert "Bound trusted OAuth identity" in result.output
    assert "still listed as pending" in result.output


def test_malformed_identity_map_is_preserved(tmp_path: Path) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    malformed = "[[identity]\n"
    identities.write_text(malformed, encoding="utf-8")

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "jason",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 1
    assert "not valid TOML" in result.output
    assert identities.read_text(encoding="utf-8") == malformed


def test_preflight_checks_a_new_tenant_binding_without_needing_registry_state(
    tmp_path: Path,
) -> None:
    identities = tmp_path / "oauth-identities.toml"

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "not-created-yet",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(tmp_path / "absent-registry.toml"),
            "--preflight",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "available" in result.output
    assert not identities.exists()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[[identity]\n", "not valid TOML"),
        (
            f'[[identity]]\niss = "{ISSUER}"\nsub = "user_123"\nslug = "somebody-else"\n',
            "already bound to 'somebody-else'",
        ),
    ],
)
def test_preflight_refuses_bad_or_unavailable_bindings_without_changing_the_map(
    tmp_path: Path, body: str, message: str
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text(body, encoding="utf-8")

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "new-person",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--preflight",
        ],
    )

    assert result.exit_code == 1
    assert message in result.output
    assert identities.read_text(encoding="utf-8") == body


def test_preflight_reports_an_active_provisioning_reservation(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    reserve_trusted_identity(identities, ISSUER, "user_123", "first")

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "second",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--preflight",
        ],
    )

    assert result.exit_code == 1
    assert "reserved for provisioning tenant 'first'" in result.output


def test_lock_failure_is_reported_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import oauth_bearer

    identities = tmp_path / "oauth-identities.toml"

    def denied(*_args: object, **_kwargs: object) -> int:
        raise PermissionError("operator cannot open the lock")

    monkeypatch.setattr(oauth_bearer.os, "open", denied)
    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "new-person",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--reserve",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be locked safely" in result.output
    assert "Traceback" not in result.output


def test_failed_atomic_replace_preserves_the_previous_identity_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import oauth_bearer

    identities = tmp_path / "oauth-identities.toml"
    original = f'[[identity]]\niss = "{ISSUER}"\nsub = "existing"\nslug = "first"\n'
    identities.write_text(original, encoding="utf-8")

    def full_disk(_source: object, _destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(oauth_bearer.os, "replace", full_disk)
    with pytest.raises(IdentityMapError, match="previous identity map was preserved"):
        bind_trusted_identity(identities, ISSUER, "new-user", "second")

    assert identities.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(f".{identities.name}.*")) == [tmp_path / f".{identities.name}.lock"]


def test_oauth_binding_reports_when_disk_changed_but_live_reload_was_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import tenant_process

    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"

    def denied(_registry: Path) -> int | None:
        raise tenant_process.TenantProcessSignalError("process is running but signal was denied")

    monkeypatch.setattr(tenant_process, "signal_reload", denied)
    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "jason",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 1
    assert "Binding is on disk" in result.output
    assert "running process still uses the previous identity map" in result.output
    assert "No running shared process found" not in result.output
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_123") == "jason"


def test_concurrent_bindings_serialize_the_read_modify_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    first_read = threading.Event()
    release_first = threading.Event()
    second_read = threading.Event()
    read_count = 0
    count_lock = threading.Lock()
    original_read_text = Path.read_text

    def coordinated_read(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal read_count
        content = original_read_text(path, *args, **kwargs)
        if path == identities:
            with count_lock:
                read_count += 1
                current = read_count
            if current == 1:
                first_read.set()
                release_first.wait(timeout=2)
            elif current == 2:
                second_read.set()
        return content

    monkeypatch.setattr(Path, "read_text", coordinated_read)
    executor = ThreadPoolExecutor(max_workers=2)
    first = executor.submit(bind_trusted_identity, identities, ISSUER, "first", "first")
    second = None
    try:
        assert first_read.wait(timeout=2)
        second = executor.submit(bind_trusted_identity, identities, ISSUER, "second", "second")
        assert not second_read.wait(timeout=0.1), (
            "the second operator read the identity map before the first update completed"
        )
    finally:
        release_first.set()
        executor.shutdown(wait=True)
    assert first.result(timeout=2)
    assert second is not None
    assert second.result(timeout=2)
    monkeypatch.setattr(Path, "read_text", original_read_text)
    mapping = IdentityMap.from_toml(identities)
    assert mapping.slug_for(ISSUER, "first") == "first"
    assert mapping.slug_for(ISSUER, "second") == "second"


def test_a_provisioning_reservation_blocks_a_competing_tenant_until_released(
    tmp_path: Path,
) -> None:
    identities = tmp_path / "oauth-identities.toml"

    reservation = reserve_trusted_identity(identities, ISSUER, "user_123", "first")

    with pytest.raises(IdentityMapError, match="reserved.*first"):
        reserve_trusted_identity(identities, ISSUER, "user_123", "second")
    with pytest.raises(IdentityMapError, match="reserved.*first"):
        bind_trusted_identity(identities, ISSUER, "user_123", "second")
    assert not identities.exists()

    assert release_trusted_identity_reservation(identities, ISSUER, "user_123", reservation)
    second = reserve_trusted_identity(identities, ISSUER, "user_123", "second")
    assert bind_trusted_identity(identities, ISSUER, "user_123", "second", reservation=second)
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_123") == "second"


def test_only_the_reservation_owner_can_finish_or_release_provisioning(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    reservation = reserve_trusted_identity(identities, ISSUER, "user_123", "jason")

    with pytest.raises(IdentityMapError, match="reservation token"):
        bind_trusted_identity(identities, ISSUER, "user_123", "jason", reservation="not-the-token")
    assert not release_trusted_identity_reservation(identities, ISSUER, "user_123", "not-the-token")

    assert bind_trusted_identity(identities, ISSUER, "user_123", "jason", reservation=reservation)


def test_a_crashed_provisioning_reservation_expires(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    clock = [time.time()]
    first = reserve_trusted_identity(identities, ISSUER, "user_123", "first", now=lambda: clock[0])
    reservation_files = list(tmp_path.glob(".*.reservation-*.json"))
    assert len(reservation_files) == 1
    assert reservation_files[0].stat().st_mode & 0o777 == 0o600

    clock[0] += 3601.0
    second = reserve_trusted_identity(
        identities, ISSUER, "user_123", "second", now=lambda: clock[0]
    )

    assert second != first
    assert bind_trusted_identity(identities, ISSUER, "user_123", "second", reservation=second)


def test_the_reservation_owner_can_renew_before_registry_mutation(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    clock = [time.time()]
    reservation = reserve_trusted_identity(
        identities, ISSUER, "user_123", "jason", now=lambda: clock[0]
    )
    clock[0] += 3599.0

    assert renew_trusted_identity_reservation(
        identities,
        ISSUER,
        "user_123",
        reservation,
        now=lambda: clock[0],
    )
    clock[0] += 2.0
    with pytest.raises(IdentityMapError, match="reserved.*jason"):
        reserve_trusted_identity(identities, ISSUER, "user_123", "other", now=lambda: clock[0])


def test_existing_binding_still_retries_the_live_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import tenant_process

    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text(
        f'[[identity]]\niss = "{ISSUER}"\nsub = "user_123"\nslug = "jason"\n',
        encoding="utf-8",
    )
    signaled: list[Path] = []

    def signal(registry_path: Path) -> int:
        signaled.append(registry_path)
        return 4321

    monkeypatch.setattr(tenant_process, "signal_reload", signal)
    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-bind",
            "jason",
            "--issuer",
            ISSUER,
            "--subject",
            "user_123",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 0, result.output
    assert signaled == [registry]
    assert "already existed" in result.output
    assert "Signaled the running shared process (pid 4321)" in result.output
