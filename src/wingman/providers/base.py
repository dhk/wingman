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
    latency_ms: int


class ProviderError(Exception):
    """A model call failed or was declined; the message says how to recover."""


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
