"""Shared MCP/CLI adapter. Persona scope follows the existing coaching feature."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import yaml

from wingman.application.coaching import get_active_persona
from wingman.application.loops import answer_loop, list_loops, start_loop
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage


def run_loop_tool(operation: str, **kwargs: Any) -> str:
    config = load_config()
    try:
        if operation == "list":
            return json.dumps(list_loops(config), ensure_ascii=False)
        # Opening an empty workspace is a valid entry point. Create only the
        # standard workspace DB, not a profile, corpus or enrollment requirement.
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with Storage(config.db_path) as storage:
            persona = get_active_persona(storage, config)
            if persona and config.strict_provider_keys and not config.privileged:
                raise ValueError("coaching is operator-only on a shared host")
            subject = persona.persona_id if persona else "self"
            if operation == "start":
                result = start_loop(config, storage, subject=subject, **kwargs)
            elif operation == "answer":
                result = answer_loop(storage, subject=subject, **kwargs)
            else:
                raise ValueError("unknown loop operation")
            result["acting_as"] = f"coach for {persona.name}" if persona else "yourself"
        return json.dumps(result, ensure_ascii=False)
    except (ValueError, OSError, sqlite3.Error, yaml.YAMLError) as exc:
        # Do not echo parser validation errors: they may contain private answer
        # values or local loop content. Return a safe actionable diagnostic.
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        return json.dumps(
            {
                "status": "error",
                "message": message,
                "recovery": "Use loop_start to resume. Confirmed answers are preserved.",
            }
        )
