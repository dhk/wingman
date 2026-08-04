"""OpenRouter adapter, web-search-grounded (RFC_WEBSEARCH pending, #222).

The only provider in wingman that reaches the open web. Every other
research feature stays inside approved/named sources (RFC-015's company
research) or user-dropped items (the heap, #113/RFC-044) — RFC-009's
"the invocation is the consent" has always meant "the user named this
source," not "go search the web." This provider exists to serve exactly
one capability class (CapabilityClass.RESEARCH_WEBSEARCH), never as a
fallback for any other — a caller that wants web-grounded results has to
ask for this class explicitly.

Uses OpenRouter's 'web' plugin (Exa-backed), not the newer agentic
'openrouter:web_search' tool-calling surface: a single, bounded
request/response round trip with a fixed max_results cap keeps cost and
behavior predictable, rather than letting a model decide autonomously how
many searches to run and what to spend doing it.

Citations come back from OpenRouter as a separate 'annotations' array
(url_citation entries: url, title, excerpt) — deliberately NOT trusted to
the model to format correctly inline. This provider appends a
deterministic "Sources:" section built straight from that metadata,
using the exact '[title](url)' shape 'application.focus' already uses
for evidence everywhere else in wingman, so downstream parsing (the same
regex kind '_EVIDENCE_URL' already uses) needs no new format to handle.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from time import perf_counter
from typing import Any

from wingman.providers.base import ModelRequest, ModelResponse, ProviderError

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_ENV_KEY = "OPENROUTER_API_KEY"
_TIMEOUT_SECONDS = 90
_DEFAULT_MAX_RESULTS = 10


def _format_sources(annotations: list[dict[str, Any]]) -> str:
    """A deterministic 'Sources:' block from OpenRouter's own citation
    metadata — never left to the model to cite correctly on its own.
    '[title](url)' matches application.focus's existing evidence
    convention exactly, so nothing downstream needs a new format."""
    seen: set[str] = set()
    lines: list[str] = []
    for annotation in annotations:
        if annotation.get("type") != "url_citation":
            continue
        citation = annotation.get("url_citation") or {}
        url = citation.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        title = citation.get("title") or url
        lines.append(f"- [{title}]({url})")
    if not lines:
        return ""
    return "\n\nSources:\n" + "\n".join(lines)


class OpenRouterProvider:
    """Calls OpenRouter's chat completions API with the web-search plugin
    enabled. 'strict', when True, suppresses the 'os.environ' fallback
    even when 'api_key' is empty — the same tenant-isolation shape every
    other provider in wingman uses (RFC-048): a tenant with no key of
    their own must fail loud, never silently inherit whatever key happens
    to be set in a shared process's environment.
    """

    provider_name = "openrouter"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_results: int = _DEFAULT_MAX_RESULTS,
        strict: bool = False,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._max_results = max_results
        self._strict = strict

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._strict:
            api_key = self._api_key or ""
        else:
            api_key = self._api_key or os.environ.get(_ENV_KEY, "").strip()
        if not api_key:
            raise ProviderError(
                f"no {_ENV_KEY} configured. Export it, set it via Manage → Keys, "
                "or configure [models.research_websearch] to use a different provider."
            )

        payload = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.prompt},
                ],
                "max_tokens": request.max_tokens,
                "plugins": [{"id": "web", "engine": "exa", "max_results": self._max_results}],
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(
            _ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        start = perf_counter()
        try:
            with urllib.request.urlopen(http_request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 401:
                raise ProviderError("OpenRouter rejected the key (invalid or revoked)") from exc
            raise ProviderError(f"OpenRouter API returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"could not reach the OpenRouter API ({exc})") from exc

        try:
            choice = body["choices"][0]
            message = choice["message"]
            text = message.get("content") or ""
            annotations = message.get("annotations") or []
            usage = body.get("usage") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"OpenRouter API response had an unexpected shape ({exc})") from exc

        text = text.rstrip() + _format_sources(annotations)

        return ModelResponse(
            text=text,
            provider="openrouter",
            model=str(body.get("model", self._model)),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=int((perf_counter() - start) * 1000),
        )
