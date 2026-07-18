# Context Snapshot: wingman people-discovery sessions
Generated: 2026-07-18T02:53:36Z
Branch: claude/wingman-pr-3-review-wzugg8
Status: in-progress
Description: People discovery built end to end; PR #22 merging; company similarity next

## Objective
Build Wingman's people-discovery surface: watchlist, public-writing ingestion,
similarity, discovery, and POV cards — CLI and MCP in lockstep — on top of the
existing profile/corpus/assessment core.

## Where Things Stand
PRs #13–#21 are merged; #22 (POV cards) has review fixes pushed and merges on
green CI. The full pipeline works: LinkedIn-seeded watchlist → feeds
(Substack/Medium/RSS/Atom/index pages, RFC-011) → FTS evidence → Voyage
embeddings + cosine similarity (RFC-010) → recommendations-graph discovery →
evidence-validated POV cards. MCP server has full CLI parity (RFC-008 parity
principle). 155+ tests; checks = pytest, ruff check/format, mypy strict.

## Technical Decisions (durable ones live in docs/RFC.md — the authoritative record)

### Delivery loop
**Decision**: build → all checks → commit (Claude trailer) → push → PR →
request Copilot review → ~280s background timer → fix-or-decline findings →
reply + resolve threads → merge on green → reset branch from origin/main.
**State**: implemented; user rejected subscribe_pr_activity — poll with timers.

### Provider/egress rules (see RFC-009/010/011)
**Decision**: network = explicit user-invoked HTTPS reads of public feeds;
embeddings = voyage/voyage-4 default with keyless 'hashed' fallback; egress
only via explicit embed/sync; graceful degradation everywhere (standing user
directive: mark anything useful for others standing up an instance —
docs/SETUP.md is the vehicle).

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
  Claude Desktop full restart for new MCP tools, seed-subscriptions.sh run,
  VOYAGE_API_KEY exported (already defined per user).
- ⚠️ Version tag (v0.2.0) must be pushed from the user's machine — container
  push is branch-scoped (tags 403).

## Next Actions
- [ ] Merge PR #22 on green CI (fixes pushed: stance cap, MCP parse message)
- [ ] Build company similarity slice i: company vectors from person-by-company
      grouping + org-attributed documents; `wingman company similar/like`
      (+ MCP parity tools) — user said go
- [ ] Then: outreach brief (compose POV card + own evidence + similarity into
      draft talking points; drafts only, RFC-006 — never sends)
- [ ] Then: Phase 3 company intelligence (Organization entity, dossiers)
- [ ] User-side payoff loop: seed script → add the 3 investor targets →
      `wingman sync` → similar/discover/pov on real data

---
*Resume:* load this file in your next session.
