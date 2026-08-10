# Release Notes

Newest first. Versions are git tags; `wingman --version` reports the build
you are running (hatch-vcs). Full decision history lives in
[`RFC.md`](RFC.md).

## v0.5.0 — 2026-08-10

Wingman became something you can run *for other people*. 152 PRs, RFC-032
through RFC-055, 71 MCP tools, 1120 tests.

### One process, many people (RFC-048)

- **Shared multi-tenancy.** Wingman used to mean one install per person —
  a Unix account, a port, a systemd unit each. That shape doesn't scale
  past a couple of friends, and every instance drifted independently. One
  process now serves every tenant: share-nothing data (a SQLite workspace
  per person, never a shared table), a capability token per tenant, and a
  registry at `/etc/wingman/tenants.toml` naming who exists. Adding
  somebody is `wingman-add-tenant.sh <slug>`; they get two links and
  install nothing.
- **Tokens rotate without a restart.** `wingman tenant rotate-token`
  writes the new token and SIGHUPs the process, which re-reads the
  registry in place — a restart would drop every other tenant's in-flight
  connections, which is why the per-instance restart button was withdrawn
  (RFC-041).
- **Persona carve-off (RFC-049, RFC-054).** Coach someone inside your own
  workspace, then hand them their own: every claim captured under their
  persona is rehomed as their first-person profile, with honestly-labelled
  placeholder evidence rather than a copy of your private capture notes.
  Phase 2 merges into a workspace that already has data, surfacing genuine
  contradictions as conflicts instead of overwriting them.
- **Operator surfaces.** `wingman tenant urls` prints the roster,
  `wingman-host-status` shows every instance on the box and what's stale,
  and the shared process declares itself to the host service registry
  (#288) so two services cannot silently claim the same port or path.

### The read surface (RFC-033)

A small web UI for the things a chat window is bad at: glancing, and
files. Uploads at the top, everything Wingman has written underneath.
The **profile page** (#284) shows each claim with its evidence and its
lineage, and names what is still missing *and what the gap costs* —
"no roles, so nothing here shows tenure or seniority", not "Roles: 0".
`profile_html` returns it as a standalone document you can keep.

### Knowing what you want, and what you're worth

- **Job criteria and scoring (RFC-035).** A short interview across five
  areas becomes a document every new posting is scored against —
  embedding recall, then a judged brief that cites the criteria it used.
- **Requirement resolution (RFC-036).** Answers you give while working
  through an application are captured as evidence and recalled before you
  are asked the same thing again.
- **Value dimensions (RFC-050/051/052).** Nominations now carry sentiment
  intensity and a company-reason taxonomy; `wingman values` infers a small
  set of named value axes from them — the model proposes axes and cites
  which captures support each, ordinary code does the scoring — and
  `values-chart` renders them as a self-contained SVG radar chart.
- **Relationship objectives (RFC-037).** A thesis per person, a tickler
  that fires on the intersection of fresh material and a stated objective,
  and a log of what actually happened — all citable evidence for the next
  conversation.

### Evidence that survives real documents

Three separate ways the evidence gate was rejecting true claims: PDFs that
extract without spaces (#278), typographic punctuation that differs from
what the model quoted (#280), and résumés whose roles were never extracted
with structure at all (#269). Profile items can now be re-kinded (#273)
and renamed (#282) without losing their lineage, for the other half of a
mis-capture.

### Keys, config, and whose key wins

The key ladder got a canonical home — `~/.config/wingman/secrets.env` and
`wingman.env`, split by whether a value is a secret (RFC-046), with a
global tier for shared credentials (RFC-047). And a reversal worth calling
out: **a key you provide now outranks the operator's environment**
(RFC-019a/RFC-034a). The original precedence made a hosted tenant's own
key silently unused, which is an attribution problem as much as a
correctness one — your spend should be yours.

### Operability, and checks that can fail honestly

- **`wingman-ctl`** (`wg`): status, start/stop, upgrade, `upgrade-all`
  across accounts (RFC-042), and `redeploy-shared` for the shared process.
- **`wingman doctor --deep`** (RFC-039) walks a guided diagnostic ladder
  instead of printing a wall of state.
- **Installs are pinned to `uv.lock` and verified** (#302, #319) — passing
  `--constraints` is not the same as having been pinned, so the install
  now checks, and refuses to report success when the deployed versions
  are not the tested ones.
- **The changelog knows when it is stale** (RFC-038, #202). It once went
  61 PRs behind and reported a confident "0 new", which a downstream agent
  relayed as fact. Freshness is now measured in commit *distance* — a
  stamped commit sha can never equal the sha of a build containing it, so
  that check warned after every correct regeneration — and the note says
  how many merges are missing.
- **Google Drive push (RFC-053).** Finished backups and digests can go to
  your own Drive, per-account device-code OAuth, closed files only. The
  live workspace stays local and single-writer, exactly where RFC-002 and
  RFC-006 put it.

### Isolation defects, found and fixed

Retiring the per-account instances removed the Unix account as an
isolation backstop, which raised the stakes on three defects a review had
found and nobody had filed. A duplicate capability token silently swapped
tenant identity — one person reading another's workspace, no error
anywhere — and now fails closed. A malformed registry crashed the whole
process through the SIGHUP handler on the next ordinary token rotation.
And `os.kill` could surface a traceback from a command that had already
written its token. (#327, #328, #329)

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
