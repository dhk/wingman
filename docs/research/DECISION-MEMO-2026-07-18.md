# Competitive Research — Decision Memo (2026-07-18)

**Context.** Three artifacts from the `docs/RESEARCH-BRIEFING.md` competitive research pass, reviewed together on 2026-07-18:

- `competitive-landscape-2026-07-18-reality-check.md` — "Wingman Competitive Landscape — v0.2.x Reality Check"
- `competitive-landscape-2026-07-18-vs-the-field.md` — "Competitive Landscape: Wingman vs. the Field"
- `comparator-rubric-2026-07-18.md` — per-tool backing data for the Reality Check matrix

These are two independent research passes over the same ~30 comparators, not one document and its draft. This memo records what they agreed on, where they diverged, and the decisions made from that review.

## What both passes confirmed (no decision needed)

- **B (opportunity assessment — quote-cited requirement verdicts) is the moat.** Neither pass found a comparator that does more than a percentage/keyword match (Jobscan, Huntr) or a deterministic score (Four-Leaf's `match_score`). No incumbent can adopt Wingman's version without abandoning the score-based UX their product is built around.
- **Evidence discipline is a real, undefended market gap.** Both cite the same two data points: Notion AI's documented link-fabrication reports and Humantic AI's ToS explicitly disclaiming accuracy ("only suggestive in nature").
- **RFC-006's never-sends invariant is validated by the market split**, not just defensible in theory. Teal, Simplify, Rezi (including its MCP server), and Four-Leaf's deep-link pattern independently converged on assist-not-auto-execute. Waalaxy, Dripify, and JobGPT's MCP tools sit on the opposite pole and carry real LinkedIn ToS exposure (User Agreement §8.2, Prohibited Software policy) — Dripify's own marketing describes "human behavior simulation" built to evade detection.
- **Confirmed drops:** application tracking/kanban, resume/ATS keyword scoring as a standalone product, a general personal CRM, a feed-reader UI, outbound send automation.
- **Confirmed integrate-not-build:** page-diff monitoring mechanics, structured company facts (Crunchbase).
- **Interview prep confirmed as the strongest near-term build** — no comparator does it with evidence discipline; both passes place it early (v0.4).

## Where the two passes disagreed

| Question | Reality Check | vs. the Field |
|---|---|---|
| Warmth score | Treated as a working deterministic feature, BUILD as-is | Calls the single score a design flaw that "hides distinct facts"; prescribes a signal vector |
| 33-tool MCP surface | Framed as a competitive asset — "no comparator combines Wingman's specific F-area architecture" | Framed as a usability risk, citing research on tool-selection accuracy degrading with tool count; recommends cutting the default surface to 8–12 composite tools |
| v0.3 sequencing | Three concrete integration bets: changedetection.io, Crunchbase, opportunity export | No integrations in v0.3; bets are unified search, evidence-graph hardening, MCP reduction. Integrations pushed to v0.4 |
| First page-monitor adapter | changedetection.io first (self-hosted, free) | Visualping first (hosted, API+webhook+MCP on free tier), changedetection.io as fallback |
| Data model | Stays at BUILD/INTEGRATE/DROP strategy level, no schema | Proposes a concrete evidence-object JSON schema as the central primitive |
| Read-only web UI | Explicit v0.5 bet to test one | Not mentioned anywhere in v0.3–v0.5 |
| C-area framing | One verdict: "BUILD (versus), with a KEEP-THIN carve-out for feed mechanics" | Two verdicts: "BUILD the evidence layer; integrate the CRM layer," plus a specified `relationship_signal` import interface |

## Decisions made 2026-07-18

**1. Warmth score → decomposed signal vector.**
`_warmth()` in `src/wingman/reporting/export.py` currently collapses to a single `(score, label, signals)` tuple computed at render time; it has no RFC. Replace the single score with an explicit vector — direct connection, recency, frequency, shared context, evidence relevance, confidence — per "vs. the Field" §1.3. Consistent with the evidence-before-assertion invariant: an uncited single number is itself an unsupported claim.
*Follow-up: RFC-019, scoping whether this is a render-layer change or also a storage change.*

**2. MCP surface: no reduction now.**
The tool-count-degrades-accuracy concern from "vs. the Field" §1.9 is real (cited research, not just opinion) but deferred until there's runtime signal on actual agent usage. RFC-008's parity principle stands unchanged — every CLI capability ships an MCP counterpart in the same change. The point of building on MCP first is to observe the user journey; refactoring the verbs before that data exists would be optimizing blind. Revisit the 33→8–12 composite-tool question only once usage data exists to justify which primitives collapse into which composites.

**3. Page-monitor vendor: changedetection.io (free/self-hosted).**
Chosen over Visualping on cost, per Reality Check §5.
*Open question, not yet decided:* RFC-015 already ships a working bespoke one-GET/hash/diff engine. Integrating changedetection.io means either replacing that engine outright or adding it as a pluggable adapter behind a new monitor-provider abstraction (the shape "vs. the Field" §1.6 proposes). Needs its own decision before an RFC amendment is drafted.

**4. Adopt the evidence-object schema.**
The schema proposed in "vs. the Field" §4.2 (`claim_id`, `claim_text`, `claim_type`, `quote`, `source_span`, `source_date`, `confidence`, `status`, `supersedes`, `derivation_rule`) is the target shape for a unified evidence layer.
*Open question, not yet decided:* RFC-005 already established provenance metadata on every influential record, and POV cards, career evidence, and `assess_job` verdicts each carry their own provenance fields today. Needs an investigation pass to determine whether this schema unifies those existing fields into one table or is additive to them.

**5. Read-only web UI: left unresolved, on purpose.**
The two passes disagree on whether this is a real gap. The working principle for this whole build is to discover the user journey through the CLI/MCP surface before presuming a UI is needed — deciding this now would presume an answer the research doesn't actually settle. Track as a future issue, not a scheduled bet.
*Open: where this issue lives (GitHub issue vs. a "Deferred" section in ROADMAP.md) — not yet decided.*

## Not adopted

- "vs. the Field"'s specific v0.3 sequencing (search + evidence-graph hardening + MCP-surface reduction, with all integrations deferred to v0.4). The MCP-surface piece is explicitly rejected per decision 2 above. The rest — whether changedetection.io/Crunchbase land in v0.3 alongside the search/evidence work or wait for v0.4 — is not rejected, just still open (see below).

## Open items carried forward (not decided, need a follow-up pass)

- **Net v0.3 scope.** With the MCP-reduction bet pulled, does the freed capacity go to changedetection.io/Crunchbase integration in v0.3 (Reality Check's sequencing), or stay pushed to v0.4 ("vs. the Field"'s sequencing)?
- **C-area relationship-signal import.** `people_import_connections` already exists (`mcp_server.py`, `cli/main.py` — LinkedIn connections import) but has no RFC entry recording the decision. The fuller `relationship_signal` interface proposed in "vs. the Field" (recency, per-channel frequency, shared orgs, notes) goes further than what's built. Decide whether to extend this existing tool or scope it as a separate build.
- **RFC-015 vs. changedetection.io architecture** (see decision 3).
- **Evidence-schema reconciliation with RFC-005** (see decision 4).
- **Deferred-UI issue tracking location** (see decision 5).
