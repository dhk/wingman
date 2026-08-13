"""Root-run: keep every shape-B user's wingman install current (#125).

Lobster (docs/MULTI-INSTANCE-DESIGN.md shape B) is multi-user, and not
every user has the same install shape (#167):

- **Checkout shape** (dhk): owns a git checkout under '~/src/wingman' (the
  code-location convention recorded in this repo's CLAUDE.md). Updated by
  'git pull --ff-only' against their own checkout, then
  'uv tool install --reinstall' from it.
- **Local-path shape** (Trent): deliberately has no GitHub access of his
  own (docs/WALKTHROUGH-SECOND-USER.md's design) — he installs via
  'uv tool install <another user's already-updated checkout path>', never
  'git pull'. There is nothing of his own to pull.

This module is the scheduled fix for both shapes, run by root on a system
timer (see docs/SERVER.md), NOT a per-instance '--user' timer: it needs to
act across accounts, which only root already can, without granting any
NEW privilege — root already owns every file on the box regardless of
whether this script exists. That is a different trust shape from #133's
self-restart action (deliberately confined to acting only on the SAME
account's own instance, reachable over the network via that instance's
own token): this tool is a local, root-scheduled maintenance job, closer
in spirit to unattended-upgrades than to a web-reachable admin surface,
and it is never exposed over HTTP.

Sequencing matters: a local-path user's install is only meaningful once
their source user's checkout has actually been pulled — checkout-shape
targets run first, local-path targets (which may depend on one of them)
run after, never the reverse and never interleaved per-user in a way that
could read a source checkout mid-pull.

Per user, after any pull: 'uv tool install --reinstall', then
'systemctl --user restart wingman-mcp.service' — explicitly NOT
wingman-ctl's nohup path (issue #125's own finding: running wingman-ctl
under a systemd-managed account kills the process out from under systemd
and relaunches it unmanaged, and 'Restart=on-failure' won't recover a
graceful stop). Each step's failure is reported and stops that user's
run early ("nothing was touched" / "nothing was restarted", mirroring
wingman-ctl's own messages) — but never aborts the other users' runs.

'systemctl --user' invoked via 'sudo -u <user>' from root needs
XDG_RUNTIME_DIR pointed at that user's own runtime directory, or it can't
reach that user's session bus at all (a well-known gotcha for exactly
this root-drives-a-user's-systemctl pattern) — every 'sudo -u' call below
carries it explicitly.
"""

from __future__ import annotations

import argparse
import os
import pwd
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from wingman.version import wingman_version

SYSTEMD_UNIT = "wingman-mcp.service"
REPO_SUBPATH = "src/wingman"
# What an account should be tracking. A checkout parked anywhere else
# receives that branch's code on every nightly run (#301).
DEFAULT_BRANCH = "main"  # CLAUDE.md's documented code-location convention

# (argv) -> (returncode, combined output). Injectable for tests: real use
# never runs a subprocess during a test.
Runner = Callable[[list[str]], tuple[int, str]]
# username -> home directory, or None if the account doesn't exist. Injectable.
HomeResolver = Callable[[str], str | None]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # fixed binaries via sudo, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=600.0, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def _default_home(username: str) -> str | None:
    try:
        return pwd.getpwnam(username).pw_dir
    except KeyError:
        return None


def _default_uid(username: str) -> int | None:
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError:
        return None


# username -> uid, or None if the account doesn't exist. Injectable.
UidResolver = Callable[[str], int | None]


@dataclass(frozen=True)
class UpgradeTarget:
    user: str
    install_source: str  # passed to `uv tool install --reinstall <this>`
    home: str | None = None  # for naming paths in diagnostics
    own_checkout: str | None = None  # `git pull --ff-only` here first when set; None = local-path
    # shape (#167) — installs from another user's (already-updated) checkout, nothing of
    # their own to pull.


@dataclass(frozen=True)
class UpgradeResult:
    user: str
    ok: bool
    steps: list[str] = field(default_factory=list)


def target_for(
    username: str,
    resolve_home: HomeResolver = _default_home,
    install_source: str | None = None,
) -> UpgradeTarget | None:
    """The upgrade target for a username, or None if the account doesn't
    exist — reported by the caller as an unresolvable user, not silently
    skipped.

    install_source, when given, makes this a local-path-shape target
    (#167): installs are pulled from that path (someone else's checkout)
    instead of pulling and installing this user's own — the checkout-shape
    default when omitted.
    """
    home = resolve_home(username)
    if home is None:
        return None
    own_checkout = f"{home.rstrip('/')}/{REPO_SUBPATH}"
    if install_source is not None:
        return UpgradeTarget(
            user=username, install_source=install_source, home=home, own_checkout=None
        )
    return UpgradeTarget(
        user=username, install_source=own_checkout, home=home, own_checkout=own_checkout
    )


def current_branch(
    checkout: str, run: Runner, sudo: Callable[[list[str]], list[str]]
) -> str | None:
    """The checked-out branch name, or None if it can't be determined."""
    code, out = run(sudo(["git", "-C", checkout, "rev-parse", "--abbrev-ref", "HEAD"]))
    return out.strip() if code == 0 and out.strip() else None


def diagnose_checkout(
    checkout: str, branch: str | None, run: Runner, sudo: Callable[[list[str]], list[str]]
) -> str | None:
    """Why this checkout cannot be pulled, in the operator's terms — or None.

    git's own errors describe a situation the operator did not know they
    were in. 'There is no tracking information for the current branch'
    never says *the checkout is parked on a feature branch*, and on the
    nightly timer nobody reads it anyway: the account silently stops
    receiving updates while the next line says [ok] for somebody else
    (#301). Every string returned here names the cause and the one
    command that fixes it.
    """
    if branch is None:
        # The branch probe itself failed, so nothing here is established.
        # Returning a confident guess would replace git's real error with
        # a derived one — say nothing and let git speak.
        return None

    code, out = run(sudo(["git", "-C", checkout, "status", "--porcelain", "--untracked-files=no"]))
    dirty = [line[3:] for line in out.splitlines() if line.strip()]

    code, _ = run(
        sudo(["git", "-C", checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    )
    if code != 0:
        fix = f"git -C {checkout} checkout {DEFAULT_BRANCH}"
        if dirty:
            return (
                f"checkout is on '{branch}', which tracks no remote, AND has uncommitted "
                f"changes to {', '.join(dirty[:3])} — commit or discard them, then: {fix}"
            )
        return (
            f"checkout is on '{branch}', which tracks no remote, so there is nothing to "
            f"pull from. To get current: {fix}"
        )
    if dirty:
        return (
            f"checkout has uncommitted changes to {', '.join(dirty[:3])}, which a "
            f"fast-forward pull will refuse. Commit or discard them first."
        )
    return None


def diagnose_reinstall(failure: str, user: str, home: str | None) -> str | None:
    """The known reinstall failures, named rather than passed through."""
    if "Permission denied" in failure and "__pycache__" in failure:
        store = f"{(home or '~').rstrip('/')}/.local/share/uv/tools/wingman"
        return (
            "root-owned bytecode in the uv tool store — something ran the installed "
            f"entry point under sudo (#276). To fix: chown -R {user}:{user} {store}"
        )
    return None


#: Who owns a path. Injectable for the same reason Runner and UidResolver
#: are: a test must not need root, and must not fake Path.stat wholesale —
#: is_dir() and rglob() go through it too.
OwnerOf = Callable[[Path], int]


def _default_owner(path: Path) -> int:
    return path.stat().st_uid


def tool_store(home: str | None) -> Path:
    """Where an account's uv tool install of wingman lives."""
    return Path((home or "~").rstrip("/")) / ".local/share/uv/tools/wingman"


def clear_root_owned_bytecode(store: Path, owner_of: OwnerOf = _default_owner) -> list[str]:
    """Remove root-owned __pycache__ directories from one account's tool
    store, returning what was removed (#406).

    This runs as ROOT — the unit's ExecStart is root, and it sudo's DOWN to
    each account — so it can delete what the unprivileged reinstall cannot.
    That asymmetry is the whole point: a single `sudo wingman motd set`
    seeds root-owned bytecode in the invoking account's store, and
    `uv tool install --reinstall` then fails as that account, days later,
    naming a package nobody has heard of.

    #377 stops this for every module except the one it lives in: Python
    writes `wingman/__init__.py`'s own .pyc as part of importing it, before
    the guard inside it executes, so no code in the package can be early
    enough. The blast radius shrank from 1,824 files to 2 and the
    operational consequence did not change — one root-owned directory
    blocks a reinstall exactly as well as 1,824 did. So the upgrade heals
    it instead of asking a human for a chown.

    Deliberately narrow: directories NAMED __pycache__, owned by uid 0,
    beneath this store only. Bytecode is regenerable, so removing it costs
    nothing; removing anything else would not be repairable, which is why
    nothing else is in scope.
    """
    if os.geteuid() != 0 or not store.is_dir():
        return []
    removed: list[str] = []
    for path in sorted(store.rglob("__pycache__")):
        if not path.is_dir():
            continue
        try:
            if owner_of(path) != 0:
                continue
            shutil.rmtree(path)
        except OSError as exc:  # pragma: no cover - defensive
            # Reported, not raised: a cache we cannot clear is the state we
            # were already in, and it must not stop the upgrade attempt.
            removed.append(f"{path} (could NOT be removed: {exc})")
            continue
        removed.append(str(path))
    return removed


def _sudo_as(user: str, argv: list[str], resolve_uid: UidResolver) -> list[str]:
    # XDG_RUNTIME_DIR is required for 'systemctl --user' to reach this
    # user's session bus when invoked via sudo from root; harmless to set
    # for the git/uv steps too, so every step uses the same wrapper.
    uid = resolve_uid(user)
    env = [f"XDG_RUNTIME_DIR=/run/user/{uid}"] if uid is not None else []
    return ["sudo", "-u", user, "env", *env, *argv]


def upgrade_one(
    target: UpgradeTarget, run: Runner = _default_runner, resolve_uid: UidResolver = _default_uid
) -> UpgradeResult:
    """One user's full upgrade: pull (checkout-shape only), reinstall,
    restart if managed by systemd. Any step's failure stops THIS user's
    sequence and is reported — it never raises, so a caller looping over
    many users can never have one bad account abort the rest."""
    steps: list[str] = []

    def sudo(argv: list[str]) -> list[str]:
        return _sudo_as(target.user, argv, resolve_uid)

    if target.own_checkout is not None:
        # One cheap call on the happy path: an account quietly parked on a
        # feature branch pulls THAT branch's code every night and nothing
        # says so (#301). The fuller diagnosis costs three more calls and
        # only runs when the pull actually fails.
        branch = current_branch(target.own_checkout, run, sudo)
        if branch is not None and branch != DEFAULT_BRANCH:
            steps.append(
                f"on '{branch}', not {DEFAULT_BRANCH} — this account tracks that branch "
                f"until: git -C {target.own_checkout} checkout {DEFAULT_BRANCH}"
            )
        code, out = run(sudo(["git", "-C", target.own_checkout, "pull", "--ff-only"]))
        if code != 0:
            # git's own text describes a state, not a cause. Prefer the
            # diagnosis; fall back to git only when nothing is recognised.
            why = diagnose_checkout(target.own_checkout, branch, run, sudo) or out
            steps.append(f"pull failed, nothing was touched: {why}")
            return UpgradeResult(target.user, ok=False, steps=steps)
        steps.append("pulled latest")

    # Before the reinstall, not after a failure: the point is that it
    # never fails for this reason again (#406).
    healed = clear_root_owned_bytecode(tool_store(target.home))
    if healed:
        steps.append(
            f"removed {len(healed)} root-owned __pycache__ director(y/ies) left by a "
            "privileged run — they would have blocked this reinstall"
        )

    code, out = run(sudo(["uv", "tool", "install", "--reinstall", target.install_source]))
    if code != 0:
        named = diagnose_reinstall(out, target.user, target.home)
        steps.append(f"reinstall failed, nothing was restarted: {named or out}")
        return UpgradeResult(target.user, ok=False, steps=steps)
    steps.append("reinstalled")

    code, out = run(sudo(["systemctl", "--user", "is-active", SYSTEMD_UNIT]))
    if out.strip() != "active":
        steps.append(
            f"'{SYSTEMD_UNIT}' is not active for this user — build updated, nothing to restart"
        )
        return UpgradeResult(target.user, ok=True, steps=steps)

    code, out = run(sudo(["systemctl", "--user", "restart", SYSTEMD_UNIT]))
    if code != 0:
        steps.append(f"restart failed: {out}")
        return UpgradeResult(target.user, ok=False, steps=steps)
    steps.append(f"restarted {SYSTEMD_UNIT}")
    return UpgradeResult(target.user, ok=True, steps=steps)


def upgrade_all(
    targets: list[UpgradeTarget],
    run: Runner = _default_runner,
    resolve_uid: UidResolver = _default_uid,
) -> list[UpgradeResult]:
    """Every target, checkout-shape first then local-path-shape (#167) —
    a local-path target may install from a checkout-shape target's
    directory, so that source must already be pulled by the time it
    runs; original relative order is kept within each group. One user's
    exception (a bug in a custom runner, say) is caught and reported
    rather than aborting the remaining users' runs, on top of
    'upgrade_one' already reporting ordinary command failures without
    raising."""
    ordered = sorted(targets, key=lambda target: target.own_checkout is None)
    results: list[UpgradeResult] = []
    for target in ordered:
        try:
            results.append(upgrade_one(target, run=run, resolve_uid=resolve_uid))
        except Exception as exc:  # noqa: BLE001 — must never abort the whole run
            results.append(UpgradeResult(target.user, ok=False, steps=[f"unexpected error: {exc}"]))
    return results


def _parse_usernames(raw: str) -> list[str]:
    return [name for name in re.split(r"[,\s]+", raw.strip()) if name]


def _source_override_env_var(username: str) -> str:
    return f"WINGMAN_UPGRADE_SOURCE_{username}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="wingman-upgrade-all",
        description=(
            "Root-run: git pull + uv reinstall + systemctl --user restart for every "
            "configured shape-B user's wingman-mcp.service (#125). Checkout-shape users "
            "(their own '~/src/wingman') pull their own history; local-path-shape users "
            "(#167 — no GitHub access of their own, e.g. Trent) install from another "
            "user's checkout instead, named via WINGMAN_UPGRADE_SOURCE_<username>. One "
            "user's failure never blocks the others."
        ),
    )
    parser.add_argument(
        "--users",
        default=os.environ.get("WINGMAN_UPGRADE_USERS", ""),
        help="Comma- or space-separated Unix usernames to upgrade. Falls back to "
        "WINGMAN_UPGRADE_USERS (systemd EnvironmentFile-friendly).",
    )
    args = parser.parse_args(argv)
    usernames = _parse_usernames(args.users)
    if not usernames:
        print(
            "no users configured (--users or WINGMAN_UPGRADE_USERS) — nothing to do",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # The orchestrator's own build, ahead of everything it reports (#407).
    #
    # This is the line that was missing when a reinstall failed and the
    # operator was told the reason was "Resolved 44 packages in 873ms".
    # 'diagnose_reinstall' had handled that exact failure since #308 — the
    # copy that was RUNNING simply predated it, because the unit's
    # ExecStart is '/root/.local/bin/wingman-upgrade-all' and root is not
    # one of the accounts this upgrades. Nothing said so: the run reports
    # on every account except the one doing the reporting.
    #
    # It cannot fix that itself — replacing site-packages under a running
    # Python process that imports lazily is a worse failure than the one it
    # would solve, and 'wg upgrade-all' refreshes root's install before
    # triggering the unit for exactly that reason. What this can do is stop
    # hiding it, which matters most on the nightly timer path, where no
    # wrapper is involved at all.
    # flush: failures go to unbuffered stderr while this goes to
    # block-buffered stdout, so without it the banner is overtaken by the
    # very lines it is meant to give context to — in a terminal and in the
    # journal, which orders by write time.
    print(
        f"wingman-upgrade-all {wingman_version()} (this orchestrator's own build)",
        flush=True,
    )

    targets: list[UpgradeTarget] = []
    unresolved: list[str] = []
    for username in usernames:
        source = os.environ.get(_source_override_env_var(username), "").strip() or None
        target = target_for(username, resolve_home=_default_home, install_source=source)
        if target is None:
            unresolved.append(username)
        else:
            targets.append(target)
    for username in unresolved:
        print(f"[FAILED] {username}: no such Unix account — skipped", file=sys.stderr)

    # The orchestrator's own build, before anything it does (#407).
    #
    # This is the line that was missing when a reinstall failed and the
    # operator was told the reason was "Resolved 44 packages in 873ms".
    # 'diagnose_reinstall' had handled that exact failure since #308 — the
    # copy of the code that was RUNNING simply predated it, because the
    # unit's ExecStart is '/root/.local/bin/wingman-upgrade-all' and root
    # is not one of the accounts this upgrades. Nothing anywhere said so:
    # the run reports on every account except the one doing the reporting.
    #
    # It cannot fix that on its own — replacing site-packages under a
    # running Python process that imports lazily is a worse failure than
    # the one it would solve. What it can do is stop hiding it.
    results = upgrade_all(targets, run=_default_runner, resolve_uid=_default_uid)
    failures = len(unresolved)
    for result in results:
        status = "ok" if result.ok else "FAILED"
        print(f"[{status}] {result.user}: " + "; ".join(result.steps))
        if not result.ok:
            failures += 1
    if failures:
        print(f"{failures}/{len(usernames)} user(s) failed to upgrade.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
