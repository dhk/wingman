# Context Snapshot: wingman people + company intelligence sessions
Generated: 2026-07-18T20:50:00Z
Branch: claude/wingman-pr-3-review-wzugg8
Status: review
Description: Backlog complete: backup, approved-source research, company themes, remote MCP

## Objective
Build Wingman's people-discovery and company-intelligence surfaces: watchlist,
public-writing ingestion, similarity, discovery, POV cards, outreach briefs,
company dossiers, research, and remote access — CLI and MCP in lockstep — on
top of the existing profile/corpus/assessment core.

## Where Things Stand
PRs #13–#46 are all merged; the ordered backlog ("MCP last") is done:
- #43 `wingman backup [DEST] [--keep N]` / `restore [--force]`: dated
  tarballs via SQLite online-backup, retention pruning, traversal-safe
  restore. MCP `backup`; restore is CLI-only (documented parity exception).
- #44 RFC-015 approved-source research: `company add-source/sources/
  remove-source/research` — one GET per user-approved URL, snapshots =
  text hash + link set, findings = deterministic diffs ("N new links since
  <date>" is the hiring signal). Dossier "Research" section, 30-day
  staleness flag. MCP `company_source` (action tool) + `company_research`.
- #45 RFC-016 company themes: `company pov "X" [--refresh]` — the
  quote-validated POV pipeline over the company's document pool, author
  attribution riding doc titles, stored under `__company__{key}`. Dossier
  renders the stored card; still zero model calls at dossier time.
  MCP `company_pov` (33 tools).
- #46 RFC-017 remote MCP: `wingman-mcp --http` — streamable HTTP on
  loopback at `/mcp/<token>` (workspace file `mcp-http-token`, 0600);
  `--rotate-token` = revocation; user-managed tunnel (tailscale serve /
  funnel) for claude.ai connectors; non-loopback bind warns. Live-verified
  200-on-path / 404-off-path.
232 tests; checks = pytest, ruff check/format, mypy strict. Copilot review
quota exhausted all day — #43–#46 merged on green CI + local suite per
precedent.

## Technical Decisions (durable ones live in docs/RFC.md — the authoritative record)

### Delivery loop
**Decision**: build → all checks → commit (Claude trailer) → push → PR →
request Copilot review → ~280s background timer → fix-or-decline findings →
merge on green → reset branch from origin/main.
**State**: implemented; user rejected subscribe_pr_activity — poll with timers.

### Provider/egress rules (RFC-009/010/011/014/015/017)
**Decision**: network = explicit user-invoked HTTPS reads (feeds, news RSS,
approved research pages — the user names research URLs, adding is the
approval); embeddings = voyage/voyage-4 with keyless 'hashed' fallback;
remote MCP exposes nothing publicly itself — loopback + capability path,
tunnel is the user's act. RFC-006 always: wingman never sends anything.

### Fabrication guards
**Decision**: every model proposal is validated deterministically — POV
stances and company themes need verbatim quotes from stored docs; outreach
talking points must cite a card stance exactly AND quote the user's corpus
verbatim; zero survivors stores nothing. Dossiers and research findings are
deterministic composition/diffs, no model call.

## User Environment
- All repos live at ~/Documents/dev — wingman is ~/Documents/dev/wingman.
  Never write 'cd ~/wingman' in instructions.
- Workspace: ~/Library/Application Support/wingman (path contains a space —
  always quote it in shell commands).
- Renders PDFs with 'npx md-to-pdf'; wants exports in custom folders (--out).

## Authoritative Inputs
- User's Substack: dhkondata (export zip workflow kept, per user decision)
- Subscriptions: 29 publications mined from Gmail (seed-subscriptions.sh sent
  to user; list also embedded in application/demo.py DEMO_WATCHLIST)
- Investor targets: Scott Brady + Harpinder Singh (both Innovation Endeavors →
  index-page source innovationendeavors.com/insights, org-attributed);
  Marko Klopets, CEO Supersimple → medium.com/@mklopets + supersimple.io/blog
- LinkedIn export: 2,619 connections seeded (emails never stored)

## Warnings
- ⚠️ Container egress proxy blocks substack.com/medium.com/news.google.com
  and general web — live fetch paths verified to their visible-failure
  branch in-container; real verification happens on the user's Mac. The
  HTTP MCP transport WAS live-verified in-container (loopback).
- ⚠️ User's Mac needs: git pull + `uv tool install --reinstall .`, then a
  full Claude Desktop restart to pick up the 4 newest MCP tools (backup,
  company_source, company_research, company_pov).
- ⚠️ Version tag (v0.2.0) must be pushed from the user's machine — container
  push is branch-scoped (tags 403).

## Next Actions
- [ ] User-side: pull + reinstall; try the daily loop
      ('wingman watchlist run investors --out ~/Downloads')
- [ ] User-side: point 'wingman backup' at a synced folder (e.g.
      ~/Dropbox/wingman-backups) and consider a cron/launchd entry
- [ ] User-side: approve research sources for target companies
      ('wingman company add-source "Supersimple" https://... --label careers')
- [ ] User-side remote MCP, when wanted: 'wingman-mcp --http' +
      'tailscale funnel 8787' + claude.ai custom connector with the printed URL
- [ ] Marko outreach: interview material ready (lead: democratize-the-doing/
      centralize-the-meaning symmetry; ask: where does semantic-layer
      authority live when agents query) — user still to compose + send
- [ ] Backlog is empty; next work arrives from field usage

---
*Resume:* load this file in your next session.
