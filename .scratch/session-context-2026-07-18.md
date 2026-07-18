# Context Snapshot: wingman people + company intelligence sessions
Generated: 2026-07-18T03:40:00Z
Branch: claude/wingman-pr-3-review-wzugg8
Status: review
Description: Queue complete: similarity, outreach briefs, company dossiers all merged

## Objective
Build Wingman's people-discovery and company-intelligence surfaces: watchlist,
public-writing ingestion, similarity, discovery, POV cards, outreach briefs,
and company dossiers — CLI and MCP in lockstep — on top of the existing
profile/corpus/assessment core.

## Where Things Stand
PRs #13–#26 are all merged; the agreed queue is done. The pipeline now runs
end to end: LinkedIn-seeded watchlist → feeds (Substack/Medium/RSS/Atom/index
pages, RFC-011) → FTS evidence → Voyage embeddings + cosine similarity
(RFC-010, people AND companies) → recommendations-graph discovery →
evidence-validated POV cards → outreach briefs (talking points + draft intro,
never sent, RFC-006) → dated company dossiers (deterministic composition,
RFC-012). MCP server has full CLI parity at 22 tools (RFC-008 parity
principle). 169 tests; checks = pytest, ruff check/format, mypy strict.

## Technical Decisions (durable ones live in docs/RFC.md — the authoritative record)

### Delivery loop
**Decision**: build → all checks → commit (Claude trailer) → push → PR →
request Copilot review → ~280s background timer → fix-or-decline findings →
reply + resolve threads → merge on green → reset branch from origin/main.
**State**: implemented; user rejected subscribe_pr_activity — poll with timers.

### Provider/egress rules (see RFC-009/010/011/012)
**Decision**: network = explicit user-invoked HTTPS reads of public feeds;
embeddings = voyage/voyage-4 default with keyless 'hashed' fallback; egress
only via explicit embed/sync; dossiers and similarity are deterministic
arithmetic over stored data; graceful degradation everywhere (standing user
directive: mark anything useful for others standing up an instance —
docs/SETUP.md is the vehicle).

### Fabrication guards
**Decision**: every model proposal is validated deterministically — POV
stances need verbatim quotes from stored docs; outreach talking points must
cite a card stance exactly AND quote the user's corpus verbatim; zero
survivors stores nothing. Company dossiers skip the model entirely and
compose validated artifacts with [fact]/[inference] labels.

## Authoritative Inputs
- User's Substack: dhkondata (export zip workflow kept, per user decision)
- Subscriptions: 29 publications mined from Gmail (seed-subscriptions.sh sent
  to user; list also embedded in application/demo.py DEMO_WATCHLIST)
- Investor targets: Scott Brady + Harpinder Singh (both Innovation Endeavors →
  index-page source innovationendeavors.com/insights, org-attributed);
  Marko Klopets, CEO Supersimple → medium.com/@mklopets + supersimple.io/blog
- LinkedIn export: 2,619 connections seeded (emails never stored)

## Warnings
- ⚠️ Container egress proxy blocks substack.com/medium.com etc. — live fetch
  paths verified only to their visible-failure branch; real verification
  happens on the user's Mac.
- ⚠️ User's Mac may still need: git pull + `uv tool install --reinstall .`,
  Claude Desktop full restart for new MCP tools (7 new since last restart:
  people_pov, people_brief, company_similar, company_like, company_dossier,
  plus earlier additions), seed-subscriptions.sh run, VOYAGE_API_KEY
  exported (already defined per user).
- ⚠️ Version tag (v0.2.0) must be pushed from the user's machine — container
  push is branch-scoped (tags 403).

## Next Actions
- [ ] User-side payoff loop: pull + reinstall → seed script → add the 3
      investor targets → `wingman sync` → similar/discover/pov/brief/dossier
      on real data
- [ ] Backlog, in no committed order: `wingman backup` command; remote MCP
      for claude.ai web/mobile (needs its own RFC); Phase 3 continuation
      (approved-source research plan — the network-scope decision RFC-012
      deferred); model-synthesized company themes if per-person POV cards
      prove insufficient; v0.2.0 tag from the user's machine

---
*Resume:* load this file in your next session.
