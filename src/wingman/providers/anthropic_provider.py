"""Anthropic adapter for the provider protocol."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import anthropic

from wingman.infrastructure import model_health
from wingman.providers.base import ModelRequest, ModelResponse, ProviderError


def _provider_message(exc: anthropic.APIStatusError) -> str:
    """The provider's own sentence, not the SDK's 'Error code: 400 - {...}'
    wrapper around it, when the body carries one."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
    return exc.message


class AnthropicProvider:
    """Calls the Anthropic Messages API.

    Reads credentials from 'api_key' when given; otherwise falls back to
    the SDK's own environment read (ANTHROPIC_API_KEY), unchanged from
    before this parameter existed. An explicit key lets callers resolve
    credentials themselves (docs/RFC.md RFC-048's per-tenant resolution)
    instead of relying on process-wide environment state.

    'strict', when True, suppresses that env fallback even when 'api_key'
    is None or empty — required for tenant isolation under a shared
    multi-tenant process (RFC-048): a tenant with no key configured must
    fail loud, never silently pick up whatever ANTHROPIC_API_KEY happens
    to be set in the shared process's environment. The Anthropic SDK
    itself only consults env when NO explicit credential argument was
    passed (checked via 'is not None', not truthiness) — so strict mode
    passes 'api_key=""' rather than omitting the argument.

    'health_path', when given, is where this workspace keeps the last
    terminal refusal (#528): written when the provider refuses the account
    itself, removed by the next call that succeeds. None keeps no record.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        strict: bool = False,
        health_path: Path | None = None,
    ) -> None:
        self._model = model
        self._health_path = health_path
        try:
            if strict:
                self._client = anthropic.Anthropic(api_key=api_key or "")
            elif api_key:
                self._client = anthropic.Anthropic(api_key=api_key)
            else:
                self._client = anthropic.Anthropic()
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
            # Two different audiences, two different remedies (#514). A
            # hosted tenant reached over MCP has no shell and no
            # environment to export into, so "set ANTHROPIC_API_KEY" named
            # the one thing they cannot do — and they were told it on
            # every model-backed call. Manage → Keys writes the workspace
            # key, which is the route the other two providers already
            # pointed at; the operator-funded path is the other, and only
            # the operator can take it.
            if strict:
                raise ProviderError(
                    "no Anthropic key configured for this workspace. Add your own via "
                    "Manage → Keys, or ask the operator to enable shared inference for "
                    "this tenant."
                )
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
            self._record_rejection(exc)
            raise ProviderError(
                "Anthropic rejected the credentials. Check ANTHROPIC_API_KEY and retry."
            ) from exc
        except anthropic.APIStatusError as exc:
            self._record_rejection(exc)
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
        if self._health_path is not None:
            model_health.clear_rejection(self._health_path)
        text = "".join(block.text for block in response.content if block.type == "text")
        return ModelResponse(
            text=text,
            provider="anthropic",
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=int((perf_counter() - start) * 1000),
        )

    def _record_rejection(self, exc: anthropic.APIStatusError) -> None:
        if self._health_path is None:
            return
        message = _provider_message(exc)
        if not model_health.is_terminal(exc.status_code, message):
            return
        model_health.record_rejection(
            self._health_path,
            provider="anthropic",
            status_code=exc.status_code,
            message=message,
            api_key=self._client.api_key,
        )
