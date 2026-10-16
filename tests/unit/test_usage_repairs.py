"""Synthetic regressions for accounting and report review findings."""

import sqlite3
import time
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from wingman.application.model_usage import render_tenant_usage, render_usage
from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant
from wingman.providers.base import CapabilityClass, ModelRequest
from wingman.providers.usage import UsageTrackingProvider


def row(**kwargs):
    return ModelUsage(
        capability="extract_fast",
        provider="anthropic",
        model="dated-model",
        payer=Payer.FUNDED,
        latency_ms=1,
        **kwargs,
    )


def test_rollup_never_migrates_an_existing_database(tmp_path):
    tenant = Tenant(slug="sample", data_dir=tmp_path)
    with sqlite3.connect(tmp_path / "wingman.db") as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
    before = (tmp_path / "wingman.db").read_bytes()
    report, failed = render_tenant_usage(tenant, 100)
    assert not failed
    assert "ledger not yet created" in report
    assert (tmp_path / "wingman.db").read_bytes() == before


def test_report_shows_units_dates_and_unknowns(tmp_path):
    config = Config(data_dir=tmp_path, data_dir_source="test")
    result = render_usage(
        config, [row(input_tokens=12, output_tokens=3), row(input_tokens=None, output_tokens=4)]
    )
    assert "input_tokens=unknown (known subtotal 12)" in result
    assert "output_tokens=7" in result
    assert datetime.now(UTC).date().isoformat() in result
    assert "lower bound" in result
    assert "counter not reported" in result


def test_explicit_pricing_model_mapping_and_diagnostic(tmp_path):
    from wingman.application.model_usage import price_usage

    config = Config(data_dir=tmp_path, data_dir_source="test")
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider="anthropic"\nmodel="alias"\npricing_models=["dated-model"]\ninput_usd_per_million=1.0\n'
    )
    assert price_usage(config, [row(input_tokens=1000000)])[0].cost_usd == 1
    result = render_usage(config, [row(input_tokens=None)])
    assert "counter not reported" in result
    assert "lacks a required current unit price" not in result


def test_cli_report_discloses_row_limit(tmp_path, monkeypatch):
    from wingman.cli.main import app

    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path))
    with Storage(tmp_path / "wingman.db") as storage:
        for _ in range(3):
            storage.add_model_usage(row(input_tokens=1))
    result = CliRunner().invoke(app, ["usage", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "newest 2 of 3" in result.output


def test_contended_recording_returns_promptly_and_preserves_result(tmp_path):
    config = Config(data_dir=tmp_path, data_dir_source="test")
    with Storage(config.db_path):
        pass

    class Provider:
        def complete(self, request):
            from wingman.providers.base import ModelResponse

            return ModelResponse(
                text="synthetic", provider="anthropic", model="dated-model", latency_ms=1
            )

    with sqlite3.connect(config.db_path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        result = UsageTrackingProvider(
            Provider(), config, CapabilityClass.EXTRACT_FAST, Payer.BYOK
        ).complete(ModelRequest(system="", prompt=""))
        elapsed = time.monotonic() - started
    assert result.text == "synthetic"
    assert elapsed < 1.5


def test_unprivileged_usage_rollup_refuses_without_reading_registry(tmp_path, monkeypatch):
    from wingman import mcp_server as mcp
    from wingman.cli.main import app

    config = Config(data_dir=tmp_path, data_dir_source="test", privileged=False)
    monkeypatch.setattr(mcp, "_ready_config", lambda: config)
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path))

    def forbidden(*args):
        raise AssertionError("registry accessed before gate")

    monkeypatch.setattr("wingman.infrastructure.tenants.load_registry", forbidden)
    assert "operator" in mcp.usage_all_tenants().lower()
    monkeypatch.setattr("wingman.cli.main.load_config", lambda: config)
    result = CliRunner().invoke(app, ["usage", "--all-tenants"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, AssertionError)


def test_workspace_file_payer_and_single_snapshot(tmp_path, monkeypatch):
    from wingman.providers import router

    config = Config(data_dir=tmp_path, data_dir_source="test", strict_provider_keys=False)
    (tmp_path / "keys.env").write_text("ANTHROPIC_API_KEY=synthetic-key\n")
    assert router.resolve_metered_credential(config, "anthropic").payer is Payer.BYOK
    (tmp_path / "keys.env").unlink()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient")
    reads = []

    def changing(*args):
        reads.append(1)
        return {} if len(reads) == 1 else {"ANTHROPIC_API_KEY": "synthetic-new-workspace"}

    monkeypatch.setattr(router, "read_workspace_keys", changing)
    monkeypatch.setattr("wingman.infrastructure.keys.read_workspace_keys", changing)
    credential = router.resolve_metered_credential(config, "anthropic")
    assert credential.key == "synthetic-ambient"
    assert len(reads) == 1


def test_strict_unfunded_keyless_call_writes_no_row(tmp_path, monkeypatch):
    from wingman.providers import router
    from wingman.providers.base import ProviderError

    config = Config(
        data_dir=tmp_path, data_dir_source="test", strict_provider_keys=True, funded=False
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient")
    with Storage(config.db_path):
        pass
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider="anthropic"\nmodel="synthetic-model"\n'
    )
    with pytest.raises(ProviderError):
        router.get_provider(CapabilityClass.EXTRACT_FAST, config).complete(
            ModelRequest(system="", prompt="")
        )
    with Storage(config.db_path) as storage:
        assert storage.list_model_usage() == []


@pytest.mark.parametrize(
    "tokens,expected", [(123, 123), (None, None), (True, None), (-1, None), ("123", None)]
)
def test_voyage_response_units_are_recorded_without_changing_vectors(
    tmp_path, monkeypatch, tokens, expected
):
    import io
    import json

    from wingman.providers.embeddings import VoyageEmbeddingProvider
    from wingman.providers.usage import UsageTrackingEmbeddingProvider

    body = {"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"total_tokens": tokens}}
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode())
    )
    config = Config(data_dir=tmp_path, data_dir_source="test")
    provider = UsageTrackingEmbeddingProvider(
        VoyageEmbeddingProvider("synthetic-voyage", api_key="synthetic", strict=True),
        config,
        Payer.BYOK,
    )
    assert provider.embed(["synthetic input"], "query") == [[1.0, 0.0]]
    with Storage(config.db_path) as storage:
        [entry] = storage.list_model_usage()
        assert entry.input_tokens == expected


def test_rollup_read_is_allowed_on_read_only_database_and_missing_db_is_preserved(tmp_path):
    from wingman.infrastructure.usage_ledger import read_usage

    config = Config(data_dir=tmp_path, data_dir_source="test")
    with Storage(config.db_path) as storage:
        storage.add_model_usage(row(input_tokens=1))
    config.db_path.chmod(0o400)
    try:
        assert read_usage(config.db_path, 10)[1] == 1
    finally:
        config.db_path.chmod(0o600)
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        read_usage(missing, 10)
    assert not missing.exists()


def test_cli_registry_failures_are_diagnosed_and_empty_registry_names_its_path(
    tmp_path, monkeypatch
):
    from wingman.cli.main import app
    from wingman.infrastructure import tenants

    config = Config(data_dir=tmp_path, data_dir_source="test", privileged=True)
    monkeypatch.setattr("wingman.cli.main.load_config", lambda: config)
    registry = tmp_path / "tenants.toml"
    monkeypatch.setattr(tenants, "tenant_registry_path", lambda: registry)
    runner = CliRunner()
    absent = runner.invoke(app, ["usage", "--all-tenants"])
    assert (
        absent.exit_code == 0 and str(registry) in absent.output and "file absent" in absent.output
    )
    registry.write_text("[[broken")
    broken = runner.invoke(app, ["usage", "--all-tenants"])
    assert broken.exit_code == 1 and "malformed" in broken.output
    assert not isinstance(broken.exception, tenants.TenantRegistryError)


def test_offline_calls_are_marked_non_billable_and_funded_ranking_is_explicit(tmp_path):
    from wingman.application.model_usage import funded_ranking

    config = Config(data_dir=tmp_path, data_dir_source="test")
    offline = row().model_copy(update={"provider": "recorded", "payer": Payer.OFFLINE})
    report = render_usage(config, [offline])
    assert "- no cost (offline): 1 call(s)" in report
    assert "unpriced" not in report.lower()
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider="anthropic"\nmodel="dated-model"\ninput_usd_per_million=1.0\n'
    )
    with Storage(config.db_path) as storage:
        storage.add_model_usage(row(input_tokens=1000000))
    report = funded_ranking([Tenant(slug="sample", data_dir=tmp_path)], 10)
    assert "sample: $1.000000" in report and "lower bound" in report


def test_operator_reports_never_load_tenant_credentials(tmp_path, monkeypatch):
    from wingman.application.model_usage import funded_ranking

    tenant = Tenant(slug="sample", data_dir=tmp_path)
    with Storage(tmp_path / "wingman.db") as storage:
        storage.add_model_usage(row(input_tokens=1))

    def forbidden(*args):
        raise AssertionError("credential-bearing tenant config loaded")

    monkeypatch.setattr(Tenant, "config", forbidden)
    report, failed = render_tenant_usage(tenant, 10)
    assert not failed, report
    assert "1 billable call(s)" in report
    assert "sample:" in funded_ranking([tenant], 10)
