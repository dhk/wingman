"""What `wingman doctor` can see of the box it runs on (#403).

`doctor` reported `All checks passed.` while every problem of a long
operator session was invisible to it: a build two releases behind the one
it named, a permission failure printed as a preamble above the checklist
and then forgotten, and no mention at all of the tenant registry or the
process serving three people from it.

This module answers the questions that were actually being asked that day,
and it is deliberately **observational**. Everything here is reported, not
graded. Most of it describes an OPERATOR's box — a registry, a shared
process, another account's install — and somebody running wingman on their
own laptop has none of those, correctly. Failing `doctor` over them would
teach every ordinary user to ignore a red line, which is the same call
RFC-072's manifest line already made (#212).

`wingman-host-status` remains the whole-box view with a row per instance.
This is the one-screen version, reached by the command people actually
type when something is wrong.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wingman.infrastructure.config import Config

#: (argv) -> (returncode, combined output). Injectable: a test must never
#: run a subprocess, and must never depend on what is installed on the box
#: running it.
Runner = Callable[[list[str]], tuple[int, str]]
#: (url) -> parsed JSON, or None when nothing answered.
Health = Callable[[str], dict[str, object] | None]

SHARED_PORT = 8789


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # no shell, fixed argv, hard timeout
            argv, capture_output=True, text=True, timeout=15.0, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - defensive
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def _default_health(url: str) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310 - fixed localhost
            payload = json.load(response)
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class HostFact:
    """One observation about the box. Printed, never failed on."""

    name: str
    detail: str
    #: True when this is a discrepancy somebody should look at. It still
    #: prints as information — it just says so in words.
    notable: bool = False


def _short(version: str) -> str:
    return version.strip() or "unknown"


def entry_point_fact(
    this_version: str,
    which: Callable[[str], str | None] = shutil.which,
    run: Runner = _default_runner,
    home: Path | None = None,
) -> HostFact:
    """Every `wingman` a person could invoke, versus the code answering now.

    Both can be honest and different: hatch-vcs stamps a version at BUILD
    time, so a project venv reports whatever it was built from while the
    installed tool reports its own. Version is the first thing anyone
    checks when diagnosing drift, and on the day this was written `doctor`
    named a build two releases behind the one actually installed.

    Two candidates, not one. `which` finds whatever the current shell
    resolves — inside `uv run` that is the project venv, which is exactly
    the copy that was reporting the stale number — so the uv tool install
    is checked as well. Reporting only one of them can miss the pair that
    disagree, which is the whole question.
    """
    root = home if home is not None else Path.home()
    candidates: list[Path] = []
    found = which("wingman")
    if found:
        candidates.append(Path(found))
    installed = root / ".local/bin/wingman"
    if installed.exists():
        candidates.append(installed)

    seen: dict[Path, str] = {}
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        code, out = run([str(candidate), "--version"])
        seen[resolved] = _short(out.split()[-1]) if code == 0 and out.split() else "unreadable"

    if not seen:
        return HostFact("installed cli", "no 'wingman' on PATH — this build only")

    mine = _short(this_version)
    described = "; ".join(f"{path} = {version}" for path, version in seen.items())
    differing = [version for version in seen.values() if version != mine]
    if not differing:
        return HostFact("installed cli", f"{described} — same as this build")
    return HostFact(
        "installed cli",
        f"this build is {mine}, but {described} — they DIFFER, so a command you type "
        "may not be the code you just changed",
        notable=True,
    )


def registry_fact(config: Config, registry: Path | None = None) -> HostFact:
    """Where the tenant registry is, whether this account can read it, and
    what it says about this workspace.

    Absent and unreadable are kept distinct. '/etc/wingman' is 750
    root:wingman, so an account outside the group gets an error that cannot
    tell the two apart — which is exactly how a present, correct registry
    was reported as 'no such file' and sent an operator down the wrong path
    for an hour (#401 fixed the same confusion in 'motd show').
    """
    from wingman.infrastructure.broadcast import permission_problem
    from wingman.infrastructure.tenants import (
        TenantRegistryError,
        load_registry,
        tenant_registry_path,
    )

    path = registry if registry is not None else tenant_registry_path()
    denied = permission_problem(path)
    if denied:
        return HostFact(
            "tenant registry",
            f"{path} exists but this account cannot read it — not in the group that owns "
            "it, or this session predates being added (log out and back in). Absent and "
            "unreadable are different problems; this is the second",
            notable=True,
        )
    if not path.is_file():
        return HostFact("tenant registry", f"{path} — none on this box (single-workspace install)")
    try:
        tenants = load_registry(path)
    except TenantRegistryError as exc:
        return HostFact(
            "tenant registry",
            f"{path} is present but MALFORMED ({exc}) — a running shared process keeps "
            "serving the last registry it loaded, so this may not match what is live",
            notable=True,
        )

    resolved = config.data_dir.resolve()
    mine = next((t for t in tenants if t.data_dir.resolve() == resolved), None)
    if mine is None:
        return HostFact(
            "tenant registry", f"{path} — {len(tenants)} tenant(s); this workspace is not one"
        )
    privilege = "privileged" if mine.privileged else "unprivileged (operator tools refuse)"
    return HostFact(
        "tenant registry",
        f"{path} — {len(tenants)} tenant(s); this workspace is '{mine.slug}', {privilege}",
    )


def shared_process_fact(
    this_version: str, health: Health = _default_health, port: int = SHARED_PORT
) -> HostFact:
    """The multi-tenant process every tenant talks to: is it up, and on what.

    A build difference here is the one that produces "No such command" for
    something that shipped hours ago (#375), so it is worth naming rather
    than leaving to a separate command somebody has to know to run.
    """
    payload = health(f"http://127.0.0.1:{port}/health")
    if payload is None:
        return HostFact("shared process", f"nothing answering on 127.0.0.1:{port}")
    running = _short(str(payload.get("version", "")))
    started = str(payload.get("started_at", "")) or "unknown"
    if running == _short(this_version):
        return HostFact("shared process", f"up on {running} (since {started}), same as this build")
    return HostFact(
        "shared process",
        f"up on {running} (since {started}); this build is {_short(this_version)} — they "
        "DIFFER, so tenants are not running what you just shipped",
        notable=True,
    )


def host_facts(
    config: Config,
    this_version: str,
    *,
    registry: Path | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Runner = _default_runner,
    health: Health = _default_health,
    port: int = SHARED_PORT,
) -> list[HostFact]:
    """Every host observation, in the order an operator would want them.

    One failing observation never hides the others: the point is the whole
    picture, and a report that stops at the first problem is what somebody
    is already doing by hand when they run this.
    """
    checks: list[Callable[[], HostFact]] = [
        lambda: entry_point_fact(this_version, which=which, run=run, home=home),
        lambda: registry_fact(config, registry=registry),
        lambda: shared_process_fact(this_version, health=health, port=port),
    ]
    facts: list[HostFact] = []
    for check in checks:
        try:
            facts.append(check())
        except Exception as exc:  # noqa: BLE001 — a broken observation must not hide the rest
            facts.append(HostFact("host", f"could not be checked ({type(exc).__name__}: {exc})"))
    return facts
