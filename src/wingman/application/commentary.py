"""The commentary corpus (RFC-058, issue #339): save the assistant's
reading, keep it out of every evidence path.

"Save that reading. It's really helpful. I need to remember that." — and
there was nowhere to put it, because every capture surface here stores the
USER's material. This module is that place. What makes it safe is not a
disclaimer in the text (an embedding cannot read a disclaimer) but where
the bytes live: `commentary_entries` is its own table, with no FTS index
joined to the corpus index and no `ProfileItemKind`, so POV synthesis,
outreach briefs, fit assessment, `evidence`, workspace `search` and every
profile render keep working exactly as before and cannot see it. Code that
does not know about this store cannot read from it — the exclusion fails
closed instead of depending on every existing filter remembering a new
kind.

Two disciplines survive from the evidence side, because they are about
honesty rather than about whose words these are:

* Authorship is structural — model, prompt version, date — recorded on
  every entry, so a reading can never be quoted back as the user's claim.
* References point at the material the reading was drawn from, validated
  at save time against the workspace's real ids. A reading that cites
  nothing checkable is exactly the unauditable assertion this codebase
  refuses everywhere else.

The confirmation gate (BP-06 echo-before-save) is a docstring protocol on
the MCP tool, the same way `qa_capture`, `interview_react` and
`resolve_requirement` enforce theirs — the store cannot tell a confirmed
save from an unconfirmed one, and pretending otherwise with a `confirmed`
flag would only teach callers to pass True.
"""

from __future__ import annotations

import re

from wingman.application.ingest import IngestError
from wingman.domain.commentary import (
    COMMENTARY_BANNER,
    NO_PROMPT_VERSION,
    UNNAMED_MODEL,
    CommentaryEntry,
    CommentaryReference,
)
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.commentary")

_SNIPPET_CHARS = 400


def _tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", query.lower()) if token]


def _candidates(storage: Storage) -> list[tuple[str, str, str]]:
    """Every id in the workspace a reading may cite: (id, kind, label)."""
    rows: list[tuple[str, str, str]] = [
        (item.item_id, "profile-item", item.name) for item in storage.list_profile_items()
    ]
    rows.extend(
        (document.doc_id, "corpus-document", document.title)
        for document in storage.list_corpus_documents()
    )
    rows.extend(
        (document.doc_id, "external-document", document.title)
        for document in storage.list_external_documents()
    )
    rows.extend((record.answer_id, "answer", record.question) for record in storage.list_answers())
    return rows


def _resolve_reference(raw: str, storage: Storage) -> CommentaryReference:
    """One cited id (or a prefix of one) as a stored reference.

    Unknown ids are refused rather than stored: a reading whose citations
    point at nothing cannot be checked against the material, which is the
    only thing that makes commentary auditable at all.
    """
    prefix = raw.strip()
    if not prefix:
        raise IngestError("a reference is empty; drop it or give a real id.")
    matches = [row for row in _candidates(storage) if row[0].startswith(prefix)]
    if not matches:
        raise IngestError(
            f"nothing in this workspace has id {prefix!r} — a reading cites the material "
            "it was drawn from (profile item, corpus document, watched person's document, "
            "or banked answer). Check 'wingman profile list' / 'wingman corpus list', or "
            "save with no references."
        )
    if len({row[0] for row in matches}) > 1:
        shorts = ", ".join(sorted(row[0][:8] for row in matches))
        raise IngestError(f"reference {prefix!r} is ambiguous ({shorts}); use more characters.")
    ref_id, kind, label = matches[0]
    return CommentaryReference(ref_id=ref_id, kind=kind, label=label)


def save_commentary(
    text: str,
    storage: Storage,
    *,
    topic: str = "",
    model: str = "",
    prompt_version: str = "",
    drawn_from: list[str] | None = None,
) -> CommentaryEntry:
    """Store one reading with explicit authorship. Never becomes evidence.

    `model` names the model that wrote it (blank records "unnamed model" —
    still not the user); `prompt_version` is the versioned prompt behind it,
    or "none" when a conversation produced it. `drawn_from` is a list of
    workspace ids (full or prefix) the reading was drawn from; every one is
    resolved here and refused if it matches nothing.
    """
    body = text.strip()
    if not body:
        raise IngestError("the commentary text is empty — nothing was saved.")
    references = [_resolve_reference(raw, storage) for raw in (drawn_from or []) if raw.strip()]
    entry = CommentaryEntry(
        text=body,
        topic=topic.strip(),
        author_model=model.strip() or UNNAMED_MODEL,
        prompt_version=prompt_version.strip() or NO_PROMPT_VERSION,
        drawn_from=references,
    )
    storage.add_commentary_entry(entry)
    _logger.info(
        "commentary saved id=%s model=%s prompt=%s refs=%d",
        entry.entry_id,
        entry.author_model,
        entry.prompt_version,
        len(references),
    )
    return entry


def list_commentary(storage: Storage) -> list[CommentaryEntry]:
    """Every stored reading, newest first."""
    return sorted(storage.list_commentary_entries(), key=lambda e: e.created_at, reverse=True)


def find_commentary(query: str, storage: Storage, limit: int = 10) -> list[CommentaryEntry]:
    """Readings whose text or topic contains every word of the query.

    Deliberately its own retrieval rather than a column in workspace
    `search`: search results are read back by a model that may be about to
    persist one (`resolve_requirement` recalls, then offers `qa_capture`),
    and a commentary hit arriving in that stream is one accepted suggestion
    away from being filed as the user's own answer.
    """
    tokens = _tokens(query)
    if not tokens:
        raise IngestError("the commentary query has no searchable words.")
    hits = [
        entry
        for entry in list_commentary(storage)
        if all(token in f"{entry.text} {entry.topic}".lower() for token in tokens)
    ]
    return hits[:limit]


def get_commentary(entry_id_prefix: str, storage: Storage) -> CommentaryEntry:
    prefix = entry_id_prefix.strip()
    if not prefix:
        raise IngestError("entry id is empty; see 'wingman commentary list' for ids.")
    matches = [entry for entry in list_commentary(storage) if entry.entry_id.startswith(prefix)]
    if not matches:
        raise IngestError(f"no commentary entry with id {prefix!r}; see 'wingman commentary list'.")
    if len(matches) > 1:
        shorts = ", ".join(entry.entry_id[:8] for entry in matches)
        raise IngestError(f"id {prefix!r} is ambiguous ({shorts}); use more characters.")
    return matches[0]


def remove_commentary(entry_id_prefix: str, storage: Storage) -> CommentaryEntry:
    """Delete one reading — reviewable and deletable like any stored item."""
    entry = get_commentary(entry_id_prefix, storage)
    storage.delete_commentary_entry(entry.entry_id)
    _logger.info("commentary removed id=%s", entry.entry_id)
    return entry


def _reference_lines(entry: CommentaryEntry, storage: Storage | None) -> list[str]:
    lines: list[str] = []
    for reference in entry.drawn_from:
        gone = ""
        if storage is not None and not any(
            row[0] == reference.ref_id for row in _candidates(storage)
        ):
            gone = " — no longer in the workspace"
        lines.append(
            f"    drawn from [{reference.kind} {reference.ref_id[:8]}] {reference.label}{gone}"
        )
    return lines


def render_commentary_entry(
    entry: CommentaryEntry, storage: Storage | None = None, full: bool = True
) -> str:
    """One entry, always under its authorship line — never as a bare quote."""
    text = entry.text
    if not full and len(text) > _SNIPPET_CHARS:
        text = text[: _SNIPPET_CHARS - 1] + "…"
    topic = f" {entry.topic}" if entry.topic else ""
    lines = [
        f"[{entry.entry_id[:8]}]{topic}",
        f"    {entry.attribution()}",
        *(f"    {line}" for line in text.splitlines()),
        *_reference_lines(entry, storage),
    ]
    return "\n".join(lines)


def render_commentary(
    entries: list[CommentaryEntry], storage: Storage | None = None, full: bool = False
) -> str:
    if not entries:
        return (
            "No commentary saved yet. When an assistant's reading of your material is worth "
            "keeping, save it here — 'wingman commentary save' / the 'commentary' tool — so "
            "it is never mistaken for something you said."
        )
    blocks = [render_commentary_entry(entry, storage, full=full) for entry in entries]
    return "\n\n".join(
        [COMMENTARY_BANNER, *blocks, f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}."]
    )
