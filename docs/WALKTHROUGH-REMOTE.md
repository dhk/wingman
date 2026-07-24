# Wingman — Claude Desktop Walkthrough (server on Lobster)

The same first session as [`WALKTHROUGH-DESKTOP.md`](WALKTHROUGH-DESKTOP.md),
but the workspace and the MCP server don't live on this machine — they run
on an always-on box (call it **Lobster**, set up per [`SERVER.md`](SERVER.md))
reachable over Tailscale. This machine never runs `uv tool install`; it
only needs Claude and, for two steps below, a browser. Budget: about
thirty minutes, most of it waiting on your LinkedIn export email.

## What Wingman is, in three paragraphs

Wingman is a local-first career-intelligence system. It optimizes the
*quality* of opportunities, not the volume of applications: the north star
is to find a small number of exceptional opportunities and make you the
obvious candidate for the best of them. "Local-first" here means Lobster —
one SQLite workspace on a machine you (or your admin) own, not a hosted
service; reaching it from your laptop over Tailscale is a longer extension
cord, not a change of custody (RFC-002).

Its discipline is **evidence before assertion**. Models propose; ordinary
code disposes. A profile claim, a stance on a POV card, a company theme, an
outreach talking point — each survives only if it carries a verbatim quote
from a stored document. Zero survivors means nothing is stored. You will
never find a polished claim in a Wingman artifact that you cannot trace to
a sentence somebody actually wrote.

And it **never acts for you**. Wingman drafts raw material — talking
points, intro bullets, briefing docks — that you compose into your own
voice and send yourself. No message, application, or external write ever
leaves the machine (RFC-006). Every tool call Claude makes over the HTTPS
connector runs the identical validation the local stdio server and the CLI
run (RFC-008) — the model cannot bypass the evidence rules just because
the request arrived over a network instead of a pipe.

## Step 1 — Add the connector

Someone (you, or whoever administers Lobster) needs the connector URL
first. On Lobster:

```bash
wingman-ctl status        # prints the ready-to-paste connector URL per tunnel
# or read it directly:
cat "$(wingman status | sed -n 's/^Data dir: //p')/mcp-http-token"
```

It looks like `https://lobster.<tailnet>.ts.net/mcp/<token>` — or, if
Lobster hosts more than one person (`MULTI-INSTANCE-DESIGN.md`),
`https://lobster.<tailnet>.ts.net/<your-name>/mcp/<token>`. **Treat this
URL like a password** — the token is the entire credential.

In Claude Desktop (or claude.ai, which syncs to Desktop and mobile):
**Settings → Connectors → Add custom connector** → paste the URL.

Expected: the tools panel shows **wingman** with its full tool set (dozens
of tools — the exact count grows as wingman does) — identical to a local
install, just reached over the tailnet instead of a local pipe.
If the URL ever leaks (browser history, a pasted log), whoever runs
Lobster reruns `wingman-mcp --http --rotate-token` and you re-paste the
new URL.

## Step 2 — Say hello

Type to Claude:

> Use wingman to show me my workspace status.

Expected: a workspace path (on Lobster, not this laptop) and zeros
everywhere — honest emptiness, not an error. Use this any time something
downstream looks wrong.

## Step 3 — Add your data

**Resume.** Attach the file to your message (PDF, DOCX, Markdown, text —
or paste a link-accessible Google Doc) and say:

> Here's my resume — add it to my wingman profile.

Claude extracts the text and calls `ingest_resume_text` (or
`ingest_resume_url`) on Lobster — the file itself never needs to land on
the server's disk, so this works exactly like the local walkthrough.
Expected: an ingestion summary, items accepted with verbatim evidence,
anything unsupported rejected *and said so*.

**LinkedIn export.** This is where remote changes things: `people_import_connections`
and `wingman ingest-linkedin` both need the zip to already be sitting on
*Lobster's* disk, not this laptop's — and there's no chat tool that
uploads a whole archive. Use the web UI instead (RFC-033), the
no-terminal path built for exactly this:

> Visit `https://lobster.<tailnet>.ts.net/ui/<token>/` and use the upload
> form for the LinkedIn export zip.

That lands the file in Lobster's inbox with an archive stamp. From there:

- **Connections** (seeds your watchlist) — back to chat:

  > Import my connections from the LinkedIn export you'll find in the
  > inbox — it should be the newest `Basic_LinkedInDataExport_*.zip`.

  Claude calls `people_import_connections` with the inbox path. Privacy
  note: connection emails are never read into the database.

- **Profile items** from the same zip still need a terminal on Lobster —
  there's no MCP tool for it yet:

  ```bash
  wingman ingest-linkedin <path from the inbox>
  ```

  If Lobster is someone else's box (a second user's setup, per
  `MULTI-INSTANCE-DESIGN.md`), this one line is the admin's to run, not
  yours.

**Your own writing** (Substack export, etc.) is the same shape as
`ingest-linkedin` — no web upload, no chat tool, a terminal command on
Lobster:

```bash
wingman corpus add substack-export.zip --source-type substack_post
```

Once it's in, everything else is chat again:

> What do I write about when it comes to <a topic>?

— `evidence`, cited quotes back, instantly.

## Step 4 — Add people

Pick someone you actually read and might want to reach. Tell Claude:

> Add Jane Author to wingman — she writes at
> https://janeauthor.substack.com and works at Acme. Then fetch her
> writing and show me what you found.

Claude calls `people_add`, `people_fetch`, `people_docs` in sequence —
identical over the connector. Expected: fetch reports how many posts were
stored; docs lists them with dates and URLs. Non-Substack sources —
Medium, WordPress, Ghost, feed-less VC blogs — go through
`feed_discover`/`feed_attach` instead; describe the site and ask Claude to
find its feed (always confirm-before-attach).

Then:

> Embed her new writing, and tell me who else writes about similar things.

— `embed` (text goes from Lobster to the embeddings provider — this
laptop is never in that path), then `people_similar`.

## Step 5 — The POV doc

Ask Claude:

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
> briefing dock.

Claude calls `people_brief` then `export_pdf` — the Markdown lands in
Lobster's `reports/` folder. To render the PDF, either:

- browse and download it from `https://lobster.<tailnet>.ts.net/ui/<token>/`
  (every report is listed there, dated), then `npx md-to-pdf "<file>"`
  locally, or
- run `npx md-to-pdf` directly on Lobster if you have a terminal there.

The PDF is a one-glance dock: outreach brief | point of view | background
(warmth, links, people in common) | recent news. The intro bullets are raw
material — you write the message, in your voice; Wingman never sends
anything.

## Where to go next

Everything below is a sentence to Claude, not a command to remember:

- **Follow a company** — "Follow Acme at https://acme.com" enrolls it (and
  your people there) for deep refresh; "run my overnight refresh" catches
  everything up and writes a dated digest. On a shared Lobster, the
  *schedule* (the systemd timer in `SERVER.md` §4) is the admin's setup,
  not yours — you just watch the digest land.
- **Ask the workspace anything** — "What do we know about semantic
  layers?" sweeps every store into one ranked, cited list, including hits
  that match by meaning rather than keyword.
- **Assess a role** — paste or link a posting and ask "does this fit me?"
  (`assess_job` / `assess_job_url`) for a cited fit brief, then "build me
  an application pack" (`pack`).
- **Company intelligence** — "give me Acme's dossier / point of view /
  latest research."
- **Check in on the digest** — `https://lobster.<tailnet>.ts.net/ui/<token>/`
  is the landing page for today's digest and every past one; it's also
  the fastest way to glance at something on your phone without opening
  Claude at all.
- **Backups and upgrades** are Lobster's admin's job (`SERVER.md` §7) —
  nothing for you to run from this laptop.

The rationale ledger behind all of it is [`RFC.md`](RFC.md); the actual
server setup this walkthrough assumes is [`SERVER.md`](SERVER.md).
