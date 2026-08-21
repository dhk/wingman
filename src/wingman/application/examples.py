"""Saving and reading judged examples (#438).

The capture half, and for now the only half: this stores what the user
thought of a document and hands it back on request. Nothing here feeds
into generation — see `domain/examples.py` for why that is deliberate.

The one rule capture enforces is the reason. Everything else about this
module is permissive on purpose: any kind, any length, wingman's work or
somebody else's. But a save without a reason is refused, because an
example nobody can explain later is a file, not a lesson.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.domain.examples import Example, ExampleAuthor, ExampleVerdict
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.examples")

#: Enough of a document to recognise it in a list without printing all of it.
_PREVIEW_CHARS = 160


def _require_choice[Choice: (ExampleVerdict, ExampleAuthor)](
    value: str, options: type[Choice], label: str
) -> Choice:
    """A bad verdict or author names the valid ones rather than just failing."""
    cleaned = value.strip().lower()
    try:
        return options(cleaned)
    except ValueError as exc:
        known = ", ".join(sorted(member.value for member in options))
        raise IngestError(f"unknown {label} {value!r} — expected one of: {known}") from exc


def save_example(
    text: str,
    verdict: str,
    kind: str,
    reason: str,
    storage: Storage,
    author: str = "wingman",
    source: str = "",
) -> Example:
    """Store one judged document.

    Refuses three things and nothing else: an empty document, an empty
    kind, and an empty reason. The reason is the one that will feel like
    friction at capture time and the one that makes the corpus worth
    having — "bad" is not a lesson, "bad, it over-claims seniority" is.
    """
    if not text.strip():
        raise IngestError("there is nothing to save — the example text is empty.")
    if not kind.strip():
        raise IngestError(
            "an example needs a kind — what sort of document is this an example of? "
            "(cover letter, briefing, POV, outreach message, ...)"
        )
    if not reason.strip():
        raise IngestError(
            "an example needs a reason, good or bad — what made it good, or what is wrong "
            "with it? A bare verdict teaches nothing later."
        )
    example = Example(
        verdict=_require_choice(verdict, ExampleVerdict, "verdict"),
        kind=kind,
        reason=reason,
        text=text,
        author=_require_choice(author, ExampleAuthor, "author"),
        source=source.strip(),
    )
    storage.add_example(example)
    # Never the document itself: an example can be a cover letter, which is
    # about as personal as this workspace gets.
    _logger.info(
        "example saved verdict=%s kind=%s author=%s", example.verdict, example.kind, example.author
    )
    return example


def list_examples(
    storage: Storage, kind: str = "", verdict: str = "", contains: str = ""
) -> list[Example]:
    """Newest first, optionally filtered by kind, verdict, and free text.

    'contains' is a plain case-insensitive substring match over the
    document, its reason and its kind — deliberately not the semantic
    search the rest of the workspace uses, because an example corpus is
    small and the question here is "where's that one I saved", not "what
    is like this".
    """
    examples = storage.list_examples(kind=kind, verdict=verdict)
    needle = contains.strip().lower()
    if not needle:
        return examples
    return [
        example
        for example in examples
        if needle in example.text.lower()
        or needle in example.reason.lower()
        or needle in example.kind.lower()
    ]


def find_example(example_id_prefix: str, storage: Storage) -> Example:
    """One example by id prefix — ids are shown truncated, so they are
    typed truncated."""
    prefix = example_id_prefix.strip().lower()
    if not prefix:
        raise IngestError("which example? Give the id shown by 'list'.")
    matches = [e for e in storage.list_examples() if e.example_id.lower().startswith(prefix)]
    if not matches:
        raise IngestError(f"no example whose id starts with {example_id_prefix!r}.")
    if len(matches) > 1:
        raise IngestError(
            f"{len(matches)} examples start with {example_id_prefix!r} — give more of the id."
        )
    return matches[0]


def remove_example(example_id_prefix: str, storage: Storage) -> Example:
    example = find_example(example_id_prefix, storage)
    storage.delete_example(example.example_id)
    _logger.info("example removed kind=%s", example.kind)
    return example


def example_kinds(storage: Storage) -> list[tuple[str, int]]:
    return storage.example_kinds()


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _PREVIEW_CHARS else f"{flat[:_PREVIEW_CHARS]}..."


def render_examples(examples: list[Example]) -> str:
    if not examples:
        return "No examples saved yet."
    lines = [f"{len(examples)} example(s), newest first:", ""]
    for example in examples:
        lines.append(
            f"[{example.verdict.upper():4s}] {example.kind}  "
            f"({example.author}, {example.saved_at.date().isoformat()})  "
            f"id {example.example_id[:8]}"
        )
        lines.append(f"    why: {example.reason}")
        if example.source:
            lines.append(f"    from: {example.source}")
        lines.append(f"    {_preview(example.text)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_example(example: Example) -> str:
    """One example in full — the whole document, not a preview."""
    lines = [
        f"[{example.verdict.upper()}] {example.kind}",
        f"why:     {example.reason}",
        f"author:  {example.author}",
        f"saved:   {example.saved_at.isoformat()}",
    ]
    if example.source:
        lines.append(f"from:    {example.source}")
    lines.extend([f"id:      {example.example_id}", "", example.text])
    return "\n".join(lines)


def render_kinds(kinds: list[tuple[str, int]]) -> str:
    if not kinds:
        return "No examples saved yet, so no kinds."
    lines = ["Kinds in use, commonest first:", ""]
    lines.extend(f"  {count:4d}  {kind}" for kind, count in kinds)
    lines.append("")
    lines.append("Kinds are free text — near-duplicates here mean the vocabulary is drifting.")
    return "\n".join(lines)
