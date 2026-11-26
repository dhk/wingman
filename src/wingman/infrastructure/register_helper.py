"""Client for the root-owned tenant-registration helper (RFC-086, #658).

The shared service's account cannot write `/etc/wingman/tenants.toml`, and
should not. This module asks a small root-owned program to append one
unprivileged tenant entry on its behalf, through a single sudoers rule an
operator installs on purpose. The helper itself is scripts/
wingman-register-tenant.py; it is standalone, and nothing here is run as root.

The helper is optional. When it is not installed, callers fall back to
printing the `sudo wingman tenant oauth-approve` command for the operator.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wingman.infrastructure.oauth_onboarding import OAuthOnboardingError

HELPER_PATH = Path("/usr/local/libexec/wingman/register-tenant")
CONF_PATH = Path("/etc/wingman/register-helper.conf")
_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class HelperConf:
    registry: Path
    data_root: Path
    service_user: str


def read_conf(path: Path | None = None) -> HelperConf | None:
    """The helper's settings (world-readable), or None if not installed.

    Read here only so the tool uses the same workspace root the helper will
    check. The helper re-reads and re-validates its own copy as root.
    """
    path = path or CONF_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    try:
        return HelperConf(
            Path(values["REGISTRY"]), Path(values["DATA_ROOT"]), values["SERVICE_USER"]
        )
    except KeyError:
        return None


def available(helper: Path | None = None) -> bool:
    """Whether the helper program is present and executable."""
    helper = helper or HELPER_PATH
    return helper.is_file() and os.access(helper, os.X_OK) and read_conf() is not None


def register_via_helper(registry_path: Path, slug: str, data_dir: Path) -> None:
    """Ask the helper to register `slug`. Raises OAuthOnboardingError on refusal.

    Only the slug crosses the boundary. `registry_path` and `data_dir` are
    checked against the helper's own config so a mismatch fails here, plainly,
    instead of registering something the caller did not mean.
    """
    conf = read_conf()
    if conf is None:
        raise OAuthOnboardingError("the registration helper is not installed")
    if registry_path != conf.registry or data_dir != conf.data_root / slug:
        raise OAuthOnboardingError(
            "the registration helper is configured for a different registry or workspace root; "
            "nothing was registered"
        )
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell, slug validated upstream
            ["sudo", "-n", str(HELPER_PATH), slug],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OAuthOnboardingError(
            f"the registration helper could not be run ({exc}); nothing was registered"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"exit status {result.returncode}"
        raise OAuthOnboardingError(f"the registration helper refused: {reason}")
