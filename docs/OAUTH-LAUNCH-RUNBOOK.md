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
5. Confirm all five browser OAuth settings are present together before running
   the `/login` → `/setup/` canary:
   `WINGMAN_SHARED_OAUTH_WEB_CLIENT_ID`,
   `WINGMAN_SHARED_OAUTH_WEB_CLIENT_SECRET`,
   `WINGMAN_SHARED_OAUTH_WEB_AUTHORIZE_URL`,
   `WINGMAN_SHARED_OAUTH_WEB_TOKEN_URL`, and
   `WINGMAN_SHARED_OAUTH_WEB_REDIRECT_URI`. The client secret belongs in the
   approved service credential store, not the change record.
6. Confirm the identity-map parent is private, writable by the shared-service
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

First decide which path the person is on:

| Person | Path |
|---|---|
| Already has a tenant (a capability URL today) | §3: `oauth-bind` each verified identity to their **existing** slug. Never `oauth-invite` or `oauth-approve` them: that creates a second, empty workspace. |
| New to this server | This section: `oauth-invite` a slug, they sign in on **both** surfaces (browser `/login` and the Claude connector), `oauth-approve` one verified identity, `oauth-bind` the other to the same slug. |

Either way, `oauth-bind` removes the identity it binds from the pending queue
(#569), so `oauth-pending` afterwards lists only people still waiting.

Send the invitee the [invitee message](OAUTH-INVITEE-MESSAGE.md) with the
connector address. It tells them that "couldn't connect" before approval is
expected and what to send you, which the Claude error itself does not.

Reserve the slug before sending the connector address:

```bash
sudo -iu wingman-shared /home/wingman-shared/.local/bin/wingman tenant oauth-invite <slug> \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml \
  --registry /etc/wingman/tenants.toml
```

The invitee's first verified sign-in remains forbidden and creates no
workspace. It records the opaque issuer/subject pair in the bounded pending
queue. Browser setup and MCP use different WorkOS token classes and issuers,
so have the invitee attempt both flows before approval. `oauth-pending` may
show two rows for that person. Confirm both identities out of band; do not
assume their opaque `sub` values are equal or infer a link from them.

Approve one exact verified identity to create the reserved tenant, then bind
the other exact verified identity to that same tenant:

```bash
sudo -iu wingman-shared /home/wingman-shared/.local/bin/wingman tenant oauth-pending \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml

sudo /home/wingman-shared/.local/bin/wingman tenant oauth-approve <slug> \
  --issuer <exact-verified-iss-for-one-surface> \
  --subject <exact-verified-sub-for-one-surface> \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml \
  --registry /etc/wingman/tenants.toml \
  --data-root /home/wingman-shared/tenants

sudo -iu wingman-shared /home/wingman-shared/.local/bin/wingman tenant oauth-bind <slug> \
  --issuer <exact-verified-iss-for-the-other-surface> \
  --subject <exact-verified-sub-for-the-other-surface> \
  --identities /home/wingman-shared/.config/wingman/oauth-identities.toml \
  --registry /etc/wingman/tenants.toml
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
2. Attempt both the browser `/login` flow and the MCP connector flow through
   WorkOS.
3. Confirm each verified-but-unapproved identity can see no tenant data.
4. Confirm the browser and MCP attempts produced their two exact pending
   identities. Do not assume their `sub` values match.
5. Approve one identity into a new unprivileged, unfunded workspace, then use
   `oauth-bind` to bind the other verified identity to the same slug.
6. Sign in again and confirm both surfaces reach the approved slug.
7. Open the OAuth-protected `/login` → `/setup/` surface, add a BYOK credential, and upload a
   harmless test CV. Confirm the credential never appears in chat, logs, or
   browser URLs.
8. Connect the MCP client and call one read-only status tool.
9. Add a unique, non-sensitive canary record through the normal Wingman tool.
10. Disconnect, sign in again, and confirm the same workspace and record return.

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

## 7. Triage: Claude says "Couldn't reach" or "Couldn't connect"

Claude shows the same generic message for an unreachable host, a failed
discovery, and a successful sign-in that Wingman then refuses with 403
because the identity is not approved yet. The message does not say which.
Work through these in order: each is cheap, and each rules out a whole layer
before the next, more expensive step.

0. **Is the host online upstream?** On the server:

   ```bash
   tailscale netcheck
   tailscale status
   ```

   Expect `UDP: true`, a public IPv4, and a nearest DERP region with a
   latency. An ISP outage looks like the LAN still working (the router
   answers) while DERP, the coordination server and DNS all time out; Funnel
   cannot deliver anything then. Nothing below is meaningful until this is
   healthy.

1. **What does the app's own record say, for the whole day?** Read the shared
   service's journal across the day, not a five-minute window around one
   attempt. A member of `adm` can do this without sudo:

   ```bash
   journalctl _UID=$(id -u wingman-shared) --since today --no-pager -o short-iso --utc \
     | grep -E 'bearer (verified|refused)'
   ```

   `bearer verified but unprovisioned` means OAuth **succeeded**: Claude
   obtained a token and Wingman refused it only because the identity is not
   bound (go to step 2). `bearer refused` means a token arrived but failed
   validation. No bearer lines at all means no Claude token ever reached the
   server. Once request logging is deployed, the `wingman.access` lines show
   every request to the OAuth surface (method, path, status, duration), so
   unauthenticated discovery requests become visible too.

2. **Is the person waiting for approval?**

   ```bash
   sudo -iu wingman-shared /home/wingman-shared/.local/bin/wingman tenant oauth-pending \
     --identities /home/wingman-shared/.config/wingman/oauth-identities.toml
   ```

   A verified identity here receives a 403 that Claude reports as "couldn't
   connect". Approve or bind it (§3/§4). Each row carries `ref=XXXX-XXXX`,
   the same reference that person was shown on the browser page and in the
   403 body. Match a quoted reference to its row. An `ofid_…` code is
   Claude's own support reference and does not identify a row. No WorkOS sign-in window opening is
   **not** evidence that OAuth never ran: WorkOS silently reuses an existing
   session, so a person who signed in through the browser earlier can
   complete the connector's OAuth flow without seeing anything.

3. **Does the public path work from outside the tailnet?** On a machine that
   is on the tailnet, MagicDNS resolves `*.ts.net` to the server's 100.x
   address, so a plain `curl` skips Funnel entirely and proves nothing about
   the public path. Force the public address:

   ```bash
   host=<server>.<tailnet>.ts.net
   for ip in $(dig +short "$host" @1.1.1.1); do
     curl -sS -o /dev/null -D - --resolve "$host:443:$ip" -X POST \
       -H 'Content-Type: application/json' -d '{}' "https://$host/shared/mcp" \
       | grep -iE '^(HTTP|www-authenticate)'
   done
   ```

   Expect `401` and a `WWW-Authenticate: Bearer resource_metadata="…"`
   header, quickly. Fetch that metadata URL the same way and check that its
   `resource` exactly equals the connector URL.

4. **Only then reproduce it live.** Start a follower, note the UTC time, and
   reconnect once:

   ```bash
   journalctl _UID=$(id -u wingman-shared) -f -o short-iso --utc
   ```

Red herring: bursts of `TLS handshake error` lines in the `tailscaled`
journal (SSLv3/TLS 1.0 version probes, ALPN lists like `"http/0.9" "spdy/1"
"h2c" "hq"`, odd cipher lists) are internet TLS scanners reaching the Funnel
address, not Claude or an invitee.
