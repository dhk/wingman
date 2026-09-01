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

# How far the committed data may fall behind HEAD before CI fails. Generous
# but real: 'wingman-ctl upgrade' keeps it near 0-1. Paired with
# domain/changelog.py's _LAG_ALLOWANCE — see the test at the bottom of this
# file for why the two must be read together.
_CI_CEILING = 15

pytestmark = pytest.mark.skipif(
    not _GIT_AVAILABLE, reason="no .git directory here (running from an installed build)"
)


def _assert_stamp_is_reachable(stamp: str) -> None:
    """A dangling GENERATED_FROM_COMMIT must fail as itself, not as a crash.

    Both tests below hand the stamp to git. When it names a commit this
    clone does not have, git exits 128 and `check=True` raises
    CalledProcessError — which surfaces as a stack trace about subprocess
    and reads like a broken test harness, in every PR at once, with no hint
    that a stamp is the problem. Diagnosing it from that output cost a
    session twice (#513, #519).

    The stamp goes dangling when a changelog regeneration runs on a feature
    branch: it used to stamp the branch head, and GitHub's squash merge
    discards that commit. `generate_changelog._stamp_commit` stamps the
    mainline merge-base now, which cannot dangle — this check is the
    backstop for a stamp that predates that fix or was written by hand.
    """
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{stamp}^{{commit}}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert exists.returncode == 0, (
        f"GENERATED_FROM_COMMIT {stamp} is not a commit in this clone. It was almost "
        "certainly stamped on a feature branch whose head the squash merge discarded "
        "(#513, #519) — regenerate on an up-to-date main with "
        "'python scripts/generate_changelog.py' and commit the result."
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

    _assert_stamp_is_reachable(GENERATED_FROM_COMMIT)
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

    _assert_stamp_is_reachable(GENERATED_FROM_COMMIT)
    gap = subprocess.run(
        ["git", "rev-list", "--count", f"{GENERATED_FROM_COMMIT}..HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(gap) <= _CI_CEILING, (
        f"changelog_data.py is {gap} commits behind HEAD (#202's original bug was "
        "61) — run 'python scripts/generate_changelog.py' and commit the result."
    )


def test_the_ci_ceiling_and_the_runtime_allowance_stay_in_view_of_each_other() -> None:
    """These two numbers answer the same question at different moments, and
    they once disagreed: this file called a one-commit lag harmless while
    staleness_note warned about it, which is what surfaced the freshness bug
    (#228 review). The ceiling must stay the looser of the two, or CI would
    pass data the running tool immediately calls stale."""
    from wingman.domain.changelog import _LAG_ALLOWANCE

    assert _LAG_ALLOWANCE == 1, "one commit: the regeneration's own commit"
    assert _CI_CEILING > _LAG_ALLOWANCE


def test_this_checkout_does_not_warn_about_itself() -> None:
    """End to end, against the real committed stamp: a checkout whose data was
    just regenerated must be silent. The old check could not satisfy this at
    all."""
    from wingman.changelog_data import GENERATED_FROM_DISTANCE
    from wingman.domain.changelog import staleness_note

    assert GENERATED_FROM_DISTANCE >= 0, "the generator could not stamp a distance"
    # The build that contains this file is one commit past what it stamped.
    assert staleness_note(f"0.4.1.dev{GENERATED_FROM_DISTANCE + 1}+gabc123def") is None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_stamp_is_a_mainline_commit_not_the_feature_branch_head(tmp_path: Path) -> None:
    """The trap that broke main twice (#513, #519), in a throwaway repo.

    Regenerating while a feature branch is checked out must stamp the
    branch's merge-base with the mainline, never the branch head. GitHub's
    squash merge discards the branch head, so a stamp naming it dangles the
    instant the PR lands — and the resulting exit-128 crash lands on every
    open PR at once, pointing at subprocess rather than at the changelog.

    Asserted against a repository built here rather than against this one,
    whose history changes with every merge.
    """
    generator = _load_generator()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "f").write_text("one", encoding="utf-8")
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", "first: a merged PR (#1)")
    mainline = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "feature")
    (repo / "f").write_text("two", encoding="utf-8")
    _git(repo, "commit", "-am", "wip: doomed by the squash")
    branch_head = _git(repo, "rev-parse", "HEAD")

    assert branch_head != mainline
    assert generator._stamp_commit(repo) == mainline


def test_the_stamp_falls_back_to_head_when_there_is_no_mainline(tmp_path: Path) -> None:
    """A build box or a clone with no 'main' still gets a stamp — the old
    behaviour, kept deliberately: no mainline to be safe against means HEAD
    is the best answer available, and refusing to stamp would be worse."""
    generator = _load_generator()
    repo = tmp_path / "orphan"
    repo.mkdir()
    _git(repo, "init", "-b", "release")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "f").write_text("one", encoding="utf-8")
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", "only: a commit (#1)")

    assert generator._stamp_commit(repo) == _git(repo, "rev-parse", "HEAD")
