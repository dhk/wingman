import asyncio
from pathlib import Path

from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config, tenant_config_scope


def test_env_var_overrides(tmp_path: Path) -> None:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "workspace")})
    assert config.data_dir == tmp_path / "workspace"
    assert ENV_DATA_DIR in config.data_dir_source


def test_blank_env_var_is_ignored() -> None:
    config = load_config(env={ENV_DATA_DIR: "  "})
    assert config.data_dir_source == "platform user data directory"


def test_platform_default_is_absolute_and_not_cwd_relative() -> None:
    config = load_config(env={})
    assert config.data_dir.is_absolute()
    assert config.data_dir_source == "platform user data directory"
    assert "wingman" in str(config.data_dir).lower()


def test_workspace_paths_derive_from_data_dir(tmp_path: Path) -> None:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    assert config.db_path == tmp_path / "wingman.db"
    assert config.inbox_dir == tmp_path / "inbox"
    assert config.reports_dir == tmp_path / "reports"


def test_tenant_config_scope_overrides_load_config(tmp_path: Path) -> None:
    """RFC-048: every 'load_config()' call inside the scope — the exact
    call every MCP tool/webui handler already makes — returns the bound
    tenant Config instead of resolving WINGMAN_DATA_DIR from env."""
    tenant_config = Config(data_dir=tmp_path / "jason", data_dir_source="tenant registry (jason)")
    assert load_config().data_dir != tenant_config.data_dir  # outside: ordinary resolution
    with tenant_config_scope(tenant_config):
        assert load_config() is tenant_config
    assert load_config().data_dir != tenant_config.data_dir  # unbound again after the block


def test_explicit_env_bypasses_tenant_config_scope(tmp_path: Path) -> None:
    """An explicit 'env=' is a deliberate escape hatch — used by tests and
    any caller wanting env-based resolution regardless of tenant context."""
    tenant_config = Config(data_dir=tmp_path / "jason", data_dir_source="tenant registry (jason)")
    with tenant_config_scope(tenant_config):
        config = load_config(env={ENV_DATA_DIR: str(tmp_path / "elsewhere")})
    assert config.data_dir == tmp_path / "elsewhere"


def test_tenant_config_scope_is_isolated_across_concurrent_asyncio_tasks(tmp_path: Path) -> None:
    """The whole design's safety property: two tenants' requests running
    concurrently on the same shared process's event loop must never see
    each other's Config, even with no thread involved (a ContextVar is
    copied per asyncio Task, not shared like 'os.environ' would be)."""
    jason_config = Config(data_dir=tmp_path / "jason", data_dir_source="tenant registry (jason)")
    bob_config = Config(data_dir=tmp_path / "bob", data_dir_source="tenant registry (bob)")

    async def resolve_as(tenant_config: Config, delay: float) -> Path:
        with tenant_config_scope(tenant_config):
            await asyncio.sleep(delay)  # yield control mid-scope, interleaving the two tasks
            return load_config().data_dir

    async def run() -> tuple[Path, Path]:
        return await asyncio.gather(
            resolve_as(jason_config, 0.02),
            resolve_as(bob_config, 0.01),
        )

    jason_seen, bob_seen = asyncio.run(run())
    assert jason_seen == jason_config.data_dir
    assert bob_seen == bob_config.data_dir
