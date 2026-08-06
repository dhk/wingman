"""Whitespace-tolerant evidence matching (RFC-026).

Every model proposal in Wingman is admitted only when its quote appears
verbatim in the source text. Source texts, though, arrive hard-wrapped:
Markdown documents wrap mid-sentence, PDF extraction emits a newline per
layout line, corpus exports carry their editor's wrapping. A model reads
the document logically — it quotes the sentence, not the line breaks — so
byte-for-byte containment rejects honest quotes whenever they span a wrap.

Folding runs of whitespace to a single space on both sides keeps the check
deterministic and exactly as strong on content: every non-space character
must still match, in order. Only presentation — wrapping and indentation —
is forgiven.
"""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def fold_whitespace(text: str) -> str:
    """Collapse every whitespace run to a single space and trim the ends."""
    return _WHITESPACE.sub(" ", text).strip()


def locate_quote(quote: str, source: str) -> str | None:
    """The SOURCE's own text for `quote`, matched ignoring whitespace entirely.

    Folding runs of whitespace (above) forgives wrapping and indentation,
    which is one step short of what real documents need: a PDF whose fonts
    carry no explicit space glyphs extracts as 'HeadofDataScience2021–2024',
    and no amount of run-folding can *insert* the spaces that were never
    there. The model reads it as words and quotes it as words, and an honest
    quote of a real line gets rejected (#278).

    Ignoring whitespace on both sides costs nothing in strength: every
    non-space character must still appear, in the same order. That is the
    guarantee RFC-026 is actually making — only presentation is forgiven.

    Returns the matching span **as the source spells it**, so the stored
    citation quotes the document rather than the model's readable
    reconstruction of it — with whitespace runs folded, because PDF layout
    extraction pads with columns of spaces and a faithful span would render
    as 'Head  of Data   Science                    2021–2024'. Folding keeps
    every source character, in order, and is the same forgiveness this
    module already grants. None when the characters genuinely aren't there,
    which is the invented-evidence case the check exists to catch.
    """
    if not quote.strip():
        return None
    offsets = [index for index, character in enumerate(source) if not character.isspace()]
    haystack = "".join(source[index] for index in offsets)
    needle = "".join(quote.split())
    position = haystack.find(needle)
    if position < 0:
        return None
    start = offsets[position]
    end = offsets[position + len(needle) - 1] + 1
    return fold_whitespace(source[start:end])
