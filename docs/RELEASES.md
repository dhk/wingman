# Release Notes

Newest first. Versions are git tags; `wingman --version` reports the build
you are running (hatch-vcs). Full decision history lives in
[`RFC.md`](RFC.md).

## v0.4.0 — 2026-07-20

One day, one theme: wingman got honest about documents that change and
lists that repeat. Eight PRs (#73–#79, #82–#83), RFC-026 through RFC-031,
48 MCP tools, 370 tests.

### The source-of-truth workflow actually works now

- **Markdown-normalized ingestion (RFC-026).** Hard-wrapped Markdown broke
  imports: the model quotes the sentence it reads, and the byte-for-byte
  evidence check couldn't find it across a line wrap — rejecting exactly
  the strongest, longest achievements. `.md` files are now normalized at
  extraction (wraps rejoined, blockquote/bold/backtick markers dropped,
  structure preserved), and every verbatim evidence gate — resume ingest,
  job requirements, POV stances, outreach quotes — matches whitespace-
  insensitively. Content still must match character for character; only
  wrapping is forgiven. This also fixes the same latent failure for PDFs.
- **Document lineage: re-ingest updates instead of piling up (RFC-028).**
  Re-ingesting an edited resume used to store every rewording as a
  side-by-side conflict ("BigQuery ×3"). Source records now carry the
  document's filename identity: a newer version's changed claims replace
  that document's own earlier ones (`Updated`), dropped claims retire
  (`Retired`), and stale conflict piles drain automatically on the next
  re-ingest. Claims vouched for by a *different* source still conflict —
  a document only speaks for itself. The database migrates itself on
  first open, retroactively, so existing workspaces heal too.
- **Profile management (RFC-027).** The Phase 1 "manual correction
  workflow" that never shipped: `wingman profile list / rm / resolve /
  clear` (MCP `profile_manage`), with git-style id prefixes. `resolve`
  converges every duplicate to the one row you pick; every mutation
  re-renders `career.md`/`career.json`.

### Companies without fictional bylines

- **Company-attached feeds (RFC-029).** `wingman company add-feed
  "Cursor" <url>` follows a company blog with zero people involved — no
  more placeholder humans. Posts are organization-attributed, flow into
  dossiers, themes, news, and search, and overnight runs fetch them for
  followed companies. The system-owned anchors underneath are invisible
  in people listings and travel correctly through company rename/delete.

### The application loop learns

- **The answer bank (RFC-030).** Answers refined during applications used
  to evaporate with the conversation. `wingman answers` (MCP
  `answer_bank`) persists the settled result — question, your confirmed
  wording, and the company/role/date it was refined for — and recalls it
  when a similar question appears for a different role. The connected
  client's protocol: surface similar banked answers first, iterate with
  you (AskUserQuestion) until *you* confirm, never bank a one-shot draft.
- **Digest triage (RFC-031).** Uninteresting actions no longer roll over
  forever. Every digest action carries a stable key; `wingman actions
  mute / snooze / unmute / list` (MCP `action_triage`) records your
  reversible verdicts, overnight applies them before the cap, and the
  digest says how many it suppressed — the filter is never silent. The
  agent walks the list with you (Keep / Mute / Snooze per item) and is
  barred from muting on its own judgment: your verdicts are the
  filtering logic.

### Docs

- `docs/SERVER.md`: run wingman on an always-on Ubuntu server — systemd
  timer for overnight, the MCP server as a service, reachable from
  claude.ai via Tailscale.
- `docs/RESEARCH-COUNCIL-DESIGN.md`: the multi-engine research council
  design, recorded (implementation destined for alexandria, issue #75).
- `ROADMAP.md` refreshed to reality; the competitive research decision
  memo is linked from it.

### Upgrading

`git pull && uv tool install --reinstall .`, restart any running MCP
server (Claude Desktop: full Cmd-Q). First workspace open migrates the
lineage column automatically. Then re-ingest your source-of-truth resume
once: expect `Updated`/`Retired` counts, zero new conflicts, and the old
conflict pile gone.

## v0.3.0 — 2026-07-19

The release where wingman became a daily operator: from "analyze what I
give you" to "watch what I care about and have answers ready." PRs
#29–#72, RFC-014 through RFC-025, 44 MCP tools, 326 tests.

### Watch and wake up to a briefing

- **Company follow + overnight (RFC-018).** `wingman company follow`
  enrolls a company (approved research sources, themes, dossier);
  `wingman overnight` deep-refreshes everything enrolled and writes the
  morning digest — ending in an **action list**: what to do, why now,
  who it's about, on what evidence, with runnable commands attached.
  `wingman digest` is the one-word morning read (`latest.md` is always
  the newest).
- **Approved-source research (RFC-015).** Adding a source IS the
  approval: research fetches exactly the pages you named, snapshots
  them, and reports deterministic diffs — new careers links surface as
  signals, not surprises.
- **Company themes (RFC-016).** Model-synthesized company POV, validated
  quote by quote against stored documents, riding the same `__company__`
  card machinery as people.
- **People news + make-it-so.** Relevance-filtered headlines (RFC-014),
  and `wingman miso` — one command that runs a person or company's whole
  pipeline; watchlists cycle it.

### From signal to application

- **`wingman assess --url` + application packs (RFC-024).** Fetch a
  posting straight from its URL, get the cited fit brief, then `wingman
  pack` composes the application pack: fit summary with evidence,
  cover-letter fodder quoting your own profile verbatim, and the
  company intelligence already in the workspace. Signal → assessment →
  pack in three commands.

### Find anything, trust everything

- **Unified search (RFC-022).** `wingman search` sweeps every store —
  your corpus, people's writing, stances, news, research, digests,
  briefs — keyword plus a semantic pass, interleaved by rank.
- **Warmth signal vector (RFC-020) and people/company manage
  (RFC-021).** The relationship score decomposed into named signals;
  rename/delete/fix for people and companies.
- **Resume intake for real files.** PDF, DOCX, LaTeX, and link-accessible
  Google Docs/Drive URLs (#29).
- **Print-ready exports.** Letter-format artifacts styled by the site
  design system; the person page as a 2×2 briefing dock; outreach briefs
  with purposes, stance dimensions, and intro bullets — never sent by
  wingman (RFC-006 stands).

### Operate it like software

- **Remote MCP (RFC-017).** `wingman-mcp --http`: loopback streamable
  HTTP behind a capability-path token — the transport claude.ai
  connectors speak.
- **Keychain-backed keys (RFC-019).** `wingman keys` stores API keys in
  the macOS Keychain; environment variables always win; secrets never
  land in files, logs, or telemetry.
- **Backup/restore.** Dated workspace tarballs with retention (#43).
- **Opt-in local telemetry (RFC-023).** A usage journal in your own
  database — CLI invocations, MCP calls, harvested transcripts — with
  no transmitter, plus always-on secret scrubbing.
- **Version stamping (#59)** via hatch-vcs, surfaced in `--version`,
  status, doctor, and MCP startup.
- **Feature-request capture (RFC-025).** "feature request: …" becomes a
  previewed, confirmed GitHub issue via your own `gh` auth — the first
  gated external write; outreach never-sends remains absolute.

### Hardening

An external code review (issues #62–#71) was remediated in full (#72):
SSRF guard on every fetch (private/loopback/link-local addresses
refused, redirects included), credential scrubbing at telemetry's single
write choke point, the HTTP token kept out of access logs, FTS
crash-proofing for `C++`/`AND`-shaped queries, and integrity fixes
across person merge and company rename/delete. Each finding carries a
regression test.

### Docs

`docs/INSTALL.md` (install → keys → MCP → operate), `docs/WALKTHROUGH.md`
(download to POV doc, guided), rewritten README, and the competitive
research pass: two independent ~30-comparator landscape studies plus the
decision memo in `docs/research/`.
