#!/usr/bin/env python3
"""Regenerate src/wingman/changelog_data.py from this checkout's git history.

Run by hand before a release (docs/RELEASES.md), automatically as part of
`wingman-ctl upgrade`/`cycle` (#202), and checked for drift by a CI test
(`tests/unit/test_changelog_freshness.py`) — NOT invoked at MCP-server
runtime. See RFC-038 / src/wingman/domain/changelog.py: the running server
cannot rely on live git access (`uv tool install` never leaves a `.git`
directory behind, verified against a real installed instance — true even
for the "install from a local checkout" path, since it builds and installs
a wheel), so this script bakes merged-PR titles into a static, committed
data module that ships as ordinary package source. It also stamps the
exact commit it ran at (`GENERATED_FROM_COMMIT`) so the running build can
tell, at runtime, whether its own copy of this data is known to be behind
(#202 — a silent, confidently-wrong changelog is worse than a missing one).

Walks `git log --first-parent` on the current branch: `--first-parent`
keeps exactly one entry per merged PR (the mainline commit) and skips the
individual feature-branch commits a merge or squash absorbs. Two GitHub
merge shapes both appear in this repo's history and are both handled:

  - squash merge: subject ends in " (#123)" -> title is everything before it
  - true merge commit: subject is "Merge pull request #123 from ..." -> the
    PR title is the commit body's first line (GitHub's own convention)

Commits matching neither (a handful of very early direct-to-main commits,
from before this repo settled on a PR-per-slice workflow) are skipped:
they carry no PR number, so there is no merged PR to cite.

Usage: python scripts/generate_changelog.py
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SQUASH_RE = re.compile(r"^(.*) \(#(\d+)\)$")
_MERGE_RE = re.compile(r"^Merge pull request #(\d+) from")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT = _REPO_ROOT / "src" / "wingman" / "changelog_data.py"

# Record separator \x1e / field separator \x1f: both are control bytes that
# never appear in a commit message, so splitting is unambiguous even when
# a PR body itself contains blank lines or punctuation.
_LOG_FORMAT = "%H%x1f%cI%x1f%s%x1f%b%x1e"


def _head_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


#: Where the mainline lives, best guess first. 'origin/main' is what a clone
#: has; a bare 'main' covers a checkout with no remote configured.
_MAINLINE_REFS = ("origin/main", "main")


def _stamp_commit(repo: Path = _REPO_ROOT) -> str:
    """The commit to stamp: HEAD's newest ancestor that is also on the mainline.

    NOT HEAD, and that distinction is the whole point of this function.
    Regenerating on a feature branch used to stamp the branch's own head —
    a commit GitHub's squash merge DISCARDS, so the moment the PR landed,
    'GENERATED_FROM_COMMIT' named a sha that exists in nobody's clone of
    main. Both freshness tests then died with 'git ... returned non-zero
    exit status 128' on every subsequent PR, not just the guilty one, and
    the failure reads like a broken harness rather than a bad stamp. This
    happened twice in one week (#513, #519) before anyone named the trap,
    because each occurrence looks like a fresh mystery.

    The merge-base is immune: it is already ON the mainline, so it survives
    whatever shape the merge takes, and it is the honest answer anyway —
    a branch's own commits are not merged PRs and contribute no entries.
    On an up-to-date main the merge-base IS head, so the ordinary
    regenerate-then-commit flow is unchanged.

    Falls back to HEAD when no mainline ref can be found (a detached build
    box, a clone with no remote), which is the historical behaviour and no
    worse than it was.

    'repo' is injectable so a test can build the squash-merge shape in a
    throwaway repository rather than asserting against this one's history,
    which changes with every merge.
    """
    for ref in _MAINLINE_REFS:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", ref],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _entries(ref: str = "HEAD") -> list[tuple[str, int, str]]:
    raw = subprocess.run(
        ["git", "log", "--first-parent", f"--format={_LOG_FORMAT}", ref],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    entries: list[tuple[str, int, str]] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        _sha, commit_date, subject, body = record.split("\x1f", 3)
        date = commit_date[:10]  # commit date's day, our PR-merge-date proxy
        squash = _SQUASH_RE.match(subject)
        if squash:
            entries.append((date, int(squash.group(2)), squash.group(1)))
            continue
        merge = _MERGE_RE.match(subject)
        if merge:
            body_lines = [line for line in body.strip().splitlines() if line.strip()]
            if body_lines:
                entries.append((date, int(merge.group(1)), body_lines[0].strip()))
            continue
    return entries


def _generated_from_distance(head: str) -> int:
    """How many commits `head` is past the most recent tag, or -1 if unknown.

    Stamped alongside the commit sha because the sha alone cannot answer
    "is this data fresh". Generating writes changelog_data.py and
    committing it produces a DIFFERENT commit, so a stamped sha can never
    equal the sha of any build that contains it — the equality check was
    unsatisfiable by construction, and warned after every regeneration.

    This distance does survive being committed: the build containing this
    file is simply one commit further along. It is exactly the number
    hatch-vcs puts in a version's '.devN' (verified: rev-list --count
    <tag>..<sha> equals the devN of the build made at that sha), so the
    runtime can subtract the two without needing git or the version
    machinery's own next-version guess.

    -1 means "cannot verify" — an untagged history, or no git — which the
    runtime treats as silence. A stamp we cannot stand behind is worse
    than no stamp.
    """
    try:
        tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", head],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        count = subprocess.run(
            ["git", "rev-list", "--count", f"{tag}..{head}"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return int(count)
    except (subprocess.CalledProcessError, ValueError):
        return -1


def _render(
    entries: list[tuple[str, int, str]], generated_from_commit: str, generated_from_distance: int
) -> str:
    rows = "\n".join(f"    ({date!r}, {pr}, {title!r})," for date, pr, title in entries)
    return (
        '"""Generated by scripts/generate_changelog.py — do not hand-edit (RFC-038).\n\n'
        "Regenerate before each release: `python scripts/generate_changelog.py`. Each\n"
        'row is (merge date "YYYY-MM-DD", PR number, PR title verbatim), newest\n'
        "first, derived from `git log --first-parent` on this checkout at generation\n"
        "time. See RFC-038 and src/wingman/domain/changelog.py for why this is a\n"
        "committed static file rather than a live git read.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "CHANGELOG_DATA: tuple[tuple[str, int, str], ...] = (\n"
        f"{rows}\n"
        ")\n\n"
        "# The full commit SHA this file was generated from (#202) — compared at\n"
        "# runtime against the running build's own hatch-vcs-embedded commit hash\n"
        "# (wingman.domain.changelog.staleness_note) so the tool can say when its\n"
        "# own data is known to be behind, instead of reporting a confident zero.\n"
        f"GENERATED_FROM_COMMIT = {generated_from_commit!r}\n\n"
        "# Commits past the most recent tag at that same sha (#202, #228 review).\n"
        "# The sha above is an IDENTITY — what the self-consistency test\n"
        "# regenerates against — but it cannot answer freshness: committing this\n"
        "# file changes the sha, so a stamped sha never equals the sha of any\n"
        "# build containing it, and the check warned after every regeneration.\n"
        "# This distance survives being committed and is the same number a\n"
        "# version's '.devN' carries, so staleness_note can subtract the two and\n"
        "# say how many merges are missing. -1 means 'cannot verify'.\n"
        f"GENERATED_FROM_DISTANCE = {generated_from_distance!r}\n"
    )


def main() -> None:
    stamp = _stamp_commit()
    entries = _entries(stamp)
    _OUTPUT.write_text(_render(entries, stamp, _generated_from_distance(stamp)), encoding="utf-8")
    # Long titles need Black-style wrapping to respect the repo's line length;
    # `ruff format` is the same tool the rest of the codebase is formatted with.
    subprocess.run(["ruff", "format", str(_OUTPUT)], cwd=_REPO_ROOT, check=False)
    print(f"Wrote {len(entries)} entries to {_OUTPUT.relative_to(_REPO_ROOT)} (as of {stamp[:9]})")


if __name__ == "__main__":
    main()
