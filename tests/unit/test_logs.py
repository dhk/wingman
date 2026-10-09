from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterator

import pytest
from rich.console import Console
from rich.logging import RichHandler

from wingman.infrastructure.logs import configure_logging


@pytest.fixture
def isolated_root() -> Iterator[logging.Logger]:
    """Give each test the root logger the way FastMCP leaves it at import:
    one RichHandler. Restore whatever pytest had afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    rich_out = io.StringIO()
    root.handlers = [RichHandler(console=Console(file=rich_out, width=80))]
    root.setLevel(logging.INFO)
    try:
        yield root
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


LONG = (
    "bearer verified but unprovisioned: sub=user_EXAMPLE0000000000000000 "
    "and enough further words to wrap any eighty-column console twice over"
)


def test_under_journald_a_long_event_is_exactly_one_line(
    isolated_root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = io.StringIO()  # not a TTY, like journald's stderr
    monkeypatch.setattr(sys, "stderr", journal)

    configure_logging()
    logging.getLogger("wingman.infrastructure.oauth_bearer").info(LONG)

    lines = journal.getvalue().splitlines()
    assert len(lines) == 1, journal.getvalue()
    assert "logger=wingman.infrastructure.oauth_bearer" in lines[0]
    assert LONG in lines[0]
    assert not any(isinstance(h, RichHandler) for h in isolated_root.handlers)


def test_a_traceback_stays_on_the_same_line(
    isolated_root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = io.StringIO()
    monkeypatch.setattr(sys, "stderr", journal)
    configure_logging()

    try:
        raise ValueError("first\nsecond")
    except ValueError:
        logging.getLogger("wingman.test").exception("it failed")

    assert len(journal.getvalue().splitlines()) == 1
    assert "ValueError" in journal.getvalue()


def test_an_interactive_terminal_keeps_rich(
    isolated_root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stderr", Terminal())
    configure_logging()

    assert any(isinstance(h, RichHandler) for h in isolated_root.handlers)


def test_handlers_that_are_not_rich_are_left_alone(
    isolated_root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = logging.StreamHandler(io.StringIO())
    isolated_root.addHandler(other)

    monkeypatch.setattr(sys, "stderr", io.StringIO())
    configure_logging()

    assert other in isolated_root.handlers
