"""Changelog domain (RFC-038, issue #145): the user-facing filter and count
math, plus issue #202's staleness self-check and release-gate regression."""

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from wingman.domain.changelog import (
    ChangelogEntry,
    counts_today_and_week,
    generation_commit,
    is_user_facing,
    load_entries,
    render_changelog,
    staleness_warning,
    user_facing_entries,
)


@pytest.mark.parametrize(
    "title",
    [
        "Add Claude Desktop walkthroughs: local install and remote (Lobster)",
        "Company-attached feeds: follow a company blog with no person needed (RFC-029)",
        "docs: SERVER.md — wingman on an always-on Ubuntu server",
        "Admin installations page: list + launch every wingman instance (#130)",
    ],
)
def test_user_facing_titles_pass(title: str) -> None:
    assert is_user_facing(title)


@pytest.mark.parametrize(
    "title",
    [
        "docs: refresh session snapshot through PR #46 (backlog complete)",
        "docs: session context snapshot for future-session resume",
        "Address review: neutralize filename traversal, clamp evidence limit",
        "chore: bump lockfile",
        "test: add regression case for FTS crash",
        "A test-only harness change with no behavior change",
        "Admin page: fix self-check deadlock and off-box launcher links",
    ],
)
def test_internal_titles_are_excluded(title: str) -> None:
    assert not is_user_facing(title)


def test_load_entries_reads_the_generated_data_module() -> None:
    entries = load_entries()
    assert entries  # the real generated file is non-empty
    assert all(isinstance(entry, ChangelogEntry) for entry in entries)
    assert all(entry.pr > 0 and entry.title for entry in entries)


def test_user_facing_entries_are_filtered_and_sorted_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wingman.changelog_data as data_module

    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (
            ("2026-07-10", 10, "Add company dossiers"),
            ("2026-07-12", 12, "docs: session snapshot for resume"),
            ("2026-07-11", 11, "Add unified search"),
        ),
    )
    entries = user_facing_entries()
    assert [entry.pr for entry in entries] == [11, 10]  # #12 excluded, newest first


def test_counts_today_and_week_window() -> None:
    entries = [
        ChangelogEntry(date="2026-07-23", pr=1, title="today"),
        ChangelogEntry(date="2026-07-23", pr=2, title="also today"),
        ChangelogEntry(date="2026-07-20", pr=3, title="within the week"),
        ChangelogEntry(date="2026-07-17", pr=4, title="exactly seven days back"),
        ChangelogEntry(date="2026-07-16", pr=5, title="just outside the window"),
        ChangelogEntry(date="2026-06-01", pr=6, title="long ago"),
    ]
    today_count, week_count = counts_today_and_week(entries, date(2026, 7, 23))
    assert today_count == 2
    assert week_count == 4  # #1, #2, #3, #4 — the 7-day window includes today


def test_counts_are_zero_for_no_entries() -> None:
    assert counts_today_and_week([], date(2026, 7, 23)) == (0, 0)


# --- issue #202: honest staleness instead of a confident wrong answer -----


def test_staleness_warning_silent_on_a_tagged_release() -> None:
    # No "+g<hash>" segment on a clean tag — nothing to compare, so this
    # deliberately does not guess.
    assert staleness_warning("0.4.1", "deadbeef" * 5) is None


def test_staleness_warning_silent_when_build_matches_generation_commit() -> None:
    full_sha = "8dc960d46aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert staleness_warning(f"0.4.1.dev82+g{full_sha[:9]}", full_sha) is None


def test_staleness_warning_fires_when_build_has_moved_past_generation() -> None:
    generated_at = "8dc960d46aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    warning = staleness_warning("0.4.1.dev143+g1a2b3c4d5e", generated_at)
    assert warning is not None
    assert "8dc960d46aaa" in warning  # the stamped generation commit, truncated
    assert "g1a2b3c4d5e" in warning  # the running build's own commit fragment


def test_staleness_warning_fires_when_generation_commit_is_unrecorded() -> None:
    assert staleness_warning("0.4.1.dev1+gabc1234", "unknown") is not None
    assert staleness_warning("0.4.1.dev1+gabc1234", "") is not None


def test_generation_commit_reads_the_generated_data_module() -> None:
    # The real, checked-in file always stamps this (see
    # scripts/generate_changelog.py) — a 40-char full sha, not the "unknown"
    # fallback reserved for a data module built before this field existed.
    commit = generation_commit()
    assert commit != "unknown"
    assert len(commit) == 40


def test_render_changelog_reports_the_filtered_count_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: '0 shown, N filtered' must be distinguishable from a bare '0'."""
    import wingman.changelog_data as data_module

    today = date(2026, 7, 23)
    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (
            ("2026-07-23", 1, "docs: session snapshot for resume"),  # filtered
            ("2026-07-22", 2, "chore: bump lockfile"),  # filtered
        ),
    )
    monkeypatch.setattr(data_module, "GENERATED_AT_COMMIT", "deadbeef" * 5)
    output = render_changelog(today, installed_version="0.4.1")  # tagged: no warning
    assert "0 new today, 0 in the last 7 days (2 filtered)." in output


def test_render_changelog_includes_the_staleness_warning_when_it_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wingman.changelog_data as data_module

    today = date(2026, 7, 23)
    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (("2026-07-23", 1, "Add a real feature"),),
    )
    monkeypatch.setattr(data_module, "GENERATED_AT_COMMIT", "8dc960d46" + "a" * 31)
    output = render_changelog(today, installed_version="0.4.1.dev143+g1a2b3c4d5e")
    assert output.startswith("⚠")
    assert "may be missing" in output


# --- issue #202: release-gate regression --------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_generate_changelog_module() -> ModuleType:
    script_path = _repo_root() / "scripts" / "generate_changelog.py"
    spec = importlib.util.spec_from_file_location("generate_changelog", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _full_git_history_available() -> bool:
    """False for a shallow checkout (e.g. actions/checkout's default
    fetch-depth: 1) or one with no .git at all — this gate needs the real
    history the generator itself walks, not a truncated view of it."""
    import subprocess

    git_dir = _repo_root() / ".git"
    if not git_dir.exists():
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "false"


@pytest.mark.skipif(
    not _full_git_history_available(),
    reason="release gate needs a full git checkout, not a shallow clone or installed build",
)
def test_changelog_data_accounts_for_every_pr_commit_since_its_newest_entry() -> None:
    """Issue #202 acceptance criterion 4 / RFC-038's revisit trigger: every
    merged-PR commit newer than CHANGELOG_DATA's own newest entry must
    already be present in the generated data — whether or not the
    user-facing filter later hides it from display. This is the check that
    would have caught #202 itself: 61 real merged PRs, newer than the
    committed file's newest entry, silently missing.

    Comparing entry *date* to HEAD's date alone would not have caught this
    reliably (a single stray filtered/omitted commit landing after the last
    entry doesn't move the date), so this compares PR numbers directly
    against a real `git log --first-parent` walk, the same one
    scripts/generate_changelog.py itself does.
    """
    module = _load_generate_changelog_module()
    live_entries: list[tuple[str, int, str]] = module._entries()

    from wingman.changelog_data import CHANGELOG_DATA

    known_prs = {pr for _date, pr, _title in CHANGELOG_DATA}
    newest_known_date = max(entry_date for entry_date, _pr, _title in CHANGELOG_DATA)

    missing = [
        (entry_date, pr, title)
        for entry_date, pr, title in live_entries
        if entry_date >= newest_known_date and pr not in known_prs
    ]
    assert not missing, (
        "src/wingman/changelog_data.py is stale: these merged PRs are newer than (or "
        "as new as) its own newest entry but are not accounted for. Run "
        "`python scripts/generate_changelog.py`, commit the result, and rerun:\n"
        + "\n".join(f"  #{pr} {entry_date} {title}" for entry_date, pr, title in missing)
    )
