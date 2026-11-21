"""oauth_resource is the tenant-free half of oauth_bearer (RFC-081).

The behavioural coverage of token validation, JWKS caching and the metadata
document lives in test_oauth_bearer.py and runs through the names oauth_bearer
re-exports. These tests pin the two promises the split makes: the re-exports
ARE the moved objects (not copies that could drift), and the new module stays
usable without any tenant machinery.
"""

from __future__ import annotations

import subprocess
import sys

from wingman.infrastructure import oauth_bearer, oauth_resource

MOVED = (
    "ALLOWED_ALGORITHMS",
    "BearerError",
    "BearerIdentity",
    "KeyResolver",
    "OAuthConfigError",
    "OAuthSettings",
    "_https_or_loopback",
    "_metadata_paths",
    "build_oauth_settings",
    "jwks_key_resolver",
    "validate_access_token",
)


def test_oauth_bearer_reexports_the_moved_objects_not_copies() -> None:
    for name in MOVED:
        assert getattr(oauth_bearer, name) is getattr(oauth_resource, name), name
    assert set(MOVED) <= set(oauth_bearer.__all__)


def test_oauth_resource_imports_without_any_tenant_machinery() -> None:
    probe = (
        "import sys\n"
        "import wingman.infrastructure.oauth_resource\n"
        "coupled = [m for m in ('wingman.infrastructure.oauth_bearer',\n"
        "                       'wingman.infrastructure.tenants',\n"
        "                       'wingman.infrastructure.tenant_asgi',\n"
        "                       'wingman.infrastructure.oauth_onboarding')\n"
        "           if m in sys.modules]\n"
        "print(','.join(coupled))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
