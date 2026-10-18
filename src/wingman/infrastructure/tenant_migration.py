"""Read migration state and atomically retire legacy capability credentials."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import tomlkit
from tomlkit.items import AoT, Array, InlineTable, Table

from wingman.infrastructure.oauth_bearer import IdentityMap
from wingman.infrastructure.oauth_onboarding import OAuthOnboardingError, _registry_lock
from wingman.infrastructure.oauth_service_issuers import ServiceIssuers
from wingman.infrastructure.tenants import TenantRegistryError, load_registry


class MigrationError(Exception):
    """Nothing was changed, with an operator-safe explanation."""


def bound_issuers(identities: IdentityMap, slug: str) -> set[str]:
    return {issuer for (issuer, _subject), target in identities.entries().items() if target == slug}


def retire_url(
    registry: Path,
    identities: IdentityMap | None,
    issuers: ServiceIssuers | None,
    slug: str,
    *,
    undo: bool = False,
    force: bool = False,
) -> None:
    """Validate and replace under the SAME lock used by tenant provisioning.

    Preserve comments, unknown keys, file ownership and permissions. The token
    is untouched. A failed replace leaves the previous registry intact.
    """
    temporary: str | None = None
    try:
        with _registry_lock(registry):
            tenant = next((item for item in load_registry(registry) if item.slug == slug), None)
            if tenant is None:
                raise MigrationError("No such tenant; nothing changed.")
            if not undo and not force:
                if identities is None or issuers is None:
                    raise MigrationError("OAuth bindings are unavailable; nothing changed.")
                if tenant.privileged:
                    raise MigrationError(
                        "Refusing privileged tenant without --force; nothing changed."
                    )
                if not set(issuers.all) <= bound_issuers(identities, slug):
                    raise MigrationError(
                        "An identity must be bound under every trusted issuer; "
                        "use oauth-bind first, or --force. Nothing changed."
                    )
            document = tomlkit.parse(registry.read_text(encoding="utf-8"))
            entries = document["tenant"]
            if not isinstance(entries, (AoT, Array)):
                raise MigrationError("Registry needs [[tenant]] tables; nothing changed.")
            for entry in entries:
                if isinstance(entry, (Table, InlineTable)) and entry.get("slug") == slug:
                    entry["capability_url_retired"] = not undo
                    break
            metadata = registry.stat()
            fd, temporary = tempfile.mkstemp(prefix=f".{registry.name}.", dir=registry.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                # Root operator replacements must remain readable by the service group.
                os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
                os.fchmod(handle.fileno(), metadata.st_mode & 0o777)
                handle.write(tomlkit.dumps(document))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, registry)
            temporary = None
    except (OSError, TenantRegistryError, OAuthOnboardingError) as exc:
        raise MigrationError(
            f"Registry update failed ({type(exc).__name__}); previous registry preserved."
        ) from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
