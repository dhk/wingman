"""Configuration loading: where the Wingman workspace lives on disk."""

from __future__ import annotations

import contextvars
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import BaseModel

APP_NAME = "wingman"
ENV_DATA_DIR = "WINGMAN_DATA_DIR"


class Config(BaseModel):
    """Resolved workspace configuration."""

    data_dir: Path
    data_dir_source: str
    # Explicit per-workspace provider credentials (docs/RFC.md RFC-048).
    # None means "no override" — providers.router falls back to
    # keys.resolve_provider_key's read-only env-or-workspace-file lookup,
    # unchanged from single-tenant behavior. A caller that resolves
    # multiple tenants in one process (RFC-048) sets these explicitly per
    # tenant instead of relying on process-wide environment state.
    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None
    openrouter_api_key: str | None = None
    # Same shape as the three provider keys above, resolved the same way —
    # but this one isn't a model provider at all: application.feature_request
    # reads it directly to authenticate 'gh' (RFC-025). Kept alongside the
    # provider keys rather than off in feature_request.py's own module so a
    # tenant's credential isolation (below) covers it too, not just the
    # three keys that happen to be providers.router's concern.
    github_api_issues_key: str | None = None
    # True only for a per-tenant Config built by a shared multi-tenant
    # process (RFC-048, infrastructure.tenants.Tenant.config()). Tells
    # providers.router to resolve keys from these two fields ALONE — never
    # keys.resolve_provider_key's env-or-workspace-file fallback, and never
    # a provider's own internal env read — so a tenant with no key
    # configured fails loud instead of silently inheriting whatever key
    # happens to be set in the shared process's environment. False
    # (default) preserves today's single-tenant/CLI/stdio ladder exactly.
    strict_provider_keys: bool = False
    #: Where feature requests from this workspace go, when the tenant
    #: registry says so. None means fall back to the host setting and then
    #: the per-workspace file — see application.feature_request (#371).
    feature_repo: str | None = None
    #: The registry's all-tenant default destination ('[defaults]
    #: feature_repo'), which an explicit per-tenant or per-workspace choice
    #: still outranks — see application.feature_request.
    default_feature_repo: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wingman.db"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def models_config_path(self) -> Path:
        return self.data_dir / "models.toml"

    @property
    def installations_config_path(self) -> Path:
        return self.data_dir / "installations.toml"


# Bound only by the shared multi-tenant HTTP server (RFC-048) for the
# duration of one incoming request, via 'tenant_config_scope' below. CLI
# and stdio-mode MCP (single-workspace-per-invocation) never touch this —
# their calls always fall through to the env-var resolution unchanged.
_tenant_config: contextvars.ContextVar[Config | None] = contextvars.ContextVar(
    "wingman_tenant_config", default=None
)


@contextmanager
def tenant_config_scope(config: Config) -> Iterator[None]:
    """Bind 'config' as the Config every 'load_config()' call returns for
    the duration of this block (and anything awaited within it — safe
    under asyncio, since a ContextVar is copied per task, not shared
    process-wide like 'os.environ').

    The shared multi-tenant HTTP server uses this to scope one incoming
    request to its resolved tenant, instead of threading a tenant
    parameter through every tool function and 'load_config()' call site
    individually.
    """
    token = _tenant_config.set(config)
    try:
        yield
    finally:
        _tenant_config.reset(token)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Resolve the data directory: a bound tenant Config first (see
    'tenant_config_scope'), else WINGMAN_DATA_DIR if set, else the
    platform user data dir.

    An explicit 'env' always bypasses tenant binding — it's a deliberate
    "resolve as if this were the environment" request, used by tests and
    by any caller that wants env-based resolution regardless of context.
    Never defaults to a path relative to the invoking directory — an
    installed CLI must not scatter data wherever it happens to be run.
    """
    if env is None:
        tenant_config = _tenant_config.get()
        if tenant_config is not None:
            return tenant_config
    environment = os.environ if env is None else env
    override = environment.get(ENV_DATA_DIR, "").strip()
    if override:
        return Config(
            data_dir=Path(override).expanduser(),
            data_dir_source=f"{ENV_DATA_DIR} environment variable",
        )
    return Config(
        data_dir=Path(user_data_dir(APP_NAME)),
        data_dir_source="platform user data directory",
    )
