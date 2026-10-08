"""Structured logging setup: stdlib logging in key-value format."""

from __future__ import annotations

import logging
import sys

APP_LOGGER = "wingman"
_FORMAT = "ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"


class _OneLineFormatter(logging.Formatter):
    """journald stores one entry per line: a traceback or a message with a
    newline in it would otherwise become several unrelated-looking entries,
    and grep finds only the first."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace("\r", "\\r").replace("\n", "\\n")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once, in key-value form. Safe to call repeatedly.

    FastMCP installs a RichHandler on the root logger when wingman.mcp_server
    is imported, which turns basicConfig below into a no-op. Rich wraps long
    messages at the console width, so under journald (stderr not a TTY) one
    event became several lines and 'grep unprovisioned' found nothing. When
    stderr is not a terminal, Rich is replaced by the single-line key-value
    format; an interactive terminal keeps Rich.
    """
    root = logging.getLogger()
    if not _is_terminal(sys.stderr):
        rich = [h for h in root.handlers if type(h).__module__.startswith("rich.")]
        for handler in rich:
            root.removeHandler(handler)
        if rich:
            plain = logging.StreamHandler(sys.stderr)
            plain.setFormatter(_OneLineFormatter(_FORMAT))
            root.addHandler(plain)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            _OneLineFormatter(_FORMAT)
            if not _is_terminal(sys.stderr)
            else logging.Formatter(_FORMAT)
        )
        root.addHandler(handler)
        root.setLevel(level)


def _is_terminal(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except (OSError, ValueError):
        return False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{APP_LOGGER}.{name}")
