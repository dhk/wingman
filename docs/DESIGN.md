# Wingman Design Specification

## Overview

Wingman is a local-first career intelligence platform that helps users discover, evaluate, and pursue high-quality career opportunities. It is explicitly **not** an autonomous job application system.

This document describes what the system is. Rationale for engineering decisions lives in [`RFC.md`](RFC.md); delivery order lives in [`../ROADMAP.md`](../ROADMAP.md); binding rules for contributors and agents live in [`../AGENTS.md`](../AGENTS.md).

## Goals

- Canonical career profile
- Opportunity assessment
- Company intelligence
- Relationship intelligence
- Daily prioritization
- Interview preparation
- Human-approved drafting

## Non-goals

- Automatic applications
- Automatic messaging
- Mass resume optimization
- Autonomous networking

## Architecture

```text
Presentation (CLI; any additional surface requires a durable RFC entry)
    |
Application Services
    |
+-----------------------------+
| Agents                      |
| - Profile Curator           |
| - Opportunity Analyst       |
| - Company Researcher        |
| - Connector                 |
| - Operator                  |
| - Writer                    |
| - Critic                    |
+-----------------------------+
    |
Domain Model
    |
Infrastructure
    |- SQLite
    |- Local Files
    |- Provider Adapters
    |- Connectors
```

The diagram maps onto the package layout under `src/wingman/`: `domain`, `application`, `infrastructure`, `agents`, `tools`, `providers`, `policies`, `evaluation`, `reporting`, and `cli`. Dependencies point inward (RFC-001).

## Agent Contracts

Each agent defines the contract checklist in [`../AGENTS.md`](../AGENTS.md) §Agent Contracts — that list is the single source of truth.

Agents call approved application tools; they do not bypass the application layer, and no agent has unrestricted connector access.

## Data Model

Entities:

- **SourceRecord** — an imported artifact (resume, job description, exported contacts, saved page): content hash, source type, a locator for the raw artifact (the bytes themselves stay on disk under `data/`, not in the database), source timestamp when known, ingestion timestamp. Source records are immutable once ingested; corrections create new records.
- **CareerProfile** — the canonical profile: achievements, skills, roles, each carrying evidence references to source records.
- **Company** — researched company facts and signals, each labeled fact / inference / hypothesis.
- **Opportunity** — a role under consideration: extracted requirements, fit assessment, status, next action.
- **Person** and **Interaction** — relationship records built from explicit imports.
- **Recommendation** — a proposed action with its decomposed score, evidence references, and outcome feedback.

Every entity that can influence a recommendation carries the full provenance metadata of RFC-005 (stable ID, source references, transformation history, confidence, fact/inference/hypothesis classification, user-override metadata). Claims in generated artifacts must trace through this chain back to a SourceRecord — that traceability is what [`EVALUATION.md`](EVALUATION.md) validates.

Conflicting values from different sources are stored side by side with their provenance; the profile exposes the conflict and records the user's resolution as an override, never a silent overwrite.

## Data Flow — Phase 1 slice

The first end-to-end flow, which later phases repeat with different inputs:

```text
resume.md
   ↓  ingestion: schema-validated read, SourceRecord persisted (deterministic)
   ↓  extraction: achievements/skills proposed with evidence spans (model, extract_fast)
   ↓  validation: Pydantic schemas, evidence references resolved (deterministic)
   ↓  conflict detection against existing profile (deterministic)
career.json   — structured canonical profile
career.md     — readable profile; every consequential claim cites its evidence
```

The pattern is fixed: deterministic ingestion and validation bracket every model step, and nothing enters the profile without a resolvable evidence reference.

## Storage

- A single SQLite database file under the user's data directory (`WINGMAN_DATA_DIR`, default `./data`) holds entities and source records. One file on the user's disk is the local-first promise made concrete (RFC-002).
- Generated artifacts (`career.md`, fit briefs, dossiers, daily briefs) are written as Markdown under `reports/` and reference the structured records they were produced from.
- Raw imported artifacts land in `data/inbox/` before ingestion and are referenced by SourceRecords.
- Prompt templates live as versioned files under `prompts/`; every score and generated artifact is attributable to an exact prompt version ([`EVALUATION.md`](EVALUATION.md)). Schemas have no separate directory — the Pydantic models in code are the schema source of truth.
- No remote persistence, no telemetry. Backup and migration design is deferred (see Open Questions).

## Approval Flow

External actions (send, post, apply, schedule, contact-update) do not exist until late phases, but the gate they will pass through is built first (`policies` layer, Phase 0):

1. An agent or use case proposes an external action as a structured request.
2. The deterministic policy engine checks it against the user's permission grants — no model participates in this decision (RFC-003, RFC-006).
3. Dry-run is the default: the CLI shows the exact effect (recipient, content, scope) without executing.
4. Execution requires explicit confirmation at the point of action; approval is per-action, never batched.
5. The decision — approved, rejected, or dry-run — is recorded with the request.

## Model Strategy

Deterministic code where possible; models only where semantic judgment or language generation adds value (RFC-003).

Runtime model access goes through capability classes (`extract_fast`, `synthesize_balanced`, `reason_frontier`, `critic_independent`) mapped to concrete providers in configuration — RFC-004 is the single home for this policy. Build-time tooling guidance, including the currently recommended build model, lives in [`product/BUILD_PLAN.md`](product/BUILD_PLAN.md).

Never hard-code provider-specific assumptions.

## Privacy

The binding rules are Product Invariants 1–4 (see [`../VISION.md`](../VISION.md)): human approval before external action, evidence before assertion, local-first private data, least-privilege tools and connectors. Security practices for contributors are in [`../AGENTS.md`](../AGENTS.md) §Security and Privacy.

## Phases

Delivery order and per-phase deliverables live in [`../ROADMAP.md`](../ROADMAP.md). Every phase must deliver an independently useful capability.

## Open Questions

Deliberately undecided; each gets designed (and, where durable, an RFC entry marked Durable) in the phase that needs it:

- **Failure and recovery model** — partial ingestion failures, corrupted records, interrupted runs (Phase 1, first real ingestion).
- **Backup and migration** — schema versioning and user data portability (Phase 1–2, once the schema stabilizes).
- **Scoring internals** — component set and weights for opportunity and action scores (Phase 2 and 5; the decomposability contract is already fixed in AGENTS.md §Scoring and Recommendations).
- **Connector architecture** — auth, scope grants, and sync model for read-only connectors (Phase 8; durable RFC entry required).
- **Write-capable connectors and approval UX** — separate architecture decision, explicitly out of scope until after Phase 8.
