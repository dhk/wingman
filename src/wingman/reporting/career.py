"""Render the canonical profile as career.json and career.md with citations."""

from __future__ import annotations

import json
from pathlib import Path

from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage


def _cite(item: ProfileItem, footnotes: dict[str, int]) -> str:
    marks = []
    for span in item.evidence:
        key = f"{span.source_record_id}:{span.quote}"
        if key not in footnotes:
            footnotes[key] = len(footnotes) + 1
        marks.append(f"[^{footnotes[key]}]")
    return "".join(marks)


def _item_line(item: ProfileItem, footnotes: dict[str, int]) -> str:
    detail = f" — {item.detail}" if item.detail else ""
    meta = f" ({item.classification.value}, confidence {item.confidence:.2f})"
    return f"- **{item.name}**{detail}{meta}{_cite(item, footnotes)}"


def render_career(storage: Storage, config: Config, run_meta: dict[str, str]) -> tuple[Path, Path]:
    items = storage.list_profile_items()
    active = [i for i in items if i.status is ItemStatus.ACTIVE]
    conflicted = [i for i in items if i.status is ItemStatus.CONFLICT]

    payload = {
        "metadata": run_meta,
        "achievements": [
            i.model_dump(mode="json") for i in active if i.kind is ProfileItemKind.ACHIEVEMENT
        ],
        "skills": [i.model_dump(mode="json") for i in active if i.kind is ProfileItemKind.SKILL],
        "conflicts": [i.model_dump(mode="json") for i in conflicted],
    }
    career_json = config.reports_dir / "career.json"
    career_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    footnotes: dict[str, int] = {}
    lines = ["# Career Profile", ""]
    for heading, kind in (
        ("## Achievements", ProfileItemKind.ACHIEVEMENT),
        ("## Skills", ProfileItemKind.SKILL),
    ):
        section = [i for i in active if i.kind is kind]
        lines.extend([heading, ""])
        if section:
            lines.extend(_item_line(i, footnotes) for i in section)
        else:
            lines.append("_None yet._")
        lines.append("")
    if conflicted:
        lines.extend(["## Conflicts (need your resolution)", ""])
        for item in conflicted:
            lines.append(
                f"{_item_line(item, footnotes)} — conflicts with item `{item.conflicts_with}`"
            )
    if footnotes:
        lines.extend(["", "## Evidence", ""])
        for key, number in sorted(footnotes.items(), key=lambda kv: kv[1]):
            record_id, quote = key.split(":", 1)
            # Quotes are untrusted source text and may span lines: render each
            # line as an indented blockquote so verbatim text cannot break the
            # footnote or inject block-level Markdown structure.
            lines.append(f"[^{number}]: source record `{record_id}`:")
            lines.extend(f"    > {line}" for line in quote.splitlines() or [quote])
    lines.append("")
    career_md = config.reports_dir / "career.md"
    career_md.write_text("\n".join(lines), encoding="utf-8")
    return career_json, career_md
