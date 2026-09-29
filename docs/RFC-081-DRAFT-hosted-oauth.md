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

## Decisions taken (owner, 2026-09-28)

- The owner is the maintainer and wants OAuth: more users are arriving **within weeks**.
- **Managed identity provider**, user identities held by a vendor. Self-hosting an authorization server is out.
- **Social login: Google and Apple.** WorkOS AuthKit lists both as supported social connections (https://workos.com/docs/authkit/social-login); each must be configured in the dashboard first. That page states nothing on plan limits or pricing.
- **Apple prerequisite (verified from Apple's docs):** Sign in with Apple for a website requires a Services ID associated with an *existing* iOS, macOS, tvOS or watchOS App ID enabled for Sign in with Apple (https://developer.apple.com/help/account/capabilities/configure-sign-in-with-apple-for-the-web/). A web-only product therefore still needs an Apple Developer account and an App ID. Apple's page does not say which membership tier is required. Not verified: how hidden-email (private relay) users appear in the token. Design consequence either way: tenant identity is keyed on `(iss, sub)`, never on email.

## Identity provider: what is and is not verified

Requirement: advertise CIMD (`client_id_metadata_document_supported`), honour RFC 8707 `resource`, and put an audience Wingman can check in the token.

- **WorkOS AuthKit — verified from its own docs (https://workos.com/docs/authkit/mcp):** CIMD is supported but *off by default* and must be enabled in the dashboard; Dynamic Client Registration is supported for backwards compatibility; resource indicators are supported. Not shown on that page: which social providers, pricing, and limits.
- **Auth0 Auth for MCP — not verified.** Its overview page (https://auth0.com/ai/docs/mcp/auth-for-mcp) does not mention CIMD, DCR or resource indicators. A third-party page (Zuplo, dated 2026-07-28) says Auth0 prioritizes `audience` over `resource` and needs DCR enabled per tenant. That is a secondary source about one vendor's older behavior.
- **Self-hosted options** (Keycloak, Zitadel, authentik, Cognito, Entra ID) are out per the decision above.
- **Clerk** advertises CIMD in a 2026-08-05 changelog. Not read beyond the title.

Provisional choice: **WorkOS AuthKit**, pending the spike below. This is a provisional pick because it is the only candidate verified against its own docs, not because it was compared on price or social-provider coverage (neither was checked). Selection is reversible: Wingman only validates tokens (`iss`, `aud`, `exp`, signature), so swapping providers changes configuration, not the ASGI wrapper.

## Phasing for a weeks-not-months timeline

1. **Spike (days).** In a branch, add bearer validation to `TenantRoutingASGIApp` behind a flag, serving PRM and a correct `401`, next to the existing URL path. Prove one end-to-end connect from a real MCP client against the chosen provider. Exit criterion: a client with no prior relationship completes the flow and can call one read tool as the right tenant, and a second identity cannot see the first tenant's data.
2. **Hosted-tenant provisioning.** `(iss, sub) → slug → data_dir` store and signup path. Operator-only flags stay out of reach.
3. **Blockers before strangers arrive:** provider-key storage (open question 2) and synchronous dispatch (see "Not addressed here"). Neither is auth; both gate opening signup.
4. **Then** the dual-accept window and legacy-token retirement for operator tenants.

## Open questions (owner input needed)

1. Account linking: the same person signing in with Google one day and Apple the next. Keyed on `(iss, sub)` they are two different tenants unless linking is added. Does the provider offer linking, and do you want it in the first release? (Not checked.)
2. Offer bring-your-own-key to strangers, or fund all hosted inference? This gates opening signup.
3. Is the UI in or out for the first hosted release? Recommendation: out.
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
