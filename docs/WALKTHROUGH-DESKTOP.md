# Wingman — Claude Desktop Walkthrough

A first session, end to end, done as a conversation with Claude Desktop:
from adding the connector to a finished, evidence-backed **point-of-view
document** about someone you want to reach. Budget: about thirty minutes,
most of it waiting on your LinkedIn export email. Prefer a terminal? See
[`WALKTHROUGH.md`](WALKTHROUGH.md) for the CLI-first version of the same
session.

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
leaves the machine (RFC-006). Network reads happen only when you ask
Claude for something that explicitly fetches, and it calls the same
validated tools the CLI does (RFC-008) — the model cannot bypass the
evidence rules just because the request arrived in prose.

## Step 1 — Add the connector to Claude Desktop

One-time setup, in a terminal — everything after this happens in the
chat.

```bash
uv tool install git+https://github.com/dhk/wingman
wingman init
export ANTHROPIC_API_KEY=sk-ant-...   # if not already in your profile
export VOYAGE_API_KEY=pa-...
wingman keys set anthropic            # stores in the macOS Keychain
wingman keys set voyage
```

Then point Claude Desktop at the server. Edit
`~/Library/Application Support/Claude/claude_desktop_config.json` (use the
absolute path from `which wingman-mcp` — the app doesn't inherit your
shell PATH):

```json
{
  "mcpServers": {
    "wingman": { "command": "/Users/you/.local/bin/wingman-mcp" }
  }
}
```

Fully restart the app (Cmd-Q, not just close-window). Expected: the tools
panel shows **wingman** with 35 tools.

No keys at all? Everything deterministic still works — ask Claude for a
workspace status check (Step 2) and it will tell you which two
credentials are missing and exactly what each unlocks.

## Step 2 — Say hello

Type to Claude:

> Use wingman to show me my workspace status.

Expected: a workspace path and zeros everywhere — honest emptiness, not an
error. This is also your go-to sanity check later: if anything downstream
looks wrong, ask Claude to run `status` again.

## Step 3 — Add your data

**Resume.** Attach the file to your message (PDF, DOCX, Markdown, text —
or paste a link-accessible Google Doc) and say:

> Here's my resume — add it to my wingman profile.

Claude extracts the text and calls `ingest_resume_text` (or
`ingest_resume_url` for a Google Doc link) — the identical validation
pipeline as the CLI. Expected: an ingestion summary, items accepted with
verbatim evidence, anything unsupported rejected *and said so*. This
builds your canonical profile; ask Claude to show it to you any time
("show me my career profile" calls `career_profile`).

**LinkedIn export.** Request yours at LinkedIn → Settings → Data privacy →
*Get a copy of your data* (the fast option — positions, skills,
recommendations, connections). Importing the profile half is still a
one-time terminal step — it's a whole zip archive and there's no upload
tool for it yet:

```bash
wingman ingest-linkedin ~/Downloads/Basic_LinkedInDataExport_*.zip
```

Once that's done, hand the connections half back to Claude:

> Import my connections from
> ~/Downloads/Basic_LinkedInDataExport_2026-07-01.zip — that seeds my
> watchlist.

Claude calls `people_import_connections` with that path — it reads the
file straight off your disk; nothing uploads anywhere. Privacy note:
connection emails are never read into the database.

**Your own writing.** Same shape as the LinkedIn export — a bulk export
(Substack, etc.) is a one-time terminal step:

```bash
wingman corpus add substack-export.zip --source-type substack_post
```

After that, ask Claude:

> What do I write about when it comes to <a topic>?

— it calls `evidence`, cited quotes back, instantly.

## Step 4 — Add people

Pick someone you actually read and might want to reach. Tell Claude:

> Add Jane Author to wingman — she writes at
> https://janeauthor.substack.com and works at Acme. Then fetch her
> writing and show me what you found.

Claude calls `people_add`, `people_fetch`, `people_docs` in sequence.
Expected: fetch reports how many posts were stored; docs lists them with
dates and URLs. Non-Substack sources — Medium, WordPress, Ghost, even
feed-less VC blogs — go through `feed_discover`/`feed_attach` instead;
just describe the site and ask Claude to find its feed (always
confirm-before-attach).

Then:

> Embed her new writing, and tell me who else writes about similar things.

— `embed` (one explicit step; text goes to the embeddings provider), then
`people_similar`.

## Step 5 — The POV doc

The destination. Ask Claude:

> Build Jane Author's point-of-view card.

Claude calls `people_pov`. Expected: 3–6 stances, each
`[values]`/`[attitude]`/`[technical]`/`[strategy]`-tagged, each backed by a
verbatim quote with its source doc — plus a "writes about" topic list. Any
stance the model proposed without a real quote was rejected, visibly.
Where's *your* side?

> Build my own point-of-view card.

— the same `my_pov` pass over your own corpus.

Now make it a document worth carrying into a meeting:

> Put together an introduction brief for Jane Author, and export a
> briefing dock I can turn into a PDF.

Claude calls `people_brief` (talking points + intro bullets) then
`export_pdf`; run `npx md-to-pdf "<the path it gives you>"` in a terminal
to render it. The PDF is a one-glance dock: outreach brief | point of view
| background (warmth, links, people in common) | recent news. The intro
bullets are raw material — you write the message, in your voice; Wingman
never sends anything.

## Where to go next

Everything below is a sentence to Claude, not a command to remember:

- **Follow a company** — "Follow Acme at https://acme.com" enrolls it (and
  your people there) for deep refresh; "run my overnight refresh" catches
  everything up and writes a dated digest — schedule it
  ([`INSTALL.md`](INSTALL.md) §5) and wake up to answers.
- **Ask the workspace anything** — "What do we know about semantic
  layers?" sweeps every store (corpus, writing, stances, news, research,
  briefs) into one ranked, cited list, including hits that match by
  meaning rather than keyword.
- **Assess a role** — paste or link a posting and ask "does this fit me?"
  (`assess_job` / `assess_job_url`) for a cited fit brief, then "build me
  an application pack" (`pack`).
- **Company intelligence** — "give me Acme's dossier / point of view /
  latest research."
- **Keep it alive** — `wingman sync` daily and `wingman backup` weekly are
  still worth a terminal or a schedule, but Claude can run either
  conversationally too ("sync my workspace", "back it up").

Every tool call Claude makes runs the same deterministic validation as the
CLI (RFC-008) — nothing the model proposes survives without a stored quote
behind it. The rationale ledger behind all of it is
[`RFC.md`](RFC.md).
