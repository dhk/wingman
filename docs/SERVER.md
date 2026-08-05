# Wingman on an always-on Ubuntu server

The deployment this guide builds: one Linux box that owns the workspace,
runs `wingman overnight` on a timer, keeps the MCP server up as a service,
and is reachable from claude.ai (web, desktop, phone) over your Tailscale
network. Your career data still lives on a machine you own — this is
local-first with a longer extension cord, not hosting (RFC-002 holds; the
hosted tiers remain a separate, parked decision).

Everything here is Ubuntu 22.04+/Debian-family; adjust package commands
for other distros.

## 1. Install

```bash
sudo apt update && sudo apt install -y git nodejs npm   # node only for PDF exports
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv, if not present
mkdir -p ~/src && git clone git@github.com:dhk/wingman.git ~/src/wingman
cd ~/src/wingman && uv tool install .
wingman --version
```

This is a **private repo over SSH**, not HTTPS — `git clone
https://github.com/dhk/wingman.git` prompts for a GitHub username that
doesn't resolve to anything useful; if this account has no SSH key
registered with GitHub yet (a fresh service account, e.g.), generate one
(`ssh-keygen -t ed25519`) and add it as a deploy key (repo → Settings →
Deploy keys) before cloning — read-only is enough, this account never
needs to push.

`~/src/wingman` (not `~/wingman`, `~/code/wingman`, or anywhere else) is
the code-location convention every other piece of tooling here assumes
— `infrastructure/upgrade_all.py`'s `REPO_SUBPATH` looks for exactly this
path when sweeping every configured account's checkout during the
nightly cross-account upgrade (§7), and `scripts/wingman-ctl`'s own
`WINGMAN_REPO` fallback (§2) is this same path. Deviating means either
every future command needs an explicit override, or upgrade-all silently
looks in the wrong place — not worth it for a install-time shortcut.

The workspace defaults to `~/.local/share/wingman` (XDG). Set
`WINGMAN_DATA_DIR` before `wingman init` if you want it elsewhere —
consistently, including inside the systemd units below.

**The `wg` alias, explicitly** — `uv tool install` puts `wingman` and
`wingman-mcp` on `PATH`, but `scripts/wingman-ctl` is a plain repo script,
not an installed entry point, so it needs its own alias:

```bash
echo "alias wg='$HOME/src/wingman/scripts/wingman-ctl'" >> ~/.bashrc
source ~/.bashrc
wg status   # confirms the alias resolved
```

(`~/.zshrc` instead of `~/.bashrc` on macOS/zsh setups.)

## 2. Host config: secrets and settings (the Keychain is macOS-only)

`wingman keys` requires macOS's `security` binary and fails visibly on
Linux. On a server host, two files under one directory are canonical
(RFC-019/034/046, #122), mirroring `dhk/alexandria`'s `alexandria.env` /
`secrets.env` split: `~/.config/wingman/secrets.env` (API keys, mode 600)
and `~/.config/wingman/wingman.env` (non-secret host settings — today,
just `WINGMAN_REPO`, mode 600). Wingman itself reads `secrets.env`
directly — CLI and MCP server alike, on a fresh shell with zero exports —
so this one file is the thing to create or copy when migrating a box or
debugging "which key file is actually in effect":

```bash
install -d -m 700 ~/.config/wingman
install -m 600 /dev/null ~/.config/wingman/secrets.env
cat >> ~/.config/wingman/secrets.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
EOF

install -m 600 /dev/null ~/.config/wingman/wingman.env
cat >> ~/.config/wingman/wingman.env <<'EOF'
WINGMAN_REPO=/home/you/src/wingman
EOF
```

The resolution order (`wingman doctor` names the winning source per key
and flags a key defined in more than one place with a different value):
**environment > Keychain (macOS) > `~/.config/wingman/secrets.env` > the
workspace's own `keys.env`**. A systemd `EnvironmentFile=` or a shell
export still works exactly as before — either just becomes the
"environment" source, which always wins — but neither is required anymore
for the CLI or MCP server to see these keys.

**Upgrading from the old single `~/.config/keys.env` (RFC-040/#122)?**
Nothing to do by hand — the very first `wingman`/`wingman-mcp` invocation
after upgrading migrates it automatically: recognized secrets move into
`secrets.env`, everything else (a `WINGMAN_REPO` line, or anything else
you'd added for systemd's `EnvironmentFile=` role, e.g.
`WINGMAN_ALLOWED_HOSTS`) moves into `wingman.env`, and the old file is
renamed to a timestamped `keys.env.migrated-YYYYMMDD` next to itself —
never deleted, never silently overwritten. The migration prints what it
did (the CLI on stderr, `wingman-mcp` to its log); `wingman doctor` also
reports the current host-config layout on every run, and flags it if the
old file is ever still present alongside the new one (that combination
means it was left alone on purpose — see RFC-046 — and needs a human's
eyes, not another automatic pass).

**A credential shared by every account on the box** (RFC-047) — today
that's `GITHUB_API_ISSUES_KEY`, one fine-grained GitHub PAT (scoped to
Issues only, on whichever repo(s) should accept feature requests) used by
every account that files `wingman feature-request` issues, for an account
that has no GitHub identity of its own. One-time setup:

```bash
sudo groupadd wingman                              # once per box
sudo usermod -aG wingman dhk && sudo usermod -aG wingman trent   # per account that needs it
# each added account needs a fresh login (or `newgrp wingman`) for the group to take effect

sudo install -d -m 750 -o root -g wingman /etc/wingman
sudo install -m 640 -o root -g wingman /dev/null /etc/wingman/global-secrets.env
echo "GITHUB_API_ISSUES_KEY=github_pat_..." | sudo tee -a /etc/wingman/global-secrets.env
```

Sits below each account's own `secrets.env` in the resolution ladder — an
account-specific override always wins over the shared default. Since
GitHub's own "opened by" field will show whichever account owns the PAT
regardless of who actually filed it, pair this with a per-account
`WINGMAN_OPERATOR_NAME=<name>` line in that account's own `wingman.env` —
`wingman feature-request` stamps `Submitted by: <name>` into the issue
body automatically, once, before it's ever previewed or filed.

## 3. Migrate the workspace from a Mac (optional)

The workspace is fully self-contained, so migration is one backup:

```bash
# on the Mac
wingman backup
scp "$HOME/Library/Application Support/wingman/backups/"<newest>.tar.gz server:

# on the server
wingman init
wingman restore ~/<newest>.tar.gz
wingman status && wingman doctor
```

## 4. Overnight on a systemd timer

`~/.config/systemd/user/wingman-overnight.service`:

```ini
[Unit]
Description=Wingman overnight run (RFC-018)

[Service]
Type=oneshot
EnvironmentFile=-%h/.config/wingman/wingman.env
EnvironmentFile=-%h/.config/wingman/secrets.env
ExecStart=%h/.local/bin/wingman overnight
```

`~/.config/systemd/user/wingman-overnight.timer`:

```ini
[Unit]
Description=Run wingman overnight every morning

[Timer]
OnCalendar=*-*-* 05:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it, and let user services run without a login session:

```bash
systemctl --user daemon-reload
systemctl --user enable --now wingman-overnight.timer
sudo loginctl enable-linger "$USER"    # services survive logout/reboot
systemctl --user list-timers           # confirm the next run
```

`Persistent=true` means a missed 05:00 (box was off) fires on next boot.
Logs: `journalctl --user -u wingman-overnight.service`.

## 5. The MCP server as a service

`~/.config/systemd/user/wingman-mcp.service`:

```ini
[Unit]
Description=Wingman MCP server (streamable HTTP, RFC-017)
After=network.target

[Service]
EnvironmentFile=-%h/.config/wingman/wingman.env
EnvironmentFile=-%h/.config/wingman/secrets.env
ExecStart=%h/.local/bin/wingman-mcp --http
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now wingman-mcp.service
```

This serves `http://127.0.0.1:8787/mcp/<token>` — loopback only, by
design. The token lives in `<workspace>/mcp-http-token` (0600); it is the
credential, treat it like one. `wingman-mcp --rotate-token` mints a new
one (then restart the service and update any connector).

**Troubleshooting: `systemctl --user status` shows `Result: resources`.**
Almost always a missing or unreadable `EnvironmentFile` (the tolerant `-`
prefix means a missing file is skipped silently at *parse* time, but a
genuinely broken one — wrong permissions, a syntax error — still fails
the unit). Check the file exists and is readable as this account
(`test -r ~/.config/wingman/wingman.env`), then:

```bash
systemctl --user daemon-reload
systemctl --user reset-failed wingman-mcp.service   # clears the failed state
systemctl --user start wingman-mcp.service
```

`reset-failed` is the step that's easy to miss — without it, systemd
refuses to retry a unit it's already marked failed, and `start` silently
no-ops.

## 6. Reach it from claude.ai via Tailscale

Install Tailscale (`https://tailscale.com/download/linux`), `sudo
tailscale up`, then publish the loopback port with TLS:

```bash
sudo tailscale serve --bg 8787        # HTTPS inside your tailnet only
# or, only if you need it reachable off-tailnet (e.g. claude.ai web):
sudo tailscale funnel --bg 8787       # public HTTPS, guarded by the token
```

The server keeps DNS-rebinding protection on and must therefore accept
the tunnel's Host header (#100). It auto-detects this machine's Tailscale
name at startup, so the setup above needs nothing extra — but if the
service can start before `tailscaled` is up (add `After=tailscale.service`
to the unit to avoid that), or another proxy fronts the port, pin the
hostname explicitly: `wingman-mcp --http --allowed-host my.front.example`
(repeatable), or `WINGMAN_ALLOWED_HOSTS=a.example,b.example` in
`wingman.env` (either `EnvironmentFile` line above picks it up; it isn't
one of `wingman.infrastructure.host_config`'s recognized names, so
wingman's own Python ladder ignores it — only systemd's plain
env-var-injection reads it). The startup banner prints a ready-to-paste
https connector URL and web-UI URL (token included) for each tunnel
hostname; `wingman-ctl start`/`status` echo them too.

**Get the URLs any time** — whether or not you started the server
yourself, and without hunting the token file: `wingman mcp url` (add
`--port`/`--prefix`/`--allowed-host` if you ran the server with
non-default flags). It computes the same lines the banner prints, straight
from the token file and Tailscale auto-detection.

In claude.ai: Settings → Connectors → Add custom connector → paste that
URL. Use the `/mcp/<token>` URL for the connector field, not the
`/ui/<token>/` one — the two are easy to confuse since `wingman mcp url`
prints both together. The same URL works in Claude Desktop and the
Claude mobile apps once the connector is added to your account.

**Troubleshooting: connector add fails on first try.** An
OAuth-registration-shaped error against a *fresh* funnel domain right
after `tailscale funnel` first comes up is often transient — Tailscale's
own DNS/TLS provisioning for a brand-new hostname can lag a few seconds
to a minute behind the CLI reporting success. Wait a moment and retry
before assuming the token or URL is wrong.

**Verify each stage, in order, before assuming something downstream is
broken:**

```bash
systemctl --user status wingman-mcp.service   # process actually running?
tailscale funnel status                        # tunnel actually mapped?
wingman mcp url                                 # URL matches what you're pasting?
curl -i http://127.0.0.1:8787/ui/$(cat ~/.local/share/wingman/mcp-http-token)/
```

A failure at any step points at that step specifically — e.g. a 200 from
the loopback `curl` but a connector-add failure means the problem is
Tailscale/DNS, not wingman itself.

**The trade, plainly:** `serve` keeps the endpoint inside your tailnet —
strongest posture, but claude.ai's servers can't reach it, so it only
serves clients on your own devices that route through the tailnet.
`funnel` makes the endpoint publicly reachable and the path token becomes
the only lock: rotate it if a URL ever leaks (browser history, pasted
logs), and remember wingman's access-log silencing (#70) exists precisely
so the token never lands in logs. If funnel makes you uneasy, that's the
signal to revisit the hosted-tier assessment instead.

## 7. Care and feeding

- **Backups:** `wingman backup` is one command. If the box is the
  workspace's only home, put it on its own weekly timer — same pattern as
  §4's overnight run, same per-account boundary as everything else here
  (#133): each Unix account backs up only its own workspace.

  `~/.config/systemd/user/wingman-backup.service`:

  ```ini
  [Unit]
  Description=Wingman weekly backup

  [Service]
  Type=oneshot
  ExecStart=%h/.local/bin/wingman backup
  ```

  `~/.config/systemd/user/wingman-backup.timer`:

  ```ini
  [Unit]
  Description=Run wingman backup weekly

  [Timer]
  OnCalendar=weekly
  Persistent=true

  [Install]
  WantedBy=timers.target
  ```

  ```bash
  systemctl --user daemon-reload
  systemctl --user enable --now wingman-backup.timer
  ```

  Tarballs land in `<workspace>/backups/`, pruned to keep-N (default 10,
  `wingman backup --keep`). `Persistent=true` catches a missed run the
  same way §4's does. Logs: `journalctl --user -u wingman-backup.service`.
- **Updates:** `cd ~/src/wingman && git pull && uv tool install --reinstall .`
  then `systemctl --user restart wingman-mcp.service`.
- **Telemetry:** `wingman telemetry on` once, if you want the usage
  journal; it is per-workspace and local-only (RFC-023).
- **Sanity:** `wingman doctor` after any change; it reports version, keys
  seen (env), and workspace health.
- **Faulting:** `wingman doctor --deep` walks a guided, one-step-at-a-time
  ladder (RFC-039) for "the server seems down" — pidfile vs. actual port
  binding (catches a stray process on the wrong `--port`, including one
  squatting the port from a *different* Unix account), loopback health,
  tunnel health, process state/logs, and a systemd restart-rate-limit
  check. Each run shows one step and one next action; rerun (or
  `--continue`) once you've acted on it.

## 8. The web UI

The HTTP server also serves `https://…/ui/<token>/` (RFC-033/034):
today's digest, every report, an upload form for LinkedIn exports and
resumes, and the validated API-key form — the no-terminal onboarding
path for a second user (see `MULTI-INSTANCE-DESIGN.md`). Same token,
same tunnel, nothing extra to run; the startup banner prints the URL.

**Restart, self-service (RFC-041).** The Manage panel's "Restart server"
button (`POST …/ui/<token>/restart`) covers "the build didn't pick up"
without an SSH round-trip — gated by that instance's own token, same as
everything else here, and only ever able to restart *this* instance.
Systemd-managed instances only (`systemctl --user restart
wingman-mcp.service`, detected via `systemctl --user is-active`); an
instance started any other way has no supervisor to bring it back up
afterward, so the button explains that instead of guessing.

**Virtual folders for multiple instances.** One hostname can front every
instance as `/dhk/…`, `/trent/…` — two ways, and they must not be
combined (the prefixes would stack):

*Native prefix (preferred — the server owns its folder):* each instance
listens on its path itself, so any pass-through front (nginx, Caddy,
direct tailnet access to the port) works with no path rewriting:

```ini
ExecStart=%h/.local/bin/wingman-mcp --http --port 8788 --prefix /trent
```

*Tailscale path mounts (no other proxy needed):* `tailscale serve
--set-path` **strips** the mount segment before forwarding, so with this
front the instances run WITHOUT `--prefix`:

```bash
sudo tailscale serve --bg --set-path /dhk   http://127.0.0.1:8787
sudo tailscale serve --bg --set-path /trent http://127.0.0.1:8788
```

Either way each person's bookmark is
`https://<host>…/<name>/ui/<their-token>/` and their MCP connector URL is
`…/<name>/mcp/<their-token>`; the UI emits only relative URLs, so pages
work identically under both fronts. The path segment is a label, not a
boundary — the token (and, under `serve`, tailnet membership) is still
the credential; isolation remains the Unix user + workspace, per the
multi-instance design.

**Native prefix alone does not fan multiple instances out from one
`tailscale serve`/`funnel` command.** `serve`/`funnel` map a hostname's
*root* to exactly one local port; running it a second time for a second
instance's port silently steals the mapping from the first (both
instances end up pointing at whichever ran last). Native prefix only
solves the *path* collision (each instance answers correctly once a
request reaches it) — getting requests to the right instance in the
first place needs either `--set-path` per instance (above), or:

*Distinct funnel ports (works with native prefix, no path-mount needed):*
give each instance its own external port instead of sharing root —
Tailscale Funnel allows 443, 8443, and 10000:

```bash
sudo tailscale funnel --bg 8787                # first instance keeps 443
sudo tailscale funnel --https=8443 --bg 8788   # second instance gets its own port
```

Set `WINGMAN_TUNNEL_PORT=8443` in that instance's `wingman-mcp.service`
`EnvironmentFile` (`~/.config/wingman/wingman.env` works, but note it's a
plain systemd env-var injection here, not one of the recognized names
`wingman.infrastructure.host_config` parses out of that file) so `wingman
mcp url` and the web UI's Connect tab print the URL with the right port
baked in — otherwise both assume the implicit 443 and print a URL that
404s.

**Seeing every instance on the box at once.** Checking on each instance
individually (`wingman mcp status` per Unix user) doesn't scale past two
people. `wingman admin url` prints a URL to a small cross-instance page —
name, version, running/stopped, and a link into each instance's own web
UI — gated by its own admin token, separate from any instance's own
capability token. It reads a hand-maintained list, `<workspace>/
installations.toml`, in whichever instance is serving the page (usually
your own):

```toml
[[instance]]
name = "dhk"
port = 8787
token = "<dhk's mcp-http-token>"

[[instance]]
name = "trent"
port = 8788
prefix = "/trent"
token = "<trent's mcp-http-token>"
stripped = true   # tailscale 'serve --set-path' strips /trent before the backend sees it
```

The "Open" link on each row prefers the same Tailscale-detected tunnel
host the Connect tab uses, so it works when the page itself is viewed
through the tunnel (the normal case) rather than only from lobster's own
loopback. `prefix` always describes the *public* URL; whether the backend
process itself also listens under that prefix depends on which of the two
multi-instance patterns above this instance uses:

- **Native `--prefix`** (default, `stripped` omitted or `false`): the
  backend really does register routes under `/trent`, so the admin page's
  health check hits `/trent/health` locally too.
- **Tailscale path mount** (`stripped = true`): the front strips `/trent`
  before forwarding, so the backend listens bare — the health check must
  hit plain `/health` locally, even though the public URL still has the
  prefix. Getting this wrong makes a genuinely healthy instance read as
  "stopped," since the health check 404s against a path the backend never
  registered.

If an instance sits on a non-default funnel port instead (two instances on
distinct ports rather than one port split by path), add `tunnel_port` the
same way `WINGMAN_TUNNEL_PORT` works for the Connect tab — but note the two
approaches solve the same problem differently; an instance normally needs
at most one of `stripped` or `tunnel_port`, not both.

Deliberately explicit rather than auto-discovered — no scanning other
users' home directories, no new cross-user read access. The page never
shows anything from inside a workspace, only whether it's up and a link
to it; each instance's own token remains the credential for its own data.

**Keeping every instance current (#125).** Manual, per-user upgrades don't
scale past two people either. `wingman-upgrade-all` is a separate,
root-run tool (not part of the per-workspace `wingman` CLI, since it acts
across accounts) that upgrades every configured shape-B user in one
scheduled pass: `git pull --ff-only`, `uv tool install --reinstall`, then
`systemctl --user restart wingman-mcp.service` for each — explicitly the
systemd path, never `wingman-ctl`'s `nohup` path (running `wingman-ctl
upgrade` under a systemd-managed account kills the process out from under
systemd and relaunches it unmanaged; `Restart=on-failure` won't recover a
graceful stop). One user's failed pull or reinstall is reported and
skipped — it never blocks the others.

This needs its own, separate root-owned install (it never touches any
workspace, never reads a key, and root should not share dhk's or Trent's
own `uv tool` install). `dhk/wingman` is a **private** repo, so root needs
either a credential of its own or a local install source — plain
`git+https://…` fails with `fatal: could not read Username for
'https://github.com': terminal prompts disabled`. Two ways to give root
one of those, in order of setup cost:

**Zero setup — install from dhk's already-cloned checkout** (the same
local-path pattern Trent's own upgrade uses below):

```bash
sudo -i                                            # or: sudo -u root -H bash
uv tool install /home/dhk/src/wingman
command -v wingman-upgrade-all                     # note the path for the unit below
```

Re-run that same `uv tool install --reinstall /home/dhk/src/wingman`
whenever `wingman-upgrade-all` itself needs upgrading (i.e. after a
change to `src/wingman/infrastructure/upgrade_all.py` lands) — after
dhk's own checkout has pulled that change, same as any local-path
install below. Ties root's own upgrade path to a human account's
checkout existing and staying current.

**A few minutes' setup — give root its own read-only credential**, so a
deployment/operation box like lobster can fetch on its own rather than
depending on any human account's checkout:

1. Create a **fine-grained** GitHub PAT (github.com → Settings → Developer
   settings → Fine-grained tokens): repository access limited to just
   `dhk/wingman`, permissions set to **Contents: Read-only** — nothing
   else. Treat it exactly like any other secret; rotate it if it ever
   leaks.
2. Store it where git's HTTPS auth already looks, root-only-readable,
   never on a command line or in a unit file (both land in `ps`/journal
   output):
   ```bash
   sudo install -m 600 /dev/null /root/.netrc
   sudo tee -a /root/.netrc >/dev/null <<'EOF'
   machine github.com
   login <your-github-username>
   password <the-fine-grained-PAT>
   EOF
   ```
3. Root's install line becomes the plain git URL, now that it has
   credentials:
   ```bash
   sudo -i
   uv tool install git+https://github.com/dhk/wingman.git
   ```

Either way root ends up with a working `wingman-upgrade-all`; pick
whichever matches how much you want root's own upgrade path independent
of any human account.

`/etc/systemd/system/wingman-upgrade-all.service` (a **system** unit,
root-run — not a `--user` unit like the ones above):

```ini
[Unit]
Description=Upgrade every wingman shape-B user (#125)

[Service]
Type=oneshot
Environment=WINGMAN_UPGRADE_USERS=dhk,trent,wingman-shared
Environment=WINGMAN_UPGRADE_SOURCE_trent=/home/dhk/src/wingman
ExecStart=/root/.local/bin/wingman-upgrade-all
```

Trent has no GitHub access of his own (#167) — omitting
`WINGMAN_UPGRADE_SOURCE_trent` doesn't skip him, it silently falls back to
the checkout-shape default (`git -C ~trent/src/wingman pull`), which fails
outright since that checkout was never meant to exist. Any local-path-shape
user needs their own `WINGMAN_UPGRADE_SOURCE_<username>` line, pointed at
whichever checkout-shape user's already-pulled checkout they install from.

`wingman-shared` (§9's shared multi-tenant account) needs no override —
it's checkout-shape just like dhk (its own real deploy-key-backed
checkout at `~/src/wingman`, §9), so it sweeps into the same rotation
with nothing beyond adding its name to the list. `systemctl --user
restart wingman-mcp.service` after its `git pull` restarts the ONE
shared process — briefly interrupting every tenant on it (Jason, Bob,
...) at once, the same accepted code-upgrade trade-off shape-B accounts
already take for themselves; only config/token changes (`wingman tenant
rotate-token`) avoid a restart, per RFC-048.

`/etc/systemd/system/wingman-upgrade-all.timer`:

```ini
[Unit]
Description=Run wingman-upgrade-all daily

[Timer]
OnCalendar=*-*-* 04:30
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wingman-upgrade-all.timer
```

Logs: `sudo journalctl -u wingman-upgrade-all.service`. Each user's
account needs no configuration for this to work — `sudo -u <user> …` from
root needs no password and grants no privilege that root didn't already
have; this only automates what root could already do by hand for every
file on the box. Assumes each listed user's checkout lives at
`~/src/wingman` (this repo's own documented convention, §1) — a user who
deviates isn't a candidate for automatic upgrade and should be upgraded
by hand.

## 9. Shared multi-tenant deployment (RFC-048)

Everything above is shape B: one Unix account, one workspace, one port,
per person. RFC-048 adds a second shape for when per-account overhead
stops paying for itself (a third-plus person, per `docs/RFC.md`'s own
trigger) — one shared process, share-nothing data (one SQLite DB per
tenant, unchanged), capability tokens per tenant. dhk and trent are not
required to move onto this — the two shapes coexist on the same box,
each on its own port.

**Run the provisioning scripts, don't hand-type this.** The steps below
were worked out live against a real box and hit exactly the pitfalls the
scripts (`scripts/wingman-provision-shared.sh`,
`scripts/wingman-add-tenant.sh`) now guard against automatically — listed
here so the reasoning survives, not as a checklist to retype:

- **SSH clone URL, never HTTPS.** A fresh service account has no GitHub
  identity and no cached credential helper; `https://github.com/...`
  prompts for a username that doesn't exist. `git@github.com:...` with a
  **read-only deploy key** (added once, on `dhk/wingman` → Settings →
  Deploy keys) is the whole fix — this account never needs push access.
- **`sudo -iu <user> <command> ~/path`** expands `~` in the CALLING
  shell, before `sudo` ever runs — silently operating on your own home
  directory instead of the target account's. Always wrap in
  `sudo -iu <user> bash -c '... ~/path ...'` so expansion happens inside
  the right shell.
- **`bash -c '...'` doesn't source `.profile`/`.bashrc`** the way an
  actual login does, so PATH fixes made via `uv tool update-shell` don't
  reach it — export PATH explicitly inside each such command instead of
  assuming it's inherited.
- **The `wingman` group** (§2, for reading `/etc/wingman/`) has to exist
  *before* adding the shared-process account to it — easy to reach this
  step before ever setting up RFC-047's global-secrets tier on a given box.
- **`systemctl --user` can't reach a fresh account's session bus at all**
  without `XDG_RUNTIME_DIR` pointed at `/run/user/<uid>` explicitly —
  `sudo -iu <user> ...` alone isn't enough, and fails with `Failed to
  connect to bus: No medium found`. The same fix `upgrade_all.py` already
  needed for this exact reason (RFC-042); `wingman-provision-shared.sh`
  forces the user manager up first, then passes the var explicitly on
  every `systemctl --user` call.

```bash
# once per box
sudo scripts/wingman-provision-shared.sh
# once per tenant
sudo scripts/wingman-add-tenant.sh jason
sudo scripts/wingman-add-tenant.sh bob
```

Both are idempotent — safe to re-run after a partial failure, or against
a box that's already partway through by hand; each step checks its own
precondition first. `WINGMAN_SHARED_USER`/`WINGMAN_SHARED_PORT`/
`WINGMAN_SHARED_TAILSCALE_PATH` env vars override the defaults
(`wingman-shared`, `8789`, `/shared`).

**Recovering or rotating a tenant's URL** (#209/#210) — no self-service
flow, no new credential, by design (RFC-048's trust surface stays
exactly the tenant registry + per-tenant token files, nothing added for
this):

```bash
sudo -iu wingman-shared bash -c 'export PATH="$HOME/.local/bin:$PATH"; wingman tenant url <slug> --port 8789 --tunnel-prefix /shared'
sudo -iu wingman-shared bash -c 'export PATH="$HOME/.local/bin:$PATH"; wingman tenant rotate-token <slug> --port 8789 --tunnel-prefix /shared'
```

Call `wingman` directly, not the `wg` alias (`wg` only exists in an
interactive shell that's sourced its own `.bashrc`, per §1's setup step
for a human's own account — `wingman-shared` was never given one, and
a non-interactive `bash -c` wouldn't source it even if it had been).
Found live migrating trent's account (RFC-048 Phase 3): the script's own
printed next-steps had this exact bug, since fixed.

`--tunnel-prefix` must match `WINGMAN_SHARED_TAILSCALE_PATH` (default
`/shared`, set at `wingman-provision-shared.sh` time) — it only changes
the printed *tunnel* URL, since `tailscale funnel --set-path` strips that
prefix before forwarding and the shared process itself always runs with
no `--prefix` of its own. Omitting it prints a URL that 404s at the
tunnel, not at wingman — easy to mistake for a broken deployment.

**`funnel`, never plain `serve`, for this mount.** Jason/Bob-style
tenants aren't on the owner's tailnet — the shared process needs public
reach, not tailnet-only. This matters beyond correctness: `tailscale
serve --set-path` for this path would silently drop Funnel for the
**whole hostname**, demoting every other already-public mount (dhk's,
trent's, alexandria's) back to tailnet-only as a side effect — funnel is
a per-hostname toggle, not a per-path one. Hit live; see
`wingman-provision-shared.sh`'s own comment at this step.

Rotation invalidates the old token and issues a new one in the same
step — no restart of the shared process, no effect on any other
tenant's session (`infrastructure/tenant_process.py`'s SIGHUP reload).

**Overnight for every tenant, one timer instead of one per account.**
Tenants under the shared process have no per-account systemd timer to
hang §4's pattern off of, so `wingman tenant overnight` loops the whole
registry in one process invocation — each tenant gets its own strict
`Tenant.config()` (never a shared key, never a shell-out with
`WINGMAN_DATA_DIR` set, which would use the full env ladder and risk one
tenant's run spending a key that isn't theirs), and one tenant's failure
(nothing enrolled, a fetch error) is reported without blocking the rest,
mirroring §7's `wingman-upgrade-all` isolation.

`~/.config/systemd/user/wingman-tenant-overnight.service` (as
`wingman-shared`):

```ini
[Unit]
Description=Wingman overnight run, every tenant (RFC-048)

[Service]
Type=oneshot
EnvironmentFile=-%h/.config/wingman/wingman.env
EnvironmentFile=-%h/.config/wingman/secrets.env
ExecStart=%h/.local/bin/wingman tenant overnight
```

`~/.config/systemd/user/wingman-tenant-overnight.timer`:

```ini
[Unit]
Description=Run wingman tenant overnight every morning

[Timer]
OnCalendar=*-*-* 05:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo -u wingman-shared env XDG_RUNTIME_DIR=/run/user/$(id -u wingman-shared) \
  systemctl --user daemon-reload
sudo -u wingman-shared env XDG_RUNTIME_DIR=/run/user/$(id -u wingman-shared) \
  systemctl --user enable --now wingman-tenant-overnight.timer
```

(the `sudo -u ... env XDG_RUNTIME_DIR=...` shape, not `sudo -iu`, for the
same reason §9's provisioning script needs it — see the pitfalls list
above.) Logs: `sudo -u wingman-shared env XDG_RUNTIME_DIR=/run/user/$(id -u wingman-shared) journalctl --user -u wingman-tenant-overnight.service`.

A one-off run any time: `sudo -iu wingman-shared bash -c 'export PATH="$HOME/.local/bin:$PATH"; wingman tenant overnight'`.

**Redeploying the shared process** (#150's "safe restart primitive" —
deliberately not wired to any automatic CI/CD trigger; every tenant on
the shared process is briefly interrupted by a restart, and this repo
merges often enough that auto-deploy-on-every-merge would mean surprise
interruptions for people who aren't the one deploying — a decision worth
making explicitly later, not defaulted into now). One command instead of
hand-constructing the pull/reinstall/restart/verify sequence live —
exactly the class of mistake that produced the systemd bus and pidfile
bugs earlier tonight:

```bash
sudo scripts/wingman-redeploy-shared.sh
```

Pulls, reinstalls, restarts, then polls `/health` (unauthenticated,
version + start time only, RFC-033) until it returns 200 with a NEW
`started_at` — confirming the restart actually took effect, not just
that the port answers.

**Migrating an existing shape-B account onto the shared process** (RFC-048
Phase 3 — dhk/trent's own accounts, once Phase 2 has run clean for a real
operating period; not the jason/bob-style new-tenant path above). Dry-run
by default, prints every step; pass `--apply` to actually do it:

```bash
sudo scripts/wingman-migrate-tenant.sh dhk          # dry-run
sudo scripts/wingman-migrate-tenant.sh dhk --apply
```

Backs up the account's live shape-B workspace (`wingman backup`), restores
it into a new tenant data dir under `wingman-shared` (`wingman restore`),
carries the account's own Anthropic/Voyage/GitHub-issues keys into that
tenant's `keys.env` (RFC-048 keeps tenants strict — never the host/global
secrets tiers), and registers it via `wingman-add-tenant.sh`. Deliberately
does **not** stop the old shape-B `wingman-mcp.service` or cut traffic over
— both processes can read the same restored snapshot independently, but
`wingman restore` is one-time, not an ongoing sync, so don't run both
against live traffic at once. Writes a durable `.migrated-from-shape-b`
marker into the new tenant's data dir (slug, timestamp, source archive,
a `cutover_confirmed` flag) — the printed next-steps walk through
verifying the new tenant, flipping that flag once confirmed, and only
then disabling (never deleting) the old unit as a kept rollback. That
marker exists because a real migration on 2026-08-04 got run and then
silently interrupted by unrelated live incidents before cutover, leaving
a registered-but-unverified tenant with no trace of what had happened —
see the script's own header for the full story.

**Editing a tenant's `keys.env` after they're already connected?** Have
them fully disconnect and reconnect their Claude client before retrying
whatever needed the new key. `TenantRoutingASGIApp` resolves
`tenant.config()` fresh per HTTP request at the ASGI layer (no server-side
caching — verified by reading the code), but an already-established
client session can keep reusing state from before the file changed. Hit
live migrating dhk's own account: a key added to a running tenant's
`keys.env` wasn't picked up until the Claude CLI session was restarted,
even though every server-side check (file contents, ownership, a direct
`wingman keys test` run as the exact account and data dir the server
uses) confirmed the key was correct and readable the whole time.
