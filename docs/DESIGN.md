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
Presentation (CLI + MCP server — stdio locally, opt-in loopback HTTP behind a
              user-managed tunnel, RFC-008/017; further surfaces need a durable RFC entry)
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
- **CorpusDocument** — one document of the user's own writing (essay, README, export entry), backed by a SourceRecord and indexed for full-text search (RFC-007); the unit of quotation for drafting and evidence lookup.
- **Person** and **Interaction** — relationship records built from explicit imports.
- **ExternalDocument** — one stored post of a watched person's public writing (or an org-attributed feed's), the unit of quotation for POV validation; kept strictly separate from news mentions about a person (RFC-014).
- **PovCard** — validated stances (verbatim quote + source doc each, dimensioned values/attitude/technical/strategy) for a person, the user's corpus (`__corpus__`), or a company's document pool (`__company__<key>`, RFC-016).
- **OutreachBrief** — purpose-shaped talking points (each citing a card stance exactly and quoting the user's corpus verbatim) plus intro bullets.
- **CompanySource** and **ResearchSnapshot** — user-approved research URLs and their fetched text-hash/link-set snapshots; findings are diffs (RFC-015).
- **Watchlist membership** — named groups of people/companies; the reserved `overnight` list is the standing-consent record for scheduled deep refreshes (RFC-018).
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

- The workspace is the user's data directory: `WINGMAN_DATA_DIR` when set, otherwise the platform user data directory (e.g. `~/.local/share/wingman` on Linux). It is never resolved relative to the invoking directory; development checkouts set `WINGMAN_DATA_DIR=./data` to keep the workspace inside the repo.
- A single SQLite database file (`wingman.db`) in the workspace holds entities and source records. One file on the user's disk is the local-first promise made concrete (RFC-002).
- Generated artifacts (`career.md`, fit briefs, dossiers, daily briefs) are written as Markdown under the workspace's `reports/` directory and reference the structured records they were produced from.
- Raw imported artifacts land in the workspace's `inbox/` directory before ingestion and are referenced by SourceRecords.
- Prompt templates are versioned files shipped inside the package (`src/wingman/prompts/`, e.g. `profile_extraction_v1.md`) so installed CLIs carry them; every score and generated artifact is attributable to an exact prompt version ([`EVALUATION.md`](EVALUATION.md)). Schemas have no separate directory — the Pydantic models in code are the schema source of truth.
- The workspace's `models.toml` (written by `wingman init`) maps capability classes to concrete providers and models (RFC-004) — the one place runtime model names live.
- No remote persistence, no telemetry. Backups are dated tarballs (`wingman backup`/`restore`): a consistent SQLite snapshot plus inbox/reports, with retention pruning and a refuse-to-clobber restore.
- API keys live in the macOS Keychain (`wingman keys`, RFC-019) and hydrate the environment at startup; an exported environment variable always wins.

## Hosted Tenancy and Sign-in

A host can serve many people from one shared process (RFC-048). Each person is a **tenant**: a slug in the tenant registry (`tenants.toml`) pointing at its own workspace directory, laid out exactly as in Storage above. Every request runs against one tenant's `Config`, bound for that request only; a tenant's API keys resolve from its own workspace and never fall through to another tier (`infrastructure/tenants.py`, `tenant_asgi.py`).

A request reaches its tenant one of three ways:

```text
Claude connector ──► <prefix>/mcp          bearer token, verified against the MCP issuer
                                           (iss, sub) ──► identity map ──► slug ──► workspace
Browser          ──► <prefix>/login, /setup  WorkOS session, verified against the browser issuer
                                           (iss, sub) ──► identity map ──► slug ──► workspace
Legacy link      ──► <prefix>/mcp/<token>  capability token ──► slug (served until #616 retires it)
```

- **Identity map** (`oauth_bearer.IdentityMap`). Keyed on `(issuer, subject)`, never on an email address. Several identities may name one slug; that is how one person's Claude sign-in and browser sign-in reach the same workspace. The map is written under a lock and replaced atomically.
- **Two trusted issuers, one subject.** The MCP issuer and the browser issuer are read from the service's own configuration (`oauth_service_issuers.py`). WorkOS gives one user the same subject on both, so binding a person writes their subject under every trusted issuer in one atomic replacement, and refuses if either key already belongs to another tenant.
- **Bearer route** (`BearerRoutingASGIApp`). Anything that fails verification, or whose identity is not in the map, is answered before it reaches FastMCP or any tool: a 401 challenge with protected-resource metadata, or a 403 for a verified but unapproved identity.
- **Browser surface** (`oauth_browser.py`). The cookie is an opaque, short-lived handle; the access token stays server-side and is revalidated on every setup request. The browser never receives a capability URL. Writes require a CSRF token.

Admission (`oauth_onboarding.py`, `oauth_invite_claim.py`):

1. **Pending queue.** A verified identity that is not in the map is recorded in a bounded pending queue and shown a short reference (`XXXX-XXXX`, a fingerprint of its own `(iss, sub)`) on whichever surface it used. The same refusal text is used on both surfaces.
2. **Operator approval.** `wingman tenant oauth-approve` creates a new tenant from a pending identity; `wingman tenant oauth-bind` attaches an identity to an existing tenant. Either binds under every trusted issuer and clears the pending row. An optional operator notification (a Todoist task) fires the first time an identity is queued.
3. **Email invites.** `wingman tenant oauth-invite` prepares a tenant for an address; the address is stored only as an HMAC under a per-install key. When an identity signs in with that address verified by the identity provider, it is bound to the prepared tenant without an operator step. Unverified or mismatched addresses fall back to the pending queue.

Registry and identity-map changes take effect in the running process on `SIGHUP`, validated as one update (`tenant_process.register_reload_handler`).

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
- **Scoring internals** — component set and weights for opportunity and action scores (Phase 2 and 5; the decomposability contract is already fixed in AGENTS.md §Scoring and Recommendations).
- **Connector architecture** — auth, scope grants, and sync model for read-only connectors (Phase 8; durable RFC entry required).
- **Write-capable connectors and approval UX** — separate architecture decision, explicitly out of scope until after Phase 8.
