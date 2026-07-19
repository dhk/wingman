"""The installed build's version, derived from git at install time (hatch-vcs).

On a tag: '0.3.0'. Past a tag: '0.3.1.dev12+g8dd341a' — tag, commits since,
and the exact commit, stamped when 'uv tool install' ran. The one honest
answer to "what code am I running": it changes only when the install does,
which is exactly the property a staleness check needs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def wingman_version() -> str:
    try:
        return version("wingman")
    except PackageNotFoundError:  # pragma: no cover — running from raw sources
        return "unknown (not installed)"
