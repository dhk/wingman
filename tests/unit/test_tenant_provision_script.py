from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "wingman-add-tenant.sh"
PROVISION_SCRIPT = ROOT / "scripts" / "wingman-provision-shared.sh"


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


def _oauth_service_fixture(commands: Path, tmp_path: Path, identities: Path) -> Path:
    config = tmp_path / "oauth.env"
    config.write_text(
        "WINGMAN_OAUTH_ISSUER=https://issuer.example\n"
        "WINGMAN_OAUTH_AUDIENCE=https://wingman.example/mcp\n"
        "WINGMAN_OAUTH_JWKS_URI=https://issuer.example/jwks\n"
        f"WINGMAN_OAUTH_IDENTITIES={identities}\n",
        encoding="utf-8",
    )
    curl_command = commands / "curl"
    curl_command.write_text(
        "#!/bin/sh\n"
        'echo \'{"resource":"https://wingman.example/mcp",'
        '"authorization_servers":["https://issuer.example"]}\'\n',
        encoding="utf-8",
    )
    curl_command.chmod(0o755)
    return config


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
    config = _oauth_service_fixture(commands, tmp_path, identities)
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")

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
            "WINGMAN_SHARED_OAUTH_CONFIG": str(config),
            "WINGMAN_SHARED_REGISTRY": str(registry),
        },
    )

    assert result.returncode != 0
    assert calls.read_text(encoding="utf-8").strip().endswith("--reserve")
    assert "identity map is malformed or identity is already bound" in result.stderr
    assert "no registry at" not in result.stderr
    assert "initializing workspace" not in result.stdout
    assert "registering" not in result.stdout


def test_oauth_tenant_is_refused_when_the_shared_service_has_no_oauth_config(
    tmp_path: Path,
) -> None:
    commands = _fake_root_commands(tmp_path)
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
            "WINGMAN_SHARED_OAUTH_CONFIG": str(tmp_path / "missing-oauth.env"),
        },
    )

    assert result.returncode != 0
    assert "shared service is not configured for OAuth" in result.stderr
    assert "no registry at" not in result.stderr
    assert "initializing workspace" not in result.stdout


def test_oauth_tenant_is_refused_when_live_metadata_does_not_match(tmp_path: Path) -> None:
    commands = _fake_root_commands(tmp_path)
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    config = _oauth_service_fixture(commands, tmp_path, identities)
    curl_command = commands / "curl"
    curl_command.write_text(
        "#!/bin/sh\n"
        'echo \'{"resource":"https://wrong.example/mcp",'
        '"authorization_servers":["https://issuer.example"]}\'\n',
        encoding="utf-8",
    )
    curl_command.chmod(0o755)

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
            "WINGMAN_SHARED_OAUTH_CONFIG": str(config),
        },
    )

    assert result.returncode != 0
    assert "OAuth route is not ready" in result.stderr
    assert "initializing workspace" not in result.stdout


def test_shared_provisioner_wires_saved_oauth_settings_into_the_service() -> None:
    body = PROVISION_SCRIPT.read_text(encoding="utf-8")

    assert "WINGMAN_SHARED_OAUTH_ISSUER" in body
    assert "WINGMAN_SHARED_OAUTH_AUDIENCE" in body
    assert "WINGMAN_SHARED_OAUTH_JWKS_URI" in body
    assert "WINGMAN_SHARED_OAUTH_IDENTITIES" in body
    assert "EnvironmentFile=-$OAUTH_CONFIG_PATH" in body
    assert "\\$WINGMAN_OAUTH_ARGS" in body
    assert "systemctl --user restart wingman-mcp.service" in body


def test_shared_provisioner_never_reowns_an_existing_identity_directory() -> None:
    body = PROVISION_SCRIPT.read_text(encoding="utf-8")

    create_guard = 'if [ ! -d "$OAUTH_IDENTITIES_PARENT" ]; then'
    create_command = (
        'install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$OAUTH_IDENTITIES_PARENT"'
    )
    assert create_guard in body
    assert body.index(create_guard) < body.index(create_command)
    assert "existing OAuth identity-map parent is not writable" in body


def test_interactive_choice_precedes_reservation_and_reservation_is_renewed_before_registry() -> (
    None
):
    body = SCRIPT.read_text(encoding="utf-8")

    assert body.index("\nresolve_telemetry\n") < body.index("--reserve)")
    assert body.index("--renew-reservation") < body.index('say "registering')


def test_oauth_only_funding_guidance_does_not_claim_a_browser_key_page_exists() -> None:
    body = SCRIPT.read_text(encoding="utf-8")

    assert "Self-funded browser key entry is not available for OAuth-only tenants yet" in body
    assert "OAuth-only tenant can make NO model call yet" in body
