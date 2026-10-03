"""Best-effort, content-free accounting wrappers for metered providers (#554)."""

from __future__ import annotations

from time import perf_counter

from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import CapabilityClass, ModelProvider, ModelRequest, ModelResponse
from wingman.providers.embeddings import EmbeddingProvider, InputType

_logger = get_logger("model_usage")


def _record(config: Config, usage: ModelUsage) -> None:
    try:
        with Storage(config.db_path) as storage:
            storage.add_model_usage(usage)
    except Exception:  # noqa: BLE001 — accounting must never alter the paid call's result
        _logger.exception(
            "model usage recording failed provider=%s model=%s capability=%s; call result preserved",
            usage.provider,
            usage.model,
            usage.capability,
        )


class UsageTrackingProvider:
    def __init__(
        self,
        inner: ModelProvider,
        config: Config,
        capability: CapabilityClass,
        payer: Payer,
        caller_name: str | None = None,
    ) -> None:
        self._inner = inner
        self._config = config
        self._capability = capability
        self._payer = payer
        self._caller_name = caller_name

    def __getattr__(self, name: str) -> object:
        """Keep adapter attributes available to diagnostics and compatibility tests."""
        return getattr(self._inner, name)

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._inner.complete(request)
        _record(
            self._config,
            ModelUsage(
                capability=self._capability.value,
                provider=response.provider,
                model=response.model,
                payer=self._payer,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cache_read_tokens=response.cache_read_tokens,
                cache_write_tokens=response.cache_write_tokens,
                search_result_count=response.search_result_count,
                latency_ms=response.latency_ms,
                caller_name=self._caller_name,
            ),
        )
        return response


class UsageTrackingEmbeddingProvider:
    def __init__(
        self,
        inner: EmbeddingProvider,
        config: Config,
        payer: Payer,
        caller_name: str | None = None,
    ) -> None:
        self._inner = inner
        self._config = config
        self._payer = payer
        self._caller_name = caller_name
        self.provider_name = inner.provider_name
        self.model = inner.model

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        started = perf_counter()
        vectors = self._inner.embed(texts, input_type)
        _record(
            self._config,
            ModelUsage(
                capability="embed_semantic",
                provider=self.provider_name,
                model=self.model,
                payer=self._payer,
                latency_ms=int((perf_counter() - started) * 1000),
                caller_name=self._caller_name,
            ),
        )
        return vectors
