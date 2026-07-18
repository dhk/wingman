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

**Parity principle (added 2026-07).** The MCP surface tracks the CLI: every user-facing capability ships on both surfaces in the same change, as thin wrappers over the same application-layer functions. Adaptations are allowed only where the medium demands them — the CLI's interactive confirm-before-attach for feeds (RFC-011) becomes a two-tool pair over MCP (`feed_discover` reports; `feed_attach` runs only after the user's explicit yes in conversation), and file inputs are accepted as local paths. A CLI command without its MCP counterpart is a review finding, not a style choice.

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

## RFC-010: Semantic similarity via embeddings, stored in SQLite

**Decision.** People similarity ("who thinks about what I think about", "if you like A and B, talk to C") is computed from text embeddings: a new `embed_semantic` capability class in `models.toml` maps to an embeddings provider (default `voyage`/`voyage-4`; `hashed` is a local, network-free fallback). Vectors are unit-length, stored as float32 blobs in `wingman.db`, and compared with a dot product (equal to cosine similarity for unit vectors) in plain Python. A person's vector is the normalized mean of their documents' vectors; the user's vector is the normalized mean of their corpus.

Boundaries that make this admissible:

- **Embedding is explicit data egress.** Document text is sent to the provider only by `wingman embed` — never as a side effect of ingesting, fetching, or searching. The command is the consent.
- **RFC-007 stands.** Evidence retrieval (`wingman evidence`, `wingman people evidence`) stays on deterministic FTS5. Embeddings serve *similarity*, a question keyword search cannot answer at all; they do not replace the retrieval path, whose revisit trigger has not fired.
- **No vector database.** One person's watchlist is hundreds of documents; brute-force dot products over in-memory vectors are instant. The vectors live in the same single SQLite file as everything else (RFC-002).
- **One model at a time.** Vectors from different embedding models are incomparable; similarity refuses to run over mixed-model vectors rather than returning garbage.

**Alternatives.** A vector database (operational weight without a workload that needs approximate search); model-judged similarity (an LLM ranking people per query — expensive, non-deterministic, unauditable as a ranking function; models are used later to *explain* a match with cited quotes, not to compute it); TF-IDF locally (the `hashed` provider is that floor, kept as the offline option).

**Rationale.** Similarity between bodies of writing is exactly what embeddings are for, and the deterministic-controls rule (RFC-003) is preserved: the model call produces data (vectors) once per document, while every ranking computed from them is auditable arithmetic. Voyage AI is the default provider per Anthropic's embeddings guidance; provider choice lives in `models.toml` like every other model name (RFC-004).

**Revisit if.** The watchlist grows to where brute force is slow (tens of thousands of embedded documents — then an ANN index becomes a documented decision), or drafting/assessment later wants semantic *retrieval* (that is RFC-007's own revisit trigger, decided on its own terms).

## RFC-011: General feeds and index-page sources (amends RFC-009)

**Decision.** A person may have multiple typed sources, not just a Substack URL: `rss` (RSS 2.0 or Atom — Substack, Medium, WordPress, Ghost, …) and `index_page` (a blog index on a site with no feed — common for VC firms and startup marketing sites, where the "CxO thinking via company blog" content lives). Sources are attributed to the `person` or, honestly, to an `organization` (a company blog post is presented as "via <org>", never as a fabricated personal byline). Two modest widenings of RFC-009, both bounded and user-configured:

- **Add-time discovery.** `wingman people add-feed` does one GET of the pasted URL, then follows HTML feed autodiscovery (`<link rel="alternate">`) or probes a short enumerable list of conventional feed paths — never a crawl. Discovery **never attaches without confirmation**: the same name can belong to different humans, and a discovered feed can be the wrong one, so the user gets the final say.
- **Index-page fetching.** For a configured `index_page` source, each fetch reads that one page, extracts post links living under the index path, and ingests at most a bounded number of not-yet-seen pages through the ordinary provenance pipeline (content-hashed SourceRecords, inbox archive, dedup).

Everything else RFC-009 established holds unchanged: explicit invocation only, read-only HTTPS to public endpoints (including across redirects), visible failures.

**Alternatives.** Substack-only forever (excludes exactly the investor/executive persona whose firms publish on feed-less Webflow sites); third-party feed-generation services (proxy the user's reading through another party); headless-browser scraping (fails the auditability and ToS bar); auto-attaching discovered feeds (see the wrong-human risk above).

**Rationale.** Public writing is the raw material of relationship intelligence, and it lives behind exactly two shapes: feeds (near-universal on personal-blog platforms) and feed-less marketing sites. Covering both with one confirm-gated command keeps the watchlist honest about attribution and keeps every fetched byte inside the existing provenance rules.

**Revisit if.** Index-page extraction proves too brittle against redesigns (then sitemap.xml `lastmod`-driven fetching becomes the refinement, still within these invariants), or per-author attribution on company blogs is wanted (post-page metadata reading — a further widening that gets its own look).

## RFC-012: Company dossiers as deterministic composition (Phase 3, slice ii)

**Decision.** `wingman company dossier <name>` produces the roadmap's dated company snapshot by composing artifacts the workspace has already validated: watched people at the company (via the Person `company` field), org-attributed sources (RFC-011), POV-card stances rendered with explicit labels (`[inference]` statement backed by a verbatim `[fact]` quote — RFC-005's fact/inference/hypothesis discipline made visible), embedding-based signals when available (alignment with the user's corpus, nearest companies), a staleness warning when the newest attributable document is older than 90 days, and an explicit Gaps section naming what is missing and the command that fills it. The dossier is deterministic — no model call, no network — and is written as dated Markdown under `reports/companies/`, the artifact convention from DESIGN.md.

Two deferrals, on the demonstrated-need rule:

- **No stored Organization entity yet.** Companies are derived at read time from Person records and document attribution. A first-class Organization row earns its place when there is user-supplied metadata to hang on it (aliases, notes, approved research sources) — not before.
- **No model-synthesized "company themes" yet.** The dossier's claims are inherited from per-person POV cards, which were already validated quote-by-quote. A company-level synthesis step (many documents → themes) is a further model surface that gets added when per-person stances prove insufficient.

**Alternatives.** Model-written dossiers (unauditable claims exactly where the roadmap demands labeled ones); live research at dossier time (Phase 3's "research plan / approved-source handling" — a bigger decision about network scope that deserves its own entry when built); storing dossiers in SQLite (they are cheap, derived, and dated — the filesystem under `reports/` is the record).

**Rationale.** Phase 3's usable outcome is "generate a dated company dossier". Every ingredient already existed with provenance attached; composing them deterministically ships the outcome without adding a model surface or a network path, and the Gaps section turns missing data into the next command to run — graceful degradation as a feature.

**Revisit if.** Per-company research sources arrive (then approved-source handling and refresh become the durable design), or users want cross-person synthesis beyond what POV cards carry.

## RFC-013: Resume import formats — PDF, DOCX, LaTeX, Google URLs

**Decision.** `wingman ingest` accepts, beyond Markdown/plain text: **PDF** (via `pypdf`, the project's one extraction dependency — PDF genuinely cannot be parsed with the standard library), **DOCX** (standard library only: the zip's `word/document.xml` carries every visible text run), **LaTeX** (a deterministic flattener — comments and command tokens stripped, braced argument text kept, `\item` → bullet), and `--url` for **link-accessible Google Docs/Drive documents** (Docs via its `export?format=txt` endpoint, Drive file links fetched and sniffed as PDF/DOCX/text; MCP counterpart `ingest_resume_url`). Extraction is deterministic and happens before hashing, so the stored text, the model's input, and every verbatim-evidence check see the same bytes. URL fetches are the existing RFC-009 shape — explicit, user-invoked, HTTPS-only — and the fetched artifact is archived to the inbox before extraction, keeping the original file as the provenance record. A permission-walled Google document returns HTML and fails visibly with sharing guidance.

**LaTeX difficulty assessment** (evaluated before building): a faithful conversion requires a TeX engine or pandoc — a heavy dependency for one input format, rejected. A deterministic flattener is ~40 lines with no dependency and preserves the visible words of moderncv/altacv-style resumes, which is sufficient because the downstream consumer is an extraction model reading flattened text, and its output is still evidence-validated verbatim against that same flattened text. Limitation accepted and documented: exotic macros degrade to their argument text; math and layout are not reproduced; an empty flatten fails visibly.

**Alternatives.** OCR for scanned PDFs (a model/system dependency for a rare case — the failure message tells the user to export text instead); Google API/OAuth integration (credentialed access contradicts the no-credentials fetch invariant; the export endpoint works for exactly the documents the user has chosen to make link-accessible); accepting arbitrary URLs (scope-creeps the fetch surface — non-Google URLs get a visible "download it and ingest the file" pointer).

**Rationale.** Resumes live in PDF and DOCX far more often than Markdown; requiring manual conversion was the biggest friction in Phase 1's front door. Each format gets the cheapest honest extraction that the evidence-validation gate can stand behind.

**Revisit if.** Scanned-PDF resumes turn out to be common (OCR becomes its own decision), or users need non-Google document hosts (the URL allowlist then becomes a configured, documented surface).

## RFC-014: Recent-news snapshots via a public news RSS endpoint

**Decision.** `wingman people news <name>` fetches recent news mentioning a person or their company from Google News's public RSS search endpoint — one explicit, user-invoked, read-only HTTPS GET of an unauthenticated public feed, squarely inside the RFC-009 invariants. Results are stored as a replace-on-refresh snapshot (a new `news_items` table, capped at 8 items), rendered as the fourth quadrant of the person export's 2x2 briefing dock with the fetch date and a staleness flag past 7 days. News items are never mixed into ExternalDocuments: a journalist's article about a person is not that person's writing, and keeping the stores separate keeps POV cards quoting only the person's own words.

**The privacy trade, stated plainly.** The query — the person's name and their company — is sent to the news provider when the user runs the command. That is the entire egress and it is disclosed in the command's help text; the command never runs implicitly (not part of `wingman sync` for now, precisely so the disclosure stays attached to the action).

**Alternatives.** News APIs with keys (credentialed egress for a commodity feed); scraping search result pages (ToS-hostile, brittle); asking the user to paste links (the manual fallback still works — any judgment about an article belongs to the user reading it either way).

**Rationale.** Outreach benefits from knowing what just happened at the person's company; a public RSS search is the cheapest honest source, and snapshot-replace semantics keep it a briefing input rather than a growing archive of third-party content.

**Revisit if.** The provider retires the endpoint (Bing News RSS is the drop-in shape), or users want news in `wingman sync` (then the disclosure has to move with it).

## RFC-015: Company research over user-approved sources (Phase 3, slice iii)

**Decision.** Per-company research sources, added explicitly by the user and fetched only on explicit command — the network-scope decision RFC-012 deferred, now resolved. `wingman company add-source "X" <url> [--label]` records an approved HTTPS source for a company; **the act of adding is the approval** — no confirmation theater at fetch time. `wingman company sources "X"` lists them (`remove-source` withdraws one, snapshot included); `wingman company research "X"` makes one read-only GET per approved source (RFC-009 shape: explicit invocation, https-only, visible failures, one page per URL — no crawling), reduces each page deterministically to a snapshot (SHA-256 of visible text + the page's set of https links), stores it replace-on-refresh keyed `(company_key, url)`, and reports the diff against the previous snapshot: **new links are the hiring/announcement signal**, a changed text hash the weaker "page changed" signal, and "unchanged since \<date\>" an honest answer. A failed source keeps its previous snapshot and is reported, never fatal. The dossier gains a "Research (approved sources)" section rendering stored snapshots with a 30-day staleness flag — no fetch at dossier time, preserving RFC-012's determinism. MCP parity (RFC-008): `company_source(action=add|remove|list, ...)` following the watchlist single-tool convention, plus `company_research`.

**Alternatives.** Fetching at dossier time (couples an artifact to the network and breaks dossier determinism); crawling beyond the approved page (scope creep — the user approved one URL, one GET honors exactly that); a model summarizing the fetched page (unauditable claims where a link-set diff is deterministic and explainable); a first-class Organization entity (still deferred — a `(company_key, url)` table is the metadata that actually arrived; aliases/notes remain the demonstrated-need trigger).

**Rationale.** Careers pages and newsrooms are where hiring signals appear first, and which pages to trust is a judgment only the user can make. Naming the exact URLs turns "did anything change at X" into a deterministic diff of user-chosen pages instead of a crawl, keeping the egress surface enumerable: every URL Wingman will ever fetch for a company is visible in `company sources`.

**Revisit if.** Users want research inside `sync`/`make-it-so` (the fetch consent and cost move with it — an explicit flag, not a default), or want change history rather than a latest-snapshot diff (snapshot generations become a real archive), or want alerting (that is RFC-006 territory: Wingman still never sends anything).

## RFC-016: Model-synthesized company themes (resolves an RFC-012 deferral)

**Decision.** `wingman company pov "X" [--refresh]` synthesizes company-level themes from the company's document pool — the same pool the dossier reads: writing by watched people at the company plus org-attributed documents. It is the existing POV-card machinery pointed at a company: one synthesize_balanced call, every proposed theme kept **only if its quote appears verbatim in a stored document** (the fabrication guard is unchanged — a company theme is still only as real as the sentence it quotes). Author attribution rides each document's title into the prompt and onto each surviving stance's `doc_title`, so a theme always names who actually said it. The card is stored in `pov_cards` under the reserved id `__company__{company_key}` (the `__corpus__` pattern, extended); a stored card renders without any model call, `--refresh` rebuilds. The dossier renders the stored card deterministically in a "Company themes (synthesized …)" section — dossier generation itself still makes no model call — and names the command in Gaps when documents exist but no card does. MCP parity: `company_pov`.

**Alternatives.** Synthesizing at dossier time (adds a model call to a deterministic artifact); a separate themes table (the card shape — stances, quotes, topics, provenance — already fits, and one table means one validation path); skipping per-person attribution (loses exactly the information that makes a company card more than a merged person card).

**Rationale.** RFC-012 deferred company-level synthesis until per-person stances proved insufficient; the demonstrated need arrived — a dossier of several people's cards answers "what does each person argue," not "what does this company collectively argue about." Reusing the validated card pipeline ships the answer without a new model surface: same prompt contract, same verbatim gate, same storage.

**Revisit if.** Company pools outgrow the 20-document prompt budget (then selection within the pool — per-author quotas, recency windows — becomes its own decision), or themes need to diff over time (cards would need versioning, which `pov_cards`' replace-on-save deliberately avoids).

## RFC-017: Remote MCP — a local HTTP listener behind a user-managed tunnel

**Decision.** `wingman-mcp --http [--port N]` serves the existing MCP tool surface over streamable HTTP, **bound to 127.0.0.1 by default**, at a capability path `/mcp/<token>` where the token is generated once into the workspace (`mcp-http-token`, mode 0600) and rotated on demand with `--rotate-token` — rotation is revocation. Reaching it from claude.ai web/mobile is the user's tunnel choice, documented but not embedded: `tailscale serve` for tailnet-only access from their own devices, `tailscale funnel` (or an equivalent authenticated tunnel) when claude.ai's connector infrastructure must reach it. Wingman itself never opens a public listener: binding a non-loopback address requires an explicit `--host` and prints a warning naming what is being exposed. The stdio transport and the tool surface are unchanged — one server, two transports, zero new tools.

**The exposure trade, stated plainly.** The MCP surface can read the whole workspace and trigger fetches and model calls, so exposing it is exposing the workspace. The mitigations are layered and honest about their limits: loopback-only default (exposure requires a second, deliberate act — running a tunnel); an unguessable capability path (an attacker who cannot read the URL cannot speak to the server — but anyone who obtains the URL can, so treat it like a password and rotate on any doubt); TLS from the tunnel (Tailscale terminates HTTPS; wingman serves plain HTTP on loopback and never pretends otherwise). RFC-006 still holds at the bottom: nothing the remote surface can invoke sends anything on the user's behalf.

**Alternatives.** A hosted relay (moves the workspace — or a credential to it — off the user's machine; contradicts local-first at the root); OAuth with dynamic client registration (the protocol-blessed connector auth, but a full authorization-server implementation inside a local CLI tool is a liability out of proportion to a single-user server; the capability URL delivers single-user access control with rotation today); binding 0.0.0.0 with the token as the only guard (LAN exposure by default is exactly the surprise a local-first tool must not spring).

**Rationale.** The workspace lives on the user's machine and should stay there; what travels is the conversation. A loopback listener plus the user's own tunnel keeps the trust decision — and the off-switch — in the user's hands, reuses battle-tested infrastructure for TLS and reachability, and adds no third party to the data path that the user did not choose themselves.

**Revisit if.** claude.ai connectors require OAuth for custom MCP servers (then dynamic client registration becomes the cost of entry and gets its own design), or multiple clients need distinct revocable grants (per-client tokens), or the MCP SDK ships built-in auth worth adopting.

## RFC-018: Follow a company — assembled focus and the consented overnight deep-refresh

**Decision.** Two commands turn "I care about this company" into standing intelligence. `wingman company follow "X" [--url https://domain]` assembles the focus in one act: the company and every known person there **with writing attached** are enrolled on the reserved `overnight` watchlist; when the user names the domain, its conventional pages (`/careers`, `/jobs`, `/blog`, `/news`, `/newsroom`, `/about`) are probed with one GET each and the live ones become approved research sources (RFC-015's "adding is the approval" extends to this: the user named the domain in an explicit invocation, and the report lists exactly what was approved, each removable). Known people *without* writing are surfaced as suggestions, never silently enrolled — enrolling someone means their name will be sent to a news provider on every run, so it stays tied to having chosen to watch their writing. `wingman overnight` is the explicit **spend-the-tokens** command: every enrolled target gets the deep pipeline — research diffs, feed fetches, news, embeddings, fresh POV cards, company themes, briefs, exports — and the run ends in a dated digest under `reports/digests/`: what changed, what failed (honestly, per target), and deterministic follow-next suggestions (similar companies by embedding, themes to watch from company cards). MCP parity: `company_follow`, `overnight`.

**The consent amendment, stated plainly.** RFC-009's rule has been "the command is the consent," per invocation. Overnight runs stretch that: the user will schedule this command (cron/launchd — documented, not installed by Wingman) and sleep through its execution. The amended rule: **enrollment is standing consent** — durable, enumerable (`wingman watchlist show overnight` lists every target whose feeds, news queries, and research pages will be touched), and revocable (`wingman watchlist remove`). The schedule itself is still the user's act on their own machine; Wingman ships no daemon, and RFC-006 is untouched — a run reads and writes locally, and sends nothing on the user's behalf.

**Alternatives.** A resident scheduler inside Wingman (a daemon is an always-on consent the user cannot see; launchd/cron is inspectable and theirs); enrolling every known connection at the company (turns one follow into dozens of names sent to a news provider nightly — suggestion-only is the honest default); crawling the domain for sources (six conventional paths, one GET each, is the reasonable ceiling — RFC-015 stays one-page-one-GET); a model deciding what "changed" overnight (the digest composes deterministic outputs — diffs, counts, step results; the model surfaces stay inside the already-validated card/brief pipelines).

**Rationale.** The user's stated loop is: name a company, wake up to answers. Everything the deep pipeline runs already existed as consented single commands; this RFC adds only assembly (follow) and sequencing plus a digest (overnight), so the cheapest new surface delivers the loop while every invariant keeps its existing enforcement point. Answers-on-demand then falls to the search improvement already queued ahead of this work.

**Revisit if.** Users want per-target depth control (research-only vs full model refresh — a per-member flag on the enrollment record), or digests need trend memory across runs (digest diffing becomes its own store), or the suggestion surface should reach beyond the workspace (that is a new egress decision, not an extension of this one).

## RFC-019: API keys in the macOS Keychain, hydrated at startup

**Decision.** `wingman keys set anthropic|voyage` stores an API key in the macOS Keychain via the system `security` CLI (service = the environment variable name, account = `wingman`, so items are recognizable in Keychain Access); `keys list` shows each key's current *source* — environment, keychain, or not set — never its value; `keys unset` removes one. At startup, both entrypoints (the CLI's root callback and `wingman-mcp`'s main) hydrate any known key that is absent from the environment. **Precedence: an exported environment variable always wins; the Keychain only fills gaps** — existing shell exports, launchd `EnvironmentVariables`, and CI secrets behave exactly as before. On systems without `security` (Linux, CI, containers) hydration is a silent no-op and `keys set` fails with a plain explanation. The payoff: `claude_desktop_config.json` needs no `env` block, launchd plists need no embedded secrets, and no wrapper scripts exist to get wrong.

**Parity exception (RFC-008), deliberate:** there is no MCP `keys` tool. Setting a key over MCP would route the secret through the model conversation — transcript, context window, provider logs — which is exactly where a credential must not travel. Key management is a terminal act.

**Alternatives.** A secrets file in the workspace (plaintext at rest — the thing being eliminated); an OS-agnostic keyring dependency (a real library, but wingman's one deployment target today is macOS and `security` is already on every Mac; the dependency earns its place when a second platform demonstrates need); passing the secret to `security` via stdin instead of argv (the `-w <value>` argv form is momentarily visible in the process list; accepted — standard practice for `security`, single-user machine, and the alternative is interactive-only).

**Rationale.** Keys kept appearing in exactly the wrong places: pasted into `claude_desktop_config.json`, embedded in launchd plists, hand-rolled wrapper scripts with wrong paths. One command puts the credential where macOS already guards secrets, and startup hydration makes every consumer — CLI, MCP stdio, MCP HTTP, scheduled overnight runs — just work without any file carrying a secret.

**Revisit if.** A second platform arrives (the `keyring` library becomes the honest answer), or more providers mean the known-key map should live in `models.toml` rather than code, or Keychain ACL prompts interfere with unattended launchd runs (then the run's first manual invocation with "Always Allow" becomes a documented setup step — it already is in the README).
