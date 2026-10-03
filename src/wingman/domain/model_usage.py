"""Content-free accounting records for successful model-provider calls (#554)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class Payer(StrEnum):
    BYOK = "byok"
    FUNDED = "funded"
    AMBIENT = "ambient"


class ModelUsage(BaseModel):
    usage_id: str = Field(default_factory=lambda: uuid4().hex)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    capability: str
    provider: str
    model: str
    payer: Payer
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    search_result_count: int | None = None
    latency_ms: int
    caller_name: str | None = None
