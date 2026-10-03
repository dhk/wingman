from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from wingman.application.model_usage import price_usage, render_tenant_usage, render_usage
from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant
from wingman.providers import router
from wingman.providers.base import CapabilityClass, ModelRequest, ModelResponse, ProviderError
from wingman.providers.embeddings import EmbeddingError
from wingman.providers.router import get_provider
from wingman.providers.usage import UsageTrackingEmbeddingProvider, UsageTrackingProvider


class _SuccessfulProvider:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text="private response that must not be stored",
            provider="test-provider",
            model="test-model",
            input_tokens=12,
            output_tokens=7,
            cache_read_tokens=3,
            cache_write_tokens=2,
            search_result_count=4,
            latency_ms=19,
        )


class _FailingProvider:
    def complete(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError("provider failed")


class _RoutedProvider:
    def __init__(
        self,
        model: str,
        api_key: str | None,
        strict: bool,
        health_path: Path | None = None,
    ) -> None:
        assert api_key == "resolved-key"
        self.model = model

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text="ok",
            provider="anthropic",
            model=self.model,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
        )


class _SuccessfulEmbeddingProvider:
    provider_name = "test-embedding"
    model = "embed-model"

    def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        return [[1.0, 0.0] for _text in texts]


class _FailingEmbeddingProvider(_SuccessfulEmbeddingProvider):
    def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        raise EmbeddingError("embedding failed")


def _config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, data_dir_source="test", privileged=False)


def test_successful_call_records_one_content_free_usage_row(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = UsageTrackingProvider(
        _SuccessfulProvider(), config, CapabilityClass.SYNTHESIZE_BALANCED, Payer.FUNDED
    )

    response = provider.complete(ModelRequest(system="secret system", prompt="secret prompt"))

    assert response.text.startswith("private response")
    with Storage(config.db_path) as storage:
        rows = storage.list_model_usage()
    assert len(rows) == 1
    row = rows[0]
    assert row.payer == Payer.FUNDED
    assert row.input_tokens == 12
    assert row.cache_read_tokens == 3
    assert row.search_result_count == 4
    assert "secret" not in config.db_path.read_bytes().decode("utf-8", errors="ignore")


def test_failed_provider_call_records_no_row(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = UsageTrackingProvider(
        _FailingProvider(), config, CapabilityClass.EXTRACT_FAST, Payer.BYOK
    )

    with pytest.raises(ProviderError):
        provider.complete(ModelRequest(system="system", prompt="prompt"))

    with Storage(config.db_path) as storage:
        assert storage.list_model_usage() == []


def test_recording_failure_never_changes_provider_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    provider = UsageTrackingProvider(
        _SuccessfulProvider(), config, CapabilityClass.EXTRACT_FAST, Payer.AMBIENT
    )
    monkeypatch.setattr(Storage, "add_model_usage", lambda *args: (_ for _ in ()).throw(OSError()))

    response = provider.complete(ModelRequest(system="system", prompt="prompt"))

    assert response.text.startswith("private response")


def test_successful_embedding_records_one_row_and_preserves_vectors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = UsageTrackingEmbeddingProvider(_SuccessfulEmbeddingProvider(), config, Payer.AMBIENT)

    vectors = provider.embed(["one", "two"], "document")

    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    with Storage(config.db_path) as storage:
        rows = storage.list_model_usage()
    assert len(rows) == 1
    assert rows[0].capability == "embed_semantic"
    assert rows[0].provider == "test-embedding"


def test_failed_embedding_records_no_row(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = UsageTrackingEmbeddingProvider(_FailingEmbeddingProvider(), config, Payer.BYOK)

    with pytest.raises(EmbeddingError):
        provider.embed(["one"], "document")

    with Storage(config.db_path) as storage:
        assert storage.list_model_usage() == []


def test_embedding_recording_failure_preserves_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    provider = UsageTrackingEmbeddingProvider(_SuccessfulEmbeddingProvider(), config, Payer.AMBIENT)
    monkeypatch.setattr(Storage, "add_model_usage", lambda *args: (_ for _ in ()).throw(OSError()))

    assert provider.embed(["one"], "query") == [[1.0, 0.0]]


def test_prices_are_applied_at_read_time_and_missing_prices_are_not_zero(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.models_config_path.write_text(
        """[models.extract_fast]
provider = "test-provider"
model = "test-model"
input_usd_per_million = 1.0
output_usd_per_million = 2.0
""",
        encoding="utf-8",
    )
    priced = ModelUsage(
        capability="extract_fast",
        provider="test-provider",
        model="test-model",
        payer=Payer.BYOK,
        input_tokens=1_000_000,
        output_tokens=500_000,
        latency_ms=1,
    )
    unpriced = ModelUsage(
        capability="synthesize_balanced",
        provider="test-provider",
        model="other",
        payer=Payer.FUNDED,
        input_tokens=1,
        latency_ms=1,
    )

    rows = price_usage(config, [priced, unpriced])

    assert rows[0].cost_usd == Decimal("2.00")
    assert rows[1].cost_usd is None
    assert "not counted as zero-cost" in render_usage(config, [priced, unpriced])


def test_historical_row_is_unpriced_after_provider_or_model_changes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.models_config_path.write_text(
        """[models.extract_fast]
provider = "new-provider"
model = "new-model"
input_usd_per_million = 99.0
""",
        encoding="utf-8",
    )
    historical = ModelUsage(
        capability="extract_fast",
        provider="old-provider",
        model="old-model",
        payer=Payer.BYOK,
        input_tokens=1_000_000,
        latency_ms=1,
    )

    assert price_usage(config, [historical])[0].cost_usd is None


def test_zero_usage_counters_do_not_require_prices(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.models_config_path.write_text(
        """[models.extract_fast]
provider = "test-provider"
model = "test-model"
input_usd_per_million = 1.0
output_usd_per_million = 2.0
""",
        encoding="utf-8",
    )
    row = ModelUsage(
        capability="extract_fast",
        provider="test-provider",
        model="test-model",
        payer=Payer.BYOK,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        search_result_count=0,
        latency_ms=1,
    )

    assert price_usage(config, [row])[0].cost_usd == Decimal("1.0")


def test_tenant_usage_read_does_not_create_a_missing_database(tmp_path: Path) -> None:
    tenant = Tenant(slug="missing", data_dir=tmp_path / "missing")

    report, failed = render_tenant_usage(tenant, 100)

    assert failed
    assert "no workspace yet" in report
    assert not (tenant.data_dir / "wingman.db").exists()


def test_tenant_usage_read_reports_a_corrupt_database(tmp_path: Path) -> None:
    tenant = Tenant(slug="broken", data_dir=tmp_path / "broken")
    tenant.data_dir.mkdir()
    tenant.data_dir.joinpath("wingman.db").write_text("not sqlite", encoding="utf-8")

    report, failed = render_tenant_usage(tenant, 100)

    assert failed
    assert "Tenant broken: could not be read" in report


@pytest.mark.parametrize(
    ("config_changes", "credential_source", "expected_payer"),
    [
        ({"anthropic_api_key": "resolved-key"}, "byok", Payer.BYOK),
        ({"strict_provider_keys": False}, "ambient", Payer.AMBIENT),
        (
            {"strict_provider_keys": True, "funded": True},
            "funded",
            Payer.FUNDED,
        ),
    ],
)
def test_router_records_resolved_payer_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_changes: dict[str, object],
    credential_source: str,
    expected_payer: Payer,
) -> None:
    config = _config(tmp_path).model_copy(update=config_changes)
    config.models_config_path.write_text(
        """[models.extract_fast]
provider = "anthropic"
model = "test-model"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(router, "AnthropicProvider", _RoutedProvider)
    if credential_source == "ambient":
        monkeypatch.setattr(router, "resolve_provider_key", lambda *_args: "resolved-key")
    elif credential_source == "funded":
        monkeypatch.setattr(router, "declared_shared_key", lambda *_args: "resolved-key")

    provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
    provider.complete(ModelRequest(system="system", prompt="prompt"))

    with Storage(config.db_path) as storage:
        rows = storage.list_model_usage()
    assert len(rows) == 1
    assert rows[0].payer == expected_payer
