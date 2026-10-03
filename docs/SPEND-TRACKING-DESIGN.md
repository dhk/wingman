# Model usage and spend tracking

Wingman records one content-free usage row after each successful model or embedding call.
The row belongs to that workspace's SQLite database, so ordinary users see only their own
usage. Recording is best-effort: a ledger write failure is logged and never changes the
model result. Failed provider calls create no row.

Each row records the time, capability, provider, model, payer (`byok`, `funded`, or
`ambient`), raw input/output/cache token counts, OpenRouter search-result count, latency,
and an optional caller name. Prompts, responses, tool arguments, and retrieved content are
never stored. Provider wrappers are the single recording boundary, avoiding duplicate rows
from application call sites.

## Pricing

Raw units are durable; dollar estimates are derived when `wingman usage` or the MCP
`usage` tool reads them. A capability's `[models.<capability>]` table may define:

- `input_usd_per_million`
- `output_usd_per_million`
- `cache_read_usd_per_million`
- `cache_write_usd_per_million`
- `search_result_usd`

If a used unit has no current price, that call is **unpriced**, never silently zero-cost.
Changing prices changes future reports without rewriting historical usage units.
Zero-valued counters do not require a rate because no unit was consumed; a missing counter
still means its usage is unknown rather than zero.
Prices apply only when the capability's current provider and model exactly match the
recorded row. Wingman intentionally performs no alias normalization: after a provider or
model switch, historical rows remain unpriced unless an authoritative matching price is
available, rather than being assigned the replacement model's rate.

## Access

`wingman usage` and MCP `usage` report the current workspace. `wingman usage
--all-tenants` and MCP `usage_all_tenants` read the tenant registry and refuse callers who
lack operator privilege. Payer provenance is fixed when the credential ladder resolves the
key: workspace keys are BYOK, explicitly funded shared keys are funded, and the historical
solo-process fallback is ambient. No secret is retained.

Budgets, warnings, and hard enforcement are a separate phase. This ledger supplies the
deterministic measurements they will consume; it does not yet prevent calls.
