"""Behaviour of scripts/wingman-register-service.sh (#288).

The script is driven end to end against stub ``tailscale`` and
``service-registry`` executables on PATH, because the things worth pinning are
exactly the shell-level ones: which paths it discovers, what it passes to the
helper, and whether it notices when the ledger disagrees with what it declared.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wingman-register-service.sh"

# One funnel, two paths on wingman's port and one belonging to another service.
FUNNEL = {
    "Web": {
        "lobster.example.ts.net:443": {
            "Handlers": {
                "/": {"Proxy": "http://127.0.0.1:8789"},
                "/alexandria": {"Proxy": "http://127.0.0.1:8797"},
                "/shared": {"Proxy": "http://127.0.0.1:8789"},
            }
        }
    },
    "AllowFunnel": {"lobster.example.ts.net:443": True},
}


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _helper(bin_dir: Path, registry: Path, *, honour_every_path: bool = True) -> Path:
    """A stand-in for /usr/local/bin/service-registry.

    Records its argv and maintains just enough of the registry shape for the
    script's own verification step to be meaningful.
    """
    log = bin_dir / "helper.log"
    _stub(
        bin_dir / "service-registry",
        f"""
import json, sys
from pathlib import Path

log = Path({str(log)!r})
registry = Path({str(registry)!r})
honour_every_path = {honour_every_path!r}

argv = sys.argv[1:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")

data = json.loads(registry.read_text()) if registry.exists() else {{"services": {{}}}}
command = next(item for item in argv if item in {{"reserve", "reserve-route"}})
service_id = argv[argv.index(command) + 1]
entry = data["services"].setdefault(service_id, {{"service_id": service_id}})


def value(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


if command == "reserve":
    entry["endpoint"] = {{
        "protocol": "tcp",
        "address": value("--address"),
        "port": int(value("--port")),
        "allocation": "static",
    }}
    entry["owner"] = value("--owner")
    entry["unit"] = value("--unit")
    entry["source"] = value("--source")
else:
    paths = [argv[i + 1] for i, item in enumerate(argv) if item == "--path"]
    if not honour_every_path:      # a helper predating multi-path support
        paths = paths[-1:]
    routes = [
        {{
            "mode": value("--mode"),
            "host": value("--host"),
            "https_port": int(value("--https-port")),
            "path": path,
            "target": value("--target"),
        }}
        for path in paths
    ]
    entry["routes"] = routes
    entry["route"] = routes[0]

registry.write_text(json.dumps(data, indent=2))
""",
    )
    return log


def _tailscale(bin_dir: Path, payload: object | None) -> None:
    body = (
        "import sys; sys.exit(1)"
        if payload is None
        else f"import json; print(json.dumps({payload!r}))"
    )
    _stub(bin_dir / "tailscale", body)


def _run(bin_dir: Path, registry: Path, **env: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    environment["WINGMAN_REGISTRY_HELPER"] = str(bin_dir / "service-registry")
    environment["WINGMAN_REGISTRY_PATH"] = str(registry)
    environment.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def test_declares_every_funnel_path_that_points_at_wingmans_port(
    bin_dir: Path, tmp_path: Path
) -> None:
    registry = tmp_path / "registry.json"
    log = _helper(bin_dir, registry)
    _tailscale(bin_dir, FUNNEL)

    result = _run(bin_dir, registry)

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    route_call = next(call for call in calls if "reserve-route" in call)
    declared = [route_call[i + 1] for i, item in enumerate(route_call) if item == "--path"]
    assert declared == ["/", "/shared"]
    # '/alexandria' is on another port and must not be claimed.
    assert "/alexandria" not in declared


def test_the_endpoint_is_reserved_before_its_route(bin_dir: Path, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    log = _helper(bin_dir, registry)
    _tailscale(bin_dir, FUNNEL)

    assert _run(bin_dir, registry).returncode == 0

    commands = [
        next(item for item in json.loads(line) if item in {"reserve", "reserve-route"})
        for line in log.read_text().splitlines()
    ]
    assert commands == ["reserve", "reserve-route"]


def test_no_health_check_is_declared_because_health_omits_the_service_field(
    bin_dir: Path, tmp_path: Path
) -> None:
    registry = tmp_path / "registry.json"
    log = _helper(bin_dir, registry)
    _tailscale(bin_dir, FUNNEL)

    assert _run(bin_dir, registry).returncode == 0

    # Declaring one would compare a missing 'service' field against 'wingman'
    # and mark the entry permanently stale.
    assert "--health-url" not in log.read_text()


def test_falls_back_to_the_configured_path_when_the_funnel_cannot_be_read(
    bin_dir: Path, tmp_path: Path
) -> None:
    registry = tmp_path / "registry.json"
    log = _helper(bin_dir, registry)
    _tailscale(bin_dir, None)

    result = _run(bin_dir, registry, WINGMAN_SHARED_TAILSCALE_PATH="/shared")

    assert result.returncode == 0, result.stderr
    route_call = next(
        call for call in map(json.loads, log.read_text().splitlines()) if "reserve-route" in call
    )
    assert [route_call[i + 1] for i, item in enumerate(route_call) if item == "--path"] == [
        "/shared"
    ]


def test_a_missing_registry_helper_is_skipped_rather_than_fatal(
    bin_dir: Path, tmp_path: Path
) -> None:
    registry = tmp_path / "registry.json"
    _tailscale(bin_dir, FUNNEL)

    result = _run(bin_dir, registry, WINGMAN_REGISTRY_HELPER=str(bin_dir / "absent"))

    # The helper belongs to another tool's pack; wingman must not require it.
    assert result.returncode == 0, result.stderr
    assert "skipping" in result.stdout
    assert not registry.exists()


def test_a_helper_that_records_only_the_last_path_is_caught(bin_dir: Path, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    _helper(bin_dir, registry, honour_every_path=False)
    _tailscale(bin_dir, FUNNEL)

    result = _run(bin_dir, registry)

    # Silently claiming one of two paths is the failure this script exists to
    # end, so it must fail loudly rather than report success.
    assert result.returncode != 0
    assert "does not match what was just declared" in result.stderr
    assert "multi-path" in result.stderr


def test_rerunning_is_idempotent(bin_dir: Path, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    _helper(bin_dir, registry)
    _tailscale(bin_dir, FUNNEL)

    assert _run(bin_dir, registry).returncode == 0
    first = registry.read_text()
    assert _run(bin_dir, registry).returncode == 0

    assert registry.read_text() == first


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean() -> None:
    result = subprocess.run(
        ["shellcheck", str(SCRIPT)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout
