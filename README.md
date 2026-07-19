# Wingman

Wingman is a local-first, AI-assisted career intelligence system.

It helps a human:

- build a canonical, evidence-backed professional profile;
- assess opportunities against real experience;
- research companies and hiring signals;
- identify credible warm introduction paths;
- prepare differentiated outreach and interviews;
- decide which actions deserve attention next.

Wingman is **not** an autonomous job application bot. It is a
human-in-the-loop decision-support system: models propose, deterministic
code validates every claim against verbatim evidence, and nothing —
no message, application, or external write — ever leaves the machine
without you doing the sending (RFC-006). The whole workspace is one SQLite
file plus an inbox and reports folder on your disk.

## Start here

- **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)** — introduction and a
  guided first session: download → install → keys → MCP server → CLI →
  Claude Code → Claude Desktop → your data and people → an evidence-backed
  POV document.
- **[docs/INSTALL.md](docs/INSTALL.md)** — install & operations:
  requirements, Keychain-backed keys, MCP from Claude Code / Desktop /
  claude.ai, scheduling overnight runs, backups, troubleshooting.

Impatient version:

```bash
uv tool install git+https://github.com/dhk/wingman
wingman init && wingman doctor
wingman demo        # guided tour on real public data, isolated workspace, no keys
```

## What it does

**Profile & corpus.** Resumes (Markdown, text, PDF, DOCX, LaTeX, Google
Docs URLs) become a cited canonical profile; every accepted claim carries a
verbatim quote, every unsupported one is rejected visibly. LinkedIn exports
add positions, skills, recommendations — and seed the watchlist from your
connections (names and roles only, never emails). Your own writing becomes
a searchable evidence corpus (`wingman evidence "kafka migration"`).

**Opportunity assessment.** `wingman assess job.md` extracts requirements
with verbatim quotes and judges each met / partial / gap / unknown, citing
only real profile items — unsupported verdicts are downgraded by
deterministic validation.

**People intelligence.** Watch the people you read: Substack, any RSS/Atom
feed, or feed-less blogs via their index page with honest org attribution
(RFC-011); fetches happen only when you ask (RFC-009). POV cards distill
what a person believes into stances — values / attitude / technical /
strategy — each kept only if its quote appears verbatim in their stored
writing. Embedding similarity finds who thinks like you (`people similar`,
`people like`); a deterministic warmth score names your actual signals;
news snapshots pull recent mentions (query disclosed: name + company go to
the provider, RFC-014).

**Company intelligence.** Companies are seen through the writing of their
people and blogs: similarity, deterministic dossiers with
`[fact]`/`[inference]` labels, model-synthesized themes validated
quote-by-quote (RFC-016), and approved-source research — you name the
careers page or newsroom, `wingman company research` diffs it, and new
links are the hiring signal (RFC-015).

**Outreach support.** `wingman people brief` builds purpose-shaped talking
points (introduction / reconnection / job / advice) that must cite a POV
stance exactly *and* quote your corpus verbatim, plus intro bullets you
compose into your own voice. Wingman drafts raw material; it never writes
your message and never sends anything.

**Orchestration.** `wingman make-it-so "Jane"` (alias `miso`) runs
everything for one target, honestly reporting each step. Watchlists cycle
groups. `wingman company follow "Acme" --url https://acme.com` assembles a
standing focus in one act, and `wingman overnight` deep-refreshes
everything followed into a dated digest — schedule it and wake up to
answers (RFC-018).

**Delivery.** Print-ready US-Letter exports styled by the site design
system: career one-pager, company dossier, and a landscape 2×2 person
briefing dock (brief | POV | background | news) with clickable sources;
`--html` adds a tabbed on-screen page that still prints as the 2×2. Render
with `npx md-to-pdf` — every export prints its exact render command.

**Plumbing.** `wingman keys` stores API keys in the macOS Keychain, and
every entrypoint hydrates them at startup — no secrets in config files
(RFC-019). `wingman backup` / `restore` move the workspace as dated,
retention-pruned tarballs safe to put in a synced folder. `wingman sync`
is the light daily fetch-and-embed.

## Use from Claude (MCP)

`wingman-mcp` exposes the workspace as 35 MCP tools running the same
deterministic validation as the CLI (RFC-008) — stdio for Claude Code and
Claude Desktop on your machine, and an opt-in loopback HTTP transport with
a rotatable capability path for claude.ai web/mobile through a tunnel you
run yourself (RFC-017). Setup for all three:
[docs/INSTALL.md](docs/INSTALL.md) §4.

```bash
claude mcp add wingman -- wingman-mcp    # Claude Code, one line
```

## Repository Guide

- [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md): introduction and guided first session
- [`docs/INSTALL.md`](docs/INSTALL.md): install & operations
- [`docs/SETUP.md`](docs/SETUP.md): graceful-degradation ladder — what works with what you have
- [`VISION.md`](VISION.md): why Wingman exists and the Product Invariants
- [`ROADMAP.md`](ROADMAP.md): phased delivery plan and current status
- [`AGENTS.md`](AGENTS.md): engineering and agent instructions
- [`docs/DESIGN.md`](docs/DESIGN.md): architecture — what the system is
- [`docs/RFC.md`](docs/RFC.md): the decision ledger (RFC-001…019) — why it is this way
- [`docs/EVALUATION.md`](docs/EVALUATION.md): how we measure that it's getting better
- [`docs/RESEARCH-BRIEFING.md`](docs/RESEARCH-BRIEFING.md): competitive-landscape research brief

## Quick Start (development checkout)

```bash
uv sync
export WINGMAN_DATA_DIR=./data   # keep workspace data inside the repo checkout
uv run wingman --help
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

## Design Principles

The binding list is the Product Invariants in
[`VISION.md`](VISION.md#product-invariants).

## License

MIT — see [`LICENSE`](LICENSE).
