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


@dataclass(frozen=True)
class Tenant:
    """One tenant's identity in the registry — no secrets attached."""

    slug: str
    data_dir: Path

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
        """
        workspace_keys = read_workspace_keys(self.data_dir)
        return Config(
            data_dir=self.data_dir,
            data_dir_source=f"tenant registry ({self.slug})",
            anthropic_api_key=workspace_keys.get(KNOWN_KEYS["anthropic"]),
            voyage_api_key=workspace_keys.get(KNOWN_KEYS["voyage"]),
            strict_provider_keys=True,
        )


def load_registry(path: Path) -> list[Tenant]:
    """Parse the tenant registry TOML file: a '[[tenant]]' array of tables,
    each with 'slug' and 'data_dir'. An absent file returns an empty list —
    a shared-process deployment with zero tenants configured yet is a
    valid (if useless) startup state, not an error; a malformed one
    raises, since silently serving zero tenants when the file exists but
    is broken would be a confusing way to fail.
    """
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise TenantRegistryError(f"tenant registry {path} could not be parsed: {exc}") from exc
    entries = data.get("tenant", [])
    if not isinstance(entries, list):
        raise TenantRegistryError(f"tenant registry {path} needs a [[tenant]] array of tables.")
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
        tenants.append(Tenant(slug=slug, data_dir=Path(data_dir).expanduser()))
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
        for tenant in tenants:
            by_slug[tenant.slug] = tenant
            token = tenant.read_token()
            if token is not None:
                by_token_hash[_hash(token)] = tenant
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
        """
        self._load(load_registry(path))

    @property
    def tenants(self) -> list[Tenant]:
        return list(self._by_slug.values())

    def by_slug(self, slug: str) -> Tenant | None:
        return self._by_slug.get(slug)

    def __len__(self) -> int:
        return len(self._by_slug)
