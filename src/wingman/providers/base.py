"""Provider-neutral model access: capability classes and the provider protocol (RFC-004)."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class CapabilityClass(StrEnum):
    EXTRACT_FAST = "extract_fast"
    SYNTHESIZE_BALANCED = "synthesize_balanced"
    REASON_FRONTIER = "reason_frontier"
    CRITIC_INDEPENDENT = "critic_independent"
    # The only capability class whose provider reaches the open web
    # (#222's person deep-dive) — every other class stays inside
    # approved/named sources (RFC-015) or user-dropped items (the heap,
    # #113). Deliberately its own class, never a fallback for the others.
    RESEARCH_WEBSEARCH = "research_websearch"


class ModelRequest(BaseModel):
    system: str
    prompt: str
    max_tokens: int = 8192


class ModelResponse(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # The API's own reason generation stopped ("stop", "length",
    # "content_filter", ...) when the provider reports one — an
    # authoritative truncation signal (#261), not something callers should
    # have to infer from output_tokens vs. the request's own max_tokens.
    # None for providers that don't surface it (e.g. RecordedProvider).
    finish_reason: str | None = None
    latency_ms: int


class ProviderError(Exception):
    """A model call failed or was declined; the message says how to recover."""


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
