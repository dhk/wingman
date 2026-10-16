"""OpenRouter web-search-grounded provider (#222's person deep-dive)."""

from __future__ import annotations

import json
import urllib.error
from typing import Self

import pytest

from wingman.providers.base import ModelRequest, ProviderError
from wingman.providers.openrouter_provider import OpenRouterProvider

_REQUEST = ModelRequest(system="you are a researcher", prompt="who is Scott Brady")


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _openrouter_body(
    text: str, annotations: list[dict] | None = None, finish_reason: str | None = "stop"
) -> dict:
    return {
        "model": "anthropic/claude-sonnet-5",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "annotations": annotations or [],
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def test_complete_appends_deterministic_sources_from_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _openrouter_body(
        "Scott Brady is a founding partner at Innovation Endeavors.",
        annotations=[
            {
                "type": "url_citation",
                "url_citation": {
                    "url": "https://www.innovationendeavors.com/team/scott-brady",
                    "title": "Scott Brady | Founding Partner",
                },
            }
        ],
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeResponse(body),  # noqa: ARG005
    )
    provider = OpenRouterProvider(model="anthropic/claude-sonnet-5", api_key="sk-or-test")
    response = provider.complete(_REQUEST)
    assert "founding partner at Innovation Endeavors" in response.text
    assert "Sources:" in response.text
    assert (
        "[Scott Brady | Founding Partner](https://www.innovationendeavors.com/team/scott-brady)"
        in response.text
    )
    assert response.provider == "openrouter"
    assert response.model == "anthropic/claude-sonnet-5"
    assert response.input_tokens == 100
    assert response.output_tokens == 50


def test_complete_deduplicates_repeated_citation_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _openrouter_body(
        "text",
        annotations=[
            {
                "type": "url_citation",
                "url_citation": {"url": "https://a.example.com", "title": "A"},
            },
            {
                "type": "url_citation",
                "url_citation": {"url": "https://a.example.com", "title": "A again"},
            },
        ],
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse(body))  # noqa: ARG005
    provider = OpenRouterProvider(model="m", api_key="sk-or-test")
    response = provider.complete(_REQUEST)
    assert response.text.count("https://a.example.com") == 1
    assert response.search_result_count == 1


def test_complete_with_no_annotations_has_no_sources_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _openrouter_body("plain text, nothing found")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse(body))  # noqa: ARG005
    provider = OpenRouterProvider(model="m", api_key="sk-or-test")
    response = provider.complete(_REQUEST)
    assert response.text == "plain text, nothing found"
    assert "Sources:" not in response.text
    assert response.search_result_count == 0


def test_no_key_raises_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    provider = OpenRouterProvider(model="m")
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        provider.complete(_REQUEST)


def test_strict_mode_never_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC-048: a tenant with no key of their own must fail loud, never
    silently inherit whatever key happens to be set in a shared process's
    environment — the same guarantee every other provider gives."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-shared-process-env")
    provider = OpenRouterProvider(model="m", api_key=None, strict=True)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        provider.complete(_REQUEST)


def test_non_strict_mode_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env")
    body = _openrouter_body("ok")
    seen_headers: list[dict] = []

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        seen_headers.append(dict(request.header_items()))
        return _FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenRouterProvider(model="m", api_key=None, strict=False)
    provider.complete(_REQUEST)
    assert seen_headers[0]["Authorization"] == "Bearer sk-or-from-env"


def test_unauthorized_key_reports_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 401, "unauthorized", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenRouterProvider(model="m", api_key="sk-or-bad")
    with pytest.raises(ProviderError, match="rejected the key"):
        provider.complete(_REQUEST)


def test_unreachable_api_reports_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(*args: object, **kwargs: object) -> None:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenRouterProvider(model="m", api_key="sk-or-test")
    with pytest.raises(ProviderError, match="could not reach"):
        provider.complete(_REQUEST)


def test_malformed_response_reports_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeResponse({"unexpected": "shape"}),  # noqa: ARG005
    )
    provider = OpenRouterProvider(model="m", api_key="sk-or-test")
    with pytest.raises(ProviderError, match="unexpected shape"):
        provider.complete(_REQUEST)


def test_request_includes_web_plugin_with_bounded_max_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole design point of using OpenRouter's plugin (not the newer
    agentic tool-calling surface): one bounded, predictable search per
    call — never a model deciding autonomously how many searches to run."""
    body = _openrouter_body("ok")
    seen_payloads: list[dict] = []

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        seen_payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenRouterProvider(model="m", api_key="sk-or-test", max_results=3)
    provider.complete(_REQUEST)
    assert seen_payloads[0]["plugins"] == [{"id": "web", "engine": "exa", "max_results": 3}]
    assert "tools" not in seen_payloads[0]  # never the open-ended agentic surface


def test_complete_reports_finish_reason_length_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#261: 'length' is the authoritative truncation signal — the caller
    shouldn't have to guess from output_tokens vs. what was requested."""
    body = _openrouter_body("this got cut off mid-sent", finish_reason="length")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse(body))  # noqa: ARG005
    provider = OpenRouterProvider(model="m", api_key="sk-or-test")
    response = provider.complete(_REQUEST)
    assert response.finish_reason == "length"


def test_complete_reports_finish_reason_stop_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _openrouter_body("a complete response.", finish_reason="stop")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse(body))  # noqa: ARG005
    provider = OpenRouterProvider(model="m", api_key="sk-or-test")
    response = provider.complete(_REQUEST)
    assert response.finish_reason == "stop"
