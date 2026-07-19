# Wingman — Install & Operations Guide

Everything needed to go from a clean machine to a running, maintained
instance. For a guided first session that ends in a real deliverable, read
[`WALKTHROUGH.md`](WALKTHROUGH.md) after step 4 here.

Wingman is single-user and local-first: the whole workspace is one SQLite
file plus an inbox and reports folder on your machine. Nothing runs in the
background, nothing is sent on your behalf (RFC-006), and every network read
is a command you ran (RFC-009/018).

## 1. Requirements

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- macOS or Linux. Keychain-backed key storage (`wingman keys`) is
  macOS-only; on Linux use environment variables.
- Optional, for PDF rendering: Node (`npx md-to-pdf`).

Deploying to an always-on Ubuntu server (systemd timers, the MCP server as
a service, claude.ai over Tailscale) has its own guide:
[`SERVER.md`](SERVER.md).

## 2. Download and install

From a release / the repository directly:

```bash
uv tool install git+https://github.com/dhk/wingman
```

Or from a checkout (lets you `git pull` upgrades):

```bash
git clone https://github.com/dhk/wingman ~/Documents/dev/wingman
cd ~/Documents/dev/wingman
uv tool install .
```

Both install two commands: **`wingman`** (the CLI) and **`wingman-mcp`**
(the MCP server). Then create the workspace and check the environment:

```bash
wingman init      # creates the workspace + models.toml (idempotent)
wingman doctor    # reports what is and isn't configured — never fails you
                  # for a missing optional credential
```

The workspace lives at `$WINGMAN_DATA_DIR` if set, otherwise the platform
data directory — macOS: `~/Library/Application Support/wingman` (note the
space: always quote that path in shell commands).

**Upgrading** (checkout install): `git pull && uv tool install --reinstall .`
— then fully restart any MCP clients (Claude Desktop: Cmd-Q) so new tools
load.

## 3. Configure keys

Two optional credentials, each unlocking one capability class:

| Key | Unlocks | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Model steps: resume ingestion, job assessment, POV cards, company themes, outreach briefs | Everything deterministic still works (imports, corpus, feeds, keyword search, dossiers, research diffs) |
| `VOYAGE_API_KEY` | Semantic similarity and embeddings at full quality | Set `provider = "hashed"` under `[models.embed_semantic]` in the workspace `models.toml` for keyless local similarity |

**macOS (recommended): store them in the Keychain once** (RFC-019). Every
entrypoint — CLI, MCP server, scheduled runs — hydrates them at startup, so
no secret ever sits in a config file, plist, or wrapper script:

```bash
wingman keys set anthropic   # takes the exported env var, or prompts hidden
wingman keys set voyage
wingman keys list            # shows each key's source, never its value
```

An exported environment variable always wins over the Keychain. On Linux,
export the variables in your shell profile; that is the whole setup.

## 4. Start the MCP server

The same server, three ways to reach it. All 35 tools run the same
deterministic validation as the CLI (RFC-008).

**Claude Code (CLI):**

```bash
claude mcp add wingman -- wingman-mcp
```

**Claude Desktop:** edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(use the absolute path from `which wingman-mcp` — the app does not inherit
your shell PATH):

```json
{
  "mcpServers": {
    "wingman": { "command": "/Users/you/.local/bin/wingman-mcp" }
  }
}
```

No `env` block is needed when keys are in the Keychain. Fully restart the
app (Cmd-Q), then check the tools panel shows **wingman**.

**Remote (claude.ai web/mobile, RFC-017):** the server speaks streamable
HTTP, bound to loopback, at an unguessable capability path; you provide the
tunnel:

```bash
wingman-mcp --http                   # http://127.0.0.1:8787/mcp/<token>
tailscale funnel 8787                # public HTTPS for a claude.ai connector
wingman-mcp --http --rotate-token    # revoke every previously shared URL
```

Treat the URL like a password. Wingman never opens a public listener
itself; a non-loopback `--host` must be stated explicitly and warns.

## 5. Operate it

**The daily commands:**

```bash
wingman status                      # what the workspace holds
wingman sync                        # fetch every watched source + embed what's new
wingman make-it-so "Jane Author"    # everything for one target, end to end (alias: miso)
wingman watchlist run targets       # cycle a named group through make-it-so
wingman overnight                   # deep-refresh everything followed → dated digest
```

**Scheduling (RFC-018).** Wingman runs no daemon; you own the schedule.
Enrollment via `wingman company follow` is the standing consent record —
`wingman watchlist show overnight` lists exactly what a run touches,
`wingman watchlist remove` revokes. macOS launchd, daily at 05:30
(`~/Library/LaunchAgents/com.wingman.overnight.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.wingman.overnight</string>
  <key>ProgramArguments</key>
  <array><string>/Users/you/.local/bin/wingman</string><string>overnight</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>/tmp/wingman-overnight.log</string>
  <key>StandardErrorPath</key><string>/tmp/wingman-overnight.log</string>
</dict></plist>
```

Keys come from the Keychain — no secrets in the plist. Run `wingman
overnight` once by hand first and click **Always Allow** on the Keychain
prompt so unattended runs never stall. `launchctl load` the file to arm it;
`launchctl unload` is the off-switch. The same pattern schedules the
lighter `wingman sync`.

**Backups.** A live SQLite file does not sync safely through
iCloud/Dropbox; a closed tarball does:

```bash
wingman backup ~/Dropbox/wingman-backups --keep 14   # dated tarball, pruned
wingman restore <archive>.tar.gz --force             # the inverse (CLI-only, deliberately)
```

**PDF rendering.** Exports are Markdown + CSS; render with
`npx md-to-pdf "<file>"` — the PDF lands next to the source. Every export
command prints its exact render command.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `wingman: command not found` | `uv tool install` puts binaries in `~/.local/bin` — ensure it's on PATH |
| Desktop shows no wingman server | Config path/JSON typo, or relative command — use the absolute `which wingman-mcp` path; Cmd-Q restart |
| New tools missing in Desktop | Stale binary or no full restart — reinstall (step 2) and Cmd-Q |
| Model steps fail with a key error | `wingman keys list` — is the key `environment`, `keychain`, or `not set`? |
| `wingman embed` fails, posts kept | Voyage configured but no key — set the key or switch `models.toml` to `hashed` |
| Overnight launchd run hangs | Keychain prompt was never answered — run once by hand, **Always Allow** |
| A feed/news/research fetch fails | Failures are always shown, never silent; the previous snapshot is kept — re-run later |
| Restore refuses to run | It found a live database — that's the guard; pass `--force` to overwrite deliberately |

`wingman doctor` diagnoses the environment; `wingman keys list` the
credentials; every command's `--help` states exactly what it touches and
what leaves the machine.
