from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.oauth_bearer import IdentityMap, bind_trusted_identity

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
