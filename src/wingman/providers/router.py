"""Capability-class routing: workspace configuration maps classes to providers (RFC-004)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from wingman.infrastructure.config import Config
from wingman.providers.anthropic_provider import AnthropicProvider
from wingman.providers.base import CapabilityClass, ModelProvider
from wingman.providers.embeddings import (
    EmbeddingProvider,
    HashedEmbeddingProvider,
    VoyageEmbeddingProvider,
)
from wingman.providers.recorded import RecordedProvider

DEFAULT_MODELS_TOML = """\
# Maps capability classes (docs/RFC.md, RFC-004) to concrete providers and models.
# Edit freely; model names belong here, never in code.
# provider = "anthropic" calls the Anthropic API (ANTHROPIC_API_KEY required).
# provider = "recorded" replays a stored response: add path = "/path/to/response.json".

[models.extract_fast]
provider = "anthropic"
model = "claude-haiku-4-5"

[models.synthesize_balanced]
provider = "anthropic"
model = "claude-sonnet-5"

[models.reason_frontier]
provider = "anthropic"
model = "claude-opus-4-8"

[models.critic_independent]
provider = "anthropic"
model = "claude-sonnet-5"

# Semantic similarity (RFC-010). provider = "voyage" calls the Voyage AI
# embeddings API (VOYAGE_API_KEY required); provider = "hashed" is a local,
# network-free keyword-level fallback.
[models.embed_semantic]
provider = "voyage"
model = "voyage-4"
"""

DEFAULT_EMBEDDING = ("voyage", "voyage-4")


class ModelConfigError(Exception):
    """The workspace model configuration is missing or invalid."""


def get_provider(capability: CapabilityClass, config: Config) -> ModelProvider:
    path = config.models_config_path
    if not path.exists():
        raise ModelConfigError(
            f"Model configuration {path} does not exist. Run 'wingman init' to create it."
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModelConfigError(f"Model configuration {path} could not be parsed: {exc}") from exc
    entry = data.get("models", {}).get(capability.value)
    if not isinstance(entry, dict):
        raise ModelConfigError(
            f"Model configuration {path} has no [models.{capability.value}] section."
        )
    provider = entry.get("provider")
    if provider == "anthropic":
        model = entry.get("model")
        if not isinstance(model, str) or not model:
            raise ModelConfigError(f"[models.{capability.value}] needs a 'model' name in {path}.")
        return AnthropicProvider(model=model)
    if provider == "recorded":
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ModelConfigError(f"[models.{capability.value}] needs a 'path' in {path}.")
        return RecordedProvider.from_file(Path(raw_path))
    raise ModelConfigError(
        f"[models.{capability.value}] in {path} names unknown provider {provider!r}; "
        "supported providers are 'anthropic' and 'recorded'."
    )


def get_embedding_provider(config: Config) -> EmbeddingProvider:
    """Resolve [models.embed_semantic]; workspaces created before RFC-010 get the default."""
    path = config.models_config_path
    if not path.exists():
        raise ModelConfigError(
            f"Model configuration {path} does not exist. Run 'wingman init' to create it."
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModelConfigError(f"Model configuration {path} could not be parsed: {exc}") from exc
    entry = data.get("models", {}).get("embed_semantic")
    if entry is None:
        return VoyageEmbeddingProvider(model=DEFAULT_EMBEDDING[1])
    if not isinstance(entry, dict):
        raise ModelConfigError(f"[models.embed_semantic] in {path} must be a table.")
    provider_name = entry.get("provider")
    if provider_name == "voyage":
        model = entry.get("model")
        if not isinstance(model, str) or not model:
            raise ModelConfigError(f"[models.embed_semantic] needs a 'model' name in {path}.")
        return VoyageEmbeddingProvider(model=model)
    if provider_name == "hashed":
        return HashedEmbeddingProvider()
    raise ModelConfigError(
        f"[models.embed_semantic] in {path} names unknown provider {provider_name!r}; "
        "supported providers are 'voyage' and 'hashed'."
    )
