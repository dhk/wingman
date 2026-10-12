# Agent loop: one agent writes, a different one reviews, a human merges

Status: **pilot, not yet run.** The workflows lint clean with `actionlint`, and the
gate, outcome and verdict logic were tested locally, but no end-to-end run has
happened. Treat the first runs as the test. The decision record is RFC-083 in
[`RFC.md`](RFC.md).

## What it does

Label an issue to pick who writes the code:

| Label | Author (writes and repairs) | Reviewers, tried in order |
|---|---|---|
| `agent:codex` | Codex | Copilot → Claude |
| `agent:claude` | Claude | Copilot → Codex |

One workflow run then:

1. **Author.** The author implements the issue and the loop opens a **draft** PR.
2. **Review.** The loop runs the CI checks (ruff, mypy, pytest). It then tries the
   reviewers in order; the first to return a valid verdict wins. Findings are
   marked `blocking` or `nit`, and the verdict is posted as a PR comment that
   names the reviewer.
3. **Repair.** If anything is blocking, the author fixes **only those findings**.
4. The review → repair cycle repeats, **3 reviews and 2 repairs at most**.
5. **Finalize.** The PR gets `ready-for-merge` (and leaves draft) or `needs-human`,
   with a comment saying why.

**You merge.** Nothing in the loop approves, enables auto-merge, or merges.

Files: [`agent-loop.yml`](../.github/workflows/agent-loop.yml) (entry and state
machine), [`agent-loop-author.yml`](../.github/workflows/agent-loop-author.yml),
[`agent-loop-review.yml`](../.github/workflows/agent-loop-review.yml), and the
verdict validator in [`.github/actions/agent-loop-verdict/`](../.github/actions/agent-loop-verdict/).

## Spending Copilot first

Copilot is always the first reviewer. Its usage comes out of your plan's monthly
allowance of AI credits, which is already paid for and does not roll over
(it resets at 00:00 UTC on the 1st). When the allowance runs out, Copilot keeps
working only if you set a budget for **additional usage**. With no such budget,
its requests fail, and the loop **falls through to the next reviewer**. So:

- Leave Copilot's additional-usage budget at $0 to get "Copilot until it runs
  out, then the API keys". Set a budget only if you would rather pay Copilot
  overage than OpenAI or Anthropic.
- This needs no quota API and no polling. A failed or unusable Copilot answer
  simply moves the round to the next reviewer.
- `--max-ai-credits` caps each Copilot response (default 500 credits, about $5).
  This is a soft cap set by the variable `AGENT_LOOP_COPILOT_MAX_CREDITS`.

How it is billed. In a personally owned repository, the workflow's own
`GITHUB_TOKEN` with `copilot-requests: write` bills **the repository owner's
Copilot seat**, so no extra token is needed. Sources, read 2026-10-12 from the
[github/docs](https://github.com/github/docs) source: *Copilot CLI in GitHub
Actions* (concepts), *Using Copilot CLI in GitHub Actions with GITHUB_TOKEN*, and
*Copilot billing for individuals*.

**Why not Copilot's own PR review.** Copilot code review can be requested through
the API, but by default it leaves a "Comment" review with no blocking/nit
distinction. Its timing is outside the run, and per GitHub's docs it "may repeat
previous comments" on re-review. That reintroduces nit ping-pong and a handoff
that can stall. The loop runs the Copilot **CLI** inside the review job instead,
with the same prompt and verdict shape as the other reviewers.

## Why it is built this way

| Concern | Design |
|---|---|
| Lost handoffs | GitHub starts no new workflow run for events created with `GITHUB_TOKEN` ([docs](https://docs.github.com/en/actions/concepts/security/github_token)). So rounds are **jobs in one run**, not a chain of events. |
| Runaway loops | The round cap is the **shape of the job graph**. There is no counter a model could reset. |
| Model-driven control flow | Every transition is deterministic (AGENTS.md: no model for status transitions or approval decisions). Reviewers only propose; a failing check is blocking whatever they say. |
| Self-review | The author is never among that round's reviewers. |
| Unusable reviewer output | Every answer goes through the same validator ([`validate.py`](../.github/actions/agent-loop-verdict/validate.py)). An invalid answer counts as that reviewer failing. If no reviewer produces a valid answer, the PR gets `needs-human`. |
| Nit ping-pong | Only `blocking` findings reach the repair prompt. |
| Repair becoming a rewrite | Deterministic line cap: 300 changed lines per repair, 1,500 for authoring. Over the cap, the patch is refused and the PR gets `needs-human`. |
| The loop editing itself | Any patch touching `.github/` or `.agent-loop/` is refused (source and destination paths are both checked). The token is never granted the Workflows permission. |
| Agents holding credentials | Authors run **with no write token**; a fresh runner applies their patch. Codex runs under `permission-profile` (`:workspace` to write, `:read-only` to review). Claude gets an explicit tool allowlist and `--max-turns`. Copilot gets read-only `git` commands, file writes denied, `--max-ai-credits`. |
| Strangers triggering spend (public repo) | Only actors in `AGENT_LOOP_ALLOWED_ACTORS` can start a run, and the trigger label is removed on start. Issue text is passed to every model as untrusted data. |
| Concurrent runs | `concurrency` per issue, never cancelling a run in progress. |
| Wall-clock | `timeout-minutes` on every job. |

## Setup (one time)

1. **Copilot:** an individual Copilot plan on the repository owner's account.
   Nothing else is needed; the workflow token carries `copilot-requests: write`.
2. **Secrets** (Settings → Secrets and variables → Actions):
   - `OPENAI_API_KEY`, used by Codex (API billing, not a ChatGPT plan).
   - `ANTHROPIC_API_KEY`, used by Claude.
   - `AGENT_LOOP_TOKEN`: a fine-grained personal access token for **this
     repository only**, with Contents, Pull requests and Issues set to read/write,
     plus Metadata read. **Do not grant Workflows.** This token makes the loop's
     pushes and PR start `ci.yml`.
3. **Variables:**
   - `AGENT_LOOP_ENABLED` = `true`. This is the kill switch: anything else stops new runs.
   - `AGENT_LOOP_ALLOWED_ACTORS` = `dhk` (comma-separated; defaults to the repo owner).
   - Optional: `AGENT_LOOP_REVIEWERS` (default `copilot,codex,claude`; it filters
     reviewers, it does not reorder them), `AGENT_LOOP_COPILOT_MODEL` (default
     `auto`), `AGENT_LOOP_COPILOT_MAX_CREDITS` (500), `AGENT_LOOP_CODEX_MODEL`,
     `AGENT_LOOP_MAX_REPAIR_LINES` (300), `AGENT_LOOP_MAX_AUTHOR_LINES` (1500).
4. **Spend ceilings at the providers.** The workflow caps rounds, turns and time,
   not dollars. Set a monthly budget on the OpenAI project and a spend limit on
   the Anthropic workspace. Leave Copilot's additional-usage budget at $0 unless
   you mean to pay overage.
5. Create the `agent:codex` and `agent:claude` labels.

## Using it

- **Start:** add `agent:codex` or `agent:claude` to an issue, or run *Agent loop*
  from the Actions tab and pick the author.
- **Stop everything:** set `AGENT_LOOP_ENABLED` to anything but `true`. To stop a
  run already in progress, cancel it in the Actions tab.
- **Re-run:** delete the `agent/issue-N` branch (or finish its PR), then label again.

## Known limits and open questions

- **Never run end to end.** The first runs are the test.
- **Copilot's failure mode when the allowance runs out** is inferred from the
  billing docs ("you can continue working by setting a budget"). Whether the CLI
  exits non-zero or prints an error is not documented. Either way the validator
  marks the answer unusable and the next reviewer runs, but check the first
  exhausted-month run.
- **Copilot's file-reading tools** are not explicitly allowed. It is told the
  inputs' paths, and read-only `git show`/`git diff` are allowed as a fallback.
- **`actionlint` 1.7.12 reports two false errors.** It doesn't know
  `copilot-requests` (GitHub's docs require it) or codex-action's
  `permission-profile` input (present on the action's `v1` ref). Its bundled
  data predates both.
- **Offline checks inside Codex's sandbox.** Whether `uv run --offline --frozen`
  works under the no-network `:workspace` profile is unverified. The review
  step's checks catch failures either way.
- **Codex has no turn or spend flag** in `codex-action` that we found. Its ceiling
  is `timeout-minutes` plus the provider budget.
- **PR author identity.** The PAT acts as you, so you author the PR and can't
  approve it; the merge is the human gate.
- **Mid-loop human comments are not read.** Comment after the loop finishes, or stop it.
