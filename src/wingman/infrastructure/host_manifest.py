"""What this version of the code expects the HOST to look like (RFC-072, #212).

Wingman's operational surface — the split host-config layout (RFC-046), the
systemd units that run the shared process and the cross-account upgrade, the
`wingman-ctl` wrapper an operator actually types — has always shipped as
one-off hand-written migrations (`migrate_legacy_host_file`) or as prose an
operator had to notice and apply. There was no single place answering "does
this box match what this build expects?", so drift was discovered when
something broke (#197, #198), and usually by somebody else.

This module is that place. It is a declarative manifest plus a checker; it
knows nothing about how a fix is applied beyond the tier each expectation
carries.

**Two tiers, and the boundary between them is the point.**

`AUTO` is a file write inside the user's own config directory that is
reversible and loses nothing — RFC-046's layout migration is the whole
category, and `migrate_legacy_host_file` already set that precedent by
running automatically and printing what moved.

`REPORT` is everything touching systemd, sudo, or another account. Those are
external actions on shared state, and AGENTS.md requires human approval
before an external action. A checker that silently `systemctl enable`d
something would be exactly the kind of unaudited change this repo refuses
elsewhere. So the manifest reports them, names the command that would fix
them, and stops.

**Why a Python module rather than a TOML under docs/.** It has to be
readable at runtime on a box that has only the installed wheel, and hatch
packages `src/wingman` as Python — a docs/ data file would not ship, and a
manifest that is absent exactly where drift matters is worse than none. It
stays as declarative and as diffable as a TOML would be.

The manifest deliberately does NOT check things the code already fixes
itself on every run, or things that vary legitimately between boxes (which
tenants exist, which accounts have a CLI). It checks what a release
*expects* and cannot repair on its own.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.host_manifest")


class DriftTier(StrEnum):
    """How far a fix is allowed to go without being asked."""

    #: A reversible, file-only write inside the user's own config dir.
    AUTO = "auto"
    #: systemd, sudo, or another account — reported, never applied here.
    REPORT = "report"


@dataclass(frozen=True)
class Expectation:
    """One thing a release expects to be true of the host it runs on."""

    name: str
    #: What this expectation is FOR, in one line — printed with any drift,
    #: because "wingman-upgrade-all.timer is missing" means nothing to
    #: somebody who does not already know what that timer does.
    why: str
    tier: DriftTier
    #: Returns None when satisfied, or a one-line description of the drift.
    check: Callable[[HostContext], str | None]
    #: The command a human would run. Printed verbatim for REPORT drift; for
    #: AUTO drift it is what `apply` does, named so the automatic action is
    #: still legible.
    fix: str
    #: AUTO only: performs the fix, returning what it changed.
    apply: Callable[[HostContext], str] | None = None


@dataclass
class HostContext:
    """Where the checker is allowed to look.

    Injected rather than read from the process, so the whole manifest is
    testable against a temporary directory and a stub systemd without
    touching the real box.
    """

    home: Path
    repo: Path | None = None
    #: Names of installed systemd units. Defaults to querying systemd once.
    units: frozenset[str] = field(default_factory=frozenset)
    #: Absolute path of the `wingman-ctl` an operator's shell would run.
    wrapper: Path | None = None


@dataclass(frozen=True)
class Drift:
    """One expectation the live host does not meet."""

    expectation: Expectation
    detail: str

    @property
    def tier(self) -> DriftTier:
        return self.expectation.tier


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def _new_layout_exists(context: HostContext) -> bool:
    directory = context.home / ".config" / "wingman"
    return (directory / "wingman.env").exists() or (directory / "secrets.env").exists()


def _legacy_host_file_migratable(context: HostContext) -> str | None:
    """RFC-040's flat file, with nothing of the new layout in the way.

    The safe case, and the only one that gets applied automatically:
    `migrate_legacy_host_file` moves every value across and overwrites
    nothing, because there is nothing there to overwrite.
    """
    legacy = context.home / ".config" / "keys.env"
    if not legacy.is_file() or _new_layout_exists(context):
        return None
    return f"{legacy} is RFC-040's flat layout; RFC-046 splits it into wingman.env + secrets.env"


def _legacy_host_file_conflicting(context: HostContext) -> str | None:
    """Both layouts present at once — a human's problem, not a migration.

    RFC-046 never overwrites a layout somebody may have set up by hand, so
    the migration declines and the old file stays. Which of the two is
    actually being read is then a question only its owner can answer, and
    deleting either one for them could lose a key.
    """
    legacy = context.home / ".config" / "keys.env"
    if not legacy.is_file() or not _new_layout_exists(context):
        return None
    return (
        f"{legacy} is still present even though ~/.config/wingman/ already holds the new "
        "layout — the migration declined rather than overwrite it, so two files disagree "
        "about where a key lives"
    )


def _apply_legacy_host_file(context: HostContext) -> str:
    from wingman.infrastructure.host_config import migrate_legacy_host_file

    result = migrate_legacy_host_file(home=context.home)
    return str(result)


def _unit_check(unit: str) -> Callable[[HostContext], str | None]:
    def check(context: HostContext) -> str | None:
        if unit in context.units:
            return None
        return f"{unit} is not installed on this box"

    return check


def _wrapper_matches_repo(context: HostContext) -> str | None:
    """The `wg` an operator types vs. the one in this checkout.

    A stale wrapper is invisible in the worst way: every command still runs,
    just with the previous release's behaviour, and nothing in its output
    says which copy answered.
    """
    if context.repo is None or context.wrapper is None:
        return None
    shipped = context.repo / "scripts" / "wingman-ctl"
    if not shipped.exists() or not context.wrapper.exists():
        return None
    if context.wrapper.resolve() == shipped.resolve():
        return None  # a symlink into the checkout: always current by construction
    if context.wrapper.read_bytes() == shipped.read_bytes():
        return None
    return f"{context.wrapper} differs from {shipped} — it is a stale copy, not a link"


MANIFEST: tuple[Expectation, ...] = (
    Expectation(
        name="host-config-layout",
        why=(
            "API keys and host settings live in ~/.config/wingman/{secrets.env,wingman.env}. "
            "The old flat keys.env still works by migration, but only until something "
            "writes to the new layout and the two disagree."
        ),
        tier=DriftTier.AUTO,
        check=_legacy_host_file_migratable,
        fix="wingman host-check --apply  (moves keys.env into the RFC-046 split layout)",
        apply=_apply_legacy_host_file,
    ),
    Expectation(
        name="host-config-conflict",
        why=(
            "Two layouts present at once means two files disagree about where a key "
            "lives, and only their owner knows which is authoritative. Deleting either "
            "one automatically could lose a key that nothing else has a copy of."
        ),
        tier=DriftTier.REPORT,
        check=_legacy_host_file_conflicting,
        fix=(
            "check ~/.config/wingman/{wingman.env,secrets.env} hold everything you need, "
            "then remove ~/.config/keys.env by hand (never auto-deleted)"
        ),
    ),
    Expectation(
        name="upgrade-all-unit",
        why=(
            "wingman-upgrade-all.service is what keeps every account's own CLI current. "
            "Without it 'wg upgrade-all' can only redeploy the shared process, and an "
            "operator's CLI silently falls behind the process it administers (#375)."
        ),
        tier=DriftTier.REPORT,
        check=_unit_check("wingman-upgrade-all.service"),
        fix="see docs/SERVER.md §7 — install the unit and its timer",
    ),
    Expectation(
        name="upgrade-all-timer",
        why=(
            "The timer is what makes the upgrade scheduled rather than remembered. "
            "The unit alone means it only ever runs when somebody thinks to run it."
        ),
        tier=DriftTier.REPORT,
        check=_unit_check("wingman-upgrade-all.timer"),
        fix="sudo systemctl enable --now wingman-upgrade-all.timer",
    ),
    Expectation(
        name="wrapper-script",
        why=(
            "'wg' is the operator's entry point. A copy that has drifted from the "
            "checkout keeps working with the previous release's behaviour, and nothing "
            "in its output says which copy answered."
        ),
        tier=DriftTier.REPORT,
        check=_wrapper_matches_repo,
        fix="ln -sf <repo>/scripts/wingman-ctl <the path on your PATH>",
    ),
)


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------


def installed_units() -> frozenset[str]:
    """Unit names systemd knows about — empty on a box without systemd.

    Never raises: a manifest check that itself fails is a diagnostic that
    made things worse. An empty set reports the units as missing, which is
    the truthful answer for a host that cannot run them anyway.
    """
    if shutil.which("systemctl") is None:
        return frozenset()
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "--no-legend", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - defensive
        _logger.warning("could not list systemd units: %s", exc)
        return frozenset()
    return frozenset(line.split()[0] for line in result.stdout.splitlines() if line.split())


def host_context(home: Path | None = None, repo: Path | None = None) -> HostContext:
    resolved_home = home or Path.home()
    resolved_repo = repo
    if resolved_repo is None:
        raw = os.environ.get("WINGMAN_REPO")
        resolved_repo = Path(raw).expanduser() if raw else None
    wrapper = shutil.which("wg") or shutil.which("wingman-ctl")
    return HostContext(
        home=resolved_home,
        repo=resolved_repo,
        units=installed_units(),
        wrapper=Path(wrapper) if wrapper else None,
    )


def check_host(context: HostContext) -> list[Drift]:
    """Every expectation this build has of the host that this host misses.

    One failing check never hides the others: the point of a manifest is the
    whole picture, and a drift report that stops at the first problem is the
    thing operators already do by hand.
    """
    drifts: list[Drift] = []
    for expectation in MANIFEST:
        try:
            detail = expectation.check(context)
        except Exception as exc:  # noqa: BLE001 — a broken check must not hide the rest
            detail = f"could not be checked ({type(exc).__name__}: {exc})"
        if detail:
            drifts.append(Drift(expectation=expectation, detail=detail))
    return drifts


def apply_auto(drifts: list[Drift], context: HostContext) -> list[str]:
    """Apply the AUTO drift only, returning what each fix changed.

    REPORT drift is not merely skipped here — it is unreachable: nothing in
    this function can touch systemd, sudo, or another account, so a future
    expectation cannot acquire that power by being mis-tiered alone.
    """
    applied: list[str] = []
    for drift in drifts:
        if drift.tier is not DriftTier.AUTO or drift.expectation.apply is None:
            continue
        try:
            applied.append(f"{drift.expectation.name}: {drift.expectation.apply(context)}")
        except Exception as exc:  # noqa: BLE001 — report, never abort the rest
            applied.append(f"{drift.expectation.name}: FAILED — {type(exc).__name__}: {exc}")
    return applied


def render_drift(drifts: list[Drift]) -> str:
    """The operator-facing report."""
    if not drifts:
        return "Host matches the manifest for this build — no operational drift."
    lines = [f"{len(drifts)} operational difference(s) between this host and this build:", ""]
    for drift in drifts:
        marker = "auto-fixable" if drift.tier is DriftTier.AUTO else "needs a human"
        lines.append(f"  [{marker}] {drift.expectation.name}")
        lines.append(f"      {drift.detail}")
        lines.append(f"      why it matters: {drift.expectation.why}")
        lines.append(f"      fix: {drift.expectation.fix}")
        lines.append("")
    if any(drift.tier is DriftTier.AUTO for drift in drifts):
        lines.append("'wingman host-check --apply' applies the auto-fixable ones.")
    if any(drift.tier is DriftTier.REPORT for drift in drifts):
        lines.append(
            "The rest touch systemd, sudo or another account and are never applied "
            "for you — run the named command yourself."
        )
    return "\n".join(lines).rstrip() + "\n"
