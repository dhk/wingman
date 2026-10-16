# Model usage and spend tracking

Wingman records one content-free usage row after each successful model or embedding call.
The row belongs to that workspace's SQLite database, so ordinary users see only their own
usage. Recording is best-effort: a ledger write failure is logged and never changes the
model result. Writes use a lightweight ledger-only connection with a 250 ms lock
timeout; they never run general workspace migrations. A write that hits a lock
(commonly this process's own open transaction or unfinished read when the model
call returns) is not dropped: the row is kept in memory, logged as deferred, and
written by the next recording, when a `Storage` closes, or at interpreter exit.
Rows still unwritable at exit, or lost to a crash, are logged as lost. A failed
write can therefore still omit a paid call, so every report warns that totals are
a lower bound. Failed provider
calls create no row. This is not yet safe for budget enforcement.

Each row records the time, capability, provider, model, payer (`byok`, `funded`,
`ambient`, or `offline` for providers that never meter), raw input/output/cache token counts, OpenRouter search-result count, latency,
and a nullable caller_name reserved for future wiring (router call sites currently
leave it unset). Prompts, responses, tool arguments, and retrieved content are
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
An absent `models.toml` means no prices are configured. An unreadable or malformed file is
reported distinctly while raw usage remains available; it is never described as missing
prices.
Negative and non-finite (`inf`/`nan`) rates are invalid and leave affected calls unpriced.
Prices apply only when the capability's current provider and model exactly match the
recorded row. Wingman intentionally performs no alias normalization: after a provider or
model switch, historical rows remain unpriced unless an authoritative matching price is
available, rather than being assigned the replacement model's rate. An operator
may explicitly declare `pricing_models = ["returned-dated-id"]` alongside the
configured model to apply that table's rates to those exact returned IDs; Wingman
never guesses alias equivalence. The output explains that missing counters, model
mismatches and invalid rates can also leave a row unpriced.

## Access

`wingman usage` and MCP `usage` report the current workspace. `wingman usage
--all-tenants` and MCP `usage_all_tenants` read the tenant registry and refuse callers who
lack operator privilege. Payer provenance is fixed when the credential ladder resolves the
key: workspace keys are BYOK, explicitly funded shared keys are funded, and the historical
solo-process fallback is ambient. No secret is retained.

Budgets, warnings, and hard enforcement are a separate phase. This ledger supplies the
deterministic measurements they will consume; it does not yet prevent calls.

## Reports and implementation limits (RFC-084)

Reports group by UTC day, payer, capability and provider/model and print all five
raw counters. If any selected row omits a counter, its aggregate is unknown with
the known subtotal shown. The header counts billable calls only and discloses
newest-N truncation against the full billable row count; the newest-N window
never includes offline rows. Operator reports rank known funded cost in the selected
rows, marking truncation or unpriced funded rows incomplete; this is not a full
invoice or an all-time spending claim.

All report database opens use SQLite `mode=ro`, including operator rollups. An
older database with no ledger is reported as "ledger not yet created", and is
never migrated by reporting. The privileged MCP rollup is an explicit read-only
accounting exception, documented in RFC-068 and the parity ledger.

Voyage's per-response `usage.total_tokens` supplies input_tokens. Missing, negative,
boolean or noninteger values remain unknown; input length is never estimated.
The adapter returns vectors plus usage to the wrapper for that same call, avoiding
shared last-response state. Contract reference: [Voyage embeddings documentation](https://docs.voyageai.com/docs/embeddings).
No paid call was made to validate this field; mocked documented responses cover it.

Offline providers (owner decision 2026-10-16, option b). A provider class that
never reaches a metered API declares `offline = True` (today `RecordedProvider`
and `HashedEmbeddingProvider`; test fakes do the same). The recording wrapper reads
that property, never the provider's name, and stamps the row `payer = offline`;
passing `offline` for a provider that does not declare it is an error. Reports
show all offline calls as one separate `no cost (offline): N call(s)` line with no
payer, never as unpriced, and exclude them from the billable header count, the
newest-N window, every billable total and the funded ranking. They must not
become paid-budget usage in a later enforcement phase. A ledger created earlier on
this branch with a payer CHECK lacking `offline` is rebuilt (rows copied) the next
time `Storage` opens it; the CHECK and index are defined once and shared by both
write paths.

A registered tenant with no workspace database yet is reported as "no usage yet"
and ranks at $0, not as a read failure. An own-workspace report on a corrupt or
locked database prints a clean error (CLI exit 1, MCP text) instead of a traceback.

Follow-up (not tracked): key-validation pings call Voyage directly (`keys.py`,
`webui.py`) and are not recorded; each is about one token.

Phase 2 requires durable accounting of omitted writes before a ledger-backed cap
can be safe. No budget configuration, warning or refusal is implemented here;
provider-side spend limits remain the backstop (RFC-080).
