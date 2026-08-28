"""What `doctor` can see of the box it runs on (#403).

Written against a real session in which `doctor` printed `All checks
passed.` while: the build it named was two releases behind the one
installed, a permission failure scrolled past above the checklist, and
neither the tenant registry nor the process serving three people was
mentioned at all.

Everything here is observational — reported, never graded. Most of it
describes an operator's box, and somebody on their own laptop has none of
it, correctly. A red line every ordinary user learns to ignore is worse
than no line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, Config
from wingman.infrastructure.host_report import (
    entry_point_fact,
    host_facts,
    registry_fact,
    shared_process_fact,
)

THIS = "0.6.1.dev12+gca8e888f8"
OTHER = "0.4.1.dev157+gac45c8c3b"


def _config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path / "ws", data_dir_source="test")


def _runner(versions: dict[str, str]):  # noqa: ANN202 — test double
    def run(argv: list[str]) -> tuple[int, str]:
        return (0, f"wingman {versions[argv[0]]}") if argv[0] in versions else (1, "not found")

    return run


# --------------------------------------------------------------------------
# Which build actually answers when you type 'wingman'
# --------------------------------------------------------------------------


def test_matching_versions_are_reported_without_alarm(tmp_path: Path) -> None:
    installed = tmp_path / ".local/bin/wingman"
    installed.parent.mkdir(parents=True)
    installed.touch()

    fact = entry_point_fact(
        THIS, which=lambda _n: str(installed), run=_runner({str(installed): THIS}), home=tmp_path
    )

    assert not fact.notable
    assert "same as this build" in fact.detail


def test_a_venv_and_an_installed_copy_that_disagree_are_both_named(tmp_path: Path) -> None:
    """The exact case that misled an operator: inside 'uv run', `which`
    resolves to the project venv — the copy reporting the stale number — so
    checking only that one misses the pair that disagree, which IS the
    question."""
    venv = tmp_path / "repo/.venv/bin/wingman"
    venv.parent.mkdir(parents=True)
    venv.touch()
    installed = tmp_path / ".local/bin/wingman"
    installed.parent.mkdir(parents=True)
    installed.touch()

    fact = entry_point_fact(
        OTHER,
        which=lambda _n: str(venv),
        run=_runner({str(venv): OTHER, str(installed): THIS}),
        home=tmp_path,
    )

    assert fact.notable
    assert str(venv) in fact.detail
    assert str(installed) in fact.detail
    assert "DIFFER" in fact.detail


def test_no_wingman_on_path_is_stated_plainly(tmp_path: Path) -> None:
    fact = entry_point_fact(THIS, which=lambda _n: None, run=_runner({}), home=tmp_path)

    assert not fact.notable
    assert "no 'wingman' on PATH" in fact.detail


# --------------------------------------------------------------------------
# The tenant registry
# --------------------------------------------------------------------------


def test_no_registry_at_all_is_not_a_problem(tmp_path: Path) -> None:
    """A single-workspace install has none, and that is the ordinary case."""
    fact = registry_fact(_config(tmp_path), registry=tmp_path / "nothing.toml")

    assert not fact.notable
    assert "single-workspace install" in fact.detail


def test_an_unreadable_registry_is_not_reported_as_absent(tmp_path: Path) -> None:
    """The confusion that cost an hour: '/etc/wingman' is 750 root:wingman,
    so an account outside the group gets an error that cannot tell 'missing'
    from 'not yours to see' — and a present, correct registry was reported
    as 'no such file'."""
    directory = tmp_path / "etc"
    directory.mkdir()
    registry = directory / "tenants.toml"
    registry.write_text("[[tenant]]\n", encoding="utf-8")
    directory.chmod(0o000)
    try:
        fact = registry_fact(_config(tmp_path), registry=registry)
    finally:
        directory.chmod(0o755)

    assert fact.notable
    assert "cannot read it" in fact.detail
    assert "Absent and unreadable are different problems" in fact.detail


def test_a_malformed_registry_warns_that_the_live_process_may_differ(tmp_path: Path) -> None:
    """A running shared process keeps serving the last registry it loaded —
    the SIGHUP handler swallows a failed reload by design — so what is on
    disk and what is live can disagree silently."""
    registry = tmp_path / "tenants.toml"
    registry.write_text("not [ valid toml", encoding="utf-8")

    fact = registry_fact(_config(tmp_path), registry=registry)

    assert fact.notable
    assert "MALFORMED" in fact.detail
    assert "last registry it loaded" in fact.detail


def test_this_workspace_is_identified_and_its_privilege_reported(tmp_path: Path) -> None:
    """'privileged' decides whether operator tools EXIST for you, and its
    refusal names no lever by design — so doctor is the only place that can
    say which way the flag is set."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "dhk"\ndata_dir = "{workspace}"\nprivileged = true\n', encoding="utf-8"
    )

    fact = registry_fact(_config(tmp_path), registry=registry)

    assert "'dhk'" in fact.detail
    assert "privileged" in fact.detail
    assert not fact.notable


def test_an_unprivileged_workspace_says_operator_tools_refuse(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "dhk"\ndata_dir = "{workspace}"\n', encoding="utf-8")

    fact = registry_fact(_config(tmp_path), registry=registry)

    assert "operator tools refuse" in fact.detail


# --------------------------------------------------------------------------
# The shared process
# --------------------------------------------------------------------------


def test_nothing_listening_is_stated_without_alarm() -> None:
    fact = shared_process_fact(THIS, health=lambda _url: None)

    assert not fact.notable
    assert "nothing answering" in fact.detail


def test_a_shared_process_on_a_different_build_is_flagged() -> None:
    """The difference that produces 'No such command' for something that
    shipped hours ago (#375)."""
    fact = shared_process_fact(
        THIS, health=lambda _url: {"version": OTHER, "started_at": "2026-08-13T19:04:18+00:00"}
    )

    assert fact.notable
    assert "tenants are not running what you just shipped" in fact.detail


def test_a_matching_shared_process_is_quiet() -> None:
    fact = shared_process_fact(THIS, health=lambda _url: {"version": THIS, "started_at": "x"})

    assert not fact.notable


def test_a_dirty_local_build_of_the_same_commit_is_not_drift() -> None:
    """hatch-vcs appends '.dYYYYMMDD' when the tree was dirty at build time,
    and this checkout is permanently a little dirty because .beads tracker
    state churns on every bd command. Comparing full strings therefore called
    every healthy box drift and told the reader tenants were behind when they
    were running that exact commit (#471) — the warning that fires when
    nothing is wrong, which is how a real one gets ignored.
    """
    fact = shared_process_fact(
        THIS + ".d20260828", health=lambda _url: {"version": THIS, "started_at": "x"}
    )

    assert not fact.notable
    assert "tenants are not running what you just shipped" not in fact.detail


def test_a_dirty_local_build_still_says_the_tree_was_dirty() -> None:
    """Not silence: a .dYYYYMMDD build really did contain uncommitted changes,
    so it is not bit-identical to a clean build of that commit. Equating them
    outright would trade a false alarm for a quiet lie."""
    fact = shared_process_fact(
        THIS + ".d20260828", health=lambda _url: {"version": THIS, "started_at": "x"}
    )

    assert "same commit" in fact.detail
    assert "dirty" in fact.detail


def test_a_dirty_build_of_a_DIFFERENT_commit_is_still_drift() -> None:
    """The dirty marker must not become a way to hide real drift."""
    fact = shared_process_fact(
        THIS + ".d20260828", health=lambda _url: {"version": OTHER, "started_at": "x"}
    )

    assert fact.notable
    assert "tenants are not running what you just shipped" in fact.detail


# --------------------------------------------------------------------------
# The whole set
# --------------------------------------------------------------------------


def test_one_broken_observation_never_hides_the_others(tmp_path: Path, monkeypatch) -> None:
    """The point is the whole picture; a report that stops at the first
    problem is what somebody is already doing by hand when they run this."""
    from wingman.infrastructure import host_report

    def explode(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202 — test double
        raise RuntimeError("boom")

    monkeypatch.setattr(host_report, "entry_point_fact", explode)

    facts = host_report.host_facts(
        _config(tmp_path), THIS, registry=tmp_path / "none.toml", health=lambda _url: None
    )

    assert any("could not be checked" in fact.detail for fact in facts)
    assert any(fact.name == "tenant registry" for fact in facts)
    assert any(fact.name == "shared process" for fact in facts)


def test_doctor_prints_the_host_facts_and_still_passes(tmp_path: Path, monkeypatch) -> None:
    """Observational, never graded: an operator surface an ordinary user
    will never have must not fail their doctor."""
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    runner = CliRunner()
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    for name in ("installed cli", "tenant registry", "shared process"):
        assert f"[info] {name}:" in result.output
    assert "[FAIL] installed cli" not in result.output


@pytest.mark.parametrize("name", ["installed cli", "tenant registry", "shared process"])
def test_every_host_fact_has_a_name_and_a_detail(tmp_path: Path, name: str) -> None:
    facts = {
        fact.name: fact
        for fact in host_facts(
            _config(tmp_path), THIS, registry=tmp_path / "none.toml", health=lambda _url: None
        )
    }

    assert name in facts
    assert facts[name].detail
