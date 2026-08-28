"""'wingman tenant overnight' — RFC-048's overnight-loop gap, closed:
runs RFC-018's deep-refresh once per tenant, each with that tenant's own
strict Config, one tenant's failure never blocking another."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wingman.application.focus import OvernightReport, follow_company
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
    assert "0/1 tenants completed" in result.output


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
    assert "0/2 tenants completed" in result.output


def test_tenant_missing_workspace_is_skipped_not_crashed(tmp_path: Path) -> None:
    """A registry entry pointing at a data_dir with no wingman.db yet
    (e.g. added before 'wingman init' ran) must be reported, not crash."""
    missing_dir = tmp_path / "ghost"
    registry = _write_registry(tmp_path, ("ghost", missing_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "ghost: no workspace yet" in result.output


def _corrupt_tenant(tmp_path: Path, slug: str) -> Path:
    """A workspace whose database is unreadable — the failure the docstring
    always named and the code never actually caught (#387)."""
    data_dir = _make_tenant(tmp_path, slug, f"tok-{slug}")
    (data_dir / "wingman.db").write_bytes(b"this is not a database, it is a pile of bytes" * 64)
    return data_dir


def test_a_corrupt_workspace_does_not_abort_the_tenants_after_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The bug this command's own docstring promised was fixed (#387): only
    IngestError was caught, so a corrupt SQLite file — sqlite3.DatabaseError —
    escaped the loop and silently cost EVERY tenant after it in registry
    order their entire night's work. Nobody finds that out, because the thing
    that would have told them is the run that never happened."""
    import wingman.application.research as research_module

    monkeypatch.setattr(research_module, "fetch_url", lambda url: _CAREERS_PAGE)

    broken_dir = _corrupt_tenant(tmp_path, "broken")
    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    with Storage(jason_dir / "wingman.db") as storage:
        add_person("Jane Author", storage, company="Acme")
        follow_company(
            "Acme", storage, url="https://acme.example.com", fetcher=lambda url: _CAREERS_PAGE
        )

    # broken is FIRST, so an escaping exception takes jason down with it.
    registry = _write_registry(tmp_path, ("broken", broken_dir), ("jason", jason_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])

    assert "broken: failed" in result.output
    assert "jason: 1 targets" in result.output
    assert (jason_dir / "reports" / "digests").exists()
    assert "1/2 tenants completed" in result.output
    assert result.exit_code == 1


def test_a_failing_tenant_is_named_with_the_reason_never_swallowed(tmp_path: Path) -> None:
    """This runs unattended on other people's behalf. A tenant who got
    nothing has to be nameable in the morning, with why."""
    broken_dir = _corrupt_tenant(tmp_path, "broken")
    registry = _write_registry(tmp_path, ("broken", broken_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])
    assert "broken: failed" in result.output
    # The exception's own type and message, not a generic 'something failed'.
    assert "DatabaseError" in result.output or "not a database" in result.output


def test_a_bad_registry_entry_fails_only_its_own_tenant(tmp_path: Path, monkeypatch) -> None:
    """Tenant.config() was outside the guarded region, so a registry entry
    that fails to resolve took the whole roster with it."""
    import wingman.infrastructure.tenants as tenants_module

    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    original = tenants_module.Tenant.config

    def explode(self):  # noqa: ANN001, ANN202 — test double
        if self.slug == "cursed":
            raise RuntimeError("this tenant's config cannot be resolved")
        return original(self)

    monkeypatch.setattr(tenants_module.Tenant, "config", explode)

    registry = _write_registry(tmp_path, ("cursed", tmp_path / "cursed"), ("jason", jason_dir))
    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])

    assert "cursed: failed" in result.output
    assert "jason: failed" in result.output  # reached at all — nothing enrolled
    assert "0/2 tenants completed" in result.output


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
    assert "1/1 tenants completed" in result.output
    assert (jason_dir / "reports" / "digests").exists()


def _partly_failed_run(failing_dir: Path):  # noqa: ANN202 — test double factory
    """Every tenant's run completes; the one at failing_dir has bad targets."""

    def run(config, storage, out_dir=None):  # noqa: ANN001, ANN202 — test double
        digest = config.data_dir / "digest-2026-08-05.md"
        digest.write_text("# digest", encoding="utf-8")
        return OvernightReport(
            targets=[],
            processed=3,
            failed=2 if config.data_dir == failing_dir else 0,
            actions=[],
            digest_path=str(digest),
        )

    return run


def test_a_tenants_failed_targets_do_not_fail_the_roster(tmp_path: Path, monkeypatch) -> None:
    """wingman-8kj: a tenant whose run completed with some bad targets got
    their night's work and a digest naming what is thin. Only a tenant that
    got NOTHING is this command's failure — which is what the docstring above
    has always claimed, and what the exit status used to contradict. The same
    conflation put the single-workspace unit into 'failed' on 2026-08-05."""
    import wingman.application.focus as focus_module

    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    bob_dir = _make_tenant(tmp_path, "bob", "tok-bob")
    registry = _write_registry(tmp_path, ("jason", jason_dir), ("bob", bob_dir))
    monkeypatch.setattr(focus_module, "overnight_run", _partly_failed_run(jason_dir))

    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry)])

    assert result.exit_code == 0, result.output
    assert "2/2 tenants completed" in result.output
    # Still named, still counted: succeeding quietly is the opposite error.
    assert "jason: 3 targets, 2 with failures" in result.output
    assert "1 of them had failed targets" in result.output


def test_strict_makes_failed_targets_fail_the_roster(tmp_path: Path, monkeypatch) -> None:
    """--strict restores the old status for callers that want it, matching the
    flag on 'wingman overnight'."""
    import wingman.application.focus as focus_module

    jason_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    bob_dir = _make_tenant(tmp_path, "bob", "tok-bob")
    registry = _write_registry(tmp_path, ("jason", jason_dir), ("bob", bob_dir))
    monkeypatch.setattr(focus_module, "overnight_run", _partly_failed_run(jason_dir))

    result = cli.invoke(app, ["tenant", "overnight", "--registry", str(registry), "--strict"])

    assert result.exit_code == 1, result.output
    assert "2/2 tenants completed" in result.output
