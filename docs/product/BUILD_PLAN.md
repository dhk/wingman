# Wingman Agentic Build Plan

This document covers **build-time process**: how Wingman gets engineered, and which models do the engineering. It is the single home for concrete build-model recommendations. Runtime model routing is a different concern, owned by [`../RFC.md`](../RFC.md) (RFC-004).

Wingman is built with coding agents as the primary engineering harness while remaining independent of any coding assistant at runtime.

## Build-Time Model Guidance

Use Claude Sonnet (current generation) for routine implementation, testing, refactoring, and repository work.

Escalate to the current frontier Claude reasoning model (Opus-class or above) for:

- architecture;
- difficult debugging;
- adversarial review;
- consequential product decisions.

Use an independent second review for changes affecting:

- privacy;
- external-action permissions;
- relationship inference;
- career-critical recommendations.

## Runtime Model Guidance

Runtime model access goes through capability classes mapped in configuration; the workload-to-class table and rationale live in RFC-004. Model names change — never scatter them through the codebase.

## Immediate Starting Point

Build Phase 0 and the smallest Phase 1 slice:

```text
resume.md
   ↓
validated ingestion
   ↓
career.json
   ↓
career.md with evidence references
```

Do not begin with a dashboard, graph database, embeddings, continuous monitoring, live Gmail ingestion, or autonomous outreach.
