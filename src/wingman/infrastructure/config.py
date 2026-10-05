"""Configuration loading: where the Wingman workspace lives on disk."""

from __future__ import annotations

import contextvars
import os
from collections.abc import Callable, Iterator, Mapping
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
    # Same shape as the three provider keys above, but NOT resolved the same
    # way, and the difference is the whole point (#506). This one isn't a
    # model provider: application.feature_request reads it to authenticate
    # 'gh' (RFC-025). The three provider keys are metered — falling back to
    # a shared one spends the operator's money on a tenant's behalf, so a
    # tenant without their own must fail loud. This key is access to an
    # operator-owned resource that every tenant is ALREADY pointed at by
    # construction ('[defaults] feature_repo'), so a shared fallback is the
    # service, not a leak. See feature_request._resolve_github_key: BYOK
    # first, then the operator's own declared file tiers, never ambient
    # process env.
    github_shared_issues_key: str | None = None
    #: Who this workspace files as, when one shared PAT means GitHub's own
    #: 'opened by' field says the operator for everybody (#506). None falls
    #: back to the box-wide WINGMAN_OPERATOR_NAME host setting, which is one
    #: file per box and therefore cannot tell two tenants apart — so the
    #: tenant registry sets this explicitly from each entry's own slug.
    operator_name: str | None = None
    # Resolve metered keys from this Config's own fields ALONE — never
    # keys.resolve_provider_key's env-or-workspace-file fallback, and never
    # a provider's own internal env read — so a workspace with no key
    # configured fails loud instead of silently inheriting whatever key
    # happens to be set in the process environment (RFC-048).
    #
    # True at the class level, deliberately, for exactly the reason
    # 'privileged' and 'funded' below are False: the value a Config gets
    # by NOT being thought about must be the one that costs a refusal.
    # This flag's safe value is the true one, so the default differs from
    # theirs while the rule is the same. It was False until #532, which is
    # the odd one out of three flags guarding one boundary — and the
    # permissive value of the only one that spends somebody else's money.
    #
    # The two paths that genuinely want the ambient ladder say so: a solo
    # install ('load_config' below — their own machine, their own keys)
    # and the demo workspace, which exists to run on whatever key the
    # person already exported.
    strict_provider_keys: bool = True
    #: Where feature requests from this workspace go, when the tenant
    #: registry says so. None means fall back to the host setting and then
    #: the per-workspace file — see application.feature_request (#371).
    feature_repo: str | None = None
    #: The registry's all-tenant default destination ('[defaults]
    #: feature_repo'), which an explicit per-tenant or per-workspace choice
    #: still outranks — see application.feature_request.
    default_feature_repo: str | None = None
    # Whether this workspace may run operator-only tools (docs/RFC.md
    # RFC-068, issue #271): coaching somebody else, carving their profile
    # off into another workspace, and — when it exists — writing a
    # broadcast every other tenant is shown.
    #
    # False at the class level, deliberately, exactly like
    # 'strict_provider_keys' above: every Config a future code path builds
    # without thinking about privilege is UNprivileged, so forgetting to
    # set it costs a refusal rather than silently handing an operator tool
    # to a tenant. The two paths that are genuinely privileged say so
    # explicitly — 'load_config()' below for a solo install (their own
    # machine, their own workspace), and 'Tenant.config()' from the
    # registry's own 'privileged' flag.
    privileged: bool = False
    # Whether the OPERATOR pays for this workspace's metered inference
    # (#514). False means strict_provider_keys is absolute: no key of your
    # own, no model calls. True lets the three metered keys fall back to
    # the operator's DECLARED file tiers — never the ambient environment,
    # exactly as the shared issues key does (#506).
    #
    # False at the class level for the same reason as 'privileged' above,
    # and more sharply: this one spends real money. A Config built without
    # thinking about funding costs a refusal, not a bill. It is also
    # deliberately per-tenant rather than box-wide — see
    # tenants._refuse_funded_default — so adding a tenant never silently
    # adds them to the operator's invoice.
    funded: bool = False

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
#
# It holds a RESOLVER, not a Config, and that is the fix for #404. A
# ContextVar is copied when a task is created, and under streamable HTTP
# a tool call does not run in the task of the POST that carried it — the
# transport creates a long-lived session task at initialize and delivers
# later messages into it. A bound Config therefore froze at whatever the
# registry said when the session opened, permanently: 'privileged = true'
# plus a SIGHUP changed nothing across two client reconnects, and only a
# process restart fixed it. A resolver re-reads the live TenantIndex on
# every call, so a stale ContextVar still yields a current Config.
_tenant_config: contextvars.ContextVar[Callable[[], Config] | None] = contextvars.ContextVar(
    "wingman_tenant_config", default=None
)


@contextmanager
def tenant_config_scope(config: Config | Callable[[], Config]) -> Iterator[None]:
    """Bind the Config every 'load_config()' call returns for the duration
    of this block (and anything awaited within it).

    Accepts either a Config or a zero-argument callable returning one.
    Prefer the CALLABLE from anything long-lived: a Config binds the value
    as it was at bind time, and a ContextVar is copied per task, so a
    session that outlives a registry reload keeps serving the old one
    (#404). A plain Config stays supported because it is the right thing
    for a scope that lives and dies inside one request, and for tests.

    The shared multi-tenant HTTP server uses this to scope one incoming
    request to its resolved tenant, instead of threading a tenant
    parameter through every tool function and 'load_config()' call site
    individually.
    """
    resolve = config if callable(config) else (lambda: config)
    token = _tenant_config.set(resolve)
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

    Both non-tenant branches set 'privileged=True' EXPLICITLY rather than
    leaning on a permissive default (RFC-068): whoever resolved a
    workspace from their own environment is running on their own machine,
    with a shell, and can already do anything an operator tool does. The
    explicitness is the point — 'Config.privileged' is False at the class
    level so that a path which never thought about privilege is
    unprivileged, and that only holds if the privileged paths name
    themselves.
    """
    if env is None:
        bound = _tenant_config.get()
        if bound is not None:
            # Called, not cached: the whole point of #404 is that this
            # answers with the registry as it stands NOW, not as it stood
            # when whatever task we are running in was created.
            return bound()
    environment = os.environ if env is None else env
    override = environment.get(ENV_DATA_DIR, "").strip()
    if override:
        return Config(
            data_dir=Path(override).expanduser(),
            data_dir_source=f"{ENV_DATA_DIR} environment variable",
            privileged=True,
            # A solo install: their machine, their exports, their keys.
            # The ambient ladder is the point here, not a leak (#532).
            strict_provider_keys=False,
        )
    return Config(
        data_dir=Path(user_data_dir(APP_NAME)),
        data_dir_source="platform user data directory",
        privileged=True,
        strict_provider_keys=False,  # solo install — see above (#532)
    )
