# Multi-Engine Research Council — Design (proposal, not yet an RFC)

**Status.** Design recorded 2026-07-19. Not built. Graduates to a numbered RFC.md entry (and a ROADMAP.md phase slice) once a v0 slice ships; until then this document is the working design and the thing to revise.

## Motivation

The immediate use the user has in mind is thought-leadership writing and outreach: ask one research question, see what OpenAI, Anthropic, Gemini, and Perplexity each independently say, and use the agreement, the disagreement, and — especially — whatever only one engine surfaced as raw material for finding a distinctive angle. That use case is real, but it is a *consumer* of this capability, not its design driver. The harness itself takes an arbitrary research question in and produces a structured, multi-engine synthesis out; it has no idea whether the question is about a company, a technology trend, or a job search, and it should stay that way. Downstream, a job-search-flavored consumer of the output belongs in Phase 6 (Drafting and Voice) or as a `council` input alongside Phase 3 company research — not inside this design.

## Goals

- Query several independent AI engines with the same prompt and keep every response distinctly attributed.
- Degrade honestly and per-engine when an account or API key is missing, rather than silently shrinking the engine count or faking a response.
- Prevent the synthesis step from quietly favoring whichever engine's "family" is doing the synthesizing.
- Produce a layered report: what everyone agrees on, where they diverge, what only one engine said.
- Support a separate, optional pressure-testing pass (steelman / strawman / adversarial) over the resulting hypotheses.
- Stay simple: reuse Wingman's existing provenance, storage, and reporting conventions rather than inventing new ones.

## Non-goals

- Not an autonomous publisher — it produces a Markdown report for the user to read and write from, nothing is sent anywhere (RFC-006 holds).
- Not a replacement for RFC-004's capability-class routing. RFC-004 answers "which one model should Wingman's own agents use for job X" (a single choice, hidden behind a capability name). This feature answers "what do several *different* named engines each independently say" and the identity of each engine is the point, not an implementation detail to hide. The two systems can share adapter shape but serve opposite goals — one collapses to one provider, the other deliberately keeps N.
- Not a general web-search crawler, and not a new HTTP dependency. The search fallback reuses `wingman.infrastructure.fetch.fetch_url` (RFC-009: stdlib `urllib`, HTTPS-only, no redirect downgrade, bounded retry, visible failures) and `wingman.application.research.extract_page` (RFC-015: stdlib `html.parser`, visible text + https links, nothing more) exactly as they exist today. No new HTTP client, no new HTML-parsing library, no paid search API required to have a fallback.
- Not required to reach all four named engines every run. Two is the honest minimum for "compare"; below that the tool says so instead of pretending.

## Terminology

An **engine** is an externally branded AI system queried as a distinct, attributed source: OpenAI/GPT, Anthropic/Claude, Google/Gemini, Perplexity, and — because the list is not closed — anything else added later behind the same adapter shape. This is deliberately a different word from Wingman's existing **provider** (`src/wingman/providers/`, RFC-004), which is an interchangeable implementation behind a capability class. An engine adapter is a new, small protocol living alongside `providers/base.py`:

```python
class EngineResponse(BaseModel):
    engine: str                      # "openai" | "anthropic" | "gemini" | "perplexity" | ...
    mode: Literal["api", "search_fallback", "skipped"]
    text: str | None
    citations: list[str] = []
    model: str | None = None
    degraded_reason: str | None = None
    latency_ms: int | None = None

class EngineAdapter(Protocol):
    def ask(self, prompt: str) -> EngineResponse: ...
```

Concrete adapters (`OpenAIEngine`, `GeminiEngine`, `PerplexityEngine`) are new work — today only `AnthropicProvider` exists. `AnthropicEngine` can wrap the existing `AnthropicProvider` under `reason_frontier` or a new `council_participant` capability entry in `models.toml`, keeping RFC-004's "model names never appear in code" rule intact.

**Alternative considered for `PerplexityEngine`: Perplexity's own MCP server, not adopted.** Perplexity ships a local, npm-packaged MCP server (stdio transport, exposing `perplexity_search`/`ask`/`research`/`reason`) as an integration path — but it still authenticates with the same `console.perplexity.ai` API key, so it does not touch the credential problem this design exists to handle. As an implementation choice for `PerplexityEngine` specifically, it's a worse fit than a direct REST adapter: it adds Wingman's first Node/npm runtime dependency and a spawned subprocess for what is otherwise one HTTP call; stdio MCP is shaped for a long-lived interactive agent↔tool session, not the one-shot, blind, parallel, provenance-logged calls the council makes; and it moves the actual network call behind a third-party process instead of the thin, Wingman-owned adapter RFC-004 already establishes for every other engine. `PerplexityEngine` calls the Sonar REST API directly, same shape as `AnthropicProvider`. (The MCP server remains a reasonable choice for ad hoc, interactive use of Perplexity directly from a Claude client outside this feature — just not as what the council's internal dispatch calls.)

## Architecture

```text
wingman council ask "<question>"
    |
1. Run-plan build — deterministic, no model call
   for each configured engine: key present? -> mode = api
                                no key, engine is search-shaped? -> mode = search_fallback
                                no key, engine is reasoning-only? -> mode = skipped
    |
2. Dispatch — parallel, blind (no engine sees another's output)
   api engines call their SDK; search_fallback engines make one
   search call (RFC-009 shape); skipped engines produce nothing
    |
3. Store raw responses verbatim, one row per engine, full provenance
   (engine, mode, model, timestamp, latency, degraded_reason) — RFC-005 shape
    |
4. Anonymize + extract claims
   strip engine identity -> "Engine A/B/C/D" (order shuffled per run,
   not alphabetical or call-order); extract_fast pulls discrete,
   quote-grounded claims per anonymized engine (RFC-016's verbatim
   fabrication guard, applied per-claim)
    |
5. Cluster — deterministic, code, not model judgment
   group claims across engines by textual/semantic overlap
   (RFC-010 embeddings if keyword overlap proves too coarse)
   -> agreement clusters / split clusters / singleton clusters
    |
6. Synthesize — model writes prose ONLY from the computed clusters
   chair = critic_independent capability, rotated across engines
   run-to-run rather than pinned to one
    |
7. Re-attach real engine names (deterministic remap, not model-visible)
    |
8. Render Markdown report + raw per-engine appendix
   -> reports/council/<date>-<slug>.md, mirrors reports/digests/ (RFC-018)
```

MCP parity (RFC-008): one tool, `research_council(action=ask|status|review, ...)`, following the single-tool-with-action convention already used for `company_source` and `watchlist`.

## Credential and capability matrix — the asymmetric-access problem

The design must not assume any particular set of engines has an account behind it. The user today has Anthropic, OpenAI, and Gemini keys and is adding Perplexity; that specific starting point is one instance of a general fact — any workspace can have any subset configured, and the subset can change over time in either direction. The rule is per-engine, not per-product, and key presence always wins over degrading: the run-plan build step (architecture step 1) resolves each engine's key exactly the way RFC-019 already resolves `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY` today — an exported environment variable wins, the macOS Keychain (`wingman keys set perplexity`, etc.) only fills the gap — and picks `api` whenever that resolves to something, before ever considering fallback or skip. Adding a key later requires no code change and no flag; the same table just starts landing on a different row for that engine.

| Engine has a key? | Engine's core capability | Behavior |
|---|---|---|
| Yes | any | `mode = api`, called normally |
| No | reasoning/chat only (OpenAI, Anthropic, Gemini) | `mode = skipped` — there is no honest substitute for "what would this specific model say"; reported plainly in the run summary with the exact fix (`wingman keys set openai`) |
| No | search-grounded (Perplexity, and any future engine whose whole differentiator is retrieval) | `mode = search_fallback` — a raw HTTP fetch-and-extract pass (below) approximates the role Perplexity would have played, and the report labels it explicitly as **"Web search (fallback — no Perplexity account configured)"**, never as if it were Perplexity's own model output |

### Search fallback mechanism

The fallback is a plain HTTP client, not a second vendor account. Adding a keyed search API (Brave Search, Bing) as the fallback would just relocate the "what if there's no key" problem one layer down — the whole point of a fallback is that it must not itself require an account. So it is built entirely from what Wingman already has:

1. One `fetch_url()` GET (RFC-009 shape: HTTPS only, honest user-agent, bounded retry, raised failures) against a keyless HTML search endpoint — DuckDuckGo's no-JS HTML endpoint (`https://html.duckduckgo.com/html/?q=...`) is the default, chosen specifically because it needs no API key/account and returns plain server-rendered HTML.
2. `extract_page()` (RFC-015's existing stdlib `html.parser` reducer) turns that response into visible text and its https link set — the same function already used to reduce a company's careers page to a snapshot, pointed at a search-results page instead.
3. Optionally, one more `fetch_url()` GET each against the top 1–2 result links (still one GET per URL, no crawling — the same ceiling RFC-015 already holds itself to) to get real page text instead of a bare snippet, when the snippet alone is too thin to extract a claim from.
4. The result becomes that engine's `EngineResponse(mode="search_fallback", text=..., citations=[urls actually fetched], degraded_reason="no Perplexity account configured")`.
5. A fetch failure (`FetchError` — timeout, block, layout change) is surfaced honestly in the run summary ("web search fallback failed: <reason>") exactly like a failed RFC-015 source fetch — never silently swallowed into an empty section.

**The trade, stated plainly.** Scraping a search-results page is not a documented, versioned API — DuckDuckGo can change markup or rate-limit an unfamiliar user-agent without notice, where a paid search API would give a stable contract instead. This is accepted for the same reason RFC-009 accepts it elsewhere: the fallback exists precisely for the zero-account case, and a scraped HTML page beats no fallback at all. It also stays honestly weaker than a real Perplexity call — links and snippets, not a search-grounded model synthesis — which is exactly why it's labeled as a fallback rather than presented as equivalent.

This also settles a surface-consistency question: the fallback must behave identically whether `council ask` runs from the CLI or as an MCP tool called by an agent that happens to have its own web-search capability (e.g., Claude Code in an interactive session). Delegating the fallback to "whatever search tool the calling agent has" would make the council's behavior depend on which surface invoked it — an overnight cron run and an interactive MCP session would degrade differently for the same missing key. One fetch-and-extract implementation, owned by Wingman, removes that inconsistency.

This reuses RFC-019's existing `keys list` machinery (source: environment / keychain / not set) rather than inventing a second credential story; `wingman council status` (or an extra column on `wingman doctor`) shows the live matrix before a run, so a missing engine is never a surprise discovered mid-run. Skipped and fallback engines are always named in the rendered report's header — "4 engines requested, 2 live (Anthropic, OpenAI), 1 fallback (Perplexity → web search), 1 skipped (Gemini, no key)" — so the reader knows exactly what they're looking at. If fewer than two engines end up live-or-fallback, the tool declines to produce a comparison and says so, rather than rendering a one-source "council."

## Bias mitigation

The tool runs inside a Claude-based harness, synthesizing answers that include Claude's own. Left unguarded, that is exactly the failure mode to worry about — the summarizer subtly preferring its own family's phrasing, ordering, or conclusions. Four structural controls, in order of how much they're relied on:

1. **Anonymize before judging.** The synthesis step never sees "Anthropic said X" — it sees "Engine A said X," with the letter-to-engine mapping shuffled per run. Real names are reattached only after synthesis, by ordinary code.
2. **Cluster before judging.** Agreement, disagreement, and uniqueness are computed by deterministic overlap/clustering over extracted claims (step 5 above), not by asking one model to eyeball four transcripts and declare a verdict. The model's job is confined to extraction (per-claim, quote-grounded) and to writing connective prose from clusters it did not choose the membership of. This is the same "code does the arithmetic, the model does semantic judgment only where it must" split RFC-003 already commits to elsewhere in Wingman.
3. **Rotate the chair.** The prose-writing pass uses whichever engine is assigned `critic_independent` for that run, rotated rather than pinned — so the same engine isn't perpetually the one framing everyone else's answers. (With only 2–4 engines live in a given run this is a soft control, not a strong one; anonymization and clustering carry the real weight.)
4. **Show the raw transcripts.** Every rendered report carries the full, real-name-attributed per-engine responses as an appendix beneath the synthesized layers, so a reader can catch synthesis bias by comparing the summary against source material directly — the same "show your work" posture as the dossier's approved-sources section (RFC-015).

## Output layering

1. **Consensus (happy path).** Executive summary, key points, points of differentiation, hypotheses, and an overall assessment — built only from agreement clusters. This is the layer that answers "if I only had one minute, what do all the engines that ran agree on."
2. **Disagreement.** Where clusters split, each side's position stated plainly with which (real-named) engines hold it. No forced resolution — a genuine split is reported as a split.
3. **Novel / unique per engine.** Singleton clusters — a claim only one engine made — rendered per engine and flagged as a potential angle, not a verdict. This is the layer the user will mine for thought-leadership material, but the harness's job stops at "here is what was unique," not "here is what you should write."
4. **Pressure-test (separate, opt-in pass — roadmap v1, not v0).** `wingman council review <run-id> --hypothesis N` runs three independent, differently-instructed lenses over one hypothesis from layer 1:
   - **Steelman** — construct the strongest possible case for the hypothesis, stronger than any single engine actually argued.
   - **Strawman** — construct the weakest, most reductive version a critic would (unfairly) attack, so the user can see it coming and preempt it.
   - **Adversarial** — actively try to refute the hypothesis: counter-evidence, unstated assumptions, failure modes.

   These three run blind to each other (same anonymize-and-don't-cross-contaminate posture as the main council) and render as three short, clearly labeled sections under the hypothesis — multiple points of view on demand, without turning every council run into a six-engine, nine-pass exercise by default.

## Storage and reporting

New SQLite tables, following existing snapshot conventions (RFC-015): `council_runs` (id, question, timestamp, engines requested/live/fallback/skipped) and `council_responses` (run_id, engine, mode, raw text, model, latency, degraded_reason). Rendered report: `reports/council/<date>-<slug>.md`, structured per the four layers above, with the raw-transcript appendix last. No model call happens at render time — rendering replays what a run already stored, matching the dossier/digest pattern elsewhere in Wingman.

## Phasing

- **v0 (MVP).** Two-plus engines, deterministic credential matrix with honest skip/fallback, anonymized synthesis, layers 1–3. No chair rotation yet (fixed `critic_independent` mapping is fine to start); no semantic clustering yet (keyword/textual overlap is fine to start).
- **v1.** Steelman / strawman / adversarial review pass (layer 4), opt-in per hypothesis.
- **v2.** Chair rotation across runs; upgrade clustering to embeddings (RFC-010) if keyword overlap is measurably too coarse once real runs exist to judge it against.

## Revisit if

- A fifth or sixth engine is added — the adapter protocol should already make this a new small file, not a design change; if it isn't, that's a sign the protocol needs revisiting.
- Perplexity access becomes available — the fallback branch simply stops firing for that engine; no code path should need to change, only the credential matrix's runtime state.
- Keyword-overlap clustering produces visibly wrong agreement/disagreement splits on real runs — that is the trigger for the RFC-010 embeddings upgrade, not doing it up front on spec.
- The job-search/thought-leadership consumer wants tighter integration (e.g., feeding a company's existing Phase 3 dossier in as context) — that is a new, separate design, not a reason to bend this harness's domain-agnostic contract.
- The DuckDuckGo HTML endpoint changes markup or starts blocking the fetch's user-agent often enough that the fallback fails routinely — that is the trigger to consider a keyed search API as an *additional*, opt-in, stronger fallback (never the default, since a fallback that itself requires a key defeats its own purpose).
