# Wingman — Introduction & Walkthrough

A first session, end to end: from download to a finished, evidence-backed
**point-of-view document** about someone you want to reach. Every step shows
the command and what you should see. Budget: about thirty minutes, most of
it waiting on your LinkedIn export email.

## What Wingman is, in three paragraphs

Wingman is a local-first career-intelligence system. It optimizes the
*quality* of opportunities, not the volume of applications: the north star
is to find a small number of exceptional opportunities and make you the
obvious candidate for the best of them. Everything lives in one SQLite
workspace on your machine.

Its discipline is **evidence before assertion**. Models propose; ordinary
code disposes. A profile claim, a stance on a POV card, a company theme, an
outreach talking point — each survives only if it carries a verbatim quote
from a stored document. Zero survivors means nothing is stored. You will
never find a polished claim in a Wingman artifact that you cannot trace to
a sentence somebody actually wrote.

And it **never acts for you**. Wingman drafts raw material — talking
points, intro bullets, briefing docks — that you compose into your own
voice and send yourself. No message, application, or external write ever
leaves the machine (RFC-006). Network reads happen only when you run a
command that says it fetches.

## Step 1 — Download

```bash
git clone https://github.com/dhk/wingman ~/Documents/dev/wingman
cd ~/Documents/dev/wingman
```

(Or skip the checkout: `uv tool install git+https://github.com/dhk/wingman`
and go to step 3.)

## Step 2 — Install

```bash
uv tool install .
wingman init
wingman doctor
```

You should see: `Workspace ready at …`, then doctor's per-check report —
`[ok] python`, `[ok] data dir`, `[ok] database`, and an honest note about
any missing key. Doctor never fails you for optional credentials.

## Step 3 — Configure keys

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # if not already in your profile
export VOYAGE_API_KEY=pa-...
wingman keys set anthropic            # stores in the macOS Keychain
wingman keys set voyage
wingman keys list
```

Expected: both keys listed with source `keychain` (or `environment` if your
shell exports them — env always wins). From here on, every entrypoint —
CLI, MCP server, scheduled runs — finds them automatically. Linux: skip
`keys set`; exported variables are the whole setup.

*No keys at all?* Everything deterministic still works, and similarity can
run keyless: put `provider = "hashed"` under `[models.embed_semantic]` in
the workspace `models.toml`. You'll stop short of the model-built POV card
in step 9 until an Anthropic key arrives.

## Step 4 — Start the MCP server

The server is the same brain with tools instead of subcommands — set it up
once, use it from three places (details and remote access:
[`INSTALL.md`](INSTALL.md) §4):

```bash
claude mcp add wingman -- wingman-mcp        # Claude Code
```

For Claude Desktop, point `claude_desktop_config.json` at the absolute
path from `which wingman-mcp` and fully restart (Cmd-Q). Verify: the tools
panel shows **wingman** with its full tool set (dozens of tools — the
exact count grows as wingman does).

## Step 5 — First commands in the bash CLI

```bash
wingman status
```

Expected: a workspace path and zeros everywhere — honest emptiness. Want to
see the machinery on real public data before feeding it yours? `wingman
demo` runs a guided tour in an isolated scratch workspace (your data
untouched, no keys required); delete its folder to remove every trace.

## Step 6 — Add your data

**Resume** (any of Markdown, text, PDF, DOCX, LaTeX, or a link-accessible
Google Doc):

```bash
wingman ingest ~/Documents/resume.pdf
```

Expected: an ingestion summary — items accepted with verbatim evidence,
anything unsupported rejected *and said so*. This builds your canonical
profile (`reports/career.md`).

**LinkedIn export** (deterministic, no model). Request at LinkedIn →
Settings → Data privacy → *Get a copy of your data* (the fast option with
positions, skills, recommendations, connections):

```bash
wingman ingest-linkedin Basic_LinkedInDataExport_*.zip           # profile items
wingman people import-connections Basic_LinkedInDataExport_*.zip # seeds the watchlist
```

Privacy note: connection emails are never read into the database.

**Your own writing** — this is what grounds outreach in *your* words:

```bash
wingman corpus add substack-export.zip --source-type substack_post
wingman evidence "a topic you write about"    # cited quotes back, instantly
```

## Step 7 — Add people

Pick someone you actually read and might want to reach:

```bash
wingman people add "Jane Author" --substack https://janeauthor.substack.com --company "Acme"
wingman people fetch "Jane Author"
wingman people docs "Jane Author"
```

Expected: fetch reports how many posts were stored; docs lists them with
dates and URLs. `add-feed` handles non-Substack sources — Medium,
WordPress, Ghost, even feed-less VC blogs (watched via their index page,
attributed to the organization, always confirm-before-attach). Then:

```bash
wingman embed                       # one explicit step; text goes to the embeddings provider
wingman people similar              # who writes about what you write about
```

## Step 8 — Use it from Claude

The same workspace, conversationally. In **Claude Code** or **Claude
Desktop** try:

> "Use wingman to show my workspace status, then fetch Jane Author's
> writing and tell me what she cares about."

Claude calls `status`, `people_fetch`, `people_pov` — the identical
validation pipeline as the CLI; the model cannot bypass the evidence rules
(RFC-008). Anything the CLI does, you can ask for in prose: assessments,
briefs, dossiers, exports, watchlists, overnight runs.

## Step 9 — The POV doc

The destination. Build the evidence-backed card of what Jane believes:

```bash
wingman people pov "Jane Author"
```

Expected: 3–6 stances, each `[values]`/`[attitude]`/`[technical]`/
`[strategy]`-tagged, each backed by a verbatim quote with its source doc —
plus a "writes about" topic list. Any stance the model proposed without a
real quote was rejected, visibly. Where's *your* side? `wingman pov` does
the same over your own corpus.

Now make it a document worth carrying into a meeting:

```bash
wingman people brief "Jane Author" --purpose introduction   # talking points + intro bullets
wingman export person "Jane Author"                         # landscape 2×2 briefing dock
npx md-to-pdf "<the path the export printed>"
```

The PDF is a one-glance dock: outreach brief | point of view | background
(warmth, links, people in common) | recent news. The intro bullets are raw
material — you write the message, in your voice; Wingman never sends
anything.

## Where to go next

- **Follow a company** — `wingman company follow "Acme" --url
  https://acme.com` enrolls it (and your people there) for deep refresh;
  `wingman overnight` catches everything up and writes a dated digest —
  schedule it and wake up to answers ([`INSTALL.md`](INSTALL.md) §5).
- **Ask the workspace anything** — `wingman search "semantic layers"`
  sweeps every store (corpus, writing, stances, news, research, briefs)
  into one ranked, cited list, including `[semantic]` hits that match by
  meaning rather than keywords; from Claude, the `search` tool is the
  starting point for any "what do we know about…" question.
- **Assess a role** — `wingman assess job.md` → a cited fit brief.
- **Company intelligence** — `company dossier`, `company pov`,
  `company research` (approved pages, new-links-as-hiring-signal).
- **Keep it alive** — `wingman sync` daily, `wingman backup` weekly.

Every command's `--help` states what it touches and exactly what, if
anything, leaves the machine. The rationale ledger behind all of it is
[`RFC.md`](RFC.md).
