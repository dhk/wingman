# Wingman capabilities

This is the detailed catalogue moved from the repository front door. Claims
here describe implemented behavior; [ROADMAP.md](../ROADMAP.md) distinguishes
delivered, partial, and planned phases.

## Profile and corpus

Resumes (Markdown, text, PDF, DOCX, LaTeX, and Google Docs URLs) become a cited
canonical profile. Every accepted claim carries a verbatim quote; unsupported
claims are rejected visibly. LinkedIn exports add positions, skills, and
recommendations, and seed the watchlist from connections (names and roles only,
never emails). Your own writing becomes a searchable evidence corpus with
`wingman evidence "kafka migration"`.

## Opportunity assessment and applications

`wingman assess job.md`, or `wingman assess --url <posting>`, extracts quoted
requirements and judges each met, partial, gap, or unknown against real profile
items. Deterministic validation downgrades unsupported verdicts. `wingman pack
"staff mle"` composes a cited fit summary, cover-letter fodder quoting your
evidence, and stored company intelligence. You write and send the final material
([RFC-024](RFC.md#rfc-024-from-hiring-signal-to-application-posting-fetch-and-the-application-pack)).

The application answer bank saves refined question, answer, and context records
for recall across applications ([RFC-030](RFC.md#rfc-030-the-application-answer-bank-refined-qacontext-reused-across-applications)).

## People intelligence

Wingman follows selected Substack, RSS/Atom, or feed-less blog sources. Fetches
happen only on an explicit command or an enrolled run. POV cards distill values,
attitude, technical, and strategy stances; a stance is kept only when its quote
appears verbatim in stored writing. Embedding similarity finds related thinkers,
a deterministic warmth vector names actual relationship signals, and news
snapshots disclose the query sent to the provider.

`wingman people brief` builds purpose-shaped talking points for an introduction,
reconnection, job, or advice request. It must cite a POV stance and quote your
corpus verbatim. It produces raw material and never sends a message.

## Company intelligence

Company dossiers combine people, blogs, similarity, and approved sources with
explicit `[fact]` and `[inference]` labels. Model-synthesized themes are validated
quote by quote. `wingman company research` fetches only sources you name, diffs
snapshots, and surfaces newly found links as hiring signals
([RFC-015](RFC.md#rfc-015-company-research-over-user-approved-sources-phase-3-slice-iii)).

## Orchestration and daily work

`wingman make-it-so "Jane"` (alias `miso`) runs the available pipeline for one
target and reports each step. Watchlists cycle groups. `wingman company follow`
creates a standing focus; `wingman overnight` deep-refreshes enrolled targets
into a dated digest with a what/why/who/evidence action list. Enrollment is the
consent record and can be revoked. `wingman digest`, its stable `latest.md`
pointer, output selection, and search make those reports reusable
([RFC-018](RFC.md#rfc-018-follow-a-company-assembled-focus-and-the-consented-overnight-deep-refresh)).

## Search and similarity

`wingman search "semantic layers"` ranks corpus documents, people's writing,
POV stances, news, research links, and briefs with attribution, dates, and
sources. Keyword search is local. Semantic search embeds the query: Voyage sends
it to that provider; the `hashed` provider computes a lower-quality vector
locally. Stored remote embeddings may also represent document text
([RFC-022](RFC.md#rfc-022-unified-search-across-every-store-keyword-plus-semantic)).

## Reports and exports

Wingman exports print-ready US-Letter career one-pagers, company dossiers, and a
landscape person briefing dock (brief, POV, background, and news) with clickable
sources. `--html` adds a tabbed screen view. Exports print the exact `npx
md-to-pdf` render command.

## Operations and interfaces

The CLI and `wingman-mcp` expose the same deterministic validation. MCP uses
local stdio or an opt-in loopback HTTP transport with a rotatable capability
path behind a user-managed tunnel. `wingman keys` stores supported keys in the
macOS Keychain. `wingman backup` and `restore` create and consume local tarballs.
`wingman sync` performs the lighter fetch-and-embed cycle.

Opt-in telemetry is a local-only usage journal of CLI invocations, MCP traffic,
and explicitly harvested Claude transcripts. It is off by default and never
transmitted ([RFC-023](RFC.md#rfc-023-opt-in-local-usage-telemetry-with-transcript-harvesting)).

For exactly what each networked feature sends, see [Trust boundaries and data
egress](TRUST.md). For setup and operational commands, see [Install and
operations](INSTALL.md).
