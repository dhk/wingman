# Wingman Evaluation Specification

This document defines how we know Wingman is actually getting better — not "the demo looked good," but measured improvement on fixed tasks, with regressions caught before they ship.

Evaluation is a Phase 0 concern. Every model-backed capability lands with its evaluation fixtures in the same change, because retrofitting golden datasets onto behavior that already shipped measures nothing.

## Principles

1. **Deterministic first.** Everything that can be asserted exactly — schema validity, citation presence, provenance completeness — is asserted exactly, in ordinary tests. Model-graded evaluation is reserved for qualities that have no deterministic check (tone, synthesis quality).
2. **Evidence-linked.** The core failure mode Wingman exists to avoid is confident, unsupported claims. Unsupported-claim detection is therefore a first-class metric, not an afterthought.
3. **Fixed tasks, moving models.** Golden datasets stay stable so that scores are comparable across time, prompt versions, and providers. When a dataset must change, it gets a new version, and history records which version each score came from.
4. **Fail visibly.** An evaluation that cannot run (missing key, provider outage) reports "not run," never a silent pass.

## Test tiers

Evaluation sits at the top of the testing pyramid defined in [`../AGENTS.md`](../AGENTS.md):

- **Unit / integration / contract tests** run in the default fast suite and gate every merge.
- **Evaluation tests** exercise model-backed behavior against fixtures. Deterministic checks over recorded model outputs run in CI. Tests that call live models are marked (`pytest -m evaluation_live`), excluded from the default suite, and run on demand and before each phase is declared done.

## Golden datasets

Golden datasets live under `fixtures/`, versioned in git, one directory per capability:

```text
fixtures/
├── profile_extraction/      # resumes → expected canonical facts
├── fit_assessment/          # job description + profile → expected fit brief
├── company_research/        # source packets → expected dossier claims
├── adversarial/             # prompt-injection and manipulation attempts
└── ...
```

Each case contains the input, the expected structured output (or expected properties, where exact output is not deterministic), and a note on what the case is guarding.

Curation rules:

- **No identifiable private data.** Fixtures are synthetic or fully anonymized. A real resume never enters the repository.
- Every case states its purpose; cases nobody can explain get deleted, not accumulated.
- Golden outputs change only with an explanation of why the new behavior is better — never merely to make a failure disappear.
- Each capability's dataset must include, from the start: happy-path cases, **missing-evidence** cases (correct answer is "unknown"), **contradictory-evidence** cases (correct answer surfaces the conflict), and adversarial cases.

## Metrics

Reported per capability, per dataset version, per model configuration:

| Metric | What it measures |
| --- | --- |
| Extraction precision / recall | Facts found vs. facts invented or missed, against golden annotations |
| Unsupported-claim rate | Claims in output with no evidence reference that resolves to a real source record |
| Citation coverage | Share of consequential claims carrying a provenance reference |
| Conflict-detection rate | Contradictory-evidence cases where the conflict is surfaced rather than smoothed over |
| Abstention correctness | Missing-evidence cases answered "unknown" rather than filled in |
| Classification fidelity | Fact / inference / hypothesis labels matching golden labels |
| Schema validity rate | Outputs passing Pydantic validation without repair |
| Injection resistance | Adversarial cases where embedded instructions were not followed |
| Cost and latency | Tokens and wall time per task, for the model-comparison matrix |

The two metrics that define Wingman — unsupported-claim rate and abstention correctness — are tracked from the first evaluation run and reported in every summary. A change that improves fluency while raising the unsupported-claim rate is a regression.

## Hallucination and unsupported-claim checks

Every generated artifact (fit brief, dossier, draft) is checked by deterministic code that:

1. extracts the consequential claims;
2. verifies each claim's evidence reference resolves to an existing record;
3. flags claims with no reference, or references that do not support the claim's classification (an "inference" cited as "fact" is a failure).

Fabrication canaries: fixture inputs deliberately omit specific attractive details (a metric, a credential, a prior relationship). Any output that supplies the omitted detail is a caught fabrication and fails the case.

## Provenance validation

Provenance is validated at two levels:

- **Record level (unit tests):** every persisted record carries the full provenance schema of RFC-005; transformations append to history rather than overwrite; conflicting values are preserved side by side.
- **Artifact level (evaluation tests):** every claim in a generated report traces back through the provenance chain to a source. A report that cannot be traced is invalid regardless of quality.

## Model-comparison benchmarks

Because routing goes through capability classes (RFC-004), any provider/model can be benchmarked on the same golden datasets by swapping configuration. Each run records provider, model, reasoning/sampling settings, prompt template version, tool-policy version, token usage, latency, and validation results — so "model X is better for extraction" is a claim with a table behind it, comparable across dataset versions.

This is how routing decisions get made and re-made as providers ship new models: re-run the matrix, read the table, update the config.

## Regression policy

- Evaluation summaries are written to `reports/` as human-readable Markdown linked to the structured results, with scores keyed by dataset version and model configuration so trends are comparable.
- A drop in a defining metric blocks the change that caused it unless the summary explains why the trade-off is acceptable.
- Prompt and template changes are versioned; every score is attributable to an exact prompt version.

## Phase expectations

- **Phase 0:** evaluation harness skeleton, fixture layout, adversarial fixture format, and this specification.
- **Phase 1 (Career Brain):** profile-extraction goldens, including missing- and contradictory-evidence cases; unsupported-claim checking wired into report generation.
- **Phase 2 (Opportunity Inbox):** fit-assessment goldens with evidence-mapping validation; first model-comparison matrix.
- **Each later phase:** ships its golden dataset and metrics in the same change as the capability, per the Definition of Done in `AGENTS.md`.
