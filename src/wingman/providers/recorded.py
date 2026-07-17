"""Recorded provider: replays a stored response for tests, CI, and offline evaluation."""

from __future__ import annotations

from pathlib import Path

from wingman.providers.base import ModelRequest, ModelResponse, ProviderError


class RecordedProvider:
    """Returns a fixed response text instead of calling a live model."""

    def __init__(self, text: str, model: str = "recorded") -> None:
        self._text = text
        self._model = model

    @classmethod
    def from_file(cls, path: Path) -> RecordedProvider:
        try:
            return cls(path.read_text(encoding="utf-8"), model=f"recorded:{path.name}")
        except OSError as exc:
            raise ProviderError(
                f"Recorded response {path} could not be read ({exc}). "
                "Fix the path in the workspace models.toml."
            ) from exc

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=self._text,
            provider="recorded",
            model=self._model,
            latency_ms=0,
        )
