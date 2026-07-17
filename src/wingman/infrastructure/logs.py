"""Structured logging setup: stdlib logging in key-value format."""

from __future__ import annotations

import logging

APP_LOGGER = "wingman"
_FORMAT = "ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once, in key-value form. Safe to call repeatedly."""
    logging.basicConfig(format=_FORMAT, level=level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{APP_LOGGER}.{name}")
