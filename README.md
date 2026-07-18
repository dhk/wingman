# Wingman

Wingman is a local-first, AI-assisted career intelligence system.

It helps a human:

- build a canonical, evidence-backed professional profile;
- assess opportunities against real experience;
- research companies and hiring signals;
- identify credible warm introduction paths;
- prepare differentiated outreach and interviews;
- decide which actions deserve attention next.

Wingman is **not** an autonomous job application bot. It is a human-in-the-loop decision-support system. No message, application, calendar invitation, or external write occurs without explicit approval.

## Status

This repository is at **Phase 2: Opportunity Inbox** (first slice).

Assess a job description against your profile and get a cited fit brief:

```bash
wingman assess path/to/job.md
```

Requirements are extracted with verbatim quotes from the posting; each is
assessed against your profile as met / partial / gap / unknown, citing only
real profile items — an unsupported "met" is downgraded to "unknown" by
deterministic validation. The brief lands in the workspace `reports/`.

Import your LinkedIn data export — positions, skills, and recommendations
become cited profile items, deterministically (no model involved):

```bash
wingman ingest-linkedin linkedin-export.zip
```

Build a searchable corpus from your own writing (Substack export zips,
READMEs, LinkedIn exports, any Markdown/text/HTML) and pull cited evidence
from it:

```bash
wingman corpus add substack-export.zip --source-type substack_post
wingman evidence "kafka migration"
```

Build a watchlist of interesting people and follow their public writing —
seeded from your own LinkedIn connections export (names and roles only, never
emails), fed by their public Substack RSS feeds (fetched only when you ask,
RFC-009):

```bash
wingman people import-connections linkedin-export.zip
wingman people add "Jane Author" --substack https://example.substack.com
wingman people add-feed "Jane Author" https://medium.com/@jane   # any RSS/Atom, autodiscovered
wingman people add-feed "Scott Brady" https://firm.example.com/insights --org "Firm"  # feed-less blogs too
wingman people fetch --all
wingman people evidence "developer tools"
wingman sync             # fetch everything + embed what's new, in one command
wingman people discover  # who your watched Substacks recommend (suggestions only)
wingman people pov "Jane Author"  # what she thinks — stances with verbatim quotes
```

POV cards summarize what a person believes, from their own writing: the
model proposes stances, and deterministic validation keeps only those whose
quote appears verbatim in the stored post — the same fabrication guard as
resume ingestion, applied to other people's words.

Beyond Substack: `add-feed` takes any public feed or blog homepage — it
autodiscovers RSS/Atom (Medium, WordPress, Ghost) and, for feed-less sites
(most VC and startup blogs), watches the blog index page instead, honestly
attributing those posts to the organization (RFC-011). Attachment always
requires your confirmation.

Find people who think about what you think about — embeddings-backed
similarity over their writing and yours (RFC-010). `wingman embed` is the one
explicit step that sends document text to the configured embeddings provider
(default Voyage AI, `VOYAGE_API_KEY`; a local `hashed` provider needs no key):

```bash
wingman embed
wingman people similar                       # closest to your own corpus
wingman people similar "Jane Author"         # closest to Jane
wingman people like "Mario Rossi" "Brian Chen"   # near the centroid of both
wingman company similar                      # companies closest to your own corpus
wingman company similar "DataCo"             # companies whose people write similarly
wingman company like "DataCo" "StreamCorp"   # near the centroid of both companies
```

Company signals come from the people you watch: each embedded post counts
toward the author's `--company` and, for organization-attributed feeds (a
firm blog), toward that organization.

The Phase 1 product proof works end to end:

```text
resume.md
   ↓
validated ingestion (SourceRecord, content-hashed, deduplicated)
   ↓
model extraction (extract_fast capability class) + deterministic evidence validation
   ↓
career.json
   ↓
career.md with evidence references
```

```bash
wingman ingest path/to/resume.md
```

Every accepted claim carries verbatim quotes from the source; claims whose
quotes do not appear in the source are rejected and reported, never stored.
Model routing is configured in the workspace `models.toml` (written by
`wingman init`; set `ANTHROPIC_API_KEY` to use the default mapping).

## Repository Guide

- [`docs/SETUP.md`](docs/SETUP.md): standing up your own instance, with graceful degradation
- [`VISION.md`](VISION.md): why Wingman exists and what good looks like
- [`ROADMAP.md`](ROADMAP.md): phased delivery plan
- [`AGENTS.md`](AGENTS.md): engineering and agent instructions
- [`docs/DESIGN.md`](docs/DESIGN.md): overall architecture — what the system is
- [`docs/RFC.md`](docs/RFC.md): engineering decisions and rationale
- [`docs/EVALUATION.md`](docs/EVALUATION.md): how we measure that Wingman is getting better
- [`docs/`](docs/): product notes

## Install

```bash
uv tool install git+https://github.com/dhk/wingman
wingman init
wingman doctor
```

New here? `wingman demo` runs a guided tour on real public data — it seeds a
watchlist of real Substack publications, fetches their feeds, and shows
evidence search and similarity, with no API keys required (similarity falls
back to the local `hashed` provider). It runs in its own isolated workspace,
so your data is never touched; delete the demo folder to remove every trace.
Then make it yours: see [`docs/SETUP.md`](docs/SETUP.md).

The workspace lives in `$WINGMAN_DATA_DIR` if set, otherwise the platform user
data directory (e.g. `~/.local/share/wingman` on Linux).

## Use from Claude (MCP)

`wingman-mcp` exposes the workspace as MCP tools running the same
deterministic validation as the CLI (RFC-008), and tracks the CLI's full
surface: status, evidence search, the cited career profile, job assessment,
resume ingestion, and the entire people surface — add, list, fetch, sync,
embed, similar, like, discover, connections import, and feed attachment
(feed_attach runs only after you confirm feed_discover's finding in
conversation). Stdio only: nothing listens on the network, and network reads
happen exactly as in the CLI — explicit public-feed fetches (RFC-009/011)
and embedding egress (RFC-010).

Claude Code:

```bash
claude mcp add wingman -- wingman-mcp
```

Claude Desktop — add to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`), using the
absolute path from `which wingman-mcp` because the app does not inherit your
shell PATH:

```json
{
  "mcpServers": {
    "wingman": { "command": "/Users/you/.local/bin/wingman-mcp" }
  }
}
```

Model-backed tools (assess, ingest) need `ANTHROPIC_API_KEY` in the server's
environment; add `"env": {"ANTHROPIC_API_KEY": "..."}` to the entry if your
key is not set system-wide.

## Quick Start (development checkout)

```bash
uv sync
export WINGMAN_DATA_DIR=./data   # keep workspace data inside the repo checkout
uv run wingman --help
uv run wingman init
uv run wingman status
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## Design Principles

The binding list is the Product Invariants in [`VISION.md`](VISION.md#product-invariants).

## License

MIT — see [`LICENSE`](LICENSE).
