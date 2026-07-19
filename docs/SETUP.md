# Graceful Degradation — what works with what you have

Wingman is built so every rung works without the rungs above it: you can
start with nothing but a resume — or nothing at all — and add credentials
and data sources as they arrive, each unlocking exactly one capability.

> Looking for installation, keys, MCP setup, scheduling, or backups?
> That's [`INSTALL.md`](INSTALL.md). For a guided first session ending in
> a POV document, [`WALKTHROUGH.md`](WALKTHROUGH.md).

## The ladder

| You have… | You get… |
|---|---|
| Nothing but the CLI | An initialized, inspectable workspace; `wingman demo` tours the machinery on real public data in an isolated scratch workspace |
| A LinkedIn export | Cited profile (roles/skills/testimonials) + a watchlist seeded from your connections — no keys, no network, emails never stored |
| + your writing | Keyword-searchable evidence corpus (`wingman evidence`) with verbatim cited quotes |
| + feeds fetched | Other people's writing, searchable with attribution; deterministic dossiers and research diffs for their companies |
| + `ANTHROPIC_API_KEY` | The model steps: resume ingestion, cited job-fit briefs, POV cards, company themes, outreach briefs — all evidence-validated |
| + `VOYAGE_API_KEY` (or the keyless `hashed` provider) | Semantic similarity: `people similar`, `people like`, `company similar`, alignment signals in dossiers |

Two structural rules make the ladder honest:

- **Degradation is visible, never silent.** A missing key skips a step and
  says so (`make-it-so` and `overnight` report every step ok / skipped /
  failed); a failed fetch keeps the previous snapshot and says so.
  `wingman doctor` reports what will and won't work without failing you.
- **The keyless floor is real.** Set `provider = "hashed"` under
  `[models.embed_semantic]` in the workspace `models.toml` and similarity
  runs fully local — no network, no credentials. Keyword evidence search
  (SQLite FTS5) never needed a key in the first place.

## Data-source bootstrap tips

**LinkedIn export** — LinkedIn → Settings & Privacy → Data privacy → *Get
a copy of your data*; the fast "specific files" option (positions, skills,
recommendations, connections) arrives by email within minutes.

**Your Substack subscriptions** — Substack's export doesn't include what
you *read*, but every newsletter lands in your inbox from
`<publication>.substack.com`. An email search for `from:substack.com` over
the last ~90 days, aggregated by sender, is a complete list of your active
subscriptions — each sender handle is the publication URL. If you use
Claude with a Gmail connector, ask it to build the `wingman people add`
script from that search.

**Feed-less blogs** (most VC and startup sites) — `wingman people add-feed`
autodiscovers RSS/Atom where it exists and otherwise watches the blog index
page, attributing posts to the organization honestly (RFC-011). Attachment
always requires your confirmation.
