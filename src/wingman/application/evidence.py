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
