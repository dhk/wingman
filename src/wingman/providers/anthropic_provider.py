"""Anthropic adapter for the provider protocol."""

from __future__ import annotations

from time import perf_counter

import anthropic

from wingman.providers.base import ModelRequest, ModelResponse, ProviderError


class AnthropicProvider:
    """Calls the Anthropic Messages API.

    Reads credentials from 'api_key' when given; otherwise falls back to
    the SDK's own environment read (ANTHROPIC_API_KEY), unchanged from
    before this parameter existed. An explicit key lets callers resolve
    credentials themselves (docs/RFC.md RFC-048's per-tenant resolution)
    instead of relying on process-wide environment state.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self._model = model
        try:
            self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        except anthropic.AnthropicError as exc:
            raise ProviderError(
                "Anthropic client could not be created. Set ANTHROPIC_API_KEY and retry."
            ) from exc
        # The SDK itself doesn't fail at construction when no key is set —
        # it defers to a bare TypeError raised deep in request header
        # validation, which none of complete()'s except clauses catch. Check
        # here so a missing key fails loud and clear instead of surfacing as
        # a cryptic error the first time a request is made (overnight P1).
        if not self._client.api_key and not self._client.auth_token:
            raise ProviderError("No Anthropic credentials found. Set ANTHROPIC_API_KEY and retry.")

    def complete(self, request: ModelRequest) -> ModelResponse:
        start = perf_counter()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.prompt}],
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                "Anthropic rejected the credentials. Check ANTHROPIC_API_KEY and retry."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"Anthropic API error ({exc.status_code}) calling {self._model}: {exc.message}. "
                "No local data was changed; retry when the provider is available."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                "Could not reach the Anthropic API (network error). "
                "No local data was changed; check connectivity and retry."
            ) from exc
        if response.stop_reason == "refusal":
            raise ProviderError(f"{self._model} declined the request. No local data was changed.")
        text = "".join(block.text for block in response.content if block.type == "text")
        return ModelResponse(
            text=text,
            provider="anthropic",
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=int((perf_counter() - start) * 1000),
        )
