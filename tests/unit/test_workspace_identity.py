"""The identity a tenant workspace carries inside its own database (#554 groundwork)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import (
    TenantIndex,
    TenantRegistryError,
    load_registry,
)
from wingman.infrastructure.workspace_identity import read_identity, stamp_identity


def _initialised_db(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / "wingman.db"
    with Storage(db):
        pass
    return db


def test_stamp_writes_one_row_and_reads_back(tmp_path: Path) -> None:
    db = _initialised_db(tmp_path / "jason")
    assert stamp_identity(db, "jason", "jason@example.com") is True
    identity = read_identity(db)
    assert identity is not None
    assert (identity.slug, identity.email) == ("jason", "jason@example.com")
    assert identity.stamped_at


def test_stamp_is_a_noop_when_nothing_changed(tmp_path: Path) -> None:
    """A reload must not rewrite the file: the row's own timestamp is the
    evidence of when the identity last actually changed."""
    db = _initialised_db(tmp_path / "jason")
    assert stamp_identity(db, "jason", "jason@example.com") is True
    first = read_identity(db)
    assert stamp_identity(db, "jason", "jason@example.com") is False
    assert read_identity(db) == first


def test_stamp_follows_the_registry_when_the_email_changes(tmp_path: Path) -> None:
    db = _initialised_db(tmp_path / "jason")
    stamp_identity(db, "jason", "old@example.com")
    assert stamp_identity(db, "jason", "new@example.com") is True
    identity = read_identity(db)
    assert identity is not None and identity.email == "new@example.com"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_identity").fetchone()[0] == 1


def test_stamp_accepts_a_tenant_with_no_email(tmp_path: Path) -> None:
    db = _initialised_db(tmp_path / "trent")
    assert stamp_identity(db, "trent", None) is True
    identity = read_identity(db)
    assert identity is not None and identity.email is None


def test_stamp_does_not_create_a_database_that_does_not_exist(tmp_path: Path) -> None:
    """A never-initialised workspace has no schema; a bare file written here
    would later look populated."""
    db = tmp_path / "ghost" / "wingman.db"
    assert stamp_identity(db, "ghost", "g@example.com") is False
    assert not db.exists()
    assert not db.parent.exists()


def test_read_identity_is_read_only_and_tolerates_absence(tmp_path: Path) -> None:
    assert read_identity(tmp_path / "nope.db") is None
    db = _initialised_db(tmp_path / "jason")
    assert read_identity(db) is None
    with sqlite3.connect(db) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "workspace_identity" not in tables, "reading must not create the table"


def test_stamp_failure_never_raises(tmp_path: Path) -> None:
    corrupt = tmp_path / "wingman.db"
    corrupt.write_bytes(b"this is not a sqlite database" * 50)
    assert stamp_identity(corrupt, "jason", "j@example.com") is False


def test_registry_load_stamps_each_initialised_tenant_and_skips_the_rest(tmp_path: Path) -> None:
    jason_dir = tmp_path / "jason"
    _initialised_db(jason_dir)
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\nemail = "  Jason@Example.COM "\n\n'
        f'[[tenant]]\nslug = "bob"\ndata_dir = "{tmp_path / "bob"}"\nemail = "bob@example.com"\n',
        encoding="utf-8",
    )
    index = TenantIndex.from_registry_path(registry)
    assert len(index) == 2
    identity = read_identity(jason_dir / "wingman.db")
    assert identity is not None
    assert (identity.slug, identity.email) == ("jason", "jason@example.com")
    assert not (tmp_path / "bob").exists(), "a tenant with no database yet is left alone"


def test_registry_reload_picks_up_an_edited_email(tmp_path: Path) -> None:
    jason_dir = tmp_path / "jason"
    db = _initialised_db(jason_dir)
    registry = tmp_path / "tenants.toml"

    def write(email: str) -> None:
        registry.write_text(
            f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason_dir}"\nemail = "{email}"\n',
            encoding="utf-8",
        )

    write("old@example.com")
    index = TenantIndex.from_registry_path(registry)
    write("new@example.com")
    index.reload(registry)
    identity = read_identity(db)
    assert identity is not None and identity.email == "new@example.com"


def test_tenant_config_carries_the_email(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{tmp_path / "jason"}"\n'
        'email = "jason@example.com"\n\n'
        f'[[tenant]]\nslug = "trent"\ndata_dir = "{tmp_path / "trent"}"\n',
        encoding="utf-8",
    )
    by_slug = {tenant.slug: tenant for tenant in load_registry(registry)}
    assert by_slug["jason"].config().owner_email == "jason@example.com"
    assert by_slug["trent"].email is None
    assert by_slug["trent"].config().owner_email is None


@pytest.mark.parametrize("bad", ['"nobody"', '"@example.com"', '"a@"', '"a b@example.com"', "42"])
def test_registry_rejects_a_malformed_email(tmp_path: Path, bad: str) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{tmp_path / "jason"}"\nemail = {bad}\n',
        encoding="utf-8",
    )
    with pytest.raises(TenantRegistryError, match="malformed 'email'"):
        load_registry(registry)


def test_registry_refuses_a_box_wide_email_default(tmp_path: Path) -> None:
    """One address stamped into every workspace is wrong for all but one of
    them, so it is refused at load rather than silently applied or ignored."""
    registry = tmp_path / "tenants.toml"
    entry = f'[[tenant]]\nslug = "jason"\ndata_dir = "{tmp_path / "jason"}"\n'
    for header in ('[defaults]\nemail = "x@example.com"\n\n', 'email = "x@example.com"\n\n'):
        registry.write_text(header + entry, encoding="utf-8")
        with pytest.raises(TenantRegistryError, match="names one person"):
            load_registry(registry)
