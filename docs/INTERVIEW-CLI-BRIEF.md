# Wingman Interview CLI — Product Brief for UX/Product Design

*Written for a designer with no prior context on this codebase. Narrower
than `docs/PRODUCT-BRIEF.md` (the whole-product brief) — this one is
scoped specifically to the interview/"Perspectives" capture flow, which is
where we want to take the terminal experience from "it works" to "it's
designed." Source: `docs/PROFILE-BOOTSTRAP-DESIGN.md`,
`src/wingman/cli/main.py`, `src/wingman/mcp_server.py`,
`src/wingman/application/interview.py`, as of 2026-07-28.*

---

## 1. Why this brief exists

Wingman just shipped a way for someone with **no existing published
writing** to still build a real, evidence-backed career/values profile —
by reacting to content and nominating people or organizations, instead of
writing essays. The mechanic works end-to-end today (capture, storage,
synthesis into a stance). What it doesn't have yet is a *designed*
interface — today it's raw, sequential terminal prompts. This brief is
the ask: **take the existing terminal mechanic and design it properly as
a CLI experience** — "a wingman CLI for interviews and stuff."

**"CLI experience" here means specifically: wingman driven from inside an
AI-assistant CLI (Claude Code, Claude Desktop, or any MCP client with
structured-question support), using that client's own native
question-asking UI — not a bespoke terminal-rendering framework built
from scratch.** See §4 for the required interaction pattern this implies;
it's a direct, resolved requirement from the product owner, not one of
the open questions left for the designer.

## 2. The mechanic, in plain terms

Every capture is the same three-step shape: **stimulus/nomination →
reaction → reasoning**.

1. The user picks something — a URL/file to react to, or names a person
   or organization.
2. They state a reaction — agree/disagree, or "I'd have dinner with
   them" / "I'd never buy from them," etc.
3. They explain **why, in their own words** — this is the only thing
   that ever becomes evidence. The submitted content or nominee's name is
   never quoted as if it were the user's own words. This is the one hard
   rule the design will not compromise on, and it constrains UI copy too:
   nothing should present "why" as anything other than the user's own
   voice, verbatim.

**Four categories** (three built, one not yet):

| Category | Ask | Ordering |
|---|---|---|
| Alignment of perspective | React to 2–3 pieces of content you agree with, 2–3 you don't | None — the entry-level tier |
| Values | Three people you'd have dinner with (pro) / be horrified to be named alongside (con) — Hitler excluded from con only. Falls back to companies ("proud to buy from" / "never buy from") if naming people is hard | Con-then-pro, and within each block the *second* nominee is asked about first |
| Mission alignment | Three orgs you'd want to work at / be proud to be associated with (pro), or horrified to be associated with (con, no exclusions) — every nomination also gets a "what do you understand this org's purpose to be" follow-up, captured as context, never as evidence | Same con-then-pro / ask-2nd-first pattern |
| *(v2, not built)* Company-alignment | The same reaction mechanic pointed at one target company's own content during a live job search | — |

The **con-then-pro / ask-second-nominee-first** ordering isn't
decorative — it's a deliberate anti-bias technique (ends on a high note;
dodges the rehearsed, front-loaded first answer). A redesign needs to
either preserve it faithfully or make a considered call to change it —
not lose it by accident because it wasn't visually obvious what order the
old prompts asked in.

A **trust ladder** suggests a default order (Tier 1: Alignment of
perspective → Tier 2: Values + Mission alignment → Tier 3: opt-in,
job-search-specific) but **nothing gates anything else** — every module
is independently invokable, any order, any number of times, forever. A
UI must not turn the ladder into an enforced wizard; it's a nudge, not a
gate.

## 3. What exists today, concretely

Two working surfaces, both thin:

- **`wingman interview <subtype> <target> <why>`** — one capture per
  invocation, plain positional arguments, no interactivity. Fine as a
  scriptable primitive; not an experience.
- **`wingman perspectives`** — a guided wizard, but a narrow one: it asks
  once, up front, "existing writing or a quick interview?", then (if
  interview) loops through Tier 1 only (agree/disagree reactions) with
  bare `typer.confirm`/`typer.prompt` calls, and simply *tells* the user
  in text to go run `wingman interview --help` for Values/Mission
  alignment. It does not walk the con-then-pro/ask-2nd-first structure,
  the primary-purpose follow-up, or the anchor-first fallback
  ("name an org you've actually been part of" when Mission alignment
  comes up empty) — those exist only as prose in the design doc and in
  an MCP tool's docstring, not as guided terminal steps.

There's also a **chat-driven path**: `interview_react`/
`perspectives_start` are MCP tools an AI assistant (Claude Desktop, Claude
Code, etc.) calls on the user's behalf. All the ordering/protocol
discipline for that path lives as *instructions in the tool's own
docstring* for the assistant to follow — "ask con before pro," "ask the
second nominee first," "show the exact text before saving" — there's no
code enforcing it, the same way `qa_capture` and `resolve_requirement`
already work in this codebase. That path is reasonably solid *because*
a capable model is doing the conversational work. **The raw terminal path
has no equivalent — it's the one that needs real design.**

## 4. Required interaction pattern

Two rules, direct from the product owner — not open for the designer to
relitigate, only to apply:

1. **Each section opens with a short explainer.** Before asking anything
   in a category (Alignment of perspective, Values, Mission alignment,
   etc.), the assistant states in plain language what the section is,
   why it's being asked, and roughly what's coming — e.g. "Values: I'll
   ask about three people you'd be horrified to be named alongside
   first, then three you'd have dinner with — for each, why, in your own
   words."
2. **Every individual question is asked via `AskUserQuestion`** (or the
   calling client's equivalent structured-question UI) — clickable
   options with a free-text/"Other" escape hatch — rather than an
   open-ended chat turn the user has to parse and respond to unprompted.

This fits naturally onto the flow's *structural* choices: agree/disagree,
which module to do next, continue-or-stop, subtype selection. It's a
genuinely awkward fit for fields that are inherently open text by
design — the nominee's name, and above all the "why," which must never
be reduced to a preset option (RFC-005's evidence discipline requires it
verbatim, in the user's own words, never coached or narrowed toward a
menu). For those, `AskUserQuestion`'s free-text/"Other" path is the right
tool, not a synthetic multiple-choice menu that would shape the answer.

Practically, this means the primary interaction surface being designed
is the **chat-driven MCP path**, not a bespoke terminal-rendering
framework — the actual implementation work is mostly in
`interview_react`'s and `perspectives_start`'s own docstrings (the
existing enforcement mechanism this codebase already uses for protocol —
see `qa_capture`, `resolve_requirement`), possibly with some
restructuring of what each tool call returns to make the
explainer-then-`AskUserQuestion` shape easy for a calling assistant to
follow consistently. `wingman interview`/`wingman perspectives` (the raw,
non-agent terminal path) stays a valid scriptable primitive underneath,
but isn't where this required pattern applies — it has no AI harness to
call `AskUserQuestion` through.

## 5. Concrete gaps in the terminal experience today

- **No visible progress.** Nothing shows "2 of 3 con nominees named" or
  "Tier 1 of 3" — a user re-running the command has no sense of where
  they left off.
- **No batch review.** Each answer commits immediately (matches how
  supersession/RFC-028 works underneath — re-answering the same target
  updates it), but there's no "here's everything you're about to save,
  confirm" moment across a full session the way the MCP path's
  show-before-save discipline provides.
- **Ordering is invisible.** The con-then-pro / ask-2nd-first sequence
  exists in code for the *chat* path's instructions, but nothing in the
  terminal wizard visually communicates *why* a question is asked in a
  particular order — it can just read as arbitrary if we ever build the
  Tier 2 terminal flow.
- **Limits are silent until hit.** Per-submission size and per-subtype
  count caps were just added (`WINGMAN_INTERVIEW_MAX_PER_SUBTYPE`,
  default 6) — they only surface as an error message at the ceiling, not
  as a running "3 of 6 used" indicator.
- **The Mission alignment anchor-first fallback isn't implemented as a
  guided step anywhere** — "if you can't name an org, try: an org you've
  actually been part of — school, employer, club" exists only as design
  prose, not as an actual re-ask a terminal user experiences.
- **Company-alignment (v2)** doesn't exist at all yet — worth knowing
  about as a near-future fourth category, not something to design against
  yet.

## 6. Constraints/invariants any redesign must preserve

- **Evidence discipline.** The "why" text is the *only* thing that ever
  becomes evidence; the stimulus/nominee is context only. Copy and layout
  should never blur that line (e.g., never headline the stimulus's own
  words as if they were the user's stance).
- **Con-then-pro / ask-2nd-first ordering**, for Values and Mission
  alignment — preserve deliberately, or change deliberately; don't lose
  it as an implementation detail.
- **The Values con-side Hitler exclusion is scoped to that one subtype
  only** — not to Mission alignment's con side, not to the company
  fallback. A shared "con" UI component shouldn't accidentally apply one
  category's exclusion rule to another.
- **Primary-purpose (Mission alignment) is context, never evidence** —
  keep that visually distinct from the "why" if both appear on screen
  together.
- **Additive, not gated.** Every module — and the corpus/interview
  top-level branch itself — must stay independently reachable in any
  order. No forced linear wizard across modules, even if the trust ladder
  suggests a default path.
- **Honest degrade, no forced answers.** If someone comes up empty (even
  after the anchor-first re-ask), the module should let them stop rather
  than coercing an answer — same pattern the rest of wingman uses ("no
  stance yet — here's the command that would produce one").
- **Local-first.** This is a single-user, local terminal tool — no
  account/login concepts belong here.

## 7. Open questions for the designer

§4's explainer-then-`AskUserQuestion` pattern is settled; these are the
things still genuinely open:

1. Should there be a **review/edit screen before final commit** for a
   whole session's worth of answers, or does today's
   answer-commits-immediately model (which already supports clean
   re-answering/supersession) stay as-is, just with an explainer +
   `AskUserQuestion` in front of each capture?
2. How should the **trust ladder** be surfaced so it reads as a helpful
   suggested path without becoming a mandatory wizard — could itself be
   an `AskUserQuestion` ("what would you like to do — react to
   something, name people you admire, name organizations, or pick up
   where you left off?") rather than a fixed menu.
3. Should the **size/count limits** show proactively ("3 of 6 nominees
   used for Values-con") — e.g. folded into a section's opening
   explainer — rather than only appearing as an error at the cap?
4. Does the **anchor-first Mission-alignment fallback** get built as an
   actual guided re-ask (its own explainer + `AskUserQuestion`) in this
   redesign, or stay a documented-but-unbuilt behavior for now?

## 8. Where to start

Recommend scoping the first design pass to **`perspectives_start` and
`interview_react`'s docstrings** for Perspectives' entry point and Tier 1
flow (Alignment of perspective) — it's the smallest, most self-contained
piece, and there's already a working (if unstructured) MCP path to react
against rather than designing from a blank page. Values and Mission
alignment's fuller con-then-pro flow is naturally the next scope once
Tier 1's explainer-then-`AskUserQuestion` shape is settled, since it
reuses the same underlying primitive (`capture_interview_reaction`) with
a more structured ordering and more questions per section on top.

---

*Everything above is grounded in this repository as read on 2026-07-28.
See `docs/PROFILE-BOOTSTRAP-DESIGN.md` for the full mechanic
specification and `docs/PRODUCT-BRIEF.md` for how this fits into wingman
as a whole.*
