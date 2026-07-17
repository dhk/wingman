# Wingman Engineering RFC

This document records engineering decisions and the reasoning behind them. It answers "why is the codebase built this way," while [`DESIGN.md`](DESIGN.md) answers "what is the system" and [`../ROADMAP.md`](../ROADMAP.md) answers "in what order."

An RFC entry explains the current engineering position and its trade-offs. Entries with durable architectural consequences carry a **Durable decision** marker — a decision point we do not expect to revisit casually.

## How to add an entry

Append a numbered section with: the decision, the alternatives considered, the rationale, and what would cause us to revisit it. Do not delete superseded entries; mark them **Superseded by RFC-NNN** so the reasoning trail survives.

---

## RFC-001: Layered architecture with inward dependencies

**Decision.** Code is organized as `domain` → `application` → `infrastructure`, with `agents`, `tools`, `providers`, `policies`, `evaluation`, `reporting`, and `cli` as peers that depend inward. The domain layer imports no model SDKs, databases, web clients, or frameworks.

**Alternatives.** A flat package (faster to start, degrades quickly as connectors and providers accumulate); a plugin architecture (premature for a single-user local tool).

**Rationale.** Wingman's riskiest dependencies — model providers, connectors, persistence — are the ones most likely to change. Keeping them at the edge means the career-domain logic stays testable without network access and survives provider churn. See RFC-004.

**Revisit if.** The domain layer stays so thin that the layering is pure ceremony after Phase 2.

## RFC-002: Technical baseline

**Decision.** Python 3.12+, `uv` for dependencies and task running, Typer for the CLI, Pydantic for schemas, SQLite for persistence, pytest for tests, Ruff for lint/format, mypy in strict mode.

**Durable decision** — not expected to be revisited casually: private user data is stored locally by default; remote persistence and telemetry require separate explicit decisions. Accepted consequences: local setup is slightly more involved; privacy boundaries are easier to understand; connectors begin read-only; backups and migration need deliberate design. (Folded from former ADR 0001.)

**Alternatives.** Poetry/pip-tools (slower, more lockfile friction than uv); Click or argparse (more boilerplate than Typer for the same result); Postgres or a document store (operational weight a local-first single-user tool does not need).

**Rationale.** Every choice favors a tool that is fast, boring, and widely understood. SQLite in particular keeps the local-first promise trivially true: the user's data is one file on their disk.

**Note — DuckDB evaluated (2026-07), not adopted.** Wingman's workload is transactional (small inserts, point lookups, single-row evidence merges over thousands of rows), the corpus search depends on SQLite FTS5's incremental indexing and `snippet()` (RFC-007), the stdlib dependency footprint is a stated feature, and career data is a decades-horizon archive where SQLite's format stability is the conservative pick — so DuckDB offers no realizable advantage as the system of record. It remains the intended **analytical attachment** if a genuinely columnar workload arrives (the EVALUATION.md model-comparison matrix, Phase 5 prioritization/funnel analytics): DuckDB's `sqlite` scanner queries `wingman.db` in place, so adoption then is a read-side query engine over the existing store, not a migration.

**Revisit if.** Multi-device sync becomes a real requirement (storage), or the CLI grows subcommand complexity Typer handles poorly; for the analytical DuckDB attachment specifically, when a columnar workload (evaluation matrices, Phase 5 analytics) measurably outgrows SQLite queries.

## RFC-003: Deterministic controls, not model judgment

**Decision.** Schema validation, permission enforcement, approval gates, arithmetic scoring, deduplication, status transitions, file routing, and secrets handling are implemented in ordinary code. Models are used only where semantic judgment or language generation adds value.

**Alternatives.** "LLM everywhere" — letting a model validate, route, and score directly.

**Rationale.** The controls that keep Wingman safe (approval before external action, least privilege, provenance) must be auditable and must fail closed. A model cannot guarantee either. Deterministic controls are also free to run, which matters when they gate every operation.

**Revisit if.** Never for safety controls. Individual scoring components may gain model-assessed *inputs*, but aggregation stays deterministic.

## RFC-004: Model routing through capability classes

**Decision.** Application code requests a capability class — `extract_fast`, `synthesize_balanced`, `reason_frontier`, `critic_independent` — and configuration maps each class to a concrete provider and model. Model IDs never appear in domain or application code.

**Durable decision** — not expected to be revisited casually: domain and application code depend on internal interfaces, never provider SDKs; provider-specific implementations live under `src/wingman/providers/`. Accepted consequences: tests can use fakes; provider-specific features require explicit adapters; a small abstraction layer is the up-front cost. (Folded from former ADR 0002.)

This entry is the single home for runtime model-routing policy; other documents point here. The mapping from workload to approach:

| Workload | Approach |
|---|---|
| Parsing, validation, deterministic scoring | No model (RFC-003) |
| High-volume extraction and classification | `extract_fast` |
| Routine synthesis and drafting | `synthesize_balanced` |
| Difficult synthesis, high-consequence reasoning | `reason_frontier` |
| Independent critique of candidate artifacts | `critic_independent` |
| External-action policy checks | No model, deterministic policy engine (RFC-006) |

Build-time tooling (which model writes the code) is a separate concern, owned by [`product/BUILD_PLAN.md`](product/BUILD_PLAN.md) — the one place a concrete build-model name may appear.

**Alternatives.** Direct model IDs at call sites (simple, but every provider change is a code change scattered across the tree); a routing service (overkill locally).

**Rationale.** Model names, prices, and relative strengths change monthly. Capability classes make "swap the extraction model" a one-line config change and make model-comparison benchmarks (see [`EVALUATION.md`](EVALUATION.md)) possible without code churn.

**Revisit if.** The four classes prove to be the wrong granularity in practice.

## RFC-005: Provenance metadata on every influential record

**Decision.** Any record that can influence a recommendation carries: stable ID, source type, source locator, source timestamp when known, ingestion timestamp, content hash where appropriate, transformation history, confidence, a fact/inference/hypothesis classification, and user-override metadata. Conflicting source values are preserved side by side, never silently overwritten.

**Alternatives.** Provenance only on "important" records (the importance judgment is exactly what goes wrong); provenance added later (retrofitting provenance onto existing data is close to impossible).

**Rationale.** Wingman's value proposition is evidence-backed career judgment. A recommendation the user cannot trace to sources is indistinguishable from a hallucination. Provenance from record one is the cheapest point to buy it.

**Revisit if.** Not expected; field-level details may evolve with the schema.

## RFC-006: Human approval gates for all external actions

**Decision.** No email, message, application, calendar write, contact update, or post occurs without explicit approval at the point of action. Dry-run is the default for any future external write, and the confirmation displays the exact effect. Approval logic is deterministic code (RFC-003) in the `policies` layer.

**Alternatives.** Blanket pre-authorization ("approve all outreach this session") — rejected because approval fatigue plus batch authorization is how autonomous-agent horror stories happen.

**Rationale.** The product boundary — decision support, not autonomous action — is Wingman's defining constraint. Enforcing it structurally, rather than by convention, means no future feature can cross it by accident.

**Revisit if.** Never. This is a product invariant, not an engineering preference.

## RFC-007: Corpus retrieval via SQLite FTS5, embeddings deferred

**Decision.** The corpus — the user's writing (Substack posts, READMEs, LinkedIn exports, other documents) — is stored as ordinary SourceRecords plus CorpusDocuments and searched with SQLite's built-in FTS5 full-text index. Ingestion is explicit imports (files, directories, export zips), never live connectors. No vector database and no embedding model in this phase.

**Alternatives.** Embeddings + vector search (semantically stronger, but adds a model dependency to a retrieval path that should be deterministic, plus an index to keep consistent — and AGENTS.md requires a demonstrated need before adding a vector database); a separate search service (operational weight a local single-user tool does not need).

**Rationale.** One person's writing is hundreds of documents, not millions; FTS5 is deterministic, ships inside the stdlib's SQLite, keeps the local-first promise (the index lives in the same wingman.db file), and its failure mode is "no results", never a fabricated match. Retrieval feeds model steps (drafting, assessment) that already validate quotes verbatim, so a keyword-recall index is the right floor.

**Revisit if.** Drafting or assessment measurably misses relevant corpus evidence that semantic search would find — then embeddings become a documented decision with the storage and provider trade-offs written down.

## RFC-008: A local MCP server as the second presentation surface

**Decision.** Wingman exposes its use cases (status, evidence search, career profile, job assessment, resume ingestion) as a stdio MCP server (`wingman-mcp`) for MCP clients running on the same machine — Claude Desktop and Claude Code. The server is a thin presentation layer over the application layer: every tool runs the same deterministic validation pipelines as the CLI, and the connected model cannot bypass evidence rules. The workspace stays on the user's machine (stdio, no network listener), so the local-first invariant is untouched.

**Alternatives.** A REST API + web UI (a bigger surface with auth/TLS obligations, and no consumer today); a remote/hosted MCP server for claude.ai web and mobile (requires a network listener and auth — deferred until wanted, and then it gets its own entry); driving the CLI through a generic shell tool (works, but loses typed inputs and puts the workspace behind an unrestricted shell rather than five narrow tools).

**Rationale.** The primary way this product is actually used is through a Claude conversation. An MCP server turns that from copy-paste into typed tool calls while keeping Wingman's guarantees in Wingman's code. Stdio-local is the smallest server that delivers this, and adds no credentials, ports, or tenancy.

**Revisit if.** A non-MCP consumer appears (REST/web UI), or remote access from claude.ai web/mobile is wanted (remote MCP with auth — a separate durable decision).

## RFC-009: Read-only public-feed fetching, explicitly invoked

**Decision.** Wingman's first network access: `wingman people fetch` reads the public, unauthenticated RSS feed of a person the user has explicitly put on their watchlist. The rules, enforced in one place (`infrastructure/fetch.py`):

- **Explicit invocation only.** A fetch happens when the user runs the command — never scheduled, never in the background, never as a side effect of another operation.
- **Read-only, HTTPS-only, unauthenticated.** GET requests to public endpoints; no credentials exist to attach, and non-HTTPS URLs are rejected.
- **Public content only, from declared sources.** Feeds belong to people the user added by hand or seeded from their own LinkedIn connections export. No crawling, no discovery beyond what the user configured. Scraping authenticated surfaces (LinkedIn itself) is explicitly out — exports only.
- **Fetched bytes enter the normal provenance pipeline.** Each post becomes a content-hashed immutable SourceRecord with the raw HTML archived in the inbox, deduplicated by hash, indexed as an ExternalDocument — separate from the user's own corpus so "my evidence" and "their point of view" never mix.
- **Failures are visible.** A feed that cannot be fetched or parsed reports an error; it never degrades silently into an empty result.

**Durable decision** — the *invariants* above (explicit, read-only, public, provenance-tracked) are the durable part; the set of supported feed types may grow.

**Alternatives.** Live connectors with scheduled sync (background network activity in a local-first privacy tool — deferred to Phase 8's connector architecture, which requires its own durable entry); scraping profile pages (violates platform terms and the least-privilege invariant); requiring manual downloads for everything (fails the reality that public writing is the product's raw material for relationship intelligence).

**Rationale.** Relationship intelligence needs other people's public writing, and an RSS feed is that writing in its author-published, terms-of-service-clean form. Confining network access to one function with hard invariants keeps the local-first promise auditable: the workspace never talks to the network unless the user just asked it to, and everything fetched is traceable to the command that fetched it.

**Revisit if.** Feed refresh becomes tedious enough that the user wants scheduled sync — that is Phase 8's connector architecture decision, not a quiet relaxation of this entry.
