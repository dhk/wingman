"""The tenant registry for a shared multi-tenant process (docs/RFC.md RFC-048).

One shared OS process can serve several people at once, share-nothing on
data: each tenant keeps their own SQLite DB, their own capability token
(`<data_dir>/mcp-http-token`, RFC-017, unchanged), and their own API keys
(`<data_dir>/keys.env`, RFC-034, unchanged). This module owns exactly one
new thing: mapping a presented capability token to the tenant it belongs
to.

The registry file itself lists only `{slug, data_dir}` per tenant — no
secrets. Centralizing tokens or API keys here would turn one file's
compromise into every tenant's credentials leaking at once, exactly
backwards from "share-nothing." Tokens and keys are read fresh from each
tenant's own files instead.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from wingman.infrastructure.config import Config
from wingman.infrastructure.keys import KNOWN_KEYS, read_workspace_keys
from wingman.infrastructure.logs import get_logger

_logger = get_logger("tenants")

#: Prefix on a Config.data_dir_source built by Tenant.config(). A config
#: carrying it came from the registry, which means this process is the
#: shared multi-tenant one and other people's workspaces are reachable from
#: it. Solo installs never carry it, and must not be constrained as if they
#: shared a machine with anyone (#333).
TENANT_CONFIG_SOURCE = "tenant registry"


def is_tenant_config(config: Config) -> bool:
    """Whether this Config was bound to a registered tenant."""
    return config.data_dir_source.startswith(TENANT_CONFIG_SOURCE)


_TOKEN_FILENAME = "mcp-http-token"  # mirrors mcp_server._TOKEN_FILENAME

# RFC-047-style default: root-provisioned, matching the global-secrets
# tier's convention of one canonical box-wide path under /etc/wingman/.
DEFAULT_REGISTRY_PATH = Path("/etc/wingman/tenants.toml")


def tenant_registry_path(home: Path | None = None) -> Path:
    """Where the tenant registry lives: the WINGMAN_TENANT_REGISTRY host
    setting if set (host_config.HOST_SETTINGS), else DEFAULT_REGISTRY_PATH.
    Shared by 'wingman-mcp --http --tenant-registry' (when the flag omits
    an explicit path) and the 'wingman tenant' CLI commands, so an
    operator sets it once per box instead of passing --registry everywhere.
    """
    from wingman.infrastructure.host_config import read_host_settings

    override = read_host_settings(home).get("WINGMAN_TENANT_REGISTRY")
    return Path(override).expanduser() if override else DEFAULT_REGISTRY_PATH


class TenantRegistryError(Exception):
    """The tenant registry file is missing, malformed, or names a bad tenant."""


class TenantRegistryUnreadable(TenantRegistryError):
    """The registry is there, but this account cannot read it (#411).

    A subclass so that every existing 'except TenantRegistryError' keeps
    catching it — the failure IS a registry failure — while the handful of
    operator-facing call sites that say "is malformed" can tell the two
    apart. Telling somebody a file they cannot even open is malformed sends
    them to edit it, which is the wrong hour to spend; '/etc/wingman' is
    750 root:wingman precisely so most accounts cannot.
    """


@dataclass(frozen=True)
class Tenant:
    """One tenant's identity in the registry — no secrets attached."""

    slug: str
    data_dir: Path
    #: Where this tenant's feature requests go, when theirs differ from the
    #: box's. Normally unset: one destination per box is the common case,
    #: and a tenant is never asked to choose one (#371).
    feature_repo: str | None = None
    #: The registry's '[defaults] feature_repo' — one destination for every
    #: tenant in this file, copied onto each of them at load time so a
    #: Tenant still answers the question alone. Ranks BELOW an explicit
    #: choice (this tenant's own 'feature_repo', or a workspace file) and
    #: above the host setting; see application.feature_request.
    default_feature_repo: str | None = None
    #: May this tenant run operator-only tools (RFC-068, issue #271)?
    #: False unless the registry entry says otherwise, and deliberately
    #: NOT settable in '[defaults]': a box-wide "everyone is privileged"
    #: is the fail-open this flag exists to prevent. Named per tenant, by
    #: the one person who can edit a root-owned file, or not at all.
    privileged: bool = False

    def token_path(self) -> Path:
        return self.data_dir / _TOKEN_FILENAME

    def read_token(self) -> str | None:
        """This tenant's own capability token, read fresh — never cached
        beyond the caller's own index, so a CLI-side 'wingman mcp
        --rotate-token' run for this tenant is picked up on the next
        index reload with no coordination needed."""
        path = self.token_path()
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def config(self) -> Config:
        """This tenant's Config, with keys resolved ONLY from this
        tenant's own workspace file — never process env, never the
        host/global secrets tiers (RFC-046/047), which are shared by
        whichever account runs the process and would otherwise leak one
        tenant's key to every tenant lacking their own. A tenant with no
        key configured gets 'strict_provider_keys=True' and fails loud
        instead of silently inheriting a shared-process env var.

        github_api_issues_key gets the exact same treatment, not just the
        three provider keys — application.feature_request's default runner
        otherwise reads GITHUB_API_ISSUES_KEY straight from os.environ,
        which under a shared process is whichever account runs it, not any
        particular tenant's own credential. Same isolation guarantee either
        way: a tenant with none of their own fails loud rather than filing
        under a key they never configured.

        'privileged' is passed through explicitly from this entry's own
        flag (RFC-068) — never omitted and left to Config's class default.
        Both say False for an ordinary tenant, but writing it here is what
        makes the ONE registry line the whole answer: a reader comparing
        the registry against what a tenant can do never has to know which
        of two files won.
        """
        workspace_keys = read_workspace_keys(self.data_dir)
        return Config(
            data_dir=self.data_dir,
            feature_repo=self.feature_repo,
            default_feature_repo=self.default_feature_repo,
            data_dir_source=f"{TENANT_CONFIG_SOURCE} ({self.slug})",
            anthropic_api_key=workspace_keys.get(KNOWN_KEYS["anthropic"]),
            voyage_api_key=workspace_keys.get(KNOWN_KEYS["voyage"]),
            openrouter_api_key=workspace_keys.get(KNOWN_KEYS["openrouter"]),
            github_api_issues_key=workspace_keys.get(KNOWN_KEYS["github"]),
            strict_provider_keys=True,
            privileged=self.privileged,
        )


def _feature_repo(value: object, whose: str, path: Path) -> str | None:
    """An owner/name repository from the registry, or None when unset.

    The registry is hand-edited by an operator, so a typo names itself
    here rather than filing somewhere unexpected later.
    """
    if value is None:
        return None
    if not isinstance(value, str) or value.count("/") != 1:
        raise TenantRegistryError(
            f"{whose} in {path} has a malformed 'feature_repo' — it must look like owner/name."
        )
    return value


def _privileged(value: object, slug: str, path: Path) -> bool:
    """This entry's 'privileged' flag: absent means False (RFC-068).

    Only a real TOML boolean counts. 'privileged = "true"' and
    'privileged = 1' are refused rather than coerced, because both
    plausible coercions are wrong in a way nobody would see: truthiness
    would grant the flag to the string "false", and a strict-equality
    check would silently DENY an operator who wrote the quoted form and
    walked away believing they had granted it. A hand-edited root-owned
    file gets told about its typo, like every other field here.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise TenantRegistryError(
            f"tenant {slug!r} in {path} has a malformed 'privileged' — "
            "it must be the bare TOML boolean true or false, unquoted."
        )
    return value


def _refuse_privileged_default(data: dict[str, object], path: Path) -> None:
    """'privileged' is per tenant only — never '[defaults]', never bare.

    A box-wide default of "everyone is privileged" is precisely the
    fail-open this flag exists to prevent, so there is nothing to read
    here. But IGNORING the key would be worse than not supporting it: an
    operator who wrote '[defaults] privileged = true' would be told
    nothing and would believe the grant had happened, and one who wrote
    'privileged = false' there would believe they had revoked something
    they had not. Refusing at load names the mistake while its author is
    still standing in front of the file.
    """
    defaults = data.get("defaults")
    tables: list[tuple[str, dict[str, object]]] = [("the top level", data)]
    if isinstance(defaults, dict):
        tables.insert(0, ("[defaults]", defaults))
    for scope, table in tables:
        if "privileged" in table:
            raise TenantRegistryError(
                f"tenant registry {path} sets 'privileged' at {scope} — privilege is granted "
                "one tenant at a time, on that tenant's own [[tenant]] entry, never to "
                "everybody at once."
            )


def _registry_default_feature_repo(data: dict[str, object], path: Path) -> str | None:
    """The all-tenant destination: '[defaults] feature_repo'.

    A bare top-level 'feature_repo = ...' is accepted as the same thing,
    because an operator will reasonably write it that way — but only when
    it sits ABOVE the first '[[tenant]]'. TOML gives a key appended at the
    bottom of the file to the last table it follows, so a bare key written
    after the tenants silently becomes that one tenant's repo; '[defaults]'
    is a table and cannot be captured that way, which is why it's the form
    the docs give.
    """
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise TenantRegistryError(f"tenant registry {path} needs a [defaults] table.")
    table = _feature_repo(defaults.get("feature_repo"), "[defaults]", path)
    bare = _feature_repo(data.get("feature_repo"), "tenant registry", path)
    if table and bare and table != bare:
        raise TenantRegistryError(
            f"tenant registry {path} sets two different default feature repos "
            f"({bare!r} at the top level, {table!r} under [defaults]) — keep the [defaults] one."
        )
    return table or bare


def load_registry(path: Path) -> list[Tenant]:
    """Parse the tenant registry TOML file: a '[[tenant]]' array of tables,
    each with 'slug' and 'data_dir'. An absent file returns an empty list —
    a shared-process deployment with zero tenants configured yet is a
    valid (if useless) startup state, not an error; a malformed one
    raises, since silently serving zero tenants when the file exists but
    is broken would be a confusing way to fail.

    An optional '[defaults]' table carries settings every tenant in the
    file shares — today just 'feature_repo', the one destination for
    everybody's feature requests, which a per-tenant 'feature_repo' still
    overrides. 'privileged' (RFC-068) is deliberately NOT one of them and
    is refused there; it is granted per tenant or not at all.

    A registry that is THERE but unreadable raises
    'TenantRegistryUnreadable' with the diagnosis (#411). Two reasons it
    cannot share the absent file's empty list. The loud one: 'Path.exists()'
    RAISES on EACCES rather than returning False, so this used to throw a
    bare PermissionError out of every 'wingman tenant' command an operator
    outside the wingman group typed — #401's bug, in the library every one
    of those commands goes through. The quiet one, which would be worse:
    were an unreadable file ever to read as an empty one, a shared process
    would start and serve ZERO tenants while calling that normal — nobody
    locked out with an error, everybody quietly non-existent. Unreadable is
    never empty.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        # Genuinely absent — the deliberate empty-list case above. Read
        # first and catch, rather than ask 'exists()' and then read: one
        # syscall answers both questions, and no window exists in which the
        # file's answer changes between the two.
        return []
    except PermissionError as exc:
        # The diagnosis lives in broadcast.permission_problem, written once
        # for #401 and reused here rather than copied. Imported lazily to
        # keep this module's import graph as it is (broadcast reaches for
        # tenants the same way, from inside its functions).
        from wingman.infrastructure.broadcast import permission_problem

        raise TenantRegistryUnreadable(permission_problem(path) or str(exc)) from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TenantRegistryError(f"tenant registry {path} could not be parsed: {exc}") from exc
    entries = data.get("tenant", [])
    if not isinstance(entries, list):
        raise TenantRegistryError(f"tenant registry {path} needs a [[tenant]] array of tables.")
    default_feature_repo = _registry_default_feature_repo(data, path)
    _refuse_privileged_default(data, path)
    tenants: list[Tenant] = []
    seen_slugs: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TenantRegistryError(f"tenant registry {path} has a malformed [[tenant]] entry.")
        slug = entry.get("slug")
        data_dir = entry.get("data_dir")
        if not isinstance(slug, str) or not slug.strip():
            raise TenantRegistryError(f"tenant registry {path} has an entry with no 'slug'.")
        if not isinstance(data_dir, str) or not data_dir.strip():
            raise TenantRegistryError(f"tenant {slug!r} in {path} has no 'data_dir'.")
        if slug in seen_slugs:
            raise TenantRegistryError(f"tenant registry {path} lists {slug!r} more than once.")
        seen_slugs.add(slug)
        feature_repo = _feature_repo(entry.get("feature_repo"), f"tenant {slug!r}", path)
        tenants.append(
            Tenant(
                slug=slug,
                data_dir=Path(data_dir).expanduser(),
                feature_repo=feature_repo,
                default_feature_repo=default_feature_repo,
                privileged=_privileged(entry.get("privileged"), slug, path),
            )
        )
    return tenants


def _hash(token: str) -> str:
    """A fixed-size, uniformly-distributed key for the token index.

    Capability tokens are 24 bytes of 'secrets.token_urlsafe' entropy
    (mcp_server._http_token), so a plain dict lookup on their hash carries
    no useful timing signal about how close a wrong guess was — unlike a
    linear 'compare_digest' loop over every tenant, which isn't needed
    here at all.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TenantIndex:
    """In-memory 'sha256(token) -> Tenant' lookup for one shared process.

    Built from the registry plus each listed tenant's own token file —
    the registry never stores tokens itself (see module docstring).
    """

    def __init__(self, tenants: list[Tenant]) -> None:
        self._by_slug: dict[str, Tenant] = {}
        self._by_token_hash: dict[str, Tenant] = {}
        self._load(tenants)

    def _load(self, tenants: list[Tenant]) -> None:
        by_slug: dict[str, Tenant] = {}
        by_token_hash: dict[str, Tenant] = {}
        # Tokens that turned out to identify more than one tenant. Held
        # separately so a later tenant carrying an already-seen token cannot
        # reinstate it by writing over the entry we removed.
        ambiguous: set[str] = set()
        for tenant in tenants:
            by_slug[tenant.slug] = tenant
            token = tenant.read_token()
            if token is None:
                continue
            digest = _hash(token)
            if digest in ambiguous:
                _logger.error(
                    "tenant %r shares a capability token with an earlier tenant; "
                    "that token stays disabled",
                    tenant.slug,
                )
                continue
            claimed = by_token_hash.pop(digest, None)
            if claimed is not None:
                # Fail CLOSED. A token that identifies two people identifies
                # nobody: resolving it to either one hands one tenant the
                # other's workspace, silently, and last-writer-wins made which
                # one depend on registry order (#327). Both lose the token;
                # each can be issued a fresh one with 'tenant rotate-token'.
                #
                # Refusing to load the whole registry would be worse — one
                # duplicated token would take every other tenant offline.
                ambiguous.add(digest)
                _logger.error(
                    "tenants %r and %r present the same capability token; disabling it for "
                    "both — rotate it ('wingman tenant rotate-token <slug>') to restore access",
                    claimed.slug,
                    tenant.slug,
                )
                continue
            by_token_hash[digest] = tenant
        self._by_slug = by_slug
        self._by_token_hash = by_token_hash

    @classmethod
    def from_registry_path(cls, path: Path) -> TenantIndex:
        return cls(load_registry(path))

    def resolve(self, token: str) -> Tenant | None:
        """The tenant this presented token belongs to, or None — the
        single check every request-handling call site needs."""
        if not token:
            return None
        return self._by_token_hash.get(_hash(token))

    def reload(self, path: Path) -> None:
        """Re-read the registry and every listed tenant's token file in
        place, so rotating one tenant's token (an ordinary 'wingman mcp
        --rotate-token' run in that tenant's own workspace) or adding a
        new tenant takes effect on a long-lived shared process without
        restarting it — a restart would drop every other tenant's
        in-flight connections, which RFC-048 treats as unacceptable
        (see the removed per-tenant self-restart button, RFC-041).

        This mutates the index IN PLACE, which is what lets a reload reach
        sessions that are already open: 'TenantRoutingASGIApp' binds a
        resolver that looks a tenant up here on every 'load_config()',
        rather than a Config snapshot. That indirection is load-bearing —
        binding the Config itself froze it for the life of a streamable-HTTP
        session, because a ContextVar is copied when a task is created and
        the session task outlives the request that bound it (#404).
        """
        self._load(load_registry(path))

    @property
    def tenants(self) -> list[Tenant]:
        return list(self._by_slug.values())

    def by_slug(self, slug: str) -> Tenant | None:
        return self._by_slug.get(slug)

    def __len__(self) -> int:
        return len(self._by_slug)
