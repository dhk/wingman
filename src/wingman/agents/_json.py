"""One JSON extractor for every agent (#97).

Models wrap JSON in markdown fences and sometimes keep talking after the
closing fence. The old per-agent edge-stripping (`text.strip("`")`)
removed the fences but left that trailing prose in place, so json.loads
died with "Extra data" — an opaque crash where a clean parse was
available. This helper takes the interior of the first fenced block when
one exists, falls back to the first balanced JSON value in the text, and
raises ValueError with the real reason when neither parses. Call sites
wrap the ValueError in their own error types.
"""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_block(model_text: str) -> object:
    """The JSON value in a model reply, tolerant of fences and commentary."""
    text = model_text.strip()
    fenced = _FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass  # the fence held prose; the JSON may still be elsewhere
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        whole_error = exc
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError(f"model output contains no JSON: {whole_error}")
    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    return value
