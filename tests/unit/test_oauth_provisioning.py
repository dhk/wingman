from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.oauth_bearer import IdentityMap

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
