"""Bounded YAML/markdown loader; raw loop files never become tool responses."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from wingman.domain.loops import LoopDefinition

BUNDLED = Path(__file__).resolve().parents[1] / "loops"


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently accepting the last value."""


def _mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("loop mappings require unique string keys")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_loop(raw: str, filename: str) -> LoopDefinition:
    if len(raw.encode()) > 64_000:
        raise ValueError("loop file exceeds 64 KB")
    if any(
        isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for t in yaml.scan(raw)
    ):
        raise ValueError("YAML anchors and aliases are not allowed")
    parts = raw.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError("loop requires YAML frontmatter")
    metadata = yaml.load(parts[1], Loader=UniqueLoader)
    if not isinstance(metadata, dict):
        raise ValueError("invalid frontmatter: expected a mapping")  # noqa: TRY004
    if set(metadata) & {"cards", "playbook"}:
        raise ValueError("frontmatter cannot redefine the loop body")
    sections = re.split(r"(?m)^## (card: [a-z][a-z0-9_-]*|playbook)\s*$", parts[2])
    if sections[0].strip() or len(sections) < 3:
        raise ValueError("loop body must contain only cards and a playbook")
    cards = []
    playbook: list[Any] = []
    for heading, body in zip(sections[1::2], sections[2::2], strict=True):
        value = yaml.load(body, Loader=UniqueLoader)
        if heading == "playbook":
            if playbook or not isinstance(value, list):
                raise ValueError("playbook must be one list")
            playbook = value
        else:
            if not isinstance(value, dict):
                raise ValueError("card must be a mapping")
            if "id" in value:
                raise ValueError("card id is defined by its heading")
            cards.append({"id": heading.removeprefix("card: "), **value})
    definition = LoopDefinition.model_validate({**metadata, "cards": cards, "playbook": playbook})
    if definition.id != Path(filename).stem:
        raise ValueError("loop id must match its filename")
    return definition


def catalogue(local_dir: Path) -> list[tuple[LoopDefinition, str, str]]:
    result = []
    seen: set[str] = set()
    for directory, tier in [(BUNDLED, "official"), (local_dir, "local")]:
        for path in sorted(directory.glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            definition = parse_loop(raw, path.name)
            if definition.id in seen:
                raise ValueError("local loops cannot shadow bundled loops")
            seen.add(definition.id)
            result.append((definition, hashlib.sha256(raw.encode()).hexdigest(), tier))
    return result
