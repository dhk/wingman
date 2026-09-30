"""A key that exists is not a key that works (#528).

Every test here drives a real 'get_provider(...).complete' call whose SDK
call raises exactly what Anthropic returned on 2026-09-04, then asks the
read-only surfaces what they now report. No test makes a network call.
"""

from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx
import pytest

from wingman.application.completeness import compute_completeness
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.model_health import health_path
from wingman.infrastructure.storage import Storage
from wingman.mcp_server import status
from wingman.providers.base import CapabilityClass, ModelRequest, ProviderError
from wingman.providers.router import DEFAULT_MODELS_TOML, get_provider, model_rejection

USAGE_LIMIT = (
    "You have reached your specified API usage limits. "
    "You will regain access on 2026-10-01 at 00:00 UTC."
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-over-budget")
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    Storage(config.db_path).close()
    return config.data_dir


def _status_error(status_code: int, message: str) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    response = httpx.Response(status_code, request=request, json=body)
    cls = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        429: anthropic.RateLimitError,
        529: anthropic.InternalServerError,
    }[status_code]
    return cls(f"Error code: {status_code} - {body}", response=response, body=body)


def _call(monkeypatch: pytest.MonkeyPatch, raises: Exception | None) -> None:
    """One model call through the router, the way every tool makes one."""
    provider = get_provider(CapabilityClass.EXTRACT_FAST, load_config())

    def fake_create(**_: object) -> object:
        if raises is not None:
            raise raises
        usage = type("Usage", (), {"input_tokens": 1, "output_tokens": 1})()
        block = type("Block", (), {"type": "text", "text": "ok"})()
        return type(
            "Message",
            (),
            {"stop_reason": "end_turn", "content": [block], "model": "m", "usage": usage},
        )()

    monkeypatch.setattr(provider._client.messages, "create", fake_create)  # type: ignore[attr-defined]
    if raises is None:
        provider.complete(ModelRequest(system="s", prompt="p"))
    else:
        with pytest.raises(ProviderError):
            provider.complete(ModelRequest(system="s", prompt="p"))


def test_an_over_budget_key_is_reported_with_the_providers_reason_and_reset(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported case: a key present, a 400 usage-limit refusal, and a
    'status' that listed seven healthy counts and nothing else."""
    assert "Model calls" not in status()

    _call(monkeypatch, _status_error(400, USAGE_LIMIT))

    reported = status()
    assert "Model calls: UNAVAILABLE" in reported
    assert "usage limits" in reported
    assert "access returns 2026-10-01 00:00 UTC" in reported
    # The counts are still true and still shown.
    assert "Source records: 0" in reported


def test_status_and_completeness_agree(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        assert compute_completeness(storage, config).inference_available

    _call(monkeypatch, _status_error(400, USAGE_LIMIT))

    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    assert not report.inference_available
    assert report.inference_refused is not None
    assert "usage limits" in report.inference_refused
    assert "Model calls: UNAVAILABLE" in status()


@pytest.mark.parametrize("status_code", [429, 529])
def test_a_transient_error_latches_nothing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _call(monkeypatch, _status_error(status_code, "rate limited / overloaded"))

    assert "Model calls" not in status()
    assert not health_path(workspace).exists()


def test_an_ordinary_bad_request_latches_nothing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 400 is usually the request's own fault and says nothing about the
    next call; only the account-level wording counts."""
    _call(monkeypatch, _status_error(400, "prompt is too long: 250000 tokens > 200000 maximum"))

    assert "Model calls" not in status()


def test_a_revoked_key_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _call(monkeypatch, _status_error(401, "invalid x-api-key"))

    reported = status()
    assert "Model calls: UNAVAILABLE" in reported
    assert "401" in reported


def test_the_next_successful_call_clears_it(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _call(monkeypatch, _status_error(400, USAGE_LIMIT))
    assert "Model calls: UNAVAILABLE" in status()

    _call(monkeypatch, None)

    assert "Model calls" not in status()


def test_a_new_key_is_not_reported_as_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _call(monkeypatch, _status_error(400, USAGE_LIMIT))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-a-fresh-one")

    assert "Model calls" not in status()


def test_the_record_expires_at_the_providers_reset(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.model_health import current_rejection

    _call(monkeypatch, _status_error(400, USAGE_LIMIT))
    path = health_path(workspace)
    key = "sk-ant-over-budget"

    assert current_rejection(path, "anthropic", key, now=datetime(2026, 9, 30, tzinfo=UTC))
    assert current_rejection(path, "anthropic", key, now=datetime(2026, 10, 1, tzinfo=UTC)) is None


def test_status_makes_no_model_call(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _call(monkeypatch, _status_error(400, USAGE_LIMIT))

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("status must not call a model")

    monkeypatch.setattr(anthropic.resources.Messages, "create", forbidden)
    assert "Model calls: UNAVAILABLE" in status()
    assert model_rejection(load_config()) is not None


def test_a_corrupt_record_invents_no_outage(workspace: Path) -> None:
    health_path(workspace).write_text("{not json", encoding="utf-8")

    assert "Model calls" not in status()
