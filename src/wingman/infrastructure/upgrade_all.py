"""Root-run: keep every shape-B user's wingman install current (#125).

Every configured user gets the identical upgrade: `uv tool install
--reinstall <source>` (default `git+https://github.com/dhk/wingman.git` —
the same clone-free pattern this tool's own root install already uses,
docs/SERVER.md §7), then `systemctl --user restart wingman-mcp.service`
if it was already active. No local git checkout, no `git pull`, on ANY
account this manages — including dhk's own service.

This replaces an earlier design (#167) that distinguished a
"checkout-shape" user, who owned a local git checkout to `git pull`
before reinstalling from it, from a "local-path-shape" user like Trent
(no GitHub access of his own) who installed from someone else's
already-pulled checkout instead, routed via a per-user
`WINGMAN_UPGRADE_SOURCE_<username>` override. That distinction — and the
failure mode it created, an upgrade silently falling back to a checkout
that was never meant to exist when the override was missing or
misconfigured (caught live: `git -C /home/trent/src/wingman pull` failing
because that path had no reason to exist) — goes away entirely once
install itself never depends on a local checkout: `uv tool install` can
fetch straight from git for every account, uniformly, so there is nothing
left to distinguish.

A developer's own working checkout (e.g. dhk's `~/src/wingman`, for
making commits) is a separate concern from keeping the deployed SERVICE
current, and is untouched by this module — `wingman-ctl upgrade`/`cycle`
(the single-account, "YOU only" developer path) still uses it. This
module is specifically the root-run, cross-account maintenance job.

This is a root-run, root-scheduled tool (see docs/SERVER.md), NOT a
per-instance `--user` timer: it needs to act across accounts, which only
root already can, without granting any NEW privilege — root already owns
every file on the box regardless of whether this script exists. That is a
different trust shape from #133's self-restart action (deliberately
confined to acting only on the SAME account's own instance, reachable
over the network via that instance's own token): this tool is a local,
root-scheduled maintenance job, closer in spirit to unattended-upgrades
than to a web-reachable admin surface, and it is never exposed over HTTP.

Per user: `uv tool install --reinstall`, then `systemctl --user restart
wingman-mcp.service` — explicitly NOT wingman-ctl's `nohup` path (issue
#125's own finding: running wingman-ctl under a systemd-managed account
kills the process out from under systemd and relaunches it unmanaged, and
`Restart=on-failure` won't recover a graceful stop). Each step's failure
is reported and stops that user's run early ("nothing was restarted",
mirroring wingman-ctl's own messages) — but never aborts the other
users' runs.

`systemctl --user` invoked via `sudo -u <user>` from root needs
XDG_RUNTIME_DIR pointed at that user's own runtime directory, or it can't
reach that user's session bus at all (a well-known gotcha for exactly
this root-drives-a-user's-systemctl pattern) — every `sudo -u` call below
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
# Same pattern this tool's OWN root install already uses (docs/SERVER.md
# §7) — 'uv tool install' fetches straight from git; no local checkout
# needed for any account this manages.
DEFAULT_INSTALL_SOURCE = "git+https://github.com/dhk/wingman.git"
ENV_INSTALL_SOURCE = "WINGMAN_UPGRADE_SOURCE"

# (argv) -> (returncode, combined output). Injectable for tests: real use
# never runs a subprocess during a test.
Runner = Callable[[list[str]], tuple[int, str]]
# username -> uid, or None if the account doesn't exist. Injectable.
UidResolver = Callable[[str], int | None]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # fixed binaries via sudo, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=600.0, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def _default_uid(username: str) -> int | None:
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError:
        return None


@dataclass(frozen=True)
class UpgradeTarget:
    user: str
    install_source: str


@dataclass(frozen=True)
class UpgradeResult:
    user: str
    ok: bool
    steps: list[str] = field(default_factory=list)


def target_for(
    username: str,
    resolve_uid: UidResolver = _default_uid,
    install_source: str | None = None,
) -> UpgradeTarget | None:
    """The upgrade target for a username, or None if the account doesn't
    exist — reported by the caller as an unresolvable user, not silently
    skipped. install_source defaults to DEFAULT_INSTALL_SOURCE, the same
    for every user; pass an override only to point the whole run at a
    fork/branch/tag for testing."""
    if resolve_uid(username) is None:
        return None
    return UpgradeTarget(user=username, install_source=install_source or DEFAULT_INSTALL_SOURCE)


def _sudo_as(user: str, argv: list[str], resolve_uid: UidResolver) -> list[str]:
    # XDG_RUNTIME_DIR is required for 'systemctl --user' to reach this
    # user's session bus when invoked via sudo from root; harmless to set
    # for the install step too, so every step uses the same wrapper.
    uid = resolve_uid(user)
    env = [f"XDG_RUNTIME_DIR=/run/user/{uid}"] if uid is not None else []
    return ["sudo", "-u", user, "env", *env, *argv]


def upgrade_one(
    target: UpgradeTarget, run: Runner = _default_runner, resolve_uid: UidResolver = _default_uid
) -> UpgradeResult:
    """One user's full upgrade: reinstall (straight from git, no local
    checkout), restart if managed by systemd. Any step's failure stops
    THIS user's sequence and is reported — it never raises, so a caller
    looping over many users can never have one bad account abort the
    rest."""
    steps: list[str] = []

    def sudo(argv: list[str]) -> list[str]:
        return _sudo_as(target.user, argv, resolve_uid)

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
    """Every target, in the order given — no shape-based sequencing needed
    anymore, since no target depends on another's checkout. One user's
    exception (a bug in a custom runner, say) is caught and reported
    rather than aborting the remaining users' runs, on top of
    'upgrade_one' already reporting ordinary command failures without
    raising."""
    results: list[UpgradeResult] = []
    for target in targets:
        try:
            results.append(upgrade_one(target, run=run, resolve_uid=resolve_uid))
        except Exception as exc:  # noqa: BLE001 — must never abort the whole run
            results.append(UpgradeResult(target.user, ok=False, steps=[f"unexpected error: {exc}"]))
    return results


def _parse_usernames(raw: str) -> list[str]:
    return [name for name in re.split(r"[,\s]+", raw.strip()) if name]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="wingman-upgrade-all",
        description=(
            "Root-run: uv tool install --reinstall (straight from git, no local checkout) "
            "+ systemctl --user restart for every configured shape-B user's "
            "wingman-mcp.service (#125). One user's failure never blocks the others."
        ),
    )
    parser.add_argument(
        "--users",
        default=os.environ.get("WINGMAN_UPGRADE_USERS", ""),
        help="Comma- or space-separated Unix usernames to upgrade. Falls back to "
        "WINGMAN_UPGRADE_USERS (systemd EnvironmentFile-friendly).",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get(ENV_INSTALL_SOURCE, "").strip() or None,
        help=f"Install source for every user (default: {DEFAULT_INSTALL_SOURCE}). Falls back to "
        f"{ENV_INSTALL_SOURCE}. Only worth overriding to test a fork/branch/tag.",
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
        target = target_for(username, resolve_uid=_default_uid, install_source=args.source)
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
