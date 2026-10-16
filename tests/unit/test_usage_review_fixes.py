"""Regressions for the #557 re-review at 094eeb5 (offline option b, lost rows, rollup gaps)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.model_usage import (
    funded_ranking,
    render_tenant_usage,
    render_usage,
    render_workspace_usage,
)
from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure import usage_ledger
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant
from wingman.providers.base import CapabilityClass, ModelRequest, ModelResponse
from wingman.providers.usage import UsageTrackingEmbeddingProvider, UsageTrackingProvider

PRICES = (
    '[models.extract_fast]\nprovider="anthropic"\nmodel="dated-model"\ninput_usd_per_million=1.0\n'
)


def _row(minutes_ago: int = 0, **changes: object) -> ModelUsage:
    values: dict[str, object] = {
        "capability": "extract_fast",
        "provider": "anthropic",
        "model": "dated-model",
        "payer": Payer.FUNDED,
        "input_tokens": 1_000_000,
        "latency_ms": 1,
        "recorded_at": datetime.now(UTC) - timedelta(minutes=minutes_ago),
    }
    values.update(changes)
    return ModelUsage.model_validate(values)


def _offline(minutes_ago: int = 0, **changes: object) -> ModelUsage:
    values: dict[str, object] = {
        "provider": "recorded",
        "model": "recorded:x.json",
        "payer": Payer.OFFLINE,
        "input_tokens": None,
    }
    values.update(changes)
    return _row(minutes_ago, **values)


class _Response:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", provider=self.provider, model="m", latency_ms=1)


class _OfflineSimulator(_Response):
    """A fake/future offline provider whose name is in no allow-list."""

    offline = True


class _OfflineEmbedder:
    provider_name = "future-local-embedder"
    model = "local"
    offline = True

    def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        return [[1.0] for _ in texts]


def _config(tmp_path: Path, **changes: object) -> Config:
    return Config(data_dir=tmp_path, data_dir_source="test").model_copy(update=changes)


def _ledger(config: Config) -> list[ModelUsage]:
    with Storage(config.db_path) as storage:
        return storage.list_model_usage()


# --- 1. Option (b): offline payer, classification, separate line, totals ---------


def test_offline_providers_declare_it_and_metered_providers_do_not() -> None:
    from wingman.providers.anthropic_provider import AnthropicProvider
    from wingman.providers.embeddings import HashedEmbeddingProvider, VoyageEmbeddingProvider
    from wingman.providers.openrouter_provider import OpenRouterProvider
    from wingman.providers.recorded import RecordedProvider

    assert RecordedProvider("x").offline is True
    assert HashedEmbeddingProvider().offline is True
    for metered in (AnthropicProvider, OpenRouterProvider, VoyageEmbeddingProvider):
        assert getattr(metered, "offline", False) is False


def test_router_and_demo_stamp_offline_payer(tmp_path: Path) -> None:
    from wingman.providers import router

    config = _config(tmp_path)
    response = tmp_path / "response.json"
    response.write_text("{}", encoding="utf-8")
    config.models_config_path.write_text(
        f'[models.extract_fast]\nprovider="recorded"\npath="{response}"\n'
        '[models.embed_semantic]\nprovider="hashed"\n',
        encoding="utf-8",
    )
    router.get_provider(CapabilityClass.EXTRACT_FAST, config).complete(
        ModelRequest(system="", prompt="")
    )
    router.get_embedding_provider(config).embed(["text"], "query")
    rows = _ledger(config)
    assert {row.provider for row in rows} == {"recorded", "hashed"}
    assert {row.payer for row in rows} == {Payer.OFFLINE}


def test_offline_classification_comes_from_the_provider_not_its_name(tmp_path: Path) -> None:
    config = _config(tmp_path)
    UsageTrackingProvider(
        _OfflineSimulator("future-simulator"), config, CapabilityClass.EXTRACT_FAST, Payer.AMBIENT
    ).complete(ModelRequest(system="", prompt=""))
    UsageTrackingEmbeddingProvider(_OfflineEmbedder(), config, Payer.FUNDED).embed(["t"], "query")
    # A billable provider is never made free by a name that looks offline.
    UsageTrackingProvider(
        _Response("recorded"), config, CapabilityClass.EXTRACT_FAST, Payer.BYOK
    ).complete(ModelRequest(system="", prompt=""))
    payers = {(row.provider, row.payer) for row in _ledger(config)}
    assert payers == {
        ("future-simulator", Payer.OFFLINE),
        ("future-local-embedder", Payer.OFFLINE),
        ("recorded", Payer.BYOK),
    }
    with pytest.raises(ValueError, match="billable"):
        UsageTrackingProvider(
            _Response("anthropic"), config, CapabilityClass.EXTRACT_FAST, Payer.OFFLINE
        )


def test_both_schema_paths_create_the_same_checked_indexed_ledger(tmp_path: Path) -> None:
    via_storage = tmp_path / "storage.db"
    via_ledger = tmp_path / "ledger.db"
    with Storage(via_storage) as storage:
        storage.add_model_usage(_offline())
    assert usage_ledger.record_usage(via_ledger, _offline()) is True

    def shape(path: Path) -> list[tuple[str, str]]:
        with sqlite3.connect(path) as conn:
            return conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE tbl_name='model_usage' ORDER BY name"
            ).fetchall()

    assert shape(via_storage) == shape(via_ledger)
    assert any(name == "idx_model_usage_recorded_at" for name, _sql in shape(via_ledger))
    with sqlite3.connect(via_ledger) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO model_usage (usage_id, recorded_at, capability, provider, model, "
            "payer, latency_ms) VALUES ('x', 't', 'c', 'p', 'm', 'nobody', 1)"
        )


def test_branch_ledger_with_the_old_payer_check_is_rebuilt_without_loss(tmp_path: Path) -> None:
    path = tmp_path / "wingman.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE model_usage (usage_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, "
            "capability TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
            "payer TEXT NOT NULL CHECK (payer IN ('byok', 'funded', 'ambient')), "
            "input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
            "cache_write_tokens INTEGER, search_result_count INTEGER, "
            "latency_ms INTEGER NOT NULL, caller_name TEXT)"
        )
        conn.execute(
            "INSERT INTO model_usage (usage_id, recorded_at, capability, provider, model, "
            "payer, input_tokens, latency_ms) VALUES ('old', ?, 'extract_fast', 'anthropic', "
            "'dated-model', 'funded', 5, 1)",
            (datetime.now(UTC).isoformat(),),
        )
    with Storage(path) as storage:
        storage.add_model_usage(_offline())
        rows = storage.list_model_usage()
    assert {(row.usage_id == "old", row.payer) for row in rows} == {
        (True, Payer.FUNDED),
        (False, Payer.OFFLINE),
    }


def test_offline_rows_are_one_separate_no_cost_line_outside_billable_totals(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.models_config_path.write_text(PRICES, encoding="utf-8")
    rows = [
        _row(),
        _offline(1),
        _offline(60 * 24 * 2, capability="synthesize_balanced"),
        _offline(2, provider="hashed", model="hashed", capability="embed_semantic"),
    ]
    report = render_usage(config, rows)
    lines = report.splitlines()
    assert lines[0] == "Model usage: 1 billable call(s)"
    offline_lines = [line for line in lines if "offline" in line]
    assert offline_lines == ["- no cost (offline): 3 call(s)"]
    billable = [line for line in lines if line.startswith("- ") and line not in offline_lines]
    assert len(billable) == 1 and "anthropic/dated-model" in billable[0]
    assert "$1.000000" in billable[0]
    assert "unpriced" not in report.lower()
    assert "recorded" not in report and "hashed" not in report


def test_offline_only_report_is_never_unpriced(tmp_path: Path) -> None:
    report = render_usage(_config(tmp_path), [_offline(), _offline(1)])
    assert report.splitlines()[0] == "Model usage: no billable model calls recorded."
    assert "- no cost (offline): 2 call(s)" in report
    assert "unpriced" not in report.lower()
    assert "$" not in report


def test_offline_rows_never_take_the_billable_limit_window(tmp_path: Path) -> None:
    """The review probe: 1 funded + 3 newer recorded rows with --limit 3."""
    config = _config(tmp_path)
    config.models_config_path.write_text(PRICES, encoding="utf-8")
    with Storage(config.db_path) as storage:
        storage.add_model_usage(_row(10))
        for minutes in (1, 2, 3):
            storage.add_model_usage(_offline(minutes))
    report = render_workspace_usage(config, 3)
    assert report.splitlines()[0] == "Model usage: 1 billable call(s)"
    assert "anthropic/dated-model" in report and "$1.000000" in report
    assert "- no cost (offline): 3 call(s)" in report
    assert "truncated" not in report
    ranking = funded_ranking([Tenant(slug="sample", data_dir=tmp_path)], 3)
    assert "- sample: $1.000000" in ranking
    assert "[incomplete]" not in ranking


def test_billable_truncation_counts_only_billable_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with Storage(config.db_path) as storage:
        for minutes in (1, 2, 3):
            storage.add_model_usage(_row(minutes))
        for minutes in (4, 5):
            storage.add_model_usage(_offline(minutes))
    report = render_workspace_usage(config, 2)
    assert report.splitlines()[0] == "Model usage: 2 billable call(s); newest 2 of 3 (truncated)"
    assert "- no cost (offline): 2 call(s)" in report


# --- 3. Same-process lock must not silently lose a usage row ---------------------


def _billable_call(config: Config) -> None:
    UsageTrackingProvider(
        _Response("anthropic"), config, CapabilityClass.EXTRACT_FAST, Payer.BYOK
    ).complete(ModelRequest(system="", prompt=""))


def test_row_blocked_by_this_process_uncommitted_write_is_kept_and_flushed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(tmp_path)
    storage = Storage(config.db_path)
    storage._conn.execute("CREATE TABLE IF NOT EXISTS scratch (v TEXT)")
    storage._conn.execute("INSERT INTO scratch VALUES ('uncommitted')")
    assert storage._conn.in_transaction
    with caplog.at_level(logging.WARNING):
        _billable_call(config)
    assert "deferred" in caplog.text
    storage._conn.commit()
    storage.close()  # closing a Storage flushes rows its own transaction blocked
    rows = _ledger(config)
    assert [row.provider for row in rows] == ["anthropic"]


def test_row_blocked_by_an_open_read_is_written_by_the_next_recording(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with Storage(config.db_path) as storage:
        for minutes in range(5):
            storage.add_model_usage(_offline(minutes))
    reader = sqlite3.connect(config.db_path)
    cursor = reader.execute("SELECT * FROM model_usage")
    cursor.fetchone()  # an unfinished read cursor holds a SHARED lock
    _billable_call(config)
    cursor.fetchall()
    reader.close()
    _billable_call(config)
    rows = [row for row in _ledger(config) if row.payer is Payer.BYOK]
    assert len(rows) == 2


def test_rows_still_pending_at_exit_are_logged_as_lost(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(tmp_path)
    with Storage(config.db_path):
        pass
    lock = sqlite3.connect(config.db_path)
    lock.execute("BEGIN IMMEDIATE")
    _billable_call(config)
    with caplog.at_level(logging.ERROR):
        usage_ledger.flush_pending_usage(at_exit=True)
    lock.rollback()
    lock.close()
    assert "lost" in caplog.text and "lower bound" in caplog.text
    assert usage_ledger.flush_pending_usage() == 0


# --- 4/5. Privileged rollup: every tenant, and new tenants are not failures ------


def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slugs: list[str]) -> Path:
    from wingman.infrastructure import tenants

    lines: list[str] = []
    for index, slug in enumerate(slugs):
        data_dir = tmp_path / slug
        data_dir.mkdir()
        if slug != "newcomer":
            with Storage(data_dir / "wingman.db") as storage:
                storage.add_model_usage(_row(input_tokens=1000 * (index + 1)))
        lines += ["[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""]
    registry = tmp_path / "tenants.toml"
    registry.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(tenants, "tenant_registry_path", lambda: registry)
    return registry


def _assert_every_tenant(output: str, slugs: list[str]) -> None:
    for index, slug in enumerate(slugs):
        assert f"Tenant {slug}: 1 billable call(s)" in output
        assert f"input_tokens={1000 * (index + 1)}" in output
        assert f"- {slug}: $" in output


def test_privileged_cli_and_mcp_rollups_report_every_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman import mcp_server as mcp
    from wingman.cli.main import app

    slugs = ["alpha", "bravo", "charlie"]
    _registry(tmp_path, monkeypatch, slugs)
    operator = _config(tmp_path / "operator", privileged=True)
    monkeypatch.setattr("wingman.cli.main.load_config", lambda: operator)
    monkeypatch.setattr(mcp, "_ready_config", lambda: operator)
    result = CliRunner().invoke(app, ["usage", "--all-tenants"])
    assert result.exit_code == 0, result.output
    _assert_every_tenant(result.output, slugs)
    _assert_every_tenant(mcp.usage_all_tenants(), slugs)


def test_registered_tenant_without_a_database_is_no_usage_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman import mcp_server as mcp
    from wingman.cli.main import app

    _registry(tmp_path, monkeypatch, ["alpha", "newcomer"])
    operator = _config(tmp_path / "operator", privileged=True)
    monkeypatch.setattr("wingman.cli.main.load_config", lambda: operator)
    monkeypatch.setattr(mcp, "_ready_config", lambda: operator)
    result = CliRunner().invoke(app, ["usage", "--all-tenants"])
    assert result.exit_code == 0, result.output
    assert "Tenant newcomer: no usage yet" in result.output
    assert "- newcomer: $0.000000" in result.output
    assert "Tenant newcomer: no usage yet" in mcp.usage_all_tenants()
    report, failed = render_tenant_usage(Tenant(slug="x", data_dir=tmp_path / "absent"), 10)
    assert not failed and "no usage yet" in report


# --- 6. Own-workspace report on an unreadable database --------------------------


def test_own_workspace_usage_on_corrupt_or_locked_db_is_a_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman import mcp_server as mcp
    from wingman.cli.main import app

    config = _config(tmp_path)
    monkeypatch.setattr("wingman.cli.main.load_config", lambda: config)
    monkeypatch.setattr(mcp, "_ready_config", lambda: config)
    config.db_path.write_text("not sqlite", encoding="utf-8")
    result = CliRunner().invoke(app, ["usage"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, sqlite3.Error)
    assert "could not be read" in result.output and str(config.db_path) in result.output
    assert "could not be read" in mcp.usage()

    config.db_path.unlink()
    with Storage(config.db_path) as storage:
        storage.add_model_usage(_row())
    lock = sqlite3.connect(config.db_path)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        locked = CliRunner().invoke(app, ["usage"])
    finally:
        lock.rollback()
        lock.close()
    assert locked.exit_code == 1
    assert not isinstance(locked.exception, sqlite3.Error)
    assert "could not be read" in locked.output


# --- Codex review of 9a6e2da: queue lock held across I/O; batches lost --------


@pytest.fixture
def _clean_queue() -> object:
    usage_ledger._pending.clear()
    yield None
    usage_ledger._pending.clear()


def _locked_ledger(tmp_path: Path, name: str) -> tuple[Path, sqlite3.Connection]:
    config = _config(tmp_path / name)
    config.data_dir.mkdir(parents=True)
    with Storage(config.db_path):
        pass
    lock = sqlite3.connect(config.db_path)
    lock.execute("BEGIN IMMEDIATE")
    return config.db_path, lock


def _timed_concurrent_records(paths: list[Path]) -> list[float]:
    import threading
    import time

    barrier = threading.Barrier(len(paths))
    elapsed = [0.0] * len(paths)

    def run(index: int, path: Path) -> None:
        barrier.wait()
        started = time.monotonic()
        usage_ledger.record_usage(path, _row(index))
        elapsed[index] = time.monotonic() - started

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(paths)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return elapsed


@pytest.mark.usefixtures("_clean_queue")
def test_locked_ledgers_of_different_tenants_do_not_queue_behind_each_other(
    tmp_path: Path,
) -> None:
    """Each call waits at most its own ledger's timeout, never other tenants'."""
    ledgers = [_locked_ledger(tmp_path, f"tenant-{n}") for n in range(8)]
    try:
        elapsed = _timed_concurrent_records([path for path, _ in ledgers])
    finally:
        for _, lock in ledgers:
            lock.rollback()
            lock.close()
    # Serialised behind one process-wide lock this took 8 x 0.25 s (~2 s).
    assert max(elapsed) < 1.0, elapsed
    assert usage_ledger.flush_pending_usage() == 0


@pytest.mark.usefixtures("_clean_queue")
def test_concurrent_calls_on_one_locked_ledger_enqueue_without_serial_retries(
    tmp_path: Path,
) -> None:
    path, lock = _locked_ledger(tmp_path, "tenant")
    try:
        elapsed = _timed_concurrent_records([path] * 8)
    finally:
        lock.rollback()
        lock.close()
    assert max(elapsed) < 1.0, elapsed
    assert usage_ledger.flush_pending_usage() == 0
    with Storage(path) as storage:
        assert len(storage.list_model_usage()) == 8


@pytest.mark.usefixtures("_clean_queue")
def test_a_non_lock_failure_keeps_every_deferred_row_for_a_later_flush(
    tmp_path: Path,
) -> None:
    path, lock = _locked_ledger(tmp_path, "tenant")
    assert usage_ledger.record_usage(path, _row(2)) is False  # deferred by the lock
    lock.rollback()
    lock.close()
    good = path.read_bytes()
    path.write_bytes(b"this is not a sqlite database" * 64)
    with pytest.raises(sqlite3.DatabaseError):
        usage_ledger.record_usage(path, _row(1))
    assert len(usage_ledger._pending[path.resolve()]) == 2  # neither row discarded
    path.write_bytes(good)
    assert usage_ledger.flush_pending_usage(path) == 0
    with Storage(path) as storage:
        assert len(storage.list_model_usage()) == 2
