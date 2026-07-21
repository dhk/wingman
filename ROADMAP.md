# Wingman Roadmap

Each phase must leave Wingman in a usable state.

## Where we are (2026-07-19, v0.3.0)

| Phase | Status |
|---|---|
| 0 — Foundation | **Delivered** (init/doctor/status, storage, logging, provenance, CI, version stamping) |
| 1 — Career Brain | **Delivered** (resume formats incl. PDF/DOCX/LaTeX/Google URLs with Markdown normalization RFC-026, LinkedIn import, career.md/json, profile manage RFC-027, document lineage & supersede-on-reingest RFC-028) |
| 2 — Opportunity Inbox | **Delivered** (cited fit briefs via `wingman assess`, posting fetch via `assess --url`, application packs RFC-024) |
| 3 — Company Research | **Delivered** (dossiers RFC-012, approved-source research RFC-015, synthesized themes RFC-016, company feeds RFC-029) |
| 4 — Relationship Intelligence | **Partial** — connections import, warmth signal vector (RFC-020), POV cards, similarity; no interaction timeline / follow-up queue yet |
| 5 — Daily Operator | **Partial** — `make-it-so`, watchlists, follow/overnight (RFC-018), and the morning digest with its what/why/who/evidence action list cover the daily loop, with persistent mute/snooze triage of digest actions (RFC-031); no scored three-action brief yet |
| 6 — Drafting and Voice | **Partial** — purpose-shaped outreach briefs with evidence-validated talking points and intro bullets; the approval boundary (never sends, RFC-006) is structural; no per-message-type drafts or critic pass |
| 7 — Interview Preparation | **Partial** — the application answer bank (RFC-030): refined Q+A+context accumulates and is recalled across applications; prep packets and mock interviews not started |
| 8 — Controlled Connectors | Not started (explicit imports only, by design so far) |
| 9 — Outcome Post-Mortems | Not started |

Shipped alongside the phases: MCP parity across the whole surface (RFC-008,
48 tools; remote transport RFC-017), design-system PDF exports, backup/
restore, Keychain-backed keys (RFC-019), object rename/delete/fix for people
and companies (RFC-021), unified ranked search with a semantic pass
(RFC-022), opt-in local telemetry (RFC-023), gated feature-request capture
(RFC-025). The decision ledger is [`docs/RFC.md`](docs/RFC.md)
(RFC-001…034).

The competitive research pass is **done**: two independent landscape
passes over ~30 comparators and the decisions drawn from them live in
[`docs/research/DECISION-MEMO-2026-07-18.md`](docs/research/DECISION-MEMO-2026-07-18.md).

**Current backlog, in order:** (1) Phase 5 scored daily brief — rank
everything the workspace knows each morning into three cited actions,
composing the pieces that now exist (fit verdicts, warmth vector, digest
actions); (2) Phase 7 interview preparation (the decision memo's strongest
near-term build); (3) multi-engine research council — query OpenAI,
Anthropic, Gemini, and Perplexity in parallel and synthesize a layered
agreement/disagreement/novelty report, with an optional steelman/strawman/
adversarial pressure-test pass (design recorded in
[`docs/RESEARCH-COUNCIL-DESIGN.md`](docs/RESEARCH-COUNCIL-DESIGN.md); RFC
candidate); (4) hosted deployment tiers (assessment recorded; RFC
candidate).

## Phase 0 — Repository and Safety Foundation

**Usable outcome:** a reliable local application skeleton.

Deliver:

- repository scaffold;
- CLI entry point;
- configuration loading;
- local storage abstraction;
- structured logging;
- provenance schema;
- approval-policy module;
- test and CI baseline;
- `wingman init`, `wingman doctor`, and `wingman status`.

## Phase 1 — Career Brain

**Usable outcome:** one canonical professional profile.

Deliver:

- resume and Markdown ingestion;
- source tracking;
- achievement and skill extraction;
- conflict detection;
- manual correction workflow;
- `career.md`;
- `career.json`.

## Phase 2 — Opportunity Inbox

**Usable outcome:** paste a job description and receive a cited fit brief.

Deliver:

- job-description ingestion;
- structured requirement extraction;
- opportunity record;
- fit, gap, and uncertainty assessment;
- evidence mapping;
- Markdown report;
- status and next action.

## Phase 3 — Company Research

**Usable outcome:** generate a dated company dossier.

Deliver:

- research plan;
- approved-source handling;
- strategy, product, leadership, market, and hiring signals;
- fact/inference/hypothesis labels;
- stale-data warnings;
- incremental refresh.

## Phase 4 — Relationship Intelligence

**Usable outcome:** identify credible warm paths and overdue follow-ups.

Start with explicit imports:

- contacts export;
- calendar export;
- selected Gmail data;
- LinkedIn export;
- personal notes.

Deliver:

- person and interaction normalization;
- deduplication;
- relationship timeline;
- transparent relationship-strength score;
- introduction paths;
- follow-up queue.

## Phase 5 — Daily Operator

**Usable outcome:** a daily list of the three highest-leverage actions.

Deliver:

- due-action engine;
- opportunity-priority model;
- expected-value components;
- freshness checks;
- daily Markdown brief;
- completed/deferred/dismissed feedback.

## Phase 6 — Drafting and Voice

**Usable outcome:** approved research becomes a credible draft.

Deliver:

- introduction request;
- direct outreach;
- recruiter response;
- follow-up;
- thank-you note;
- voice examples;
- critic pass;
- approval boundary.

## Phase 7 — Interview Preparation

**Usable outcome:** a structured preparation packet and mock interview.

Deliver:

- role-specific questions;
- company-specific hypotheses;
- achievement-to-requirement mapping;
- STAR story library;
- mock interview loop;
- answer critique.

## Phase 8 — Controlled Connectors

**Usable outcome:** reduce manual import work without reducing control.

Add read-only connectors first:

- Gmail;
- Calendar;
- Contacts;
- GitHub;
- approved job and company sources.

Write capabilities require a separate architecture decision and approval UX.

## Phase 9 — Outcome Post-Mortems

**Usable outcome:** close the loop — record what actually happened to an opportunity and see whether your own fit/gap verdicts predicted it.

Deliver:

- outcome status per opportunity (advanced, rejected, withdrawn, ghosted, offer, declined);
- verbatim feedback capture — quoted from the recruiter/interviewer's own words, never paraphrased into a claim;
- a dated post-mortem note per closed opportunity;
- a verdict-to-outcome report: deterministic arithmetic over met/partial/gap history versus the stage actually reached, across closed opportunities;
- pattern surfacing (which requirement types most often preceded a stall) computed from stored verdicts, not inferred causation;
- no cause is asserted without a user-supplied note or a verbatim-quoted reason — an unexplained rejection stays unexplained.

## Initial Vertical-Slice Backlog

1. Scaffold repository and quality gates.
2. Implement provenance-aware local records.
3. Ingest one resume into `career.json` and `career.md`.
4. Add conflict detection and manual overrides.
5. Ingest one job description.
6. Produce a cited fit-and-gap report.
7. Add transparent opportunity scoring.
8. Generate a company dossier.
9. Import a small anonymized relationship dataset.
10. Generate a three-action daily brief.
11. Draft one evidence-backed introduction request.
12. Add an evaluation runner and baseline report.
