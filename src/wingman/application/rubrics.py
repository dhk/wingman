"""Loading rubrics (docs/QUESTION-BLOCKS-DESIGN.md, issue #436).

Rubrics ship as TOML data files inside the package
(`wingman/rubrics/*.toml`), read through `importlib.resources` — the same
packaged-data pattern `wingman/prompts/*.md` already uses, so a rubric
survives a wheel install and needs no path configuration.

**Packaged only, in this slice.** Q5 of the design doc — whether rubrics
should also live in the workspace, editable per tenant — is open, and it
immediately raises what a coached persona's rubrics are and who may write
them (docs/COACHING-MODE-DESIGN.md). One shipped rubric is enough for v0
and settles nothing prematurely; `load_rubric` is the single seam a
workspace lookup would be added to.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files

from pydantic import ValidationError

from wingman.domain.rubric import Rubric
from wingman.infrastructure.logs import get_logger

_logger = get_logger("application.rubrics")

RUBRIC_PACKAGE = "wingman.rubrics"


class RubricError(Exception):
    """A rubric could not be found, parsed, or validated."""


def _parse(raw: str, rubric_id: str) -> Rubric:
    """One rubric file's text into a validated `Rubric`.

    The file's shape is flattened here rather than in the domain model:
    TOML expresses a list of tables as `[[dimension]]`, which reads
    naturally in the file, while the model wants `dimensions`. Keeping the
    translation in one function means the file format can change without
    the domain following it.
    """
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise RubricError(f"rubric {rubric_id!r} is not valid TOML ({exc}).") from exc

    header = data.get("rubric")
    if not isinstance(header, dict):
        raise RubricError(f"rubric {rubric_id!r} has no [rubric] table.")

    payload = {
        **{key: value for key, value in header.items() if key != "provenance"},
        "provenance": header.get("provenance", {}),
        "dimensions": data.get("dimension", []),
    }
    try:
        rubric = Rubric.model_validate(payload)
    except ValidationError as exc:
        raise RubricError(f"rubric {rubric_id!r} is malformed: {exc}.") from exc

    if rubric.id != rubric_id:
        raise RubricError(
            f"rubric file {rubric_id}.toml declares id {rubric.id!r}; "
            "the filename and the declared id must match."
        )
    return rubric


def list_rubric_ids() -> list[str]:
    """Every packaged rubric's id, sorted. Reads filenames only — a rubric
    that fails to parse still appears here, so a broken file is visible as
    a broken file rather than as an absence."""
    return sorted(
        entry.name.removesuffix(".toml")
        for entry in files(RUBRIC_PACKAGE).iterdir()
        if entry.name.endswith(".toml")
    )


def load_rubric(rubric_id: str) -> Rubric:
    """One packaged rubric by id, validated.

    Raises `RubricError` naming what is available when the id is unknown —
    a caller mistyping a rubric id should not have to go and read the
    package to find out what it should have said.
    """
    rubric_id = rubric_id.strip()
    if not rubric_id:
        raise RubricError("a rubric id is required.")
    available = list_rubric_ids()
    if rubric_id not in available:
        raise RubricError(
            f"unknown rubric {rubric_id!r}; available: {', '.join(available) or 'none'}."
        )
    raw = files(RUBRIC_PACKAGE).joinpath(f"{rubric_id}.toml").read_text(encoding="utf-8")
    rubric = _parse(raw, rubric_id)
    _logger.info(
        "rubric_loaded id=%s dimensions=%d tier=%s",
        rubric.id,
        len(rubric.dimensions),
        rubric.provenance.tier.value,
    )
    return rubric


def resolve_rubric_id(requested: str = "") -> str:
    """The rubric id to use when the caller did not name one.

    An empty request resolves to the only packaged rubric when there is
    exactly one, and is an error naming the choices as soon as there are
    two. Picking a default alphabetically the moment a second rubric ships
    would silently change what every existing caller measures against —
    the one thing a rubric must never do quietly.
    """
    requested = requested.strip()
    if requested:
        return requested
    available = list_rubric_ids()
    if len(available) == 1:
        return available[0]
    if not available:
        raise RubricError("no rubrics are packaged with this build.")
    raise RubricError(f"more than one rubric is available; name one of: {', '.join(available)}.")


def load_all_rubrics() -> list[Rubric]:
    """Every packaged rubric that parses, sorted by id. A malformed file is
    logged and skipped rather than taking down a listing — the same
    "degrade visibly, never silently" posture the rest of this codebase
    takes with partial data."""
    loaded: list[Rubric] = []
    for rubric_id in list_rubric_ids():
        try:
            loaded.append(load_rubric(rubric_id))
        except RubricError as exc:  # noqa: PERF203 — one bad file must not sink the listing
            _logger.warning("rubric_skipped id=%s error=%s", rubric_id, exc)
    return loaded
