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
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

SYSTEMD_UNIT = "wingman-mcp.service"
REPO_SUBPATH = "src/wingman"  # CLAUDE.md's documented code-location convention

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
    own_checkout: str | None  # `git pull --ff-only` here first when set; None = local-path
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
        return UpgradeTarget(user=username, install_source=install_source, own_checkout=None)
    return UpgradeTarget(user=username, install_source=own_checkout, own_checkout=own_checkout)


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
        code, out = run(sudo(["git", "-C", target.own_checkout, "pull", "--ff-only"]))
        if code != 0:
            steps.append(f"pull failed, nothing was touched: {out}")
            return UpgradeResult(target.user, ok=False, steps=steps)
        steps.append("pulled latest")

    code, out = run(sudo(["uv", "tool", "install", "--reinstall", target.install_source]))
    if code != 0:
        steps.append(f"reinstall failed, nothing was restarted: {out}")
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
