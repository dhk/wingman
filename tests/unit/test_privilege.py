"""Operator-only tools and the privilege that gates them (docs/RFC.md
RFC-068, issue #271).

Every one of these fails without the change: before it, `coach_persona`
and `carve_off_persona` ran for anybody who could reach them, which on a
shared box (RFC-048) is every tenant holding a valid capability token.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wingman.application.coaching import find_or_create_persona
from wingman.application.interview import capture_interview_reaction
from wingman.infrastructure.config import (
    ENV_DATA_DIR,
    Config,
    load_config,
    tenant_config_scope,
)
from wingman.infrastructure.privilege import operator_only_refusal
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant, load_registry

WHY_PRO = "She spent decades building trust with a species that can't reciprocate in words."


def _seed_persona_capture(config: Config, persona_name: str = "Mike Chen") -> None:
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona(persona_name, storage)
        capture_interview_reaction(
            "values_pro", "Jane Goodall", WHY_PRO, config, storage, persona_id=persona.persona_id
        )


@pytest.fixture
def shared_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Tenant]:
    """One registry, two tenants: 'dhk' privileged, 'trent' ordinary."""
    dhk_dir = tmp_path / "tenants" / "dhk"
    trent_dir = tmp_path / "tenants" / "trent"
    for directory in (dhk_dir, trent_dir):
        directory.mkdir(parents=True)
        Storage(directory / "wingman.db").close()
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "dhk"\ndata_dir = "{dhk_dir}"\nprivileged = true\n\n'
        f'[[tenant]]\nslug = "trent"\ndata_dir = "{trent_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    return {tenant.slug: tenant for tenant in load_registry(registry)}


@pytest.fixture
def solo_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "solo"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


# --- the flag itself ---------------------------------------------------------


def test_config_privileged_is_false_at_the_class_level(tmp_path: Path) -> None:
    """The fail-closed guarantee, asserted on the DEFAULT rather than on any
    caller: a Config built by a code path that never thought about privilege
    is unprivileged. Flipping this default would silently grant every such
    path an operator tool, which is the whole failure mode #271 names."""
    assert Config.model_fields["privileged"].default is False
    config = Config(data_dir=tmp_path, data_dir_source="a path that thought about nothing else")
    assert config.privileged is False


def test_the_solo_paths_declare_themselves_privileged_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both non-tenant branches of load_config(), because somebody resolving
    a workspace from their own environment is on their own machine and can
    already do anything an operator tool does. Explicitly, not by default —
    the class default above is what makes forgetting safe."""
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "solo"))
    assert load_config().privileged is True
    monkeypatch.delenv(ENV_DATA_DIR)
    assert load_config().privileged is True  # platform user data directory


def test_a_tenant_config_carries_the_registrys_flag(shared_box: dict[str, Tenant]) -> None:
    assert shared_box["dhk"].privileged is True
    assert shared_box["trent"].privileged is False
    assert shared_box["dhk"].config().privileged is True
    assert shared_box["trent"].config().privileged is False


def test_operator_only_refusal_answers_none_only_when_privileged(tmp_path: Path) -> None:
    allowed = Config(data_dir=tmp_path, data_dir_source="solo", privileged=True)
    refused = Config(data_dir=tmp_path, data_dir_source="tenant")
    assert operator_only_refusal(allowed, "coach_persona") is None
    assert "coach_persona" in (operator_only_refusal(refused, "coach_persona") or "")


def test_the_refusal_sends_nobody_after_a_file_they_cannot_see(tmp_path: Path) -> None:
    """A tenant cannot read /etc/wingman/tenants.toml (RFC-047, 750
    root:wingman) and cannot edit it, so naming the file or the flag would
    be an instruction that cannot be followed. The message says the tool
    isn't theirs and stops."""
    message = operator_only_refusal(Config(data_dir=tmp_path, data_dir_source="tenant"), "x") or ""
    for leak in ("tenants.toml", "privileged", "registry", "/etc/wingman"):
        assert leak not in message
    assert "operator" in message


# --- coach_persona -----------------------------------------------------------


def test_coach_persona_refuses_every_action_for_an_unprivileged_tenant(
    shared_box: dict[str, Tenant],
) -> None:
    """Including the read-only actions: 'list' names every person this
    workspace has ever coached, and 'who' is only useful to somebody who
    could 'set'."""
    from wingman.mcp_server import coach_persona

    config = shared_box["trent"].config()
    with tenant_config_scope(config):
        for action in ("set", "who", "list", "clear"):
            assert "isn't available in this workspace" in coach_persona(action, "Mike Chen")

    with Storage(config.db_path) as storage:
        assert storage.list_personas() == []  # the refused 'set' created nothing


def test_coach_persona_runs_for_a_privileged_tenant(shared_box: dict[str, Tenant]) -> None:
    from wingman.mcp_server import coach_persona

    with tenant_config_scope(shared_box["dhk"].config()):
        assert "Acting as: coach for Mike Chen." in coach_persona("set", "Mike Chen")
        assert coach_persona("who") == "Acting as: coach for Mike Chen."
        assert coach_persona("clear") == "Acting as: yourself."


def test_coach_persona_runs_on_a_solo_install(solo_workspace: Config) -> None:
    from wingman.mcp_server import coach_persona

    assert "Acting as: coach for Mike Chen." in coach_persona("set", "Mike Chen")


# --- carve_off_persona -------------------------------------------------------


def test_carve_off_persona_refuses_an_unprivileged_tenant(
    shared_box: dict[str, Tenant], tmp_path: Path
) -> None:
    """The target here is legal (a brand-new directory outside the registry)
    and the persona exists — so what refuses this is the privilege gate and
    nothing else."""
    from wingman.mcp_server import carve_off_persona

    config = shared_box["trent"].config()
    _seed_persona_capture(config)
    target = tmp_path / "mike"
    with tenant_config_scope(config):
        assert "isn't available in this workspace" in carve_off_persona("Mike Chen", str(target))
    assert not target.exists()  # refused before anything was written


def test_carve_off_persona_runs_for_a_privileged_tenant(
    shared_box: dict[str, Tenant], tmp_path: Path
) -> None:
    from wingman.mcp_server import carve_off_persona

    config = shared_box["dhk"].config()
    _seed_persona_capture(config)
    target = tmp_path / "mike"
    with tenant_config_scope(config):
        assert "Carved off 'Mike Chen'" in carve_off_persona("Mike Chen", str(target))
    with Storage(target / "wingman.db") as storage:
        assert len(storage.list_profile_items()) == 1


def test_carve_off_persona_runs_on_a_solo_install(solo_workspace: Config, tmp_path: Path) -> None:
    from wingman.mcp_server import carve_off_persona

    _seed_persona_capture(solo_workspace)
    target = tmp_path / "mike-solo"
    assert "Carved off 'Mike Chen'" in carve_off_persona("Mike Chen", str(target))


# --- everything else is untouched --------------------------------------------


def test_no_other_tool_gained_a_gate() -> None:
    """Cheap and specific: only deliberately operator-wide tools are gated."""
    from wingman import mcp_server

    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    gated = re.findall(r'operator_only_refusal\(config, "(\w+)"\)', source)
    assert gated == ["usage_all_tenants", "coach_persona", "carve_off_persona"]


def test_an_ordinary_tool_still_works_for_an_unprivileged_tenant(
    shared_box: dict[str, Tenant],
) -> None:
    """The gate is authorization for two tools, not a hosted-mode downgrade:
    a tenant's own workspace tools are exactly as they were."""
    from wingman.mcp_server import status

    with tenant_config_scope(shared_box["trent"].config()):
        report = status()
    assert "isn't available in this workspace" not in report
    assert "Source records:" in report
