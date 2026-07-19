# Code Review Brief — wingman, the unreviewed span (PRs #30–#57)

You are reviewing `dhk/wingman`, a local-first career-intelligence CLI + MCP
server (Python 3.12, uv, Typer, FastMCP, SQLite, Pydantic v2). Machine review
(GitHub Copilot) covered this repo only through **PR #29**; its quota died
mid-day 2026-07-18, and every PR since merged on green CI + the local suite
only. Your job: a comprehensive correctness/security review of everything
that has never had a second pair of eyes.

## Scope

```bash
git log --reverse --oneline 4575c9f..origin/main   # PR #30 → HEAD, ~28 merges
git diff 4575c9f..origin/main --stat
```

`4575c9f` is PR #29's squash-merge — the last reviewed commit. Note two
merges in the span (`458f330` RFC-021 manage, plus a warmth RFC-020 commit)
were authored by a *different* session; review their interaction with the
rest, not just each in isolation.

## How to run everything

```bash
uv sync
uv run pytest -q            # ~302 tests, all must pass
uv run ruff check . && uv run ruff format --check . && uv run mypy src   # strict
export WINGMAN_DATA_DIR=./data && uv run wingman init   # scratch workspace
```

Container/live caveat: outbound network in CI/dev containers is blocked;
live fetch paths were only ever verified to their visible-failure branches.

## The invariants your review must police (docs/VISION.md, docs/RFC.md)

1. **Never sends** (RFC-006): no code path may transmit user content
   anywhere except the explicitly disclosed egress points below.
2. **Enumerable egress**: Anthropic/Voyage model+embedding calls; explicit
   user-invoked HTTPS GETs (feeds RFC-009/011, Google News RFC-014,
   approved research pages RFC-015, follow-probe RFC-018, job-posting-ish
   fetches); search-query embedding (RFC-022). Anything else is a finding.
3. **Evidence before assertion**: every model proposal must pass verbatim
   deterministic validation (`_validate_proposal`, outreach exact-citation,
   assess downgrade-to-unknown). Any bypass is a critical finding.
4. **Visible degradation**: failures reported, never silent (miso steps,
   overnight, search notes, telemetry never-break).
5. **Secrets**: keys only via env or macOS Keychain (RFC-019); never in
   files, logs, telemetry, or argv journals.

## What was built in the span (feature → main files)

| Area | RFC | Files |
|---|---|---|
| Design-system exports (Letter, 2×2 dock, tabs/print CSS) | — | `reporting/export.py` |
| Warmth signals, dimensions, purposes, own-POV | 020(warmth) | `application/outreach.py`, `application/pov.py`, `domain/*` |
| News snapshots + relevance filter | 014 | `application/news.py` |
| Feed discovery (anchors, root paths) | 011 | `application/people.py` |
| make-it-so + watchlists | — | `application/pipeline.py` |
| Backup/restore (tar, retention) | — | `application/backup.py` |
| Approved-source research (page diffs) | 015 | `application/research.py` |
| Company themes | 016 | `application/pov.py` (build_company_pov) |
| Remote MCP (loopback HTTP + token path) | 017 | `mcp_server.py` main/_http_token |
| Follow + overnight + digest + action list | 018 | `application/focus.py` |
| Keychain keys + startup hydration | 019 | `infrastructure/keys.py` |
| People/company manage (rename/delete/fix) | 021 | `application/people.py`, `application/research.py` (external session) |
| Unified search, keyword + semantic | 022 | `application/search.py` |
| Telemetry + transcript harvest | 023 | `infrastructure/telemetry.py`, `application/telemetry_harvest.py`, CLI `run()`, MCP `_instrument_tools` |

## Hotspots I would review first (author's honest list)

1. **`infrastructure/fetch.py` has no private-IP/redirect-target guard** —
   https-only and an https-only redirect handler, but nothing stops
   `https://internal.host` or DNS-rebinding to loopback. All fetch surfaces
   (feeds, research, follow-probe, news) funnel through it. Assess whether
   SSRF matters for a single-user local tool and whether a block list is
   warranted.
2. **MCP HTTP capability token lives in the URL path** (RFC-017) — uvicorn
   access logs and any reverse proxy will log it. Check what actually gets
   logged and whether that undermines "rotation is revocation".
3. **`mcp_server._instrument_tools`** reaches into FastMCP internals
   (`server._tool_manager._tools`, swapping `tool.fn`) — version fragility,
   and check the wrapper preserves signatures/validation for every tool
   shape. Also: telemetry records MCP *results* — confirm no secret can
   transit (keys tools are CLI-only, but audit).
4. **`cli/main.py run()`** — SystemExit/exception handling around Typer;
   double-record risks; `redact_argv` covers `keys set` only — is any other
   argv secret-bearing?
5. **`application/backup.py`** — tar `filter="data"` semantics, restore
   overwrite behavior, prune-by-mtime with the `-N` same-second suffix.
6. **`application/search.py`** — cross-module private imports
   (`_dot/_normalize/_require_one_model`), FTS error paths, the 0.2
   semantic threshold, interleave correctness, `config.reports_dir` digest
   file reads.
7. **`application/focus.py`** — regex-parsing miso step details for action
   generation (fragile contract), `_company_deep`/`_person_deep` action
   correctness, jobish-link heuristic.
8. **`infrastructure/telemetry.py`** — per-event sqlite connections
   (performance under overnight), broad exception swallowing, schema
   executescript on every call, harvest parser robustness on real
   transcripts.
9. **`infrastructure/keys.py`** — secret passed via `security` argv
   (documented trade), `-U` update semantics, 0600 on the token file but
   what about `mcp-http-token` vs keys interactions.
10. **Interaction of RFC-021 manage (rename/delete) with everything keyed
    by `company_key`/person_id** — renames move sources/cards/watchlists;
    check nothing (embeddings? briefs? news?) is orphaned.

## Known accepted trades (don't re-litigate, but verify the guardrails)

- Semantic search egresses the query by default (owner decision, RFC-022).
- Telemetry captures conversation text unredacted (owner opt-in, RFC-023).
- `keys set` value momentarily visible in process list (RFC-019).
- LaTeX flattening is lossy; hashed embeddings are keyword-level.

## Deliverable

Findings ranked by severity, each with `file:line`, a concrete failure
scenario, and a suggested fix. Skip style — ruff/mypy strict already gate.
Flag any place where a test asserts the wrong thing rather than the code
being wrong. End with a verdict: what must be fixed before this codebase
takes real user data beyond its author.
