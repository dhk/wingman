"""Backups: consistent snapshots out, refuse-to-clobber restores back in."""

import io
import tarfile
from pathlib import Path

import pytest

from wingman.application.backup import create_backup, restore_backup
from wingman.application.ingest import IngestError
from wingman.application.people import add_person
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, company="Acme")
    config.models_config_path.write_text('[models.embed_semantic]\nprovider = "hashed"\n')
    (config.inbox_dir / "resume.md").write_text("# Resume\n")
    (config.reports_dir / "pdf").mkdir()
    (config.reports_dir / "pdf" / "jane.md").write_text("# Jane\n")
    return config


def _archive_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as tar:
        return {member.name for member in tar.getmembers() if member.isfile()}


def test_backup_packs_workspace_and_restore_roundtrips(workspace: Config, tmp_path: Path) -> None:
    report = create_backup(workspace, dest=tmp_path / "safe")
    archive = Path(report.path)
    assert archive.exists() and report.size_bytes == archive.stat().st_size
    assert _archive_names(archive) == {
        "wingman/wingman.db",
        "wingman/models.toml",
        "wingman/inbox/resume.md",
        "wingman/reports/pdf/jane.md",
    }
    assert report.files == 4 and report.pruned == []

    # mutate, then restore: the snapshot's state comes back
    with Storage(workspace.db_path) as storage:
        add_person("Bob Later", storage)
    (workspace.inbox_dir / "resume.md").write_text("mutated")
    restore = restore_backup(archive, workspace, force=True)
    assert restore.files == 4
    assert (workspace.inbox_dir / "resume.md").read_text() == "# Resume\n"
    with Storage(workspace.db_path) as storage:
        names = {person.name for person in storage.list_people()}
    assert names == {"Jane Author"}


def test_backup_excludes_backups_and_demo_dirs(workspace: Config, tmp_path: Path) -> None:
    nested = workspace.reports_dir / "demo"
    nested.mkdir()
    (nested / "sample.md").write_text("demo output")
    report = create_backup(workspace, dest=tmp_path / "safe")
    names = _archive_names(Path(report.path))
    assert not any("demo" in name for name in names)


def test_backup_prunes_to_keep(workspace: Config, tmp_path: Path) -> None:
    dest = tmp_path / "safe"
    kept: list[str] = []
    for _ in range(3):
        # same-second runs get a -N suffix instead of clobbering, so this is safe fast
        kept.append(Path(create_backup(workspace, dest=dest, keep=2).path).name)
    assert len(set(kept)) == 3
    remaining = {p.name for p in dest.glob("wingman-backup-*.tar.gz")}
    assert remaining == set(kept[-2:])
    # keep=0 keeps everything
    create_backup(workspace, dest=dest, keep=0)
    assert len(list(dest.glob("wingman-backup-*.tar.gz"))) == 3


def test_backup_requires_initialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "empty"))
    with pytest.raises(IngestError, match="wingman init"):
        create_backup(load_config())


def test_restore_refuses_live_workspace_without_force(workspace: Config, tmp_path: Path) -> None:
    report = create_backup(workspace, dest=tmp_path / "safe")
    with pytest.raises(IngestError, match="--force"):
        restore_backup(Path(report.path), workspace)


def test_restore_rejects_archives_without_database(workspace: Config, tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-backup.tar.gz"
    with tarfile.open(bogus, "w:gz") as tar:
        data = b"hello"
        info = tarfile.TarInfo("wingman/readme.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(IngestError, match="does not look like a wingman backup"):
        restore_backup(bogus, workspace, force=True)
    with pytest.raises(IngestError, match="Nothing was restored"):
        restore_backup(tmp_path / "missing.tar.gz", workspace, force=True)


def test_restore_rejects_path_traversal_members(workspace: Config, tmp_path: Path) -> None:
    hostile = tmp_path / "hostile.tar.gz"
    with tarfile.open(hostile, "w:gz") as tar:
        data = b"pwned"
        info = tarfile.TarInfo("wingman/../../escape.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(IngestError, match="could not read"):
        restore_backup(hostile, workspace, force=True)
    assert not (tmp_path / "escape.txt").exists()
