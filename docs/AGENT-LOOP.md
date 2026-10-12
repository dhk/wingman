# Agent loop: Codex authors, Claude reviews, a human merges

Status: **pilot, not yet run.** The workflows lint clean with `actionlint`, and the
gate and outcome logic were tested locally, but no end-to-end run has happened.
Treat the first runs as the test. The decision record is RFC-083 in
[`RFC.md`](RFC.md).

## What it does

Label an issue `codex`. One workflow run then:

1. **Author.** Codex implements the issue and the loop opens a **draft** PR.
2. **Review.** The loop runs the CI checks (ruff, mypy, pytest). Claude reads the
   diff, the check output and `AGENTS.md`, then returns a JSON verdict with
   findings marked `blocking` or `nit`. The verdict is posted as a PR comment.
3. **Repair.** If anything is blocking, Codex fixes **only those findings**.
4. The review → repair cycle repeats, **3 reviews and 2 repairs at most**.
5. **Finalize.** The PR gets `ready-for-merge` (and leaves draft) or `needs-human`,
   with a comment saying why.

**You merge.** Nothing in the loop approves, enables auto-merge, or merges.

Files: [`agent-loop.yml`](../.github/workflows/agent-loop.yml) (entry and state
machine), [`agent-loop-codex.yml`](../.github/workflows/agent-loop-codex.yml),
[`agent-loop-review.yml`](../.github/workflows/agent-loop-review.yml).

## Why it is built this way

| Concern | Design |
|---|---|
| Lost handoffs | GitHub starts no new workflow run for events created with `GITHUB_TOKEN` ([docs](https://docs.github.com/en/actions/concepts/security/github_token)). So rounds are **jobs in one run**, not a chain of events, and no handoff depends on an event firing. |
| Runaway loops | The round cap is the **shape of the job graph**. There is no counter a model could reset, and a 4th round cannot exist. |
| Model-driven control flow | Every transition is a deterministic expression (AGENTS.md: no model for status transitions or approval decisions). Claude only proposes a verdict. A failing check counts as blocking whatever Claude says. |
| Nit ping-pong | Only `blocking` findings reach the repair prompt. Nits are posted for you and never start a round. |
| Repair becoming a rewrite | A deterministic line cap on each patch: 300 changed lines per repair, 1,500 for authoring. Over the cap, the patch is refused and the PR gets `needs-human`. |
| The loop editing itself | Any patch that touches `.github/` or `.agent-loop/` is refused. Both source and destination paths are checked, so a rename can't slip through. As a second barrier, the token is never granted the Workflows permission. |
| Codex holding credentials | Codex runs in a job **with no write token**, under `permission-profile: ":workspace"` (no network) and the default `drop-sudo` safety strategy ([codex-action](https://github.com/openai/codex-action)). A fresh runner applies and pushes its patch, and git hooks are disabled while the patch is exported. |
| Claude holding credentials | Claude gets `Read`, `Grep`, `Glob` and read-only `git` only, with `--max-turns 30`. It has no GitHub write tools; a workflow step posts its comment. |
| Strangers triggering spend (public repo) | Only actors in `AGENT_LOOP_ALLOWED_ACTORS` can start a run, and the trigger label is removed on start. Issue text is passed to both models as untrusted data. |
| Concurrent runs | `concurrency` per issue, never cancelling a run in progress. |
| Wall-clock | `timeout-minutes` on every job: Codex 30, review 25, push 10. Worst case is about 3 hours per issue. |

## Setup (one time)

1. **Secrets** (Settings → Secrets and variables → Actions):
   - `OPENAI_API_KEY`: OpenAI API key. The action bills to the API, not a ChatGPT plan.
   - `ANTHROPIC_API_KEY`: Anthropic API key.
   - `AGENT_LOOP_TOKEN`: a fine-grained personal access token scoped to
     **this repository only**, with Contents read/write, Pull requests read/write,
     Issues read/write and Metadata read. **Do not grant Workflows.** The token
     is what makes the loop's pushes and PR start `ci.yml`.
2. **Variables:**
   - `AGENT_LOOP_ENABLED` = `true`. This is the kill switch: anything else stops new runs.
   - `AGENT_LOOP_ALLOWED_ACTORS` = `dhk` (comma-separated; defaults to the repo owner).
   - Optional: `AGENT_LOOP_CODEX_MODEL`, `AGENT_LOOP_MAX_REPAIR_LINES` (300),
     `AGENT_LOOP_MAX_AUTHOR_LINES` (1500).
3. **Spend ceilings at the providers.** The workflow caps rounds, turns and time,
   but not dollars. Set a monthly budget on the OpenAI project and a spend limit
   on the Anthropic workspace that hold these keys.
4. Create a `codex` label.

## Using it

- **Start:** add the `codex` label to an issue, or run *Agent loop* from the
  Actions tab with an issue number.
- **Stop everything:** set `AGENT_LOOP_ENABLED` to anything but `true`. To stop a
  run already in progress, cancel it in the Actions tab; the loop never cancels
  itself.
- **Re-run:** delete the `codex/issue-N` branch (or finish its PR), then label again.

## Known limits and open questions

- **Never run end to end.** The first runs are the test.
- **Offline checks inside Codex's sandbox.** Codex is told to use
  `uv run --offline --frozen`. Whether uv works under the `:workspace` profile
  with no network is unverified. If it doesn't, Codex can't run tests itself, and
  the review step's checks still catch failures.
- **Codex has no turn or spend flag** in `codex-action` that we found. Its ceiling
  is the job's `timeout-minutes`, plus the provider budget.
- **PR author identity.** The PAT acts as you, so the PR is authored by you and
  GitHub won't let you approve it. The merge is the human gate. A dedicated
  GitHub App would separate the identities later.
- **Mid-loop human comments are not read.** The loop sees only Claude's findings.
  Comment after it finishes, or stop it.
