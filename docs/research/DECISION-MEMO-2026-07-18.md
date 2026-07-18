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

**1. Warmth score → decomposed signal vector. IMPLEMENTED.**
`_warmth()` in `src/wingman/reporting/export.py` collapsed to a single `(score, label, signals)` tuple computed at render time. Replaced with `WarmthSignal(name, points, description)` — a named vector (`direct_connection`, `email_on_file`, `shared_connections`) — with a separate `_warmth_label()` doing the sum-to-display-score arithmetic. Documented as **RFC-020** in `docs/RFC.md`. Rendered output is unchanged (`tests/unit/test_export.py` passes as-is); this is a data-shape decision, not a UX change. RFC-020 explicitly scopes out interaction-history signals (recency, frequency) — see item 2 below, now tracked as [issue #53](https://github.com/dhk/wingman/issues/53).

**2. MCP surface: no reduction now.**
The tool-count-degrades-accuracy concern from "vs. the Field" §1.9 is real (cited research, not just opinion) but deferred until there's runtime signal on actual agent usage. RFC-008's parity principle stands unchanged — every CLI capability ships an MCP counterpart in the same change. The point of building on MCP first is to observe the user journey; refactoring the verbs before that data exists would be optimizing blind. Revisit the 33→8–12 composite-tool question only once usage data exists to justify which primitives collapse into which composites.

**3. Page-monitor vendor: changedetection.io in principle, but no integration work now.**
changedetection.io is the chosen vendor if/when this integration happens (free/self-hosted, per Reality Check §5), but RFC-015's existing bespoke one-GET/hash/diff engine already works and is in production use. **Decision: leave it alone as long as it stays good enough.** No monitor-provider abstraction, no RFC-015 amendment, no changedetection.io wiring right now — this was speculative "someday" scope, not a near-term need. Revisit only if RFC-015's engine actually starts failing users (client-rendered pages, auth walls, anti-bot systems — the failure modes "vs. the Field" §1.6 predicted), not on a schedule.

**4. Adopt the evidence-object schema — investigated, partially adopted.**
Investigation (2026-07-18) compared the proposed schema against the four existing provenance-bearing models: `Stance` (POV cards, `domain/pov.py`), `ProfileItem`/`EvidenceSpan` (career evidence, `domain/profile.py`), `RequirementAssessment` (job verdicts, `domain/opportunity.py`), and `ResearchSnapshot` (company research, `domain/research.py`), plus the base `Provenance` model from RFC-005 (`domain/provenance.py`).
**Finding: mostly document, not unify.** Most of the proposed fields already exist under different names, scattered by design across per-type models each shaped for its own validation gate (verbatim-quote fabrication guard for POV, dedup/conflict for profile items, requirement-cited verdicts for assessments, hash-diff for research) — `quote`, `source_record_id`, `source_date`/`published_at`, and `confidence` all already exist in at least two of the four models. Three fields are genuinely new to the codebase: a staleness-aware `status` (today `ItemStatus` only has ACTIVE/CONFLICT; staleness is computed ad hoc at display time, never persisted), `supersedes` (RFC-016's POV cards are explicitly replace-on-save with no versioning), and `derivation_rule` (no equivalent anywhere).
**Decision: do not migrate the four record types into one table.** Each stays shaped for its own gate. The three genuinely-new fields (status/supersedes/derivation_rule) are a real, separable follow-up worth their own RFC if/when contradiction-tracking or claim-supersession becomes a live need — not before.

**5. Read-only web UI: left unresolved, tracked as [issue #52](https://github.com/dhk/wingman/issues/52).**
The two passes disagree on whether this is a real gap. Per the working principle — discover the user journey through CLI/MCP before presuming a UI is needed — this stays unscheduled until there's evidence that CLI/MCP friction, not a missing feature, is the actual blocker.

**6. Net v0.3 scope: stays as originally committed.**
The freed capacity from not doing the MCP-surface reduction (decision 2) does not get spent pulling changedetection.io or Crunchbase integration forward into v0.3 — both are deferred per decisions 3–4 above anyway. v0.3 remains scoped to what was already queued (search improvement) plus whatever the evidence-graph hardening work turns out to need; no new integration bets are being added to it as a result of this research pass.

## Not adopted

- "vs. the Field"'s specific v0.3 sequencing (search + evidence-graph hardening + MCP-surface reduction, with all integrations deferred to v0.4). The MCP-surface piece is explicitly rejected per decision 2. The rest is moot per decision 6 — v0.3 stays at its originally committed scope regardless of which pass's sequencing it happens to match.
- A monitor-provider abstraction / changedetection.io wiring as near-term work (decision 3) — the existing RFC-015 engine is good enough for now.
- Migrating POV cards, career evidence, job verdicts, and research snapshots into one unified evidence table (decision 4) — the four record types stay separately shaped.

## Resolved 2026-07-18 (follow-up)

All open items from the initial review now have a disposition:

| Item | Disposition |
|---|---|
| Warmth vector | Implemented — RFC-020, code shipped, tests green |
| MCP surface reduction | Deferred, no change |
| RFC-015 vs. changedetection.io | Left as-is; no integration work scheduled |
| Evidence-object schema | Investigated; not unifying, three new fields identified as a future, separable RFC candidate |
| `relationship_signal` import scope | Documented above; tracked in [issue #53](https://github.com/dhk/wingman/issues/53) |
| Read-only web UI | Tracked in [issue #52](https://github.com/dhk/wingman/issues/52) |
| Net v0.3 scope | Stays as originally committed — no expansion |
