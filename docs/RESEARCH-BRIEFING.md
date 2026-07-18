# Research Briefing: Wingman vs. the Field

**Purpose.** A reality check before the next few versions. Wingman has grown a
wide surface quickly (34 merged PRs, 33 MCP tools). This briefing instructs a
researcher — human or AI agent with live web access — to compare that surface
against the tools that already exist, and to answer three questions per
feature area:

1. **Where is Wingman's surface immature** relative to a mature incumbent?
2. **Where is Wingman duplicating** something the market already does well?
3. **Where should the next few versions go** — build (versus), integrate, or
   deliberately drop?

This is a **versus vs. integration** conversation, not a feature race.
Wingman's premise is that its invariants (below) are the product; a feature
that duplicates an incumbent *without* those invariants adding value is a
candidate for integration or removal.

> Container note: the session that authored this briefing has no general web
> egress. Run the research from an environment that does, then bring the
> findings back as a memo against this document.

---

## 1. The Manifesto (what must survive contact with the market)

From VISION.md — these are the non-negotiables the comparison must be scored
against, not traded away silently:

- **Optimizes opportunity quality, not application volume.** North star:
  *find a small number of exceptional opportunities and make the user the
  obvious candidate for the best of them.*
- **Evidence before assertion.** Every model-proposed claim (profile items,
  POV stances, company themes, outreach talking points) survives only with a
  verbatim quote from a stored document. Zero survivors → nothing stored.
- **Human approval before external action; Wingman never sends anything**
  (RFC-006). Drafting support ends at material the user composes in their own
  voice.
- **Deterministic composition over model claims** where possible: dossiers,
  similarity, research diffs, warmth scores are arithmetic and diffs, not
  generation.
- **No invented familiarity; partial truth over polished fiction.**

**Out of scope for this comparison** (per user direction, 2026-07-18):
local-first data residency and the explicit-egress model are Wingman
implementation choices, not scoring criteria — do not award or dock
comparators for where data lives or how it fetches. Score against the
bullets above only.

**Explicitly not:** an autonomous applicant, a mass-application engine, a
keyword optimizer, a status-tracking CRM, or anything that sends on the
user's behalf.

---

## 2. Feature Inventory (the surface to compare, as of v0.2.x / PR #47)

### A. Profile & corpus
- Resume ingestion: Markdown, text, PDF, DOCX, LaTeX, link-accessible Google
  Docs/Drive URLs; evidence-validated extraction into a canonical cited
  profile (career.md).
- LinkedIn export ingestion (profile + 2,600-connection graph; emails never
  stored).
- Personal corpus (own writing, Substack export) with FTS5 keyword search and
  cited evidence retrieval.

### B. Opportunity assessment
- Job description → requirement extraction with verbatim quotes → met /
  partial / gap / unknown verdicts citing only real profile items.

### C. People intelligence
- Watchlist with feeds (Substack, RSS/Atom, index pages), smart feed
  discovery, org-attributed sources.
- Per-person POV cards: stances with verbatim quotes, dimensioned
  (values/attitude/technical/strategy).
- Embedding similarity: person↔person, person↔corpus, "people like these".
- Warmth score (deterministic: connection, email, shared-company signals).
- News snapshots per person via Google News RSS with relevance filtering.
- Recommendation-graph discovery (who watched Substacks recommend).

### D. Company intelligence
- Company similarity and alignment (embeddings over its people's writing).
- Dossiers: deterministic dated snapshots with [fact]/[inference] labels.
- Approved-source research (RFC-015): user-named pages, one GET each,
  snapshot = text hash + link set, findings = "N new links since date."
- Company themes (RFC-016): quote-validated synthesis over the company's
  document pool with author attribution.

### E. Outreach support
- Purpose-typed briefs (introduction/reconnection/job/advice): talking points
  that must cite a POV stance exactly AND quote the user's corpus verbatim;
  intro bullets for the user's own voice. Never sends.

### F. Delivery & orchestration
- Design-system Letter PDF exports (career portrait, company dossier, person
  2×2 briefing dock; tabbed HTML that prints to the 2×2).
- `make-it-so` one-command daily pipeline; watchlists that cycle it; `sync`.
- Backup/restore (dated tarballs, retention).
- MCP server (33 tools, CLI parity) — stdio and loopback HTTP + capability
  token for remote use (RFC-017).

### G. Known-immature by our own assessment (starting hypotheses, to confirm)
- **Search** is the weakest utility: keyword FTS5 and embedding similarity
  exist separately; there is no unified ranked search across corpus + people
  + companies + news + research. (Already flagged as the favored next build.)
- News relevance filtering is heuristic; common-word companies remain hard.
- Feed discovery covers common cases but fails on JS-only sites.
- Warmth is a 3-signal score; no interaction history (email/calendar) feeds it.
- No UI beyond CLI/MCP/PDF; no scheduling (by design, but worth pressure-testing).
- Interview preparation and action prioritization (both in the vision) are
  essentially unbuilt.

---

## 3. Comparator Set (research these; add any discovered adjacents)

Group by category; per category, at least the named tools plus one open-source
alternative if one exists.

| Category | Tools to research | Overlaps Wingman area |
|---|---|---|
| Job-search CRMs | Teal, Huntr, Simplify, Careerflow | B, E, (A) |
| Resume/ATS optimizers | Jobscan, Rezi, Kickresume | A, B |
| Personal CRM / relationship mgmt | Clay.earth, Dex, folk, Monica (OSS) | C (warmth, watchlist) |
| Sales intelligence / prospecting | Clay.com, Apollo, LinkedIn Sales Navigator, Humantic/Crystal | C, D, E |
| Company signals / market intel | Harmonic, Crunchbase, PitchBook, Google Alerts | D |
| Page-change monitoring | Visualping, Distill.io, changedetection.io (OSS) | D (RFC-015 research) |
| Feed reading / monitoring | Feedly (+Leo), Inoreader, Readwise Reader | C (feeds, news) |
| AI outreach writers | LinkedIn assistants (Waalaxy, Dripify class), general AI writers | E |
| PKM + AI | Obsidian ecosystem, Notion AI, Rewind/Limitless | A, C |
| MCP-native / agentic career tools | whatever now exists — search explicitly | F (the integration surface) |

## 4. Rubric (record per tool, date-stamped)

1. **Evidence discipline** — does it fabricate? Any provenance on claims?
2. **Autonomy posture** — does it send or apply on the user's behalf?
   (Relevant to the never-sends / human-approval invariants; data residency
   and fetch mechanics are out of scope per §1.)
3. **Feature overlap** — which inventory items (A–F) it covers, and *how much
   better/worse* (1–5 maturity vs Wingman's version, with one sentence why).
4. **Interfaces** — API? MCP? Export formats? Could Wingman integrate rather
   than compete?
5. **Price & model** — free/paid tiers; what the paid tier actually gates.
6. **ToS friction** — especially LinkedIn-adjacent functionality.

House style applies to the findings: label claims **[fact]** (vendor docs,
observed behavior, dated) vs **[inference]** (your read). No undated claims.

## 5. The Verdict Buckets (the actual deliverable)

For each Wingman feature area A–F, place it in one bucket and defend it:

- **BUILD (versus):** the invariants make Wingman's version categorically
  different, and the incumbent can't follow (e.g., evidence-validated
  synthesis probably lives here — verify).
- **KEEP THIN (duplicated but strategic):** the market version is better but
  Wingman needs a minimal internal version for the pipeline to compose
  (candidates: news fetching, PDF rendering, page-diff research — test
  whether changedetection.io/Visualping + an import beats RFC-015).
- **INTEGRATE OR DROP:** mature, cheap, and orthogonal to the invariants —
  Wingman should consume it (via MCP, export/import, or documented workflow)
  instead of rebuilding (candidates: resume keyword scoring vs Jobscan;
  application status tracking, which Wingman deliberately lacks — confirm
  users pair Wingman with a tracker rather than wanting one inside).

Then: a **version-shaping memo** — given the buckets, what should v0.3–v0.5
prioritize? Assume search improvement is already queued first. The memo
should name at most three bets per version and say what is explicitly *not*
being built because the market already covers it.

## 6. Method Notes

- Prefer vendor docs, pricing pages, changelogs, and hands-on trials over
  review roundups; note the access date on everything.
- Where a comparator has an API or MCP server, record enough detail (auth
  model, rate limits, data shapes) that an RFC-018-style integration decision
  could be drafted from the memo alone.
- Deliver as `docs/product/competitive-landscape-YYYY-MM-DD.md` in this repo,
  with the comparison matrix as a table and the memo as prose.

---

## Appendix: current backlog context (so the memo lands in sequence)

1. **Search utility improvement** — favored next build (unified ranked search
   across corpus/people/companies/news/research; the memo may sharpen scope).
2. **Hosted deployment tiers** — backlogged. Assessment on record: path A
   (Dockerfile for self-hosting, ~days), path B (managed single-tenant,
   ~weeks of ops), path C (multi-tenant SaaS — DB-per-tenant to preserve the
   application layer; a product pivot, not a task). RFC-018 candidate.
3. **This research** — the memo it produces feeds version shaping for
   v0.3–v0.5.
