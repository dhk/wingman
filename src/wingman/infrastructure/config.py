"""Configuration loading: where the Wingman workspace lives on disk."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import BaseModel

APP_NAME = "wingman"
ENV_DATA_DIR = "WINGMAN_DATA_DIR"


class Config(BaseModel):
    """Resolved workspace configuration."""

    data_dir: Path
    data_dir_source: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wingman.db"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def models_config_path(self) -> Path:
        return self.data_dir / "models.toml"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Resolve the data directory: WINGMAN_DATA_DIR if set, else the platform user data dir.

    Never defaults to a path relative to the invoking directory — an installed
    CLI must not scatter data wherever it happens to be run.
    """
    environment = os.environ if env is None else env
    override = environment.get(ENV_DATA_DIR, "").strip()
    if override:
        return Config(
            data_dir=Path(override).expanduser(),
            data_dir_source=f"{ENV_DATA_DIR} environment variable",
        )
    return Config(
        data_dir=Path(user_data_dir(APP_NAME)),
        data_dir_source="platform user data directory",
    )
