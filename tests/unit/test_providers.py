from pathlib import Path

import pytest

from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.providers.base import CapabilityClass, ModelRequest
from wingman.providers.recorded import RecordedProvider
from wingman.providers.router import DEFAULT_MODELS_TOML, ModelConfigError, get_provider


def test_recorded_provider_replays_text() -> None:
    response = RecordedProvider("hello").complete(ModelRequest(system="s", prompt="p"))
    assert response.text == "hello"
    assert response.provider == "recorded"


def test_router_requires_config_file(tmp_path: Path) -> None:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    with pytest.raises(ModelConfigError, match="wingman init"):
        get_provider(CapabilityClass.EXTRACT_FAST, config)


def test_router_builds_recorded_provider(tmp_path: Path) -> None:
    recorded = tmp_path / "response.json"
    recorded.write_text('{"items": []}', encoding="utf-8")
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    config.models_config_path.write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{recorded}"\n',
        encoding="utf-8",
    )
    provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
    assert provider.complete(ModelRequest(system="s", prompt="p")).text == '{"items": []}'


def test_router_rejects_unknown_provider(tmp_path: Path) -> None:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider = "carrier-pigeon"\n', encoding="utf-8"
    )
    with pytest.raises(ModelConfigError, match="unknown provider"):
        get_provider(CapabilityClass.EXTRACT_FAST, config)


def test_default_config_covers_every_capability_class() -> None:
    for capability in CapabilityClass:
        assert f"[models.{capability.value}]" in DEFAULT_MODELS_TOML
