"""Tenant administration from an MCP session, for a super user (issue #658).

What this module does is exactly the part of onboarding a person that the
shared service's own account can already do: look at the invite and
pending-sign-in queue, reserve a slug, withdraw one. What it deliberately
does NOT do is create the tenant. That writes `/etc/wingman/tenants.toml`,
which is root-owned (RFC-047, `750 root:wingman`); the shared process can
read it and cannot write it, and nothing here loosens that. The last step is
therefore a command for the operator to run, printed exactly, never run.

Everything a person typed or a token carried is data. Slugs are validated
before they appear in a command; issuer and subject come from a verified
sign-in but are still quoted for the shell, because anybody on the internet
can cause a pending row to exist.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingError,
    OAuthOnboardingStore,
    PendingIdentity,
    pending_reference,
)
from wingman.infrastructure.tenants import Tenant

_DEFAULT_DATA_ROOT_PLACEHOLDER = "<data-root>"


def _day(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=UTC).strftime("%Y-%m-%d")


def render_overview(store: OAuthOnboardingStore) -> str:
    """Reserved invites and verified sign-ins awaiting approval. No addresses."""
    try:
        invites = store.invites()
        pending = store.pending()
    except OAuthOnboardingError as exc:
        return f"Could not read the onboarding state: {exc}. Nothing was changed."
    lines = ["Reserved invites:"]
    if invites:
        for invite in invites:
            kind = "email" if invite.email_bound else "slug"
            expires = _day(invite.expires_at) if invite.expires_at is not None else "-"
            lines.append(
                f"  {invite.slug}  kind={kind} created={_day(invite.created_at)} "
                f"expires={expires} status={invite.status()}"
            )
    else:
        lines.append("  none")
    lines.append("Verified sign-ins awaiting approval:")
    if pending:
        for identity in pending:
            lines.append(
                f"  ref={pending_reference(identity.issuer, identity.subject)} "
                f"first_seen={_day(identity.first_seen)} sign_ins={identity.sign_in_count}"
            )
    else:
        lines.append("  none")
    return "\n".join(lines)


def reserve_invite(
    store: OAuthOnboardingStore, slug: str, existing_slugs: set[str], *, confirmed: bool
) -> str:
    """Reserve one slug. Previews unless `confirmed`; creates no workspace or access."""
    slug = slug.strip()
    try:
        store.check_slug(slug, existing_slugs)
        if any(invite.slug == slug for invite in store.invites()):
            raise OAuthOnboardingError(f"invite slug {slug!r} is already reserved")
    except OAuthOnboardingError as exc:
        return f"Not reserved: {exc}. Nothing was changed."
    if not confirmed:
        return (
            f"Preview: would reserve the invite slug {slug!r}. It creates no workspace and "
            "gives nobody access; it only holds the name for one trusted person. "
            "Call again with confirmed=true to reserve it."
        )
    try:
        store.reserve_invite(slug, existing_slugs=existing_slugs)
    except OAuthOnboardingError as exc:
        return f"Not reserved: {exc}. Nothing was changed."
    return (
        f"Reserved invite slug {slug!r}. No workspace or access was created. "
        "Once that person has signed in, use action 'list' to find their reference, then "
        f"action 'approve' with slug {slug!r} and that ref for the command that admits them."
    )


def revoke_invite(store: OAuthOnboardingStore, slug: str, *, confirmed: bool) -> str:
    """Withdraw an unclaimed invite. Previews unless `confirmed`."""
    slug = slug.strip()
    try:
        known = any(invite.slug == slug for invite in store.invites())
    except OAuthOnboardingError as exc:
        return f"Not revoked: {exc}. Nothing was changed."
    if not known:
        return f"No reserved invite {slug!r}. Nothing was changed."
    if not confirmed:
        return (
            f"Preview: would withdraw the reserved invite {slug!r}, deleting any email hash "
            "with it. A workspace already prepared for an email invite stays registered but "
            "unbound. Call again with confirmed=true to withdraw it."
        )
    try:
        removed = store.revoke_invite(slug)
    except OAuthOnboardingError as exc:
        return f"Not revoked: {exc}. Nothing was changed."
    if not removed:
        return f"No reserved invite {slug!r}. Nothing was changed."
    return f"Revoked invite {slug!r}; any email hash was deleted."


def _common_data_root(tenants: Sequence[Tenant]) -> str:
    parents = {str(tenant.data_dir.parent) for tenant in tenants}
    return parents.pop() if len(parents) == 1 else _DEFAULT_DATA_ROOT_PLACEHOLDER


def approve_command(
    store: OAuthOnboardingStore,
    slug: str,
    ref: str,
    *,
    identities_path: Path,
    registry_path: Path,
    tenants: Sequence[Tenant],
) -> str:
    """The exact `sudo wingman tenant oauth-approve` to run. Never runs it."""
    slug = slug.strip()
    ref = ref.strip().upper()
    try:
        # Format only: an email invite's tenant is already in the registry by
        # design, so "already exists" is not a reason to refuse here.
        store.check_slug(slug, set())
        reserved = any(invite.slug == slug for invite in store.invites())
        pending = store.pending()
    except OAuthOnboardingError as exc:
        return f"No command produced: {exc}. Nothing was changed."
    if not reserved:
        return (
            f"No command produced: {slug!r} is not a reserved invite. Reserve it first. "
            "Nothing was changed."
        )
    matches: list[PendingIdentity] = [
        identity
        for identity in pending
        if pending_reference(identity.issuer, identity.subject) == ref
    ]
    if len(matches) != 1:
        return (
            f"No command produced: {len(matches)} waiting sign-ins match ref {ref!r}. "
            "Use action 'list' for the references. Nothing was changed."
        )
    identity = matches[0]
    data_root = _common_data_root(tenants)
    command = " ".join(
        [
            "sudo wingman tenant oauth-approve",
            shlex.quote(slug),
            f"--issuer {shlex.quote(identity.issuer)}",
            f"--subject {shlex.quote(identity.subject)}",
            f"--identities {shlex.quote(str(identities_path))}",
            f"--registry {shlex.quote(str(registry_path))}",
            f"--data-root {shlex.quote(data_root)}",
        ]
    )
    note = ""
    if data_root == _DEFAULT_DATA_ROOT_PLACEHOLDER:
        note = (
            "\nReplace <data-root> with the directory that holds the tenant workspaces "
            "(existing tenants do not share one parent, so I could not infer it)."
        )
    return (
        "Run this on the Wingman host. It was not run from here, because creating the "
        "tenant writes the root-owned registry:\n\n"
        f"{command}{note}\n\n"
        "Approving binds that sign-in to the reserved slug, so check the reference with the "
        "person first."
    )
