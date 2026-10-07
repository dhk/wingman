# Invite-only OAuth launch runbook

This is the operator checklist for moving the hosted shared service from
permanent capability URLs to invite-only OAuth. It is intentionally not a
public-signup guide. A Google account signing into Wingman is an application
user; it does **not** need access to the operator's WorkOS dashboard.

The launch is additive for existing tenants and OAuth-only for new invitees:

- existing capability tenants keep working during the migration window;
- an existing tenant may also receive an OAuth identity binding to the same
  workspace;
- a new invitee receives an isolated, unprivileged, unfunded workspace and no
  `mcp-http-token` file or permanent `/mcp/{token}` URL;
- login never grants a tenant by itself. An invite or explicit operator
  approval is the trust decision.

## 1. Inputs and secret boundary

Record the non-secret deployment values in the operator's change record:

- public MCP resource URL;
- WorkOS issuer;
- WorkOS JWKS URL;
- Google connection enabled for the WorkOS environment;
- identity-map path;
- shared-service registry path and system account;
- the exact release commit being deployed.

Keep the WorkOS client secret and provider keys only in the approved service
credential store. Never put them in this document, a command-line flag, a Git
issue, an identity map, or a tenant registry. The identity map contains stable
issuer/subject bindings, not email addresses or bearer tokens.

## 2. Pre-deploy gates

Before changing the live service:

1. Confirm CI is green for the exact release commit.
2. Back up the tenant registry, identity map, and every affected workspace.
3. Confirm the previous build is still serving. If installation fails, report
   **stale but serving** rather than **down**.
4. Confirm all four shared OAuth settings are present together:
   `WINGMAN_SHARED_OAUTH_ISSUER`, `WINGMAN_SHARED_OAUTH_AUDIENCE`,
   `WINGMAN_SHARED_OAUTH_JWKS_URI`, and
   `WINGMAN_SHARED_OAUTH_IDENTITIES`.
5. Confirm the identity-map parent is private, writable by the shared-service
   account, and backed up. Permission denial is not a missing path.

Deploy with `scripts/wingman-provision-shared.sh` or the existing shared
redeploy procedure in [SERVER.md](SERVER.md). After deployment, read the live
protected-resource metadata and confirm that its `resource` and
`authorization_servers` exactly match the saved configuration.

## 3. Existing-tenant migration window

Do not revoke an existing tenant's capability URL merely because OAuth is now
available. Bind the verified WorkOS `(iss, sub)` to the tenant's existing slug
and workspace, reload, then test both authentication paths. Keep the old path
as rollback for at least one normal operating cycle.

The legacy escape hatch in `wingman-add-tenant.sh` is accepted only for
`scripts/wingman-migrate-tenant.sh` after that script has restored an existing
workspace. On an OAuth-enabled service, an ordinary add-tenant invocation with
no verified OAuth identity now stops before creating anything. This prevents a
missed flag from silently issuing a permanent URL credential to a new person.

Retire an existing capability token only after all of these are true:

- the same tenant reaches the same workspace through OAuth;
- at least one real MCP tool call succeeds;
- the old service or token has not been needed for one operating cycle;
- the operator has recorded a tested rollback path;
- the user has been told that the old URL will stop working.

## 4. New invitee policy

Reserve the slug before sending the connector address:

```bash
sudo -iu wingman-shared /home/wingman-shared/.local/bin/wingman tenant oauth-invite <slug> \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml \
  --registry /etc/wingman/tenants.toml
```

The invitee's first verified sign-in remains forbidden and creates no
workspace. It records the opaque issuer/subject pair in the bounded pending
queue. Inspect that queue, confirm the person out of band, and approve the
exact verified identity:

```bash
sudo -iu wingman-shared /home/wingman-shared/.local/bin/wingman tenant oauth-pending \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml

sudo wingman tenant oauth-approve <slug> \
  --issuer <exact-verified-iss> \
  --subject <exact-verified-sub> \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml \
  --registry /etc/wingman/tenants.toml \
  --data-root /home/wingman-shared/tenants
```

Do not obtain `sub` from email, a screenshot, or user input; use the verified
pending record from the completed OAuth exchange. The lower-level direct
trusted-user command in [SERVER.md](SERVER.md) remains an operator fallback,
not the normal invite path.

After provisioning, verify deterministically:

```bash
# Replace these with the actual paths for the approved slug.
test -d /home/wingman-shared/tenants/<slug>
test ! -e /home/wingman-shared/tenants/<slug>/mcp-http-token
```

Also confirm that the registry entry is unprivileged and unfunded unless the
operator separately approved those grants. Absence of a model key is expected:
model-free tools work, and model-backed tools must refuse until BYOK setup or an
explicit per-tenant funding decision succeeds.

## 5. One-person launch canary

Use one real invited Google identity as canary A and a different identity as
canary B. Do not use production tenant data as the test marker.

For canary A:

1. Reserve the invite slug.
2. Sign in through WorkOS.
3. Confirm the verified-but-unapproved identity can see no tenant data.
4. Approve it into a new unprivileged, unfunded workspace.
5. Sign in again and confirm it reaches the approved slug.
6. Open the OAuth-protected `/login` → `/setup/` surface, add a BYOK credential, and upload a
   harmless test CV. Confirm the credential never appears in chat, logs, or
   browser URLs.
7. Connect the MCP client and call one read-only status tool.
8. Add a unique, non-sensitive canary record through the normal Wingman tool.
9. Disconnect, sign in again, and confirm the same workspace and record return.

For canary B:

1. Sign in without approval and confirm it sees no data from canary A.
2. If approved into its own workspace, confirm A's canary record is absent.
3. Presenting A's MCP session ID under B's valid credential must be refused.

Finish by confirming that canary A has no `mcp-http-token`, existing capability
tenants still work, the shared process stayed up, and no secret or private CV
content appeared in logs. Record pass/fail and the exact deployment commit.

## 6. Failure and rollback

- **New OAuth path fails, existing path serves:** leave existing capability
  tenants in place, restore the previous OAuth configuration/build, and report
  the service as stale but serving.
- **Shared process is down:** restore the previous unit/build first; do not
  mutate identity bindings while the process state is unknown.
- **Approval or provisioning fails:** preserve any successfully created
  workspace, report whether the registry or identity map changed, and retry
  only after reconciling that state. Never claim nothing changed without
  checking both files.
- **Isolation check fails:** stop the launch. Preserve logs with tokens and
  private content redacted; do not approve another identity.
- **Browser setup fails:** keep the tenant unfunded. Never move a key through
  chat as a workaround.

Public signup, automatic funding, and autonomous external actions remain out
of scope after this canary passes.
