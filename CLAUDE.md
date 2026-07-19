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
