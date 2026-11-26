"""Tenant administration from an MCP session, for a super user (issue #658).

What this module does is the part of onboarding a person that the shared
service's own account can do: look at the invite and pending-sign-in queue,
reserve a slug, withdraw one, and approve a waiting sign-in. The one step
that account cannot do is write `/etc/wingman/tenants.toml`, which is
root-owned (RFC-047, `750 root:wingman`) and stays that way. Approval does it
through a standalone root-owned helper the operator installs on purpose
(RFC-086); with no helper installed, approval falls back to printing the exact
command for the operator to run (RFC-085). This module never writes the
registry itself.

Everything a person typed or a token carried is data. Slugs are validated
before they appear in a command; issuer and subject come from a verified
sign-in but are still quoted for the shell, because anybody on the internet
can cause a pending row to exist.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingError,
    OAuthOnboardingStore,
    PendingIdentity,
    approve_pending_tenant,
    pending_reference,
)
from wingman.infrastructure.tenant_process import TenantProcessSignalError
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


def _resolve_waiting(store: OAuthOnboardingStore, slug: str, ref: str) -> PendingIdentity | str:
    """The one waiting sign-in this slug and ref name, or the refusal text."""
    slug = slug.strip()
    ref = ref.strip().upper()
    try:
        # Format only: an email invite's tenant is already in the registry by
        # design, so "already exists" is not a reason to refuse here.
        store.check_slug(slug, set())
        reserved = any(invite.slug == slug for invite in store.invites())
        pending = store.pending()
    except OAuthOnboardingError as exc:
        return f"{exc}"
    if not reserved:
        return f"{slug!r} is not a reserved invite. Reserve it first"
    matches: list[PendingIdentity] = [
        identity
        for identity in pending
        if pending_reference(identity.issuer, identity.subject) == ref
    ]
    if len(matches) != 1:
        return (
            f"{len(matches)} waiting sign-ins match ref {ref!r}. "
            "Use action 'list' for the references"
        )
    return matches[0]


def _render_command(
    identity: PendingIdentity,
    slug: str,
    *,
    identities_path: Path,
    registry_path: Path,
    tenants: Sequence[Tenant],
) -> str:
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
    found = _resolve_waiting(store, slug, ref)
    if isinstance(found, str):
        return f"No command produced: {found}. Nothing was changed."
    return _render_command(
        found, slug, identities_path=identities_path, registry_path=registry_path, tenants=tenants
    )


def approve(
    store: OAuthOnboardingStore,
    slug: str,
    ref: str,
    *,
    confirmed: bool,
    identities_path: Path,
    registry_path: Path,
    tenants: Sequence[Tenant],
    trusted_issuers: Sequence[str],
    service_user: str,
    helper_available: bool,
    data_root: Path | None,
    register: Callable[[Path, str, Path], object],
    reload_service: Callable[[Path], int | None],
) -> str:
    """Admit one waiting sign-in as the reserved slug, through the root helper.

    Falls back to the printed command (never silently) when the helper is not
    installed. Previews unless `confirmed`, because approving binds that
    person's identity to a workspace and cannot be taken back from chat.
    """
    slug = slug.strip()
    found = _resolve_waiting(store, slug, ref)
    if not helper_available or data_root is None:
        return (
            approve_command(
                store,
                slug,
                ref,
                identities_path=identities_path,
                registry_path=registry_path,
                tenants=tenants,
            )
            + "\n\nThe registration helper is not installed on this host, so approval from "
            "chat is not available. See the OAuth runbook, 'Approving from chat'."
            if not isinstance(found, str)
            else f"No command produced: {found}. Nothing was changed."
        )
    if isinstance(found, str):
        return f"Not approved: {found}. Nothing was changed."
    identity = found
    if not confirmed:
        return (
            f"Preview: would admit sign-in ref {pending_reference(identity.issuer, identity.subject)} "
            f"as tenant {slug!r}: an isolated workspace, not privileged, not funded, bound on "
            "every surface this service trusts. Confirm the reference with the person first. "
            "Call again with confirmed=true to approve."
        )
    try:
        data_dir = approve_pending_tenant(
            identity_path=identities_path,
            registry_path=registry_path,
            data_root=data_root,
            slug=slug,
            issuer=identity.issuer,
            subject=identity.subject,
            service_user=service_user,
            trusted_issuers=trusted_issuers,
            register=register,
        )
    except OAuthOnboardingError as exc:
        return (
            f"Not approved: {exc}. No access was granted. A partly prepared workspace, if any, "
            "is unreachable and the same approval can be retried."
        )
    try:
        pid = reload_service(registry_path)
    except TenantProcessSignalError as exc:  # best effort; the approval itself stands
        return (
            f"Approved {slug!r} (workspace {data_dir}; not privileged, not funded), but the "
            f"service reload failed ({exc}). It will pick the tenant up at its next reload."
        )
    reloaded = (
        "The running service was signaled to reload."
        if pid is not None
        else "No running service was found to signal; it loads at next start."
    )
    return (
        f"Approved {slug!r}: isolated workspace {data_dir}; not privileged, not funded. "
        f"{reloaded} Ask the person to reconnect."
    )
