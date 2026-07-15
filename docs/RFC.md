# Wingman Engineering RFC

This document records engineering decisions and the reasoning behind them. It answers "why is the codebase built this way," while [`DESIGN.md`](DESIGN.md) answers "what is the system" and [`../ROADMAP.md`](../ROADMAP.md) answers "in what order."

Decisions with durable architectural consequences also get an ADR under [`adr/`](adr/). An RFC entry explains the current engineering position and its trade-offs; an ADR records a decision point we do not expect to revisit casually.

## How to add an entry

Append a numbered section with: the decision, the alternatives considered, the rationale, and what would cause us to revisit it. Do not delete superseded entries; mark them **Superseded by RFC-NNN** so the reasoning trail survives.

---

## RFC-001: Layered architecture with inward dependencies

**Decision.** Code is organized as `domain` → `application` → `infrastructure`, with `agents`, `providers`, `policies`, `evaluation`, `reporting`, and `cli` as peers that depend inward. The domain layer imports no model SDKs, databases, web clients, or frameworks.

**Alternatives.** A flat package (faster to start, degrades quickly as connectors and providers accumulate); a plugin architecture (premature for a single-user local tool).

**Rationale.** Wingman's riskiest dependencies — model providers, connectors, persistence — are the ones most likely to change. Keeping them at the edge means the career-domain logic stays testable without network access and survives provider churn. See ADR 0002.

**Revisit if.** The domain layer stays so thin that the layering is pure ceremony after Phase 2.

## RFC-002: Technical baseline

**Decision.** Python 3.12+, `uv` for dependencies and task running, Typer for the CLI, Pydantic for schemas, SQLite for persistence, pytest for tests, Ruff for lint/format, mypy in strict mode.

**Alternatives.** Poetry/pip-tools (slower, more lockfile friction than uv); Click or argparse (more boilerplate than Typer for the same result); Postgres or a document store (operational weight a local-first single-user tool does not need).

**Rationale.** Every choice favors a tool that is fast, boring, and widely understood. SQLite in particular keeps the local-first promise trivially true: the user's data is one file on their disk. See ADR 0001.

**Revisit if.** Multi-device sync becomes a real requirement (storage), or the CLI grows subcommand complexity Typer handles poorly.

## RFC-003: Deterministic controls, not model judgment

**Decision.** Schema validation, permission enforcement, approval gates, arithmetic scoring, deduplication, status transitions, file routing, and secrets handling are implemented in ordinary code. Models are used only where semantic judgment or language generation adds value.

**Alternatives.** "LLM everywhere" — letting a model validate, route, and score directly.

**Rationale.** The controls that keep Wingman safe (approval before external action, least privilege, provenance) must be auditable and must fail closed. A model cannot guarantee either. Deterministic controls are also free to run, which matters when they gate every operation.

**Revisit if.** Never for safety controls. Individual scoring components may gain model-assessed *inputs*, but aggregation stays deterministic.

## RFC-004: Model routing through capability classes

**Decision.** Application code requests a capability class — `extract_fast`, `synthesize_balanced`, `reason_frontier`, `critic_independent` — and configuration maps each class to a concrete provider and model. Model IDs never appear in domain or application code.

**Alternatives.** Direct model IDs at call sites (simple, but every provider change is a code change scattered across the tree); a routing service (overkill locally).

**Rationale.** Model names, prices, and relative strengths change monthly. Capability classes make "swap the extraction model" a one-line config change and make model-comparison benchmarks (see [`EVALUATION.md`](EVALUATION.md)) possible without code churn. See ADR 0002.

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
