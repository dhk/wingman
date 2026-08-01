from pathlib import Path

import pytest

from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.keys import store_workspace_key
from wingman.providers.anthropic_provider import AnthropicProvider
from wingman.providers.base import CapabilityClass, ModelRequest, ProviderError
from wingman.providers.recorded import RecordedProvider
from wingman.providers.router import DEFAULT_MODELS_TOML, ModelConfigError, get_provider


def test_recorded_provider_replays_text() -> None:
    response = RecordedProvider("hello").complete(ModelRequest(system="s", prompt="p"))
    assert response.text == "hello"
    assert response.provider == "recorded"


def test_anthropic_provider_fails_loud_with_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ANTHROPIC_API_KEY must raise a clear ProviderError at
    construction time, not surface later as the SDK's bare TypeError from
    request header validation (overnight P1)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider("claude-x")


def test_anthropic_provider_uses_explicit_api_key_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC-048: an explicit key lets a caller resolve credentials itself
    instead of relying on process-wide environment state."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    provider = AnthropicProvider("claude-x", api_key="sk-ant-explicit")
    assert provider._client.api_key == "sk-ant-explicit"


def test_router_passes_explicit_config_key_to_anthropic_provider(tmp_path: Path) -> None:
    """RFC-048: Config.anthropic_api_key, when set, wins over env/workspace
    resolution — the mechanism a per-tenant caller would use."""
    config = Config(
        data_dir=tmp_path, data_dir_source="test", anthropic_api_key="sk-ant-from-config"
    )
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider = "anthropic"\nmodel = "claude-x"\n', encoding="utf-8"
    )
    provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
    assert provider._client.api_key == "sk-ant-from-config"


def test_router_falls_back_to_workspace_key_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an explicit Config key, the router still resolves the
    workspace 'keys.env' file (RFC-034) — unchanged single-tenant behavior,
    now read fresh instead of relying on a prior os.environ mutation."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    store_workspace_key(tmp_path, "anthropic", "sk-ant-workspace")
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider = "anthropic"\nmodel = "claude-x"\n', encoding="utf-8"
    )
    provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
    assert provider._client.api_key == "sk-ant-workspace"


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
