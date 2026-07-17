"""Embedding provider resolution and failure modes (RFC-010)."""

from pathlib import Path

import pytest

from wingman.infrastructure.config import load_config
from wingman.providers.embeddings import EmbeddingError, VoyageEmbeddingProvider
from wingman.providers.router import (
    DEFAULT_MODELS_TOML,
    ModelConfigError,
    get_embedding_provider,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    return tmp_path


def test_default_models_toml_resolves_voyage(workspace: Path) -> None:
    config = load_config()
    config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    provider = get_embedding_provider(config)
    assert provider.provider_name == "voyage"
    assert provider.model == "voyage-4"


def test_pre_rfc010_workspace_defaults_to_voyage(workspace: Path) -> None:
    config = load_config()
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider = "anthropic"\nmodel = "claude-haiku-4-5"\n',
        encoding="utf-8",
    )
    provider = get_embedding_provider(config)
    assert provider.provider_name == "voyage"
    assert provider.model == "voyage-4"


def test_hashed_provider_selection_and_unknown_provider(workspace: Path) -> None:
    config = load_config()
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    assert get_embedding_provider(config).provider_name == "hashed"
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "mystery"\n', encoding="utf-8"
    )
    with pytest.raises(ModelConfigError, match="mystery"):
        get_embedding_provider(config)


def test_voyage_without_key_fails_with_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    provider = VoyageEmbeddingProvider(model="voyage-4")
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        provider.embed(["text"], input_type="document")
