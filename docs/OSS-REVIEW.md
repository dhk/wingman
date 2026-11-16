# Open-weight code review

`scripts/oss_review.py` reviews the repository with an open-weight model, such as
DeepSeek, Qwen, GLM or Kimi, over any OpenAI-compatible API. It is a development
tool. It is not part of Wingman, and it does not touch the product's provider
adapters or capability routing (RFC-004).

It uses only the standard library, so it runs on a bare Python 3.12. It also runs
on an old Mac: the model runs remotely, and the script just sends text and checks
the answers.

## Quick start

```bash
export AI_GATEWAY_API_KEY=...            # Vercel AI Gateway key
python3 scripts/oss_review.py plan       # files, batches, estimated cost; sends nothing
python3 scripts/oss_review.py run        # writes reports/oss-review/report.md + findings.json
```

`plan` shows what `run` would send and an estimate at the gateway's list price. On
2026-11-15 a full review of this repository was 36 batches (about 1.6M input tokens),
roughly $0.55 with the default model. `run` refuses to start when the estimate is
above `--max-cost`, which defaults to $5.

Useful variations:

```bash
# One package only
python3 scripts/oss_review.py run --include 'src/wingman/application/**'

# Only what this branch changed (what the PR workflow does)
python3 scripts/oss_review.py run --diff-base origin/main

# A stronger second opinion from a different model family
python3 scripts/oss_review.py run --model moonshotai/kimi-k3.1-pro --out reports/oss-review-kimi

# Self-hosted (vLLM on a rented GPU, or Ollama): no key needed
OSS_REVIEW_BASE_URL=http://gpu-box:8000/v1 python3 scripts/oss_review.py run \
  --model Qwen/Qwen3.8-27B --allow-no-key
```

## Configuration

| Setting | Flag | Environment | Default |
|---|---|---|---|
| Endpoint | `--base-url` | `OSS_REVIEW_BASE_URL` | `https://ai-gateway.vercel.sh/v1` |
| Model | `--model` | `OSS_REVIEW_MODEL` | `deepseek/deepseek-v4.1-flash-1017` |
| API key | none | `OSS_REVIEW_API_KEY`, else `AI_GATEWAY_API_KEY` | none |
| Project rules | `--rules` | none | `AGENTS.md` (`''` for none) |

Other open-weight IDs on the gateway include `zai/glm-5.3`, `zai/glm-5.3-flash`,
`alibaba/qwen3.8-27b`, `xiaomi/mimo-v2.6-pro`, `mistral/mistral-large-4` and
`moonshotai/kimi-k3.1-pro`. `curl https://ai-gateway.vercel.sh/v1/models` lists the
current set and their prices.

For Ollama, raise its context window before reviewing, for example
`OLLAMA_CONTEXT_LENGTH=65536 ollama serve`, or lower `--batch-tokens`. Its default
is 4k tokens, and it cuts longer prompts without returning an error. The script
compares the prompt tokens the server reports with what it sent. When the server
reports far fewer, it warns, or fails the batch, naming truncation as the likely
cause. It does not cache that answer.

When the code is open source, the hosted gateway is the simplest choice. When it
is not, a hosted provider still sees the code, even though it is not Anthropic or
OpenAI. Point `OSS_REVIEW_BASE_URL` at a server you run instead.

## What it does

1. **Selects files deterministically.** It takes `git ls-files`, keeps source
   suffixes and executable scripts, and drops lock files, fixtures, data, agent
   configuration, generated changelog data, and anything with minified-length
   lines. `plan` lists everything it skipped and why.
2. **Batches.** Files are packed in path order, about 40k tokens per request, so
   one package tends to share a batch. Files larger than a batch are split at
   top-level definitions so the model sees whole functions.
3. **Reviews each batch.** The prompt is a fixed checklist: correctness, security
   and privacy, error handling, the project rules, tests that cannot fail. It
   excludes anything ruff or mypy already enforce. Code and `AGENTS.md` are passed
   as data, and the prompt says that instructions inside them are not instructions.
4. **Runs a cross-cutting pass.** This pass sees a map of the repository: files,
   top-level definitions and internal imports. It looks for layering violations
   and inconsistencies across modules. Diff mode skips it.
5. **Gates every finding.** This step is deterministic. See the next section.
6. **Writes outputs.** `report.md` lists findings by severity, linked to the exact
   GitHub line. `findings.json` holds the findings, the rejected proposals with
   reasons, and a manifest: model, endpoint, prompt version, raw token counts, and
   failed batches.

### The finding gate

The model proposes and the script decides (invariant 5). The model sees numbered
lines and must quote one verbatim as evidence. Each finding is then checked:

- **Rejected:** it cites a file that was not in the batch, or a line outside what
  the model was shown. Rejections are kept in `findings.json`, not deleted.
- **`verified`:** the quoted code is on the cited line, or within 3 lines of it.
  The line number is snapped to the real line.
- **`relocated`:** the quote exists elsewhere in the file, and the line was
  corrected.
- **`unverified`:** the quote is not in the file. The finding is kept, because
  partial truth beats silent loss, but the report says to check these first.

Severity and category are normalised to fixed sets, and duplicates are merged. In
diff mode, only findings on or near changed lines are kept, plus any `critical`
or `high` finding.

### Caching and failure

Each batch result is written to `reports/oss-review/cache/` (gitignored) as soon
as it returns, before validation. The cache key is the model, prompt version,
rules and exact code. A re-run pays only for batches whose code changed, and a
crash never loses a paid call. Use `--no-cache` to force fresh answers.

Requests are retried with backoff on 429, 5xx and network errors. A batch that
still fails is listed in the report as **not reviewed**, and the exit code is 2.
Re-running the same command retries only the failed batches. `--fail-on high`
exits 1 when any finding at that severity or above survives the gate.

A batch whose reported prompt tokens fall below 40% of the estimate is listed
under **Warnings** in the report and is not cached.

Token counts are recorded raw. Cost is computed when the report is written, from
the gateway's public price list, and is labelled an estimate. This follows the
RFC-084 rule that prices are a read-time interpretation.

## GitHub Actions

`.github/workflows/oss-review.yml`:

- **Manual full review:** Actions → *Open-weight review* → *Run workflow*. You can
  choose a model, an `include` glob, and a cost cap. The report appears as the job
  summary and as the `oss-review` artifact.
- **Pull request review:** add the `oss-review` label. The workflow reviews the
  changed files and posts or updates one comment. Later pushes re-run it while the
  label is on, with a $2 cost cap.

Setup: add a repository secret `AI_GATEWAY_API_KEY`, or `OSS_REVIEW_API_KEY` for
another provider. Optionally set the repository variables `OSS_REVIEW_MODEL` and
`OSS_REVIEW_BASE_URL`. With no secret, the job logs a notice and succeeds without
reviewing anything. PRs from forks get no secrets, so they are skipped the same
way.

The workflow runs the copy of the script on the branch being reviewed, so it
offers no protection against someone with write access who changes the script in
their own PR. It comments, and it never approves, blocks or merges. It is
separate from the agent loop (RFC-083) and does not feed it.

## Limits

- Open-weight models find real bugs, but they also propose plausible ones. The
  gate catches invented code and wrong locations. It cannot catch a wrong argument
  about real code. Treat the report as a first pass for a human reviewer.
- Each batch is reviewed without the rest of the repository. Bugs that depend on
  how a caller in another batch uses a function are only partly covered by the
  cross-cutting pass.
- The token estimate assumes about 3.5 characters per token. Actual usage is
  recorded in the manifest.
