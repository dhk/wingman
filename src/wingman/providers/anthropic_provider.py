"""Anthropic adapter for the provider protocol."""

from __future__ import annotations

from time import perf_counter

import anthropic

from wingman.providers.base import ModelRequest, ModelResponse, ProviderError


class AnthropicProvider:
    """Calls the Anthropic Messages API. Reads credentials from the environment."""

    def __init__(self, model: str) -> None:
        self._model = model
        try:
            self._client = anthropic.Anthropic()
        except anthropic.AnthropicError as exc:
            raise ProviderError(
                "Anthropic client could not be created. Set ANTHROPIC_API_KEY and retry."
            ) from exc

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
