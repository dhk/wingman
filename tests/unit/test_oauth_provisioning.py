from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.oauth_bearer import IdentityMap, bind_trusted_identity

cli = CliRunner()
ISSUER = "https://example.authkit.app"


def _bind_after_start(start: object, path: str, subject: str) -> None:
    start.wait()  # type: ignore[attr-defined]
    bind_trusted_identity(Path(path), ISSUER, subject, subject)


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


def test_concurrent_identity_bindings_do_not_lose_updates(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    # Fork is substantially faster on Linux CI. Python 3.12 warns about it
    # from a multi-threaded macOS parent, so use spawn there instead.
    context = multiprocessing.get_context("spawn" if sys.platform == "darwin" else "fork")
    start = context.Event()
    subjects = [f"user_{number}" for number in range(12)]
    processes = [
        context.Process(target=_bind_after_start, args=(start, str(identities), subject))
        for subject in subjects
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    loaded = IdentityMap.from_toml(identities)
    assert loaded.slugs() == set(subjects)


def test_rebinding_exact_identity_repairs_private_permissions(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text(
        f'[[identity]]\niss = "{ISSUER}"\nsub = "user_123"\nslug = "jason"\n',
        encoding="utf-8",
    )
    identities.chmod(0o644)

    assert bind_trusted_identity(identities, ISSUER, "user_123", "jason") is True
    assert identities.stat().st_mode & 0o777 == 0o600


def test_exact_binding_still_reloads_the_running_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    bind_trusted_identity(identities, ISSUER, "user_123", "jason")
    reloads: list[Path] = []
    monkeypatch.setattr(
        "wingman.infrastructure.tenant_process.signal_reload",
        lambda path: reloads.append(path) or 123,
    )

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
    assert reloads == [registry]
    assert "pid 123" in result.output


def test_identity_map_write_error_is_a_concise_cli_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"
    monkeypatch.setattr(
        "wingman.infrastructure.oauth_bearer.tempfile.mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

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
    assert "could not be updated" in result.output
    assert not isinstance(result.exception, PermissionError)


def test_preflight_rejects_conflict_before_tenant_exists(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    bind_trusted_identity(identities, ISSUER, "user_123", "jason")
    before = identities.read_bytes()

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
            "--preflight",
        ],
    )

    assert result.exit_code == 1
    assert "already bound to 'jason'" in result.output
    assert identities.read_bytes() == before


def test_preflight_checks_temporary_file_and_rename_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    monkeypatch.setattr(
        "wingman.infrastructure.oauth_bearer.tempfile.mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
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
            "--preflight",
        ],
    )

    assert result.exit_code == 1
    assert "could not be updated" in result.output


def test_preflight_checks_write_access_when_permissions_need_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text(
        f'[[identity]]\niss = "{ISSUER}"\nsub = "user_123"\nslug = "taylor"\n',
        encoding="utf-8",
    )
    identities.chmod(0o644)
    monkeypatch.setattr(
        "wingman.infrastructure.oauth_bearer.tempfile.mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
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
            "--preflight",
        ],
    )

    assert result.exit_code == 1
    assert "could not be updated" in result.output


def test_non_bmp_identity_values_round_trip_as_valid_toml(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    subject = "user_😀"
    slug = "career_🛩️"

    bind_trusted_identity(identities, ISSUER, subject, slug)

    assert IdentityMap.from_toml(identities).slug_for(ISSUER, subject) == slug
    assert "😀" in identities.read_text(encoding="utf-8")


def test_reload_permission_error_is_not_reported_as_no_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.tenant_process import TenantProcessSignalError

    registry = _registry(tmp_path, "jason")
    identities = tmp_path / "oauth-identities.toml"

    def denied(_path: Path) -> None:
        raise TenantProcessSignalError("running but may not signal it; on-disk change is written")

    monkeypatch.setattr("wingman.infrastructure.tenant_process.signal_reload", denied)
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
    assert "may not signal it" in result.output
    assert "No running shared process found" not in result.output


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_add_tenant_executes_fresh_oauth_provisioning_and_resumes(tmp_path: Path) -> None:
    script = Path(__file__).parents[2] / "scripts" / "wingman-add-tenant.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "wingman.log"
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    tenant_root = tmp_path / "tenants"
    identities = tmp_path / "oauth-identities.toml"
    _executable(
        bin_dir / "id",
        'if [ "${1:-}" = "-u" ]; then echo 0; else /usr/bin/id "$@"; fi\n',
    )
    _executable(bin_dir / "sudo", 'shift 2\nexec "$@"\n')
    _executable(
        bin_dir / "wingman",
        'printf \'%s\\n\' "$*" >> "$WINGMAN_TEST_LOG"\n'
        'if [ "${1:-}" = "init" ]; then mkdir -p "$WINGMAN_DATA_DIR"; '
        'touch "$WINGMAN_DATA_DIR/wingman.db"; fi\n',
    )
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "WINGMAN_SHARED_USER": "test-user",
            "WINGMAN_TENANT_REGISTRY": str(registry),
            "WINGMAN_SHARED_TENANT_ROOT": str(tenant_root),
            "WINGMAN_TEST_LOG": str(log),
        }
    )
    command = [
        "bash",
        str(script),
        "taylor",
        "--no-telemetry",
        "--oauth-issuer",
        ISSUER,
        "--oauth-subject",
        "user_123",
        "--oauth-identities",
        str(identities),
    ]

    fresh = subprocess.run(command, capture_output=True, text=True, env=environment, check=False)
    resumed = subprocess.run(command, capture_output=True, text=True, env=environment, check=False)

    assert fresh.returncode == 0, fresh.stderr
    assert resumed.returncode == 0, resumed.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls.count("init") == 1
    assert (
        sum(call.startswith("tenant oauth-bind") and "--preflight" in call for call in calls) == 2
    )
    assert (
        sum(call.startswith("tenant oauth-bind") and "--preflight" not in call for call in calls)
        == 2
    )
    assert not any("rotate-token" in call for call in calls)
    assert registry.read_text(encoding="utf-8").count('slug = "taylor"') == 1
    assert "resuming OAuth provisioning" in resumed.stdout
