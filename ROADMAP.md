# Wingman Roadmap

Each phase must leave Wingman in a usable state.

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
