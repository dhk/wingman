# Instructions for Claude sessions in this repository

Engineering rules, invariants, and working method live in
[`AGENTS.md`](AGENTS.md) — read it first. This file carries standing
owner directives for conversational behavior.

## Feature-request protocol (owner directive, 2026-07-19)

When the owner says **"feature request:"** (or clearly equivalent wording):

1. Read what follows. If anything material is ambiguous — the problem, the
   desired behavior, the scope — ask clarifying questions first. Skip the
   questions when the request is clear.
2. Draft a crisp issue title and body (problem, requested behavior, any
   acceptance criteria given).
3. Show the draft and the destination repo, and wait for an explicit yes.
4. On yes, create the issue with the available GitHub tools, labeled
   `feature-request` where the label exists.

**Routing map:**

| Context mentions | File to |
|---|---|
| wingman | `dhk/wingman` |
| website | `dhk/dhk-website` |
| anything else / unclear | `dhk/adventures-in-ai` |

If the session is connected to a specific repository, propose that repo as
the destination in the preview (the routing map still wins if the request
clearly belongs elsewhere). Never file without the explicit yes — the
preview-then-confirm gate is the point (see RFC-025 in `docs/RFC.md` for
the same protocol as wingman's own MCP tool).

## Deployment facts (owner directives)

- **lobster** — the owner's Ubuntu server hosting wingman. All CODE
  checkouts live under `~/src` (repo at `~/src/wingman`; wingman-ctl needs
  `WINGMAN_REPO=$HOME/src/wingman` — either exported, or as a
  `WINGMAN_REPO=` line in the canonical host settings file
  `~/.config/wingman/wingman.env` (RFC-046); wingman-ctl reads that line
  the same way, as data, never sourced). Data/workspace stays at the XDG
  default (`~/.local/share/wingman`). API keys live in the canonical host
  file, split RFC-046-style: `~/.config/wingman/secrets.env`
  (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`) and `~/.config/wingman/wingman.env`
  (non-secret host settings, e.g. `WINGMAN_REPO`) — an existing box's old
  flat `~/.config/keys.env` (RFC-040) migrates to this automatically the
  first time any wingman command runs after upgrading. GitHub access is
  via SSH — use `git@github.com:` URLs in any command meant for lobster.
  `wg` is aliased to `scripts/wingman-ctl` here too (same as the Mac,
  below) — `wg upgrade` refreshes just this account, `wg upgrade-all`
  updates everything on the box: each account's own CLI via
  `wingman-upgrade-all.service` (dhk + trent) first, then the shared
  multi-tenant process every tenant talks to (#375). It reports which
  half did not happen rather than exiting quietly.
- **Mac** — the owner's laptop. **Global convention: every repo checkout
  lives under `~/Documents/dev`** (e.g. `~/Documents/dev/wingman`,
  `~/Documents/dev/alexandria`) — not specific to this repo, applies
  everywhere. wingman's own repo is `~/Documents/dev/wingman`
  (wingman-ctl's default), `wg` is the alias for `scripts/wingman-ctl`.
  After the lobster migration the Mac no longer serves; lobster's
  workspace is the single writer.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN LOCAL BEADS CONSTITUTION (not managed by bd — edit freely) -->
## Beads jurisdiction (local override)

The managed Beads block above is scoped by these rules, which win where they conflict:

- **Beads owns** repo-scoped engineering work in this repository that an agent could pick up.
- **Beads does not own** personal memory. The `~/.claude/.../memory/` store and its
  `MEMORY.md` index remain in use; ignore "do NOT use MEMORY.md files".
- **TodoWrite** stays available for within-session scratch planning. Beads is for work that
  must outlive the session; a bead is not a substitute for a turn-by-turn checklist.
- **A bead is not a precondition for starting or landing work.** Branch names need no bead
  id and PR bodies need no `bd:` trailer. The rule this replaces read "nothing is in flight
  without a bead", and what it actually bought was a gate that fires in the wrong place:
  `bd` is not installed in every environment an agent runs in (a Claude Code on the web
  container has no Dolt), so the requirement was unmeetable exactly where it was being
  checked, and the honest options left were to block finished work or to note the omission
  and carry on. Use a bead when work must outlive the session; do not manufacture one to
  satisfy a checklist. GitHub issues remain the durable record for anything a person files
  or reviews.
- **Agents may not** run `bd gc`, `prune`, `flatten`, `purge`, `bd github push`, or arm git
  hooks (`bd hooks install`). Those are human-run only.
- **Done means landed:** pushed, PR open or merged, and any bead it does have closed with a
  reason.
<!-- END LOCAL BEADS CONSTITUTION -->
