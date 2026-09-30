from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "wingman-add-tenant.sh"


def test_add_tenant_rejects_an_unusable_oauth_identities_path_early(tmp_path: Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    id_command = commands / "id"
    id_command.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
    id_command.chmod(0o755)
    sudo_command = commands / "sudo"
    sudo_command.write_text("#!/bin/sh\nshift 2\nexec \"$@\"\n", encoding="utf-8")
    sudo_command.chmod(0o755)
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
