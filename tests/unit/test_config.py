from pathlib import Path

from wingman.infrastructure.config import ENV_DATA_DIR, load_config


def test_env_var_overrides(tmp_path: Path) -> None:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "workspace")})
    assert config.data_dir == tmp_path / "workspace"
    assert ENV_DATA_DIR in config.data_dir_source


def test_blank_env_var_is_ignored() -> None:
    config = load_config(env={ENV_DATA_DIR: "  "})
    assert config.data_dir_source == "platform user data directory"


def test_platform_default_is_absolute_and_not_cwd_relative() -> None:
    config = load_config(env={})
    assert config.data_dir.is_absolute()
    assert config.data_dir_source == "platform user data directory"
    assert "wingman" in str(config.data_dir).lower()


def test_workspace_paths_derive_from_data_dir(tmp_path: Path) -> None:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    assert config.db_path == tmp_path / "wingman.db"
    assert config.inbox_dir == tmp_path / "inbox"
    assert config.reports_dir == tmp_path / "reports"
