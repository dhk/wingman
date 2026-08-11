"""Changelog domain (RFC-038, issue #145): the user-facing filter and count math."""

from datetime import date

import pytest

from wingman.domain.changelog import (
    ChangelogEntry,
    _distance_past_tag,
    counts_today_and_week,
    is_user_facing,
    load_entries,
    render_changelog,
    staleness_note,
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


def test_distance_parses_a_dev_build() -> None:
    assert _distance_past_tag("0.4.1.dev82+g8dc960d46") == 82
    # A dirty tree appends '.dYYYYMMDD'. The old '+gHASH$' parse could not
    # read that at all and went silent — on exactly the state a box is in
    # right after an upgrade regenerates this file.
    assert _distance_past_tag("0.4.1.dev82+g8dc960d46.d20260810") == 82


@pytest.mark.parametrize("version", ["0.4.0", "unknown (not installed)", "not-a-version-at-all"])
def test_distance_is_none_when_unparseable(version: str) -> None:
    assert _distance_past_tag(version) is None


def test_a_freshly_committed_regeneration_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug this replaced. Generating writes the file and committing it
    makes a new commit, so the build containing a regeneration is ALWAYS one
    commit past the sha stamped in it. Comparing shas could therefore never
    return 'fresh' for a committed artifact — it warned every single time the
    data was correctly regenerated, which is how a real warning gets ignored.
    """
    import wingman.changelog_data as data_module

    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", 82)

    assert staleness_note("0.4.1.dev83+ge01a0573f") is None


def test_staleness_note_is_silent_when_unverifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    import wingman.changelog_data as data_module

    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", 82)
    assert staleness_note("0.4.0") is None  # an exact-tag build: no '.devN' to compare

    # And when the generator itself could not work out a distance.
    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", -1)
    assert staleness_note("0.4.1.dev88+ge01a0573f") is None


def test_a_new_tag_since_generation_is_silence_not_a_false_all_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tag restarts the count, so the two numbers stop being comparable.
    The subtraction goes negative and this says nothing — the safe direction."""
    import wingman.changelog_data as data_module

    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", 148)
    assert staleness_note("0.5.1.dev3+ge01a0573f") is None


def test_staleness_note_says_how_many_merges_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old note could only say merges 'may be missing'. Distance turns
    that into a number."""
    import wingman.changelog_data as data_module

    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", 82)
    note = staleness_note("0.4.1.dev88+ge01a0573f")

    assert note is not None
    assert "5 merges behind" in note  # 88 - 82 - 1 for the regeneration commit
    assert "floor, not a total" in note

    single = staleness_note("0.4.1.dev84+ge01a0573f")
    assert single is not None
    assert "1 merge behind" in single


def test_render_changelog_leads_with_the_staleness_note(monkeypatch: pytest.MonkeyPatch) -> None:
    import wingman.changelog_data as data_module

    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (("2026-07-10", 10, "Add company dossiers"),),
    )
    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", 82)
    rendered = render_changelog(date(2026, 7, 23), version_string="0.4.1.dev88+ge01a0573f")
    assert rendered.startswith("Note:")
    assert "Add company dossiers" in rendered


def test_render_changelog_reports_internal_filtered_count_in_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wingman.changelog_data as data_module

    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (
            ("2026-07-23", 10, "docs: session snapshot for resume"),
            ("2026-07-23", 11, "chore: bump lockfile"),
        ),
    )
    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", -1)
    rendered = render_changelog(date(2026, 7, 23), version_string="0.4.0")
    assert "0 new today, 0 in the last 7 days" in rendered
    assert "2 internal-only filtered from the last 7 days" in rendered


def test_every_pr_is_a_url_people_can_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare '#123' is only meaningful to somebody who already knows the
    repository. The changelog is read by tenants who do not (#357)."""
    import wingman.changelog_data as data_module
    from wingman.domain.changelog import pr_url

    monkeypatch.setattr(
        data_module, "CHANGELOG_DATA", (("2026-07-10", 10, "Add company dossiers"),)
    )
    monkeypatch.setattr(data_module, "GENERATED_FROM_DISTANCE", -1)

    rendered = render_changelog(date(2026, 7, 23))

    assert pr_url(10) in rendered
    assert "https://github.com/dhk/wingman/pull/10" in rendered
