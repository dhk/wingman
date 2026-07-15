# Wingman Agentic Build Plan

Wingman is built with Codex as the primary engineering harness while remaining independent of any coding assistant at runtime.

## Build-Time Model Guidance

Use the current recommended Codex model for routine implementation, testing, refactoring, and repository work.

Escalate to a frontier reasoning model for:

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

| Workload | Preferred approach |
|---|---|
| Parsing, validation, deterministic scoring | No model |
| High-volume extraction and classification | Small/fast model |
| Routine synthesis and drafting | Balanced model |
| Difficult synthesis and critique | Frontier reasoning model |
| External-action policy checks | Deterministic policy engine |

Model names change. Store aliases in configuration and route by capability.

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
