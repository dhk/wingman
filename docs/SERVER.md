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
git clone https://github.com/dhk/wingman.git ~/wingman
cd ~/wingman && uv tool install .
wingman --version
```

The workspace defaults to `~/.local/share/wingman` (XDG). Set
`WINGMAN_DATA_DIR` before `wingman init` if you want it elsewhere —
consistently, including inside the systemd units below.

## 2. Keys (the canonical host file — the Keychain is macOS-only)

`wingman keys` requires macOS's `security` binary and fails visibly on
Linux. On a server host, one file is canonical (RFC-019/034/040, #122):
`~/.config/keys.env`, mode 600. Wingman itself reads it directly — CLI and
MCP server alike, on a fresh shell with zero exports — so this one file is
the only thing to create or copy when migrating a box or debugging "which
key file is actually in effect":

```bash
install -m 600 /dev/null ~/.config/keys.env
cat >> ~/.config/keys.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
EOF
```

The resolution order (`wingman doctor` names the winning source per key
and flags a key defined in more than one place with a different value):
**environment > Keychain (macOS) > `~/.config/keys.env` > the workspace's
own `keys.env`**. A systemd `EnvironmentFile=` or a shell export still
works exactly as before — either just becomes the "environment" source,
which always wins — but neither is required anymore for the CLI or MCP
server to see these keys.

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
EnvironmentFile=-%h/.config/keys.env
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
EnvironmentFile=-%h/.config/keys.env
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
`keys.env`. The startup banner prints a ready-to-paste https connector
URL and web-UI URL (token included) for each tunnel hostname; `wingman-ctl
start`/`status` echo them too.

**Get the URLs any time** — whether or not you started the server
yourself, and without hunting the token file: `wingman mcp url` (add
`--port`/`--prefix`/`--allowed-host` if you ran the server with
non-default flags). It computes the same lines the banner prints, straight
from the token file and Tailscale auto-detection.

In claude.ai: Settings → Connectors → Add custom connector → paste that
URL. The same URL works in Claude Desktop and the Claude mobile apps once
the connector is added to your account.

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
- **Updates:** `cd ~/wingman && git pull && uv tool install --reinstall .`
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
`EnvironmentFile` (`~/.config/keys.env` works, but note it's a plain
systemd env-var injection here, not one of the known keys wingman's own
resolution ladder parses out of that file) so `wingman mcp url` and the
web UI's Connect tab print the URL with the right port baked in —
otherwise both assume the implicit 443 and print a URL that 404s.

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
scheduled pass: `uv tool install --reinstall` straight from git (no local
checkout, for any account — same clone-free pattern as this tool's own
install just below), then `systemctl --user restart wingman-mcp.service`
for each — explicitly the systemd path, never `wingman-ctl`'s `nohup`
path (running `wingman-ctl upgrade` under a systemd-managed account kills
the process out from under systemd and relaunches it unmanaged;
`Restart=on-failure` won't recover a graceful stop). One user's failed
reinstall is reported and skipped — it never blocks the others. Every
configured user is upgraded identically; there is no per-user checkout to
keep in sync, so nothing to misconfigure per account.

This needs its own, separate root-owned install (it never touches any
workspace, never reads a key, and root should not share dhk's or Trent's
own `uv tool` install):

```bash
sudo -i                                            # or: sudo -u root -H bash
uv tool install git+https://github.com/dhk/wingman.git
command -v wingman-upgrade-all                     # note the path for the unit below
```

`/etc/systemd/system/wingman-upgrade-all.service` (a **system** unit,
root-run — not a `--user` unit like the ones above):

```ini
[Unit]
Description=Upgrade every wingman shape-B user (#125)

[Service]
Type=oneshot
Environment=WINGMAN_UPGRADE_USERS=dhk,trent
ExecStart=/root/.local/bin/wingman-upgrade-all
```

Add `Environment=WINGMAN_UPGRADE_SOURCE=<git url>` only to point every
user's install at a fork/branch/tag for testing — the default
(`git+https://github.com/dhk/wingman.git`) is right for normal use, and
applies identically to every configured user.

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
