# Spend tracking: a per-tenant model-usage ledger

Status: DRAFT for discussion. Extends RFC-080 (#514, operator-funded tenants) and RFC-048 (shared multi-tenant process). Not an RFC yet; number to be assigned on acceptance.

## Problem

Wingman resolves metered keys per tenant (RFC-048) and lets the operator fund a tenant (`funded = true`, RFC-080), but nothing records what a call cost. Consequences:

1. The operator cannot see what a funded tenant is spending. RFC-080 rejected a spend cap because "wingman sees no billing data and cannot enforce a cap it cannot measure." That is still true today.
2. Token counts exist and are discarded. `ModelResponse` carries `input_tokens` and `output_tokens` (`providers/base.py`), populated by both the Anthropic and OpenRouter providers, then never persisted.
3. The existing telemetry journal (RFC-023) is not a substitute. It records usage, not tokens, is default-off, captures tool arguments and results unredacted by owner decision, and its own "Revisit if" clause says redaction becomes mandatory once a second user's data enters the journal path. Hosting other people is exactly that case, so reusing telemetry for cost would import its privacy problem.

Goal: make spend measurable per tenant, per capability class, per payer, with no conversation content stored. Enforcement is a later phase that depends on the measurement.

## Facts this design relies on (verified in the code at 1556b5a)

- Every model call goes through `providers/router.get_provider()`; the embedding path goes through `get_embedding_provider()`. Two choke points.
- Four capability classes map to Anthropic; `research_websearch` maps to OpenRouter; `embed_semantic` maps to Voyage. `reason_frontier` and `critic_independent` have no call sites in `src/`.
- Each tenant has its own `Config` (`data_dir`, `db_path`, `funded`, `strict_provider_keys`, `operator_name` set from the tenant slug).
- `router.metered_key()` already decides which tier supplies a key: BYOK, funded-declared, or solo ambient. That decision is exactly the payer, and it is currently thrown away.
- `AnthropicProvider.complete()` reads only `usage.input_tokens` and `usage.output_tokens`. Cache read and creation tokens are ignored, and no prompt caching is used anywhere in `src/`.
- OpenRouter's web-search fee is computed from a constant (`SEARCH_RESULT_PRICE_USD = 0.004` times result cap) and is not token-denominated.

## Design

### 1. Record raw units, never dollars

A table in the tenant's own `wingman.db`:

```
model_usage(
  event_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,            -- UTC ISO-8601
  capability TEXT NOT NULL,    -- extract_fast, synthesize_balanced, embed_semantic, ...
  provider TEXT NOT NULL,      -- anthropic | openrouter | voyage
  model TEXT NOT NULL,         -- as returned by the API
  payer TEXT NOT NULL,         -- byok | funded | ambient
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER,
  search_results INTEGER,      -- OpenRouter web plugin units
  latency_ms INTEGER,
  caller TEXT                  -- MCP tool or CLI command name; optional
)
```

Dollars are computed at read time from a price table, so a price change or a correction reprices history. Storing computed dollars would freeze mistakes in.

No prompt, response, argument, or result text is stored. The columns are numbers and fixed labels only. That is the property that distinguishes this from telemetry.

### 2. One wrapper at the router

`get_provider()` and `get_embedding_provider()` return the provider wrapped in a thin `MeteredProvider` that calls the inner provider, then appends one row. Because both functions are the only constructors, no call site changes and none can forget to record. `RecordedProvider` is not wrapped.

Rules, mirroring telemetry's discipline:
- A failed recording never breaks or changes the model call; log and continue.
- Failed API calls write no row (no tokens were returned to count).
- Always on. A ledger that is opt-in cannot be the basis for a cap.

### 3. Payer attribution

Refactor `metered_key()` so it also returns which branch produced the key, then pass that to the wrapper. Only `funded` rows are the operator's money. BYOK rows are informational for the tenant. This answers RFC-080's own "revisit" note that once funding exists "the interesting question is attribution rather than access."

### 4. Close the provider data gaps

- Anthropic: also capture `cache_read_input_tokens` and `cache_creation_input_tokens`. Cheap now, and it prevents under-counting if prompt caching is adopted later.
- OpenRouter: record `search_results` so the search fee is attributable, because it is not in the token counts.
- Voyage: the embeddings response reports token usage; capture it (to be confirmed against the provider's response shape before implementation).

### 5. Prices live in configuration, not code

Follow RFC-004's rule that model names belong in `models.toml`: put per-model price fields next to each `[models.*]` entry (input, output, cache read, cache write per million tokens; per-result fee for search). Unknown model means a row with tokens and a null cost, reported as "unpriced", never silently zero. Prices are an operator-maintained input with no authoritative source in the repo, so they must be checked against the providers' published pricing when entered.

### 6. Read surfaces

- Tenant: an MCP `usage` tool and `wingman usage` CLI showing their own tokens and estimated cost by capability, by day, and by payer.
- Operator: a privileged cross-tenant rollup (same `privileged` gate as the other operator-only tools, RFC-068) that walks the registry and sums each tenant's table. Funded tenants ranked by spend.
- `completeness` could surface "you have spent X of your allowance" once a cap exists.

### 7. Enforcement (phase 2, after the ledger has data)

Add an optional per-tenant `monthly_budget_usd` in the registry, valid only with `funded = true`, in the same registry position and with the same bare-value rules as `funded`. Before a funded call, sum the month's priced rows; at 80% warn in the tool response, at 100% refuse with a message naming the remedy (bring your own key, or ask the operator). The check is best-effort: concurrent calls can overshoot by one request, and unpriced rows count as unknown. The hard backstop stays a spend limit on the provider workspace/account, because the ledger is wingman's view, not the provider's invoice. RFC-080's rejection of a cap is explicitly conditioned on spend not being visible, so this phase supersedes that clause and the RFC should be amended.

## Alternatives considered

- **Extend telemetry (RFC-023).** Rejected: default-off, stores content, single-owner consent model.
- **Rely on provider consoles only.** Anthropic's Console can separate spend by workspace and set limits, and that is still the right hard cap. But it cannot attribute spend to a wingman tenant or capability class unless each tenant has its own key or workspace. Keep it as the backstop, not the ledger.
- **One central operator database.** Simpler rollups, but it makes one shared file the writer for every tenant and breaks per-tenant data locality. Per-tenant tables plus a privileged rollup keep that boundary.
- **Store computed dollars.** Rejected for the repricing reason above.

## Open questions

1. Retention: keep forever, or prune like backups (keep-N)? Rows are small, so this is low priority.
2. Should `caller` be populated from the telemetry wrapper's tool name, or a context variable set at the MCP tool boundary? The wrapper already wraps every registered tool once at import, so a context variable set there is the likely route; needs a check that it propagates into the thread or task that calls the provider.
3. Does any shared-process code path construct a provider without a tenant `Config` (and so without a ledger to write to)? Needs an audit before implementation.
4. Batch API for non-interactive pipelines (overnight runs) would roughly halve input and output cost per Anthropic's published batch discount, but it changes the synchronous `complete()` contract. Out of scope here; the ledger would need a `batch` flag when that lands.

## Acceptance criteria

- Every successful provider call through `get_provider()` or `get_embedding_provider()` writes exactly one `model_usage` row, with no content stored.
- A funded tenant's rows are marked `payer = funded`; a BYOK tenant's are `byok`.
- A tenant can see their own usage; only a privileged tenant can see others'.
- A recording failure never changes a call's result.
- Unpriced models report tokens with cost "unpriced", not zero.
- Test: a fixture tenant with a recorded provider produces the expected rows; a strict unfunded tenant still fails loud with no row written.
