"""Capability-class routing: workspace configuration maps classes to providers (RFC-004)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from wingman.infrastructure.config import Config
from wingman.infrastructure.keys import KNOWN_KEYS, declared_shared_key, resolve_provider_key
from wingman.infrastructure.model_health import ModelRejection, current_rejection, health_path
from wingman.providers.anthropic_provider import AnthropicProvider
from wingman.providers.base import CapabilityClass, ModelProvider
from wingman.providers.embeddings import (
    EmbeddingProvider,
    HashedEmbeddingProvider,
    VoyageEmbeddingProvider,
)
from wingman.providers.openrouter_provider import OpenRouterProvider
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

# Open-web research (#222's person deep-dive, #350's company deep-dive) — the
# only capability class whose provider reaches the open web rather than staying inside
# approved/named sources (RFC-015) or user-dropped items (the heap,
# #113). provider = "openrouter" calls OpenRouter's web-search plugin
# (OPENROUTER_API_KEY required); nothing else in wingman calls this
# class — only an explicit deep-dive action does.
[models.research_websearch]
provider = "openrouter"
model = "anthropic/claude-sonnet-5"
"""

DEFAULT_EMBEDDING = ("voyage", "voyage-4")


class ModelConfigError(Exception):
    """The workspace model configuration is missing or invalid."""


def metered_key(
    config: Config,
    provider: str,
    home: Path | None = None,
    global_path: Path | None = None,
) -> str | None:
    """The api_key for one METERED provider, under this workspace's policy.

    BYOK always wins: a workspace that supplied its own key spends its own
    money, and nothing below is consulted. After that the two policies
    differ in what a missing key falls back to.

    Not strict (a solo install, CLI, stdio): the historical ladder, ambient
    environment included. That environment IS the operator's own, so there
    is nothing to protect them from.

    Strict (a registry tenant on the shared process, RFC-048) and NOT
    funded: nothing. This is the refusal the flag exists for — an unfunded
    tenant silently billing the operator is exactly the failure mode.

    Strict and FUNDED (#514): the operator's DECLARED file tiers, and still
    never the ambient environment. The operator said, per tenant and in a
    root-owned file, that they pay for this person; reading the files they
    actually wrote honours that without reopening the hole RFC-048 closed,
    where whatever the account launching a shared process happened to
    export leaked into every tenant. Same distinction, and the same
    'declared_shared_key', as the shared issues key (#506).

    'home'/'global_path' are injectable exactly as keys.read_host_keys
    and keys.read_global_keys are, so a test can point at a fixture
    instead of silently resolving — and live-testing — the real
    operator's credential.
    """
    declared: str | None = getattr(config, f"{provider}_api_key")
    if declared is not None:
        return declared
    env_var = KNOWN_KEYS[provider]
    if not config.strict_provider_keys:
        return resolve_provider_key(env_var, config.data_dir)
    if config.funded:
        return declared_shared_key(env_var, home, global_path)
    return None


def model_rejection(config: Config) -> ModelRejection | None:
    """Has the provider refused this workspace's key, and does that still stand (#528)?

    The other half of "can this workspace call a model": 'metered_key'
    says whether there is a key to spend, this says whether the provider
    last accepted it. 'status' and 'completeness' ask both through here and
    'metered_key', so the two surfaces can never disagree. Reads a file;
    never calls the provider.
    """
    return current_rejection(
        health_path(config.data_dir), "anthropic", metered_key(config, "anthropic")
    )


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
        api_key = metered_key(config, "anthropic")
        return AnthropicProvider(
            model=model,
            api_key=api_key,
            strict=config.strict_provider_keys,
            health_path=health_path(config.data_dir),
        )
    if provider == "openrouter":
        model = entry.get("model")
        if not isinstance(model, str) or not model:
            raise ModelConfigError(f"[models.{capability.value}] needs a 'model' name in {path}.")
        api_key = metered_key(config, "openrouter")
        return OpenRouterProvider(model=model, api_key=api_key, strict=config.strict_provider_keys)
    if provider == "recorded":
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ModelConfigError(f"[models.{capability.value}] needs a 'path' in {path}.")
        return RecordedProvider.from_file(Path(raw_path))
    raise ModelConfigError(
        f"[models.{capability.value}] in {path} names unknown provider {provider!r}; "
        "supported providers are 'anthropic', 'openrouter', and 'recorded'."
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
    voyage_api_key = metered_key(config, "voyage")
    if entry is None:
        return VoyageEmbeddingProvider(
            model=DEFAULT_EMBEDDING[1], api_key=voyage_api_key, strict=config.strict_provider_keys
        )
    if not isinstance(entry, dict):
        raise ModelConfigError(f"[models.embed_semantic] in {path} must be a table.")
    provider_name = entry.get("provider")
    if provider_name == "voyage":
        model = entry.get("model")
        if not isinstance(model, str) or not model:
            raise ModelConfigError(f"[models.embed_semantic] needs a 'model' name in {path}.")
        return VoyageEmbeddingProvider(
            model=model, api_key=voyage_api_key, strict=config.strict_provider_keys
        )
    if provider_name == "hashed":
        return HashedEmbeddingProvider()
    raise ModelConfigError(
        f"[models.embed_semantic] in {path} names unknown provider {provider_name!r}; "
        "supported providers are 'voyage' and 'hashed'."
    )
