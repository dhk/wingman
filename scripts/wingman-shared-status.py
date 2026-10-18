"""Read-only shared-host status. Configuration is data and never echoed."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
import tomllib
from pathlib import Path


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def configured_path_exists(path: Path) -> bool:
    """Keep absent distinct from inaccessible on every supported Python.

    Path.exists() suppresses all OSError exceptions from Python 3.14 onward.
    Explicit stat lets main treat EACCES as shared configuration needing sudo.
    """
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def run(*args: str) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=6, check=False)
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def env_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and not name.startswith("#"):
            value = value.strip()
            values[name] = (
                value[1:-1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}
                else value
            )
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ.get("WINGMAN_HOST_ROOT", "/"))
    etc = root / "etc/wingman"
    try:
        configured = any(
            configured_path_exists(p)
            for p in (
                etc / "oauth-config-path",
                etc / "tenants.toml",
                args.home / ".config/systemd/user/wingman-mcp.service",
            )
        )
    except PermissionError:
        configured = True
    if not configured:
        return 3
    print("Shared HTTP service:")
    state = run(
        "systemctl",
        "--user",
        f"--machine={os.environ.get('WINGMAN_SHARED_USER', 'wingman-shared')}@.host",
        "show",
        "wingman-mcp.service",
        "--property=ActiveState,ActiveEnterTimestamp",
    )
    # systemd's user bus can be inaccessible to an adm operator; never prompt for sudo.
    print("  Unit: " + (state.replace("\n", "; ") if state != "unavailable" else "(needs sudo)"))
    try:
        library = args.home / ".local/share/uv/tools/wingman/lib"
        metadata = []
        try:
            python_dirs = list(library.iterdir())
        except FileNotFoundError:
            python_dirs = []
        for python_dir in python_dirs:
            if not python_dir.name.startswith("python"):
                continue
            packages = python_dir / "site-packages"
            try:
                distributions = list(packages.iterdir())
            except FileNotFoundError:
                continue
            metadata.extend(
                entry / "METADATA"
                for entry in distributions
                if entry.name.startswith("wingman-") and entry.name.endswith(".dist-info")
            )
        versions = [
            line.removeprefix("Version: ")
            for p in metadata
            for line in (read(p) or "").splitlines()
            if line.startswith("Version: ")
        ]
        print("  Version: " + (versions[0] if versions else "unavailable"))
    except PermissionError:
        print("  Version: (needs sudo)")
    except (OSError, UnicodeDecodeError):
        print("  Version: unavailable")
    values: dict[str, str] = {}
    try:
        pointer = read(etc / "oauth-config-path")
        config = Path(pointer.strip()) if pointer else etc / "oauth.env"
        values = env_values(read(config) or "")
        identities_args = shlex.split(values.get("WINGMAN_OAUTH_ARGS", ""))
        identity_path = (
            Path(identities_args[identities_args.index("--oauth-identities") + 1])
            if "--oauth-identities" in identities_args
            else None
        )
        if identity_path is not None:
            state_path = identity_path.with_name(identity_path.stem + "-onboarding.json")
            state_text = read(state_path)
            state_data = (
                json.loads(state_text) if state_text is not None else {"pending": [], "invites": []}
            )
            print(
                f"  Pending: {len(state_data['pending'])}; outstanding invites: "
                f"{sum(1 for row in state_data['invites'] if row.get('expires_at', float('inf')) > time.time())}"
            )
        else:
            print("  Pending / outstanding invites: unavailable")
        notification_values: dict[str, str] = {}
        for source in (
            args.home / ".config/wingman/wingman.env",
            args.home / ".config/wingman/secrets.env",
            config,
            etc / "oauth-web.env",
        ):
            notification_values.update(env_values(read(source) or ""))
        notification = (
            "on" if notification_values.get("WINGMAN_OPERATOR_TODOIST_TOKEN", "").strip() else "off"
        )
        print(f"  Operator notifications: {notification}")
    except PermissionError:
        print("  Pending / outstanding invites / operator notifications: (needs sudo)")
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        print("  Pending / outstanding invites / operator notifications: unavailable")
    try:
        registry = read(etc / "tenants.toml")
        count = len(tomllib.loads(registry).get("tenant", [])) if registry is not None else 0
        print(f"  Tenants: {count}")
    except PermissionError:
        print("  Tenants: (needs sudo)")
    except (OSError, ValueError):
        print("  Tenants: unavailable (invalid registry)")
    port = os.environ.get("WINGMAN_SHARED_PORT", "8789")
    try:
        unit_text = read(args.home / ".config/systemd/user/wingman-mcp.service") or ""
        for line in unit_text.splitlines():
            if line.startswith("ExecStart="):
                argv = shlex.split(line.partition("=")[2])
                if "--port" in argv:
                    candidate = argv[argv.index("--port") + 1]
                    if candidate.isdecimal() and 0 < int(candidate) < 65536:
                        port = candidate
    except PermissionError:
        print("  Probe configuration: (needs sudo); using provisioned default")
    except (OSError, ValueError, IndexError):
        print("  Probe configuration: unavailable; using provisioned default")
    base = f"http://127.0.0.1:{port}"
    print(f"  Loopback: {base}; routes /.well-known/oauth-protected-resource/mcp and /mcp")
    for label, route, expected in (
        ("PRM", "/.well-known/oauth-protected-resource/mcp", "200"),
        ("/mcp", "/mcp", "401"),
    ):
        code = run(
            "curl",
            "--disable",  # Must be first: ignore the operator's curlrc credentials/options.
            "--noproxy",
            "*",  # Loopback health checks must never leave via an inherited proxy.
            "--silent",
            "--max-time",
            "3",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            base + route,
        )
        print(f"  {label}: {code} (expected {expected})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
