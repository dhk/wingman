# RFC-081 (DRAFT): Hosted multi-user Wingman — OAuth 2.1 resource server, capability tokens retired for hosted tenants

**Status.** Draft for owner review. Not merged into `docs/RFC.md`; number provisional (last entry read was RFC-080). Written 2026-09-28.

**Supersedes (partially).** The auth half of RFC-048 ("capability tokens per tenant — no OAuth"). Does **not** supersede RFC-048's data model: one SQLite database per tenant, share-nothing, a `Config` bound per request through a ContextVar.

**Why this is being decided now.** Both `docs/OAUTH-MULTITENANCY-CONSIDERATION.md` and RFC-048 rejected OAuth, and both named the condition for reopening it: *a deliberate hosted-product decision, made in daylight as its own RFC.* The owner has decided to offer Wingman as a public/hosted product. This RFC is that decision's auth and secrets half. It does not claim the earlier reasoning was wrong at four trusted tenants. It claims the threat model changed.

## What changes in the threat model

| | RFC-048 (four known tenants) | Hosted (strangers) |
|---|---|---|
| Who holds a credential | People the operator knows | Anyone who signs up |
| Credential | 24-byte token in the URL path, no expiry, no scope, no audience | Must be revocable, expiring, audience-bound |
| Tenant provisioning | Operator edits root-owned `/etc/wingman/tenants.toml` | Self-service; cannot be a root-owned file |
| Provider keys | Plaintext `keys.env`, mode 0600, per tenant | Custody of strangers' keys |
| Noisy neighbour | Accepted at four tenants (synchronous tool dispatch) | Availability is a security property |

## Decision

1. **Wingman becomes a pure OAuth 2.1 resource server for hosted tenants.** It does not issue tokens. The authorization server is external (see Open Questions).
2. **One choke point changes.** `TenantRoutingASGIApp` (`infrastructure/tenant_asgi.py`) currently reads `path_params["token"]` and calls `TenantIndex.resolve`. For hosted tenants it instead validates `Authorization: Bearer` (signature, `iss`, `exp`, audience equal to this server's canonical resource URI per RFC 8707) and resolves `(iss, sub)` to a tenant. Everything downstream (`tenant_config_scope`, `load_config()`, per-tenant SQLite) is unchanged. The route becomes plain `/mcp`.
3. **Discovery per the MCP authorization spec.** Serve Protected Resource Metadata (RFC 9728). Unauthenticated and invalid requests get `401` with `WWW-Authenticate: Bearer resource_metadata="…"`. Today the 401 body is `Not Found` with no header, so a compliant client cannot discover anything. Insufficient scope gets `403` with `error="insufficient_scope"`.
4. **Tokens never appear in URLs.** The MCP spec requires the Authorization header and forbids query-string tokens. Hosted tenants never see a token-bearing URL, so the connect panel and `request_origin`-derived URLs (`mcp_url`, `ui_url`) do not embed one for them.
5. **Scopes** are per tool group, least privilege first (for example read-only profile/people versus writes). Names to be settled in review.
6. **Legacy capability tokens are kept only for operator-provisioned tenants** (`dhk`, `trent`, `jason`, `bob`) during a fixed dual-accept window, then retired. Every legacy-path use is logged so the window can end on evidence.
7. **Registry moves from a root-owned TOML to a provisioning store** mapping `(iss, sub) → slug → data_dir`, written by a provisioning service. `privileged` and `funded` stay operator-only and are never settable through signup, consistent with RFC-048 and RFC-068.
8. **Provider keys.** Hosted tenants' bring-your-own keys are encrypted at rest with envelope encryption. Decision on whether to offer bring-your-own-key at all is left to review (see Open Questions).
9. **Operator-only tools stay off the hosted surface.** `carve_off_persona(persona, target_dir)` takes a filesystem path and writes to it. It is gated by `operator_only_refusal` today. For hosted tenants it must be unreachable, not just refused.

## The web UI

`/ui/{token}/` cannot send a bearer header from a browser link. Options:

- **A. Drop the UI from the hosted surface.** MCP only. Smallest attack surface.
- **B. Cookie session from the same identity provider.** Uses a vetted OIDC client library. This is the "second auth system" RFC-033 warned about, so it is only acceptable if the library does the security-critical work.

Recommendation: A for the first hosted release. Revisit B when there is user demand.

## Alternatives considered

- **Keep URL capability tokens and add subdomain routing (#135).** Solves memorability, not expiry, scope, audience or revocation. Rejected for strangers, retained for operator tenants.
- **Run our own authorization server.** Rejected: it is the "most dangerous code in the repo" argument from RFC-033, now with strangers as users.
- **Row-level tenancy in one shared database.** Rejected again, for RFC-048's reason: share-nothing data removes a class of scoping bugs.
- **Process or container per tenant.** Strongest isolation, restores something like the kernel boundary the OAuth-consideration doc valued. Deferred, not rejected. It is the fallback if shared-process isolation cannot be made convincing for strangers.

## Not addressed here (each needs its own entry)

- **Synchronous tool dispatch** on one event loop (RFC-048's accepted trade-off). Public hosting exceeds "before a fifth tenant" by a wide margin. Needs an async-offload or worker design before launch.
- **Drive push in a shared process.** RFC.md notes its per-account isolation is Unix-home-scoped and needs its own design pass.
- **`GITHUB_SHARED_ISSUES_KEY`** is box-wide by design (#506). Confirm that is acceptable when tenants are strangers.
- **Abuse and metering** for `funded` inference spend.

## Open questions (owner input needed)

1. Which identity provider? Must support the MCP client flow (Client ID Metadata Documents preferred, Dynamic Client Registration deprecated but retained in the spec's draft revision). Not yet researched; no claim is made here about any vendor.
2. Offer bring-your-own-key to strangers, or fund all hosted inference?
3. Is the UI in or out for the first hosted release?
4. Is the hosted product a new deployment, or do operator tenants and hosted tenants share one process?

## Migration

1. Map each existing URL token to a tenant identity. Rotate all existing tokens now (they have been present in URLs and logs).
2. Ship OAuth validation behind a flag alongside the URL path. Log legacy use.
3. Move operator tenants over on a stated date, then remove the legacy path.

## Revisit if

- Shared-process isolation cannot be demonstrated to the standard RFC-048 held itself to (a test per claim, including concurrent-request isolation). Then move to process- or container-per-tenant.
- The identity provider choice forces a session system inside Wingman.

## Sources

- MCP authorization specification, draft revision: https://modelcontextprotocol.io/specification/draft/basic/authorization
- In this repo: `docs/OAUTH-MULTITENANCY-CONSIDERATION.md`; `docs/RFC.md` RFC-017, RFC-033, RFC-048, RFC-068; `src/wingman/infrastructure/tenant_asgi.py`, `tenants.py`, `keys.py`; `src/wingman/webui.py`; `src/wingman/mcp_server.py` (`carve_off_persona`).
