# Standing Up Your Own Wingman

A from-scratch guide for one person bringing up their own instance. Wingman is
single-user and local-first: everything lives in one SQLite file on your
machine, and each capability degrades gracefully when a credential or data
source is missing — you can start with nothing but a resume.

## 1. Install

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <your-fork-or-this-repo> wingman
cd wingman
uv tool install .        # installs the 'wingman' and 'wingman-mcp' commands
wingman init             # creates the workspace + models.toml
wingman doctor           # tells you what is and isn't configured
```

The workspace lives in your platform user-data directory (macOS:
`~/Library/Application Support/wingman`), or wherever `WINGMAN_DATA_DIR`
points. `wingman doctor` never fails you for a missing optional credential —
it reports what will and won't work.

**Want to see it working before feeding it your data?** Run `wingman demo`:
it seeds a watchlist of real public Substack publications, fetches their
feeds, and demonstrates evidence search and similarity end to end — no API
keys needed (similarity uses the local `hashed` provider until you configure
Voyage). The demo runs in its own isolated workspace (a `demo` folder next
to your real one): your data is never read, written, or sent anywhere, and
deleting that folder removes every trace.

## 2. Credentials — all optional, each unlocking one capability

| Credential | Unlocks | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Resume ingestion (`wingman ingest`), job assessment (`wingman assess`) — the model-extraction steps | Everything deterministic still works: LinkedIn import, corpus, watchlist, feeds, keyword search |
| `VOYAGE_API_KEY` | Semantic similarity (`wingman embed`, `people similar`, `people like`) at full quality | Two fallbacks: set `provider = "hashed"` under `[models.embed_semantic]` in the workspace `models.toml` for keyword-level similarity with zero network/keys — or skip embedding entirely; keyword search (`evidence`) is unaffected |

Export them in your shell profile. Nothing is sent to any provider except
when you run the specific commands above (see RFC-009/RFC-010 in
[`RFC.md`](RFC.md) for the exact egress rules).

## 3. Feed it your data — in any order, skip what you don't have

Each source is independent; ingest what exists, add the rest later.

**Resume** (needs `ANTHROPIC_API_KEY`):

```bash
wingman ingest path/to/resume.md
```

**LinkedIn data export** (no key needed — fully deterministic). Request it at
LinkedIn → Settings & Privacy → Data privacy → Get a copy of your data
(choose the fast "specific files" option with positions, skills,
recommendations, connections; arrives by email within minutes):

```bash
wingman ingest-linkedin Basic_LinkedInDataExport_*.zip     # profile items
wingman people import-connections Basic_LinkedInDataExport_*.zip  # watchlist seed
```

Privacy note: connection emails are never read into the database, and the
connections CSV is never copied out of your export zip.

**Your own writing** (no key needed). For Substack: Settings → Exports →
"Export your data", then:

```bash
wingman corpus add substack-export.zip --source-type substack_post
```

Also works on directories of Markdown/text/HTML (blog checkouts, README
collections, essays).

**People you read** (no key needed; network access only when you run fetch):

```bash
wingman people add "Author Name" --substack https://their.substack.com
wingman people fetch --all
```

*Bootstrap tip — finding your subscriptions:* Substack's export doesn't
include what you *read*. But every newsletter lands in your inbox from
`<publication>.substack.com`, so an email search for `from:substack.com`
over the last ~90 days, aggregated by sender, is a complete list of your
active subscriptions — each sender handle is the publication URL. If you use
Claude with a Gmail connector, ask it to build the `wingman people add`
script from that search; it takes a minute.

## 3½. Keep it fresh — one command, optionally scheduled

`wingman sync` fetches every watched source and embeds whatever is new, in
one explicit invocation. Wingman never fetches in the background on its own
(RFC-009), but *you* can schedule the command with your OS. On macOS, save
this as `~/Library/LaunchAgents/io.wingman.sync.plist` (adjust the binary
path to `which wingman`), then `launchctl load` it — it runs every morning
at 08:30:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>io.wingman.sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/.local/bin/wingman</string>
    <string>sync</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>VOYAGE_API_KEY</key><string>pa-your-key</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>/tmp/wingman-sync.log</string>
  <key>StandardErrorPath</key><string>/tmp/wingman-sync.log</string>
</dict>
</plist>
```

Graceful degradation: without a Voyage key, either set
`provider = "hashed"` in `models.toml` (sync then stays fully local) or the
embed step fails visibly in the log while fetched posts are kept.

## 4. Verify and use

```bash
wingman status        # counts of everything ingested
wingman evidence "a topic you write about"      # cited quotes from your corpus
wingman assess path/to/job.md                   # cited fit brief (needs ANTHROPIC_API_KEY)
wingman embed && wingman people similar         # who thinks like you (needs a key or 'hashed')
```

## 5. Optional: connect Claude

Expose the workspace to Claude Desktop / Claude Code as MCP tools — see
"Use from Claude (MCP)" in the [README](../README.md). The server is
stdio-only: nothing listens on the network.

## Degradation summary

| You have… | You get… |
|---|---|
| Nothing but the CLI | An initialized, inspectable workspace |
| A LinkedIn export | Cited profile (roles/skills/testimonials) + a seeded watchlist — no keys, no network |
| + your writing | Keyword-searchable evidence corpus (`wingman evidence`) |
| + feeds fetched | Other people's writing, searchable with attribution |
| + `ANTHROPIC_API_KEY` | Resume ingestion and cited job-fit briefs |
| + `VOYAGE_API_KEY` (or `hashed`) | Semantic similarity: `people similar`, `people like` |

Every rung works without the rungs above it.
