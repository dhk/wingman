from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from wingman.application.model_usage import price_usage, render_usage
from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import CapabilityClass, ModelRequest, ModelResponse, ProviderError
from wingman.providers.usage import UsageTrackingProvider


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
