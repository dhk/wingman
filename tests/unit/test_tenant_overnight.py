"""'wingman tenant overnight' — RFC-048's overnight-loop gap, closed:
runs RFC-018's deep-refresh once per tenant, each with that tenant's own
strict Config, one tenant's failure never blocking another."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wingman.application.focus import follow_company
from wingman.application.people import add_person
from wingman.cli.main import app
from wingman.infrastructure.storage import Storage

cli = CliRunner()

_CAREERS_PAGE = b'<html><body><a href="/jobs/researcher">Researcher</a></body></html>'


def _make_tenant(tmp_path: Path, slug: str, token: str) -> Path:
    data_dir = tmp_path / slug
    for directory in (data_dir, data_dir / "inbox", data_dir / "reports"):
        directory.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    data_dir.joinpath("models.toml").write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    return data_dir


def _write_registry(tmp_path: Path, *entries: tuple[str, Path]) -> Path:
    registry = tmp_path / "tenants.toml"
    lines = []
    for slug, data_dir in entries:
        lines += ["[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""]
    registry.write_text("\n".join(lines), encoding="utf-8")
    return registry


def test_no_tenants_is_a_clean_noop(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path)
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert result.exit_code == 0
    assert "no tenants" in result.output


def test_malformed_registry_exits_nonzero(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text("not [ valid toml", encoding="utf-8")
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "malformed" in result.output


def test_tenant_with_nothing_enrolled_is_reported_not_fatal(tmp_path: Path) -> None:
    """A fresh tenant workspace (nothing followed yet) must be reported
    as a per-tenant failure, never crash the whole loop."""
    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", jason_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert result.exit_code == 1  # the one tenant failed
    assert "jason: failed" in result.output
    assert "0/1 tenants completed cleanly" in result.output


def test_one_tenants_failure_never_blocks_another(tmp_path: Path) -> None:
    """The core isolation property: jason has nothing enrolled (fails),
    bob has a real workspace too (also nothing enrolled, also fails
    independently) — both must be attempted and reported, neither
    skipped because of the other."""
    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    bob_dir = _make_tenant(tmp_path, "bob", "tok-bob")
    registry = _write_registry(tmp_path, ("jason", jason_dir), ("bob", bob_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert "jason: failed" in result.output
    assert "bob: failed" in result.output
    assert "0/2 tenants completed cleanly" in result.output


def test_tenant_missing_workspace_is_skipped_not_crashed(tmp_path: Path) -> None:
    """A registry entry pointing at a data_dir with no wingman.db yet
    (e.g. added before 'wingman init' ran) must be reported, not crash."""
    missing_dir = tmp_path / "ghost"
    registry = _write_registry(tmp_path, ("ghost", missing_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "ghost: no workspace yet" in result.output


def test_tenant_with_enrolled_target_produces_a_real_digest(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: a tenant with a real followed company gets a real
    overnight run through the exact same overnight_run() the
    single-workspace 'wingman overnight' command calls."""
    import wingman.application.research as research_module

    monkeypatch.setattr(research_module, "fetch_url", lambda url: _CAREERS_PAGE)

    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", jason_dir))

    with Storage(jason_dir / "wingman.db") as storage:
        add_person("Jane Author", storage, company="Acme")
        follow_company(
            "Acme", storage, url="https://acme.example.com", fetcher=lambda url: _CAREERS_PAGE
        )

    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert result.exit_code == 0
    assert "jason: 1 targets" in result.output
    assert "1/1 tenants completed cleanly" in result.output
    assert (jason_dir / "reports" / "digests").exists()
