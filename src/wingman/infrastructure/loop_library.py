"""Bounded YAML/markdown loader; raw loop files never become tool responses."""

from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

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


@dataclass
class LoopCatalogue:
    entries: list[tuple[LoopDefinition, str, str]] = field(default_factory=list)
    invalid_loops: list[dict[str, Any]] = field(default_factory=list)


def _problems(exc: Exception) -> list[str]:
    """Actionable diagnostics without echoing card prose or invalid input values."""
    if isinstance(exc, ValidationError):
        return [
            f"{'.'.join(str(part) for part in error['loc']) or 'loop'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
    if isinstance(exc, UnicodeError):
        return ["loop file must be UTF-8 text"]
    if isinstance(exc, yaml.YAMLError):
        mark = getattr(exc, "problem_mark", None)
        location = f" near line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        return [f"invalid YAML{location}"]
    if isinstance(exc, OSError):
        return [f"cannot read loop source ({type(exc).__name__})"]
    if isinstance(exc, RecursionError):
        return ["loop nesting is too deep"]
    return [str(exc)]


def _reject(result: LoopCatalogue, path: Path, tier: str, problems: list[str]) -> None:
    result.invalid_loops.append(
        {
            "filename": path.name,
            "loop_id": path.stem,
            "tier": tier,
            "status": "rejected",
            "problems": problems,
        }
    )


def _admit_files(result: LoopCatalogue, paths: list[Path], tier: str) -> None:
    seen = {entry[0].id for entry in result.entries} | {
        item["loop_id"] for item in result.invalid_loops if item["tier"] == "official"
    }
    for path in paths:
        try:
            if path.suffix != ".md":
                raise ValueError("loop files must use the .md extension")
            with path.open("rb") as handle:
                raw = handle.read(64_001)
            if len(raw) > 64_000:
                raise ValueError("loop file exceeds 64 KB")
            definition = parse_loop(raw.decode("utf-8"), path.name)
            if definition.id in seen:
                raise ValueError(
                    "local loops cannot shadow bundled loops or duplicate accepted ids"
                )
            seen.add(definition.id)
            # Only this successful gate admits a definition to the runnable set.
            result.entries.append((definition, hashlib.sha256(raw).hexdigest(), tier))
        except (ValueError, yaml.YAMLError, OSError, RecursionError) as exc:
            _reject(result, path, tier, _problems(exc))


def _admit_directory(
    result: LoopCatalogue, directory: Path, tier: str, *, optional: bool = False
) -> None:
    try:
        paths = sorted(path for path in directory.iterdir() if path.suffix == ".md")
    except FileNotFoundError as exc:
        if not optional:
            _reject(result, directory, tier, _problems(exc))
        return
    except OSError as exc:
        _reject(result, directory, tier, _problems(exc))
        return
    if not paths and not optional:
        _reject(result, directory, tier, ["no loop markdown files found"])
    _admit_files(result, paths, tier)


def catalogue(local_dir: Path) -> LoopCatalogue:
    result = LoopCatalogue()
    _admit_directory(result, BUNDLED, "official")
    _admit_directory(result, local_dir, "local", optional=True)
    return result


def validate_loop_source(path: Path) -> LoopCatalogue:
    """Local author/CI gate, using exactly the same admission checks as runtime."""
    result = LoopCatalogue()
    try:
        is_directory = stat.S_ISDIR(path.stat().st_mode)
    except OSError as exc:
        _reject(result, path, "local", _problems(exc))
        return result
    if is_directory:
        _admit_directory(result, path, "local")
    else:
        _admit_files(result, [path], "local")
    return result
