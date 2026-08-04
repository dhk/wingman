"""RFC-046: the split canonical host layout, and the one-time migration off
the old flat '~/.config/keys.env' (#122/RFC-040) that made it necessary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.infrastructure.host_config import (
    HostEnvironmentError,
    MigrationResult,
    legacy_host_keys_path,
    migrate_legacy_host_file,
    read_host_settings,
    wingman_env_path,
)
from wingman.infrastructure.keys import host_keys_path, read_host_keys


def _write_legacy(home: Path, content: str) -> Path:
    path = legacy_host_keys_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_migrate_noop_when_no_legacy_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = migrate_legacy_host_file(home)
    assert result == MigrationResult(
        migrated=False, detail="no legacy host file (~/.config/keys.env) to migrate"
    )
    assert not wingman_env_path(home).exists()
    assert not host_keys_path(home).exists()


def test_migrate_splits_secrets_from_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_legacy(
        home,
        "ANTHROPIC_API_KEY=sk-ant-real\n"
        "VOYAGE_API_KEY=pa-real\n"
        "WINGMAN_REPO=/home/dhk/src/wingman\n",
    )

    result = migrate_legacy_host_file(home)

    assert result.migrated is True
    assert result.secret_names == ["ANTHROPIC_API_KEY", "VOYAGE_API_KEY"]
    assert result.setting_names == ["WINGMAN_REPO"]
    assert result.legacy_backup is not None
    assert result.legacy_backup.name.startswith("keys.env.migrated-")

    # the secrets went to secrets.env, exactly as the existing ladder reads it
    assert read_host_keys(home) == {
        "ANTHROPIC_API_KEY": "sk-ant-real",
        "VOYAGE_API_KEY": "pa-real",
    }
    # WINGMAN_REPO went to wingman.env, and is readable there
    assert read_host_settings(home) == {"WINGMAN_REPO": "/home/dhk/src/wingman"}
    # nothing else leaked across files
    assert "WINGMAN_REPO" not in host_keys_path(home).read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in wingman_env_path(home).read_text(encoding="utf-8")


def test_migrate_secrets_only_no_settings_line(tmp_path: Path) -> None:
    """The common real-world case today: no WINGMAN_REPO line at all yet."""
    home = tmp_path / "home"
    _write_legacy(home, "ANTHROPIC_API_KEY=sk-ant-real\nVOYAGE_API_KEY=pa-real\n")

    result = migrate_legacy_host_file(home)

    assert result.migrated is True
    assert result.secret_names == ["ANTHROPIC_API_KEY", "VOYAGE_API_KEY"]
    assert result.setting_names == []
    assert read_host_keys(home) == {
        "ANTHROPIC_API_KEY": "sk-ant-real",
        "VOYAGE_API_KEY": "pa-real",
    }


def test_migrate_preserves_unrecognized_lines_and_comments(tmp_path: Path) -> None:
    """RFC-040's file also doubled as a plain systemd EnvironmentFile, so it
    may carry lines wingman's own Python ladder never parsed (e.g.
    WINGMAN_ALLOWED_HOSTS, per docs/SERVER.md §8) — losing one during
    migration would silently break whatever depended on it in production.
    """
    home = tmp_path / "home"
    _write_legacy(
        home,
        "# lobster host config\n"
        "ANTHROPIC_API_KEY=sk-ant-real\n"
        "WINGMAN_ALLOWED_HOSTS=a.example,b.example\n"
        "WINGMAN_TUNNEL_PORT=8443\n",
    )

    result = migrate_legacy_host_file(home)

    assert result.secret_names == ["ANTHROPIC_API_KEY"]
    assert result.setting_names == ["WINGMAN_ALLOWED_HOSTS", "WINGMAN_TUNNEL_PORT"]
    settings_text = wingman_env_path(home).read_text(encoding="utf-8")
    assert "# lobster host config" in settings_text
    assert "WINGMAN_ALLOWED_HOSTS=a.example,b.example" in settings_text
    assert "WINGMAN_TUNNEL_PORT=8443" in settings_text
    # unrecognized names are preserved in the file but not surfaced by the
    # strict recognized-name reader (mirrors KNOWN_KEYS's "ignored, not an
    # error" behavior for secrets.env)
    assert read_host_settings(home) == {}


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_legacy(home, "ANTHROPIC_API_KEY=sk-ant-real\nWINGMAN_REPO=/home/dhk/src/wingman\n")

    first = migrate_legacy_host_file(home)
    assert first.migrated is True
    secrets_before = host_keys_path(home).read_text(encoding="utf-8")
    settings_before = wingman_env_path(home).read_text(encoding="utf-8")

    second = migrate_legacy_host_file(home)
    assert second.migrated is False
    assert second.detail == "already on the new host-config layout"
    # re-running must not touch either new file
    assert host_keys_path(home).read_text(encoding="utf-8") == secrets_before
    assert wingman_env_path(home).read_text(encoding="utf-8") == settings_before
    # and must not resurrect or re-move the (already renamed) legacy file
    assert not legacy_host_keys_path(home).exists()


def test_migrate_never_deletes_the_legacy_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    legacy = _write_legacy(home, "ANTHROPIC_API_KEY=sk-ant-real\n")

    result = migrate_legacy_host_file(home)

    assert not legacy.exists()  # renamed, not left in place...
    assert result.legacy_backup is not None
    assert result.legacy_backup.exists()  # ...and never lost
    assert result.legacy_backup.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=sk-ant-real\n"


def test_migrate_never_overwrites_a_manually_created_new_layout(tmp_path: Path) -> None:
    """If a human already created either new file by hand, this must never
    clobber it — even though the legacy file is still sitting right there."""
    home = tmp_path / "home"
    _write_legacy(home, "ANTHROPIC_API_KEY=sk-ant-legacy\n")
    wingman_env_path(home).parent.mkdir(parents=True, exist_ok=True)
    wingman_env_path(home).write_text("WINGMAN_REPO=/hand/edited/path\n", encoding="utf-8")

    result = migrate_legacy_host_file(home)

    assert result.migrated is False
    assert wingman_env_path(home).read_text(encoding="utf-8") == "WINGMAN_REPO=/hand/edited/path\n"
    assert legacy_host_keys_path(home).exists()  # left exactly where it was
    assert not host_keys_path(home).exists()  # never fabricated from the legacy file


def test_migrate_file_permissions_are_0600(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_legacy(home, "ANTHROPIC_API_KEY=sk-ant-real\nWINGMAN_REPO=/x/wingman\n")

    migrate_legacy_host_file(home)

    assert (host_keys_path(home).stat().st_mode & 0o777) == 0o600
    assert (wingman_env_path(home).stat().st_mode & 0o777) == 0o600


def test_migrate_ignores_blank_and_malformed_lines(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_legacy(
        home,
        "\n   \nANTHROPIC_API_KEY=sk-ant-real\nnot-a-valid-line-at-all\n=missing-name\n",
    )

    result = migrate_legacy_host_file(home)

    assert result.secret_names == ["ANTHROPIC_API_KEY"]
    # a genuinely garbled line is preserved verbatim rather than dropped —
    # a human reviewing the migrated wingman.env can see and fix it
    settings_text = wingman_env_path(home).read_text(encoding="utf-8")
    assert "not-a-valid-line-at-all" in settings_text


def test_read_host_settings_ignores_unrecognized_names(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("WINGMAN_REPO=/x/wingman\nSOME_OTHER_THING=ignored\n", encoding="utf-8")

    assert read_host_settings(home) == {"WINGMAN_REPO": "/x/wingman"}


def test_read_host_settings_is_quote_aware(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text('WINGMAN_REPO="/path with spaces/wingman"\n', encoding="utf-8")

    assert read_host_settings(home) == {"WINGMAN_REPO": "/path with spaces/wingman"}


def test_read_host_settings_raises_with_file_and_line_on_bad_quoting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("WINGMAN_REPO=/ok/path\nWINGMAN_REPO=unterminated 'quote\n", encoding="utf-8")

    with pytest.raises(HostEnvironmentError, match=r"wingman\.env:2"):
        read_host_settings(home)


def test_read_host_settings_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_host_settings(tmp_path / "home") == {}


def test_operator_name_reads_the_setting(tmp_path: Path) -> None:
    from wingman.infrastructure.host_config import operator_name

    home = tmp_path / "home"
    assert operator_name(home) is None

    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("WINGMAN_OPERATOR_NAME=Trent\n", encoding="utf-8")
    assert operator_name(home) == "Trent"


def test_read_host_settings_recognizes_operator_name_alongside_repo(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("WINGMAN_REPO=/x/wingman\nWINGMAN_OPERATOR_NAME=Dave\n", encoding="utf-8")
    assert read_host_settings(home) == {
        "WINGMAN_REPO": "/x/wingman",
        "WINGMAN_OPERATOR_NAME": "Dave",
    }


def test_read_host_settings_recognizes_gdrive_client_id_and_secret(tmp_path: Path) -> None:
    """RFC-053 (#205): the Drive OAuth client identifiers are a host
    SETTING (same value for every account on a box), not a per-account
    KNOWN_KEYS secret — they belong in wingman.env, not secrets.env."""
    home = tmp_path / "home"
    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(
        "WINGMAN_GDRIVE_CLIENT_ID=abc.apps.googleusercontent.com\n"
        "WINGMAN_GDRIVE_CLIENT_SECRET=shh\n",
        encoding="utf-8",
    )
    assert read_host_settings(home) == {
        "WINGMAN_GDRIVE_CLIENT_ID": "abc.apps.googleusercontent.com",
        "WINGMAN_GDRIVE_CLIENT_SECRET": "shh",
    }
