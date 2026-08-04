"""Regression test for #202: changelog_data.py must not silently drift.

Runs against THIS checkout's real git history — skipped when none is
available (an installed build has no .git, per RFC-038/domain/changelog.py's
own documented constraint). In CI and any dev clone, both are present, so
this is the automated backstop for the manual regeneration step.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GIT_AVAILABLE = (_REPO_ROOT / ".git").exists()

pytestmark = pytest.mark.skipif(
    not _GIT_AVAILABLE, reason="no .git directory here (running from an installed build)"
)


def _load_generator():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    import generate_changelog  # noqa: PLC0415

    return generate_changelog


def test_changelog_data_matches_a_regeneration_at_its_own_stamped_commit() -> None:
    """Self-consistency: regenerating AT the commit changelog_data.py claims
    to be generated from must reproduce it exactly. Catches hand-edits or a
    corrupted/partial write — not staleness (see the test below for that)."""
    from wingman.changelog_data import CHANGELOG_DATA, GENERATED_FROM_COMMIT

    generator = _load_generator()
    live = tuple(generator._entries(GENERATED_FROM_COMMIT))
    assert CHANGELOG_DATA == live, (
        "src/wingman/changelog_data.py doesn't match a fresh regeneration at its "
        "own GENERATED_FROM_COMMIT — was it hand-edited? Run "
        "'python scripts/generate_changelog.py' and commit the result (#202)."
    )


def test_changelog_data_is_not_far_behind_head() -> None:
    """The actual #202 bug: catches drift of many commits, not the harmless
    one-commit lag every regeneration has (it can't include the commit that
    adds it). A generous-but-real ceiling — 'wingman-ctl upgrade' keeps this
    near 0-1 in practice; this is the backstop for whenever that doesn't run."""
    from wingman.changelog_data import GENERATED_FROM_COMMIT

    gap = subprocess.run(
        ["git", "rev-list", "--count", f"{GENERATED_FROM_COMMIT}..HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(gap) <= 15, (
        f"changelog_data.py is {gap} commits behind HEAD (#202's original bug was "
        "61) — run 'python scripts/generate_changelog.py' and commit the result."
    )
