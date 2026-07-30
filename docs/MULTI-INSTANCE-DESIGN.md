# Multiple Simultaneous Wingman Instances — Design (proposal, not yet an RFC)

**Status.** Design recorded 2026-07-20. Prompted by a real second user: a
trusted friend with his own LinkedIn graph wants to run wingman + a Claude
client ("Woven") of his own. Graduates to a numbered RFC entry when the
first multi-instance deployment actually runs; until then this is the
working design.

## The one-sentence design

One person = one workspace = one instance; instances never share data,
and "multiple instances" means N workspaces + N ports + N tokens — a
deployment pattern over primitives that already exist, not a multi-tenant
rewrite.

## What already isolates (no build needed)

Everything wingman persists or serves is scoped to the workspace
directory, selected by `WINGMAN_DATA_DIR`:

| Concern | Where it lives | Per-instance today? |
|---|---|---|
| The graph, profile, corpus, answers, verdicts | `wingman.db` | ✓ |
| Inbox artifacts, reports, digests, packs | `inbox/`, `reports/` | ✓ |
| Model routing | `models.toml` | ✓ |
| HTTP capability token | `mcp-http-token` | ✓ |
| HTTP server pidfile (RFC-032) | `mcp-http.pid` | ✓ |
| Telemetry opt-in | `telemetry.on` | ✓ |
| Feature-request repo | `feature-repo` | ✓ |

`wingman-mcp --http --port <p>` already takes a per-instance port. Two
instances on one machine are therefore: two `WINGMAN_DATA_DIR`s, two
ports, two tokens — done. SQLite concurrency is a non-issue because
instances never open each other's databases.

## What is deliberately NOT shared

- **The graphs.** Trent's connections are Trent's data. There is no
  cross-workspace query, no shared people table, no merged corpus —
  RFC-002's local-first privacy posture applies per person, and the
  boundary between two friends' workspaces is exactly as hard as the
  boundary between a workspace and the internet.
- **API keys are per-person.** Each instance spends its owner's Anthropic
  and Voyage keys, so cost and rate limits land on the right person and
  either can revoke without affecting the other.
- **Overlap queries ("do we both know her?") are a non-goal.** That's a
  future *federation* feature requiring both owners' explicit consent per
  query, and it must be designed as its own thing (likely
  intersection-only, no graph exchange). Nothing in this design should
  make it accidentally easy.

## Deployment shapes, in order of preference

### Shape A — own machine each (recommended; zero build)

Trent clones the repo, `uv tool install .`, `wingman init`, imports his
LinkedIn export, configures his own keys (Keychain on macOS, env vars on
Linux), points his own Claude Desktop/Woven at his own MCP server.
Total isolation by geography. INSTALL.md and WALKTHROUGH.md already
cover this end to end — the only "multi-instance" fact is that nothing
about dhk's instance needs to be known at all.

### Shape B — shared always-on server, one Unix user each

The SERVER.md deployment, multiplied by OS accounts: each person gets a
Unix user, their own canonical `~/.config/wingman/{secrets.env,wingman.env}`
(0600 each, RFC-046/#122 — one pair of files per account, since each
account has its own `$HOME`), their own
user-level systemd units (`wingman-mcp.service` on a distinct port,
`wingman-overnight.timer`), their own workspace under their own home.
Isolation is enforced by file permissions, not politeness. Each person's
claude.ai connector gets their own Tailscale-served URL + token.
Requires: distinct `--port` per user (exists), nothing else.

### Shape C — one Unix user, N workspaces (discouraged, works)

`WINGMAN_DATA_DIR=/srv/wingman/trent wingman-mcp --http --port 8788`.
Same binary, same account, env-var-switched workspaces. Acceptable for
a household machine where both parties trust each other with root
anyway; unacceptable wherever "trusted friend" shouldn't mean "can read
my job search." One account means one `~/.config/wingman/secrets.env` —
#122/RFC-046's host secrets file is *shared* across every workspace on
this shape, unlike shape B, so it cannot be how two instances get
different keys. If chosen, per-service
`EnvironmentFile`s keep keys separate (a real exported env var still
outranks the host file in the resolution ladder), and every cron/systemd
unit must pin `WINGMAN_DATA_DIR` explicitly — a unit that forgets inherits
the wrong workspace, which is the failure mode that makes this shape
discouraged.

## The small build gaps (in order of value)

1. **`wingman doctor` prints whose workspace it's in.** It reports the
   data dir today; it should also make the active-workspace fact loud
   ("workspace: /srv/wingman/trent — set by WINGMAN_DATA_DIR") so shape
   B/C operators can't act on the wrong instance silently.
2. **`wingman-ctl` takes the environment it's given.** It already honors
   `WINGMAN_REPO`/`WINGMAN_LOG`; it should pass `WINGMAN_DATA_DIR`
   through and include the workspace path in `status` output. One-line
   changes.
3. **Port in the pidfile.** RFC-032's revisit clause, now real: with two
   instances, `wingman mcp status` should say which port this
   workspace's server holds.
4. **A `--workspace` global CLI flag** as sugar for `WINGMAN_DATA_DIR`
   (`wingman --workspace ~/w/trent status`). Nice for shape C, pure
   convenience — env var stays the mechanism.
5. **INSTALL.md one-liner** stating the invariant: one person, one
   workspace, never shared.

None of these block Trent starting today on shape A.

## Onboarding checklist for the second user (shape A)

1. Clone + install; `wingman init`; `wingman doctor`.
2. Own API keys: `wingman keys set anthropic` / `voyage` (macOS) or env.
3. `wingman ingest` his source-of-truth resume; `wingman ingest-linkedin`
   his export — his graph, on his disk.
4. `wingman company follow` his targets; schedule his own overnight.
5. Connect his Claude client to his own MCP server (stdio config or
   `--http` + connector); the tool docstrings carry every interactive
   protocol (answers, triage, feature requests), so his Woven sessions
   get the same behaviors with zero extra setup.

## Revisit if

- Both users end up on one server (shape B's gaps 1–3 stop being
  optional and this graduates to an RFC with tests).
- Anyone asks for shared or intersecting graphs (a federation design
  with per-query consent — its own document, its own RFC, likely its
  own review).
- A third user appears (three friends is a product; the hosted-tiers
  assessment stops being parked).
- Upgrade coordination hurts (N instances of shape B want one
  `wingman-ctl upgrade --all-users` story; today each user upgrades
  their own).

See `docs/OAUTH-MULTITENANCY-CONSIDERATION.md` for the fuller writeup of
why per-account isolation (this document's shape B) stays the answer for
now, even after a rough night of hands-on lobster operation that might
otherwise have looked like pressure toward OAuth/multi-tenancy.
