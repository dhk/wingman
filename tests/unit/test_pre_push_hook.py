"""The pre-push hook that refuses a push to an already-merged branch (#182).

The failure it prevents is silent at the moment it happens and confusing
much later: a squash merge rewrites the branch's work under a new sha, so
further pushes strand commits git no longer sees as applied, and the first
symptom is '405 Pull Request has merge conflicts' on an unrelated later
merge.

Driven as a subprocess against a stub 'gh', because the only interesting
behaviour is which answers make it refuse.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "pre-push"

ZERO = "0" * 40
SHA = "a" * 40


def _stub_gh(bin_dir: Path, *, merged_pr: str | None, exit_code: int = 0) -> None:
    body = "#!/usr/bin/env bash\n"
    if merged_pr is not None:
        body += f"echo {merged_pr}\n"
    body += f"exit {exit_code}\n"
    path = bin_dir / "gh"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _push(
    bin_dir: Path, stdin: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("WINGMAN_ALLOW_MERGED_BRANCH_PUSH", None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(HOOK), "origin", "git@github.com:dhk/wingman.git"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _line(branch: str, local_sha: str = SHA) -> str:
    return f"refs/heads/{branch} {local_sha} refs/heads/{branch} {ZERO}\n"


def test_a_branch_whose_pr_already_merged_is_refused(bin_dir: Path) -> None:
    _stub_gh(bin_dir, merged_pr="179")

    result = _push(bin_dir, _line("fix/something"))

    assert result.returncode == 1
    assert "REFUSED" in result.stderr
    assert "#179" in result.stderr


def test_the_refusal_explains_the_recovery_not_just_the_rule(bin_dir: Path) -> None:
    """The whole point is that the downstream symptom names neither the
    cause nor the fix. A refusal that did the same would be no better."""
    _stub_gh(bin_dir, merged_pr="179")

    stderr = _push(bin_dir, _line("fix/something")).stderr

    assert "git checkout -b" in stderr
    assert "git rebase --onto origin/main" in stderr
    assert "405" in stderr  # names the confusing symptom it is preventing


def test_a_branch_with_no_merged_pr_is_allowed(bin_dir: Path) -> None:
    _stub_gh(bin_dir, merged_pr=None)

    assert _push(bin_dir, _line("fix/in-progress")).returncode == 0


def test_a_deletion_push_is_always_allowed(bin_dir: Path) -> None:
    """An all-zero local sha deletes the branch — exactly the cleanup this
    hook wants to encourage, so refusing it would be backwards."""
    _stub_gh(bin_dir, merged_pr="179")

    assert _push(bin_dir, _line("fix/something", local_sha=ZERO)).returncode == 0


def test_main_is_never_checked(bin_dir: Path) -> None:
    _stub_gh(bin_dir, merged_pr="179")

    assert _push(bin_dir, _line("main")).returncode == 0
    assert _push(bin_dir, _line("master")).returncode == 0


def test_a_missing_gh_never_blocks_a_push(bin_dir: Path) -> None:
    """A hook that stops work when a tool is absent is a worse failure than
    the one it prevents. It refuses only on a positive answer."""
    # bash by absolute path, so emptying PATH hides gh without hiding the
    # interpreter too.
    bash = shutil.which("bash")
    assert bash
    result = subprocess.run(
        [bash, str(HOOK), "origin", "url"],
        input=_line("fix/something"),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(bin_dir)},
        check=False,
    )

    assert result.returncode == 0


def test_gh_failing_or_unauthenticated_never_blocks_a_push(bin_dir: Path) -> None:
    _stub_gh(bin_dir, merged_pr=None, exit_code=1)

    assert _push(bin_dir, _line("fix/something")).returncode == 0


def test_the_escape_hatch_works(bin_dir: Path) -> None:
    _stub_gh(bin_dir, merged_pr="179")

    result = _push(bin_dir, _line("fix/something"), {"WINGMAN_ALLOW_MERGED_BRANCH_PUSH": "1"})

    assert result.returncode == 0


def test_one_bad_ref_among_several_still_refuses(bin_dir: Path) -> None:
    """git can push several refs at once; a merged one anywhere in the set
    has to fail the push."""
    _stub_gh(bin_dir, merged_pr="179")

    result = _push(bin_dir, _line("fix/a") + _line("fix/b"))

    assert result.returncode == 1


def test_a_tag_push_is_not_treated_as_a_branch(bin_dir: Path) -> None:
    _stub_gh(bin_dir, merged_pr="179")

    result = _push(bin_dir, f"refs/tags/v1.0 {SHA} refs/tags/v1.0 {ZERO}\n")

    assert result.returncode == 0
