"""The host manifest: what a build expects of the box it runs on (RFC-072, #212).

The value here is entirely in the tier boundary. AUTO means a reversible
file write inside the user's own config directory; REPORT means systemd,
sudo, or another account — external actions on shared state, which
AGENTS.md requires a human to approve. A manifest that quietly crossed that
line would be worse than no manifest, so most of these tests are about what
`apply` refuses to do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.host_manifest import (
    MANIFEST,
    DriftTier,
    HostContext,
    apply_auto,
    check_host,
    render_drift,
)

ALL_UNITS = frozenset({"wingman-upgrade-all.service", "wingman-upgrade-all.timer"})


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    return home


def _context(tmp_path: Path, **overrides) -> HostContext:  # noqa: ANN003
    defaults = {"home": _home(tmp_path), "units": ALL_UNITS}
    defaults.update(overrides)
    return HostContext(**defaults)


def _names(drifts) -> set[str]:  # noqa: ANN001
    return {drift.expectation.name for drift in drifts}


def test_a_host_that_matches_reports_nothing(tmp_path: Path) -> None:
    assert check_host(_context(tmp_path)) == []


def test_a_legacy_flat_config_is_auto_fixable(tmp_path: Path) -> None:
    """RFC-046's migration is the whole AUTO category: it moves every value
    across and overwrites nothing, because nothing is there to overwrite."""
    context = _context(tmp_path)
    (context.home / ".config" / "keys.env").write_text("ANTHROPIC_API_KEY=sk-x\n", encoding="utf-8")

    drifts = check_host(context)

    assert _names(drifts) == {"host-config-layout"}
    assert drifts[0].tier is DriftTier.AUTO


def test_applying_the_migration_actually_moves_the_key(tmp_path: Path) -> None:
    context = _context(tmp_path)
    (context.home / ".config" / "keys.env").write_text("ANTHROPIC_API_KEY=sk-x\n", encoding="utf-8")

    applied = apply_auto(check_host(context), context)

    assert applied
    assert (context.home / ".config" / "wingman" / "secrets.env").exists()
    assert "sk-x" in (context.home / ".config" / "wingman" / "secrets.env").read_text()
    # And the drift is gone on a re-check — the fix has to actually fix it.
    assert check_host(context) == []


def test_both_layouts_at_once_is_a_humans_problem_not_a_migration(tmp_path: Path) -> None:
    """RFC-046 declines rather than overwrite a layout somebody set up by
    hand, so two files disagree about where a key lives. Deleting either for
    them could lose a key nothing else has a copy of."""
    context = _context(tmp_path)
    (context.home / ".config" / "keys.env").write_text("ANTHROPIC_API_KEY=old\n", encoding="utf-8")
    new = context.home / ".config" / "wingman"
    new.mkdir(parents=True)
    (new / "secrets.env").write_text("ANTHROPIC_API_KEY=new\n", encoding="utf-8")

    drifts = check_host(context)

    assert _names(drifts) == {"host-config-conflict"}
    assert drifts[0].tier is DriftTier.REPORT


def test_a_conflicting_layout_is_never_touched_by_apply(tmp_path: Path) -> None:
    """The central refusal. Both files must survive untouched."""
    context = _context(tmp_path)
    legacy = context.home / ".config" / "keys.env"
    legacy.write_text("ANTHROPIC_API_KEY=old\n", encoding="utf-8")
    new = context.home / ".config" / "wingman"
    new.mkdir(parents=True)
    (new / "secrets.env").write_text("ANTHROPIC_API_KEY=new\n", encoding="utf-8")

    assert apply_auto(check_host(context), context) == []
    assert legacy.read_text() == "ANTHROPIC_API_KEY=old\n"
    assert (new / "secrets.env").read_text() == "ANTHROPIC_API_KEY=new\n"


@pytest.mark.parametrize("missing", ["wingman-upgrade-all.service", "wingman-upgrade-all.timer"])
def test_a_missing_systemd_unit_is_reported_never_applied(tmp_path: Path, missing: str) -> None:
    context = _context(tmp_path, units=ALL_UNITS - {missing})

    drifts = check_host(context)

    assert all(drift.tier is DriftTier.REPORT for drift in drifts)
    assert apply_auto(drifts, context) == []


def test_no_systemd_at_all_reports_every_unit_rather_than_crashing(tmp_path: Path) -> None:
    """A box without systemd cannot run them, so 'missing' is the truthful
    answer — and a manifest check that raised would be a diagnostic that
    made things worse."""
    drifts = check_host(_context(tmp_path, units=frozenset()))

    assert _names(drifts) == {"upgrade-all-unit", "upgrade-all-timer"}


def test_a_wrapper_copy_that_drifted_from_the_checkout_is_reported(tmp_path: Path) -> None:
    """A stale 'wg' keeps working with the previous release's behaviour and
    nothing in its output says which copy answered."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "wingman-ctl").write_text("# current\n", encoding="utf-8")
    stale = tmp_path / "bin" / "wg"
    stale.parent.mkdir()
    stale.write_text("# from three releases ago\n", encoding="utf-8")

    drifts = check_host(_context(tmp_path, repo=repo, wrapper=stale))

    assert _names(drifts) == {"wrapper-script"}
    assert drifts[0].tier is DriftTier.REPORT


def test_a_wrapper_symlinked_into_the_checkout_is_current_by_construction(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shipped = repo / "scripts" / "wingman-ctl"
    shipped.write_text("# current\n", encoding="utf-8")
    link = tmp_path / "bin" / "wg"
    link.parent.mkdir()
    link.symlink_to(shipped)

    assert check_host(_context(tmp_path, repo=repo, wrapper=link)) == []


def test_a_check_that_raises_never_hides_the_others(tmp_path: Path, monkeypatch) -> None:
    """The point of a manifest is the whole picture; a report that stops at
    the first problem is what operators already do by hand."""
    from wingman.infrastructure import host_manifest

    exploding = host_manifest.Expectation(
        name="exploding",
        why="test double",
        tier=DriftTier.REPORT,
        check=lambda _context: (_ for _ in ()).throw(RuntimeError("boom")),
        fix="n/a",
    )
    monkeypatch.setattr(host_manifest, "MANIFEST", (exploding, *MANIFEST))

    drifts = host_manifest.check_host(_context(tmp_path, units=frozenset()))

    assert "exploding" in _names(drifts)
    assert "could not be checked" in next(
        d.detail for d in drifts if d.expectation.name == "exploding"
    )
    assert {"upgrade-all-unit", "upgrade-all-timer"} <= _names(drifts)


def test_every_auto_expectation_carries_something_that_applies_it() -> None:
    """An AUTO tier with no apply is a promise the report cannot keep."""
    for expectation in MANIFEST:
        if expectation.tier is DriftTier.AUTO:
            assert expectation.apply is not None, expectation.name


def test_no_report_expectation_can_apply_anything() -> None:
    """The tier boundary as a structural property, not a convention: a
    mis-tiered expectation cannot acquire the power to touch systemd."""
    for expectation in MANIFEST:
        if expectation.tier is DriftTier.REPORT:
            assert expectation.apply is None, expectation.name


def test_the_report_says_why_it_matters_and_how_to_fix_it(tmp_path: Path) -> None:
    """'wingman-upgrade-all.timer is missing' means nothing to somebody who
    does not already know what that timer does."""
    rendered = render_drift(check_host(_context(tmp_path, units=frozenset())))

    assert "why it matters" in rendered
    assert "fix:" in rendered
    assert "never applied for you" in rendered.replace("\n", " ")


def test_a_clean_host_says_so_plainly(tmp_path: Path) -> None:
    assert "no operational drift" in render_drift(check_host(_context(tmp_path)))


def test_the_cli_exits_nonzero_on_drift_so_a_deploy_can_gate_on_it(monkeypatch) -> None:
    from wingman.infrastructure import host_manifest

    monkeypatch.setattr(host_manifest, "installed_units", frozenset)
    result = CliRunner().invoke(app, ["host-check"])

    assert result.exit_code == 1
    assert "upgrade-all-unit" in result.output


def test_the_cli_is_clean_when_the_host_matches(monkeypatch) -> None:
    from wingman.infrastructure import host_manifest

    monkeypatch.setattr(host_manifest, "installed_units", lambda: ALL_UNITS)
    result = CliRunner().invoke(app, ["host-check"])

    assert result.exit_code == 0
    assert "no operational drift" in result.output
