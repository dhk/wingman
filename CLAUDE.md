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
  checkouts live under `~/src` (repo at `~/src/wingman`; set
  `WINGMAN_REPO=$HOME/src/wingman` for wingman-ctl — a separate,
  as-yet-unmerged PR would let wingman-ctl read this from the canonical
  host settings file below instead; until that lands, export it).
  Data/workspace stays at the XDG default (`~/.local/share/wingman`). API
  keys live in the canonical host file, split RFC-046-style:
  `~/.config/wingman/secrets.env` (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`)
  and `~/.config/wingman/wingman.env` (non-secret host settings) — an
  existing box's old flat `~/.config/keys.env` (RFC-040) migrates to this
  automatically the first time any wingman command runs after upgrading.
  GitHub access is via SSH — use `git@github.com:` URLs in any command
  meant for lobster.
- **Mac** — the owner's laptop. **Global convention: every repo checkout
  lives under `~/Documents/dev`** (e.g. `~/Documents/dev/wingman`,
  `~/Documents/dev/alexandria`) — not specific to this repo, applies
  everywhere. wingman's own repo is `~/Documents/dev/wingman`
  (wingman-ctl's default), `wg` is the alias for `scripts/wingman-ctl`.
  After the lobster migration the Mac no longer serves; lobster's
  workspace is the single writer.
