"""Persisted "active persona" pointer (docs/COACHING-MODE-DESIGN.md) —
one small JSON file in the workspace, the exact same shape as
doctor_deep's own persisted step-cursor (infrastructure/doctor_deep.py):
MCP tools in this codebase hold no session state between calls, so a file
is the mechanism for "act as coach for Mike, then everything is scoped to
Mike" to survive across calls without requiring an explicit persona
argument on every one.

No expiry, no time window — persists until explicitly cleared (the design
doc's resolved answer). That only stays safe because it's never silently
applied: every persona-aware tool call is expected to render a visible
"acting as: <name>" indicator using the same active persona this module
resolves, so which persona is active is never something the coach has to
separately check or remember.
"""

from __future__ import annotations

import json
from pathlib import Path

from wingman.infrastructure.config import Config

STATE_FILENAME = "active-persona.json"


def _state_path(config: Config) -> Path:
    return config.data_dir / STATE_FILENAME


def read_active_persona_id(config: Config) -> str | None:
    """The active persona's id, or None if nothing is set (the coach's own
    work — the default for every existing user of wingman)."""
    path = _state_path(config)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        persona_id = data["persona_id"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    return persona_id if isinstance(persona_id, str) and persona_id else None


def write_active_persona_id(config: Config, persona_id: str) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"persona_id": persona_id}), encoding="utf-8")


def clear_active_persona(config: Config) -> None:
    _state_path(config).unlink(missing_ok=True)
