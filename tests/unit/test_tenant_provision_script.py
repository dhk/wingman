from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "wingman-add-tenant.sh"


def _fake_root_commands(tmp_path: Path) -> Path:
    commands = tmp_path / "bin"
    commands.mkdir()
    id_command = commands / "id"
    id_command.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
    id_command.chmod(0o755)
    sudo_command = commands / "sudo"
    sudo_command.write_text('#!/bin/sh\nshift 2\nexec "$@"\n', encoding="utf-8")
    sudo_command.chmod(0o755)
    return commands


def test_add_tenant_rejects_an_unusable_oauth_identities_path_early(tmp_path: Path) -> None:
    commands = _fake_root_commands(tmp_path)
    identities_directory = tmp_path / "identities"
    identities_directory.mkdir()

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "taylor",
            "--no-telemetry",
            "--oauth-issuer",
            "https://issuer.example",
            "--oauth-subject",
            "user_123",
            "--oauth-identities",
            str(identities_directory),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "OAuth identity-map path is not a regular file" in result.stderr
    assert "unusable by service account" in result.stderr
    assert "no registry at" not in result.stderr
    assert "initializing workspace" not in result.stdout


def test_add_tenant_preflights_the_binding_before_creating_tenant_state(tmp_path: Path) -> None:
    commands = _fake_root_commands(tmp_path)
    wingman_command = commands / "wingman"
    wingman_command.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" > "$WINGMAN_TEST_CALLS"\n'
        'echo "identity map is malformed or identity is already bound" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    wingman_command.chmod(0o755)
    calls = tmp_path / "calls"
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "taylor",
            "--no-telemetry",
            "--oauth-issuer",
            "https://issuer.example",
            "--oauth-subject",
            "user_123",
            "--oauth-identities",
            str(identities),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "WINGMAN_TEST_CALLS": str(calls),
        },
    )

    assert result.returncode != 0
    assert calls.read_text(encoding="utf-8").strip().endswith("--preflight")
    assert "identity map is malformed or identity is already bound" in result.stderr
    assert "no registry at" not in result.stderr
    assert "initializing workspace" not in result.stdout
    assert "registering" not in result.stdout
