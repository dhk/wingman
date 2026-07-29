# UX-0001 — Perspectives, asked properly

*A designed interaction for wingman's interview capture, run from inside Claude
Code or Claude Desktop.*

| | |
|---|---|
| **Status** | Draft · v0.1 — §2's BP-01/03/04/06/07/08/09 and §8's Q2/Q3/Q4 resolutions implemented in `interview_react`/`perspectives_start`'s docstrings and return values (position, resume detection). §10's structured JSON tool-contract (BP-10) not yet built — still docstring-only protocol. |
| **Answers** | `docs/INTERVIEW-CLI-BRIEF.md` (PR #191) |
| **Surface** | MCP client chat (Claude Code, Claude Desktop, any MCP client) |
| **Control** | `AskUserQuestion` (or the client's structured-question equivalent) |
| **Enforcement** | Tool return value + docstring |
| **Scope** | Tier 1 built now; Tier 2 shape specified |

Every section opens with a short explainer; every question is asked through the
client's own structured-question UI. The surface being designed is the
conversation — the artifact is a tool contract and a set of docstrings, not a
terminal-rendering framework.

Rendered version: [`docs/assets/UX-0001-interview-flow.html`](assets/UX-0001-interview-flow.html)
(self-contained, opens offline in any browser).

---

## 1. Goals & non-goals

**Goals**

- Someone with no published writing can produce an evidence-backed stance in one
  sitting, without composing prose.
- Every question arrives as a clickable structured question with a free-text
  escape — never a bare chat turn the user must decode.
- The "why" stays verbatim, in the user's own voice, never narrowed toward a menu.
- The con-then-pro / ask-second-first ordering survives, and is *visible* as a
  deliberate device.
- Behaviour is consistent across Claude Code, Claude Desktop, and any MCP client —
  because it is carried in the tool's return, not the model's memory.

**Non-goals (this pass)**

- No bespoke terminal-rendering framework. `wingman interview` stays a scriptable
  primitive underneath.
- No stateful server-side wizard. The tool answers "what's next"; it does not own
  a session.
- No accounts, no sync, no remote state — local-first, single user.
- Company-alignment (v2) is named but not designed against.

---

## 2. Best practices for an assistant-asked surface

This surface is unusual: the interface is rendered by a client we don't control,
driven by a model we don't control, from a contract we do. There is no layout to
art-direct. The design lives in **what gets asked, in what order, with what
exits**. Ten rules, proposed as the house standard for any wingman flow driven
this way.

BP-01 and BP-02 restate the product owner's two settled requirements. BP-03…10
are this spec's proposals.

| | Rule | Why |
|---|---|---|
| **BP-01** | **Explain, then ask** | Every section opens with two or three sentences: what this is, why it's being asked, what's coming. No question arrives cold. A clickable option is only as good as the frame around it. |
| **BP-02** | **One decision per card** | Each structured question asks exactly one thing. Bundling "who, and why" into one turn makes the user hold two decisions to answer either, and the free-text half always suffers. |
| **BP-03** | **Options for structure, prose for substance** | Presets are correct for agree/disagree, which-module-next, continue-or-stop. They are **never** correct for a field that becomes evidence. A menu shapes the answer — fatal when the answer *is* the data. |
| **BP-04** | **Every card has an exit** | Free text, "skip this one", and "I'm done for now" are always reachable — as options, not as instructions to type something. A user who can't leave a question will leave the session. |
| **BP-05** | **Say where you are, and what it costs** | Position and budget belong in the explainer, not in an error at the ceiling: "Con 2 of 3 · you've used 4 of 6 slots." Chat has no persistent chrome — the sentence *is* the progress bar. |
| **BP-06** | **Echo verbatim before you commit** | Show the exact string that will be stored, in a quote block, and save on confirm. A model paraphrasing the user's words into a tool call is the single largest risk to evidence discipline on this path. |
| **BP-07** | **Name the reason for an odd order** | Con before pro, and nominee #2 before #1, are anti-bias devices. Said once per section they read as craft; unsaid they read as a bug, and the next maintainer "fixes" them. |
| **BP-08** | **Empty is a valid answer** | One anchor-first re-ask, then a clean stop naming the command that would produce a stance later. Never a third attempt. Coerced answers are worse than no answers — they become evidence. |
| **BP-09** | **Suggest, never gate** | The trust ladder is an ordering of options in one card with the recommended step first — not a locked sequence. No option is ever disabled; nothing is a prerequisite for anything. |
| **BP-10** | **Protocol travels in the return value** | Each call returns the next explainer and the next question's shape. Docstrings tell the assistant *how* to render; the payload tells it *what*. Prose-only protocol drifts across models, clients, and context lengths. |

---

## 3. Anatomy of a capture

Every capture in every category is the same four-beat loop. The beats never merge —
in particular, **reaction and reasoning are separate cards**, because reasoning is
the only beat that produces evidence.

| Beat | Form | Content |
|---|---|---|
| 1 · explainer | Plain prose | What, why, what's next, where you are. Not a question. |
| 2 · stimulus | Free-text card | URL, file, or a name. Options only for recall aids and "skip". |
| 3 · reaction | Options card | The one genuinely structural choice. Maps 1:1 to a subtype. |
| 4 · reasoning | **Free text only — evidence** | Stored verbatim. No presets, ever. Echoed back before save. |

Mission alignment inserts a fifth beat between 3 and 4 — "what do you understand
this organisation's purpose to be" — captured as **context**, rendered in the
muted context style, never in the evidence style.

---

## 4. Entry: `perspectives_start`

The trust ladder as a single card. Recommended next step first; resume first when
prior captures exist. No option disabled, no tier locked (BP-09).

> **You** — I don't really publish anything. Can wingman still figure out what I think?
>
> **Claude** — Yes — Perspectives builds the same evidence base by having you react
> to things rather than write. You react, you say why, and only your own words are
> ever stored as evidence. Three ways in; you can do any of them, in any order, as
> many times as you like, and stop whenever. Nothing is saved until you confirm it.

**`AskUserQuestion` · single select · header "Start"** — *Where would you like to start?*

| Option | Description |
|---|---|
| React to things I've read | Suggested first step · 2–3 you agree with, 2–3 you don't · ~10 min |
| Name people | Three you'd have dinner with, three you'd hate to be listed beside · ~8 min |
| Name organisations | Places you'd want — or refuse — to be associated with · ~8 min |
| I do have writing to add | Skip the interview — use the corpus path instead |
| *Other* | Free text — always present, always last |

**Why a card and not a numbered menu:** a menu in prose has to be read, parsed,
and replied to with a number the user then has to trust the model to interpret. A
card is one click and the answer arrives structured. The tier ordering is preserved
as *option order* plus one "suggested first step" label — which is exactly as much
gating as the ladder deserves.

---

## 5. Tier 1: alignment of perspective

The smallest complete loop, and the recommended first build (`interview_react`
with subtypes `alignment_of_perspective_agree` / `…_disagree`).

**Explainer**

> **Alignment of perspective.** You point me at something you've read — a link, or
> a file on your machine — and tell me whether you agree with it and why. Your
> "why" is the only part that gets stored; the article itself is just context, and
> I'll never quote it back as though it were your view.
>
> *Capture 1 · aiming for 2–3 agree and 2–3 disagree · stop any time.*

**Card 1 — `AskUserQuestion` · free text primary · header "Source"**

*What have you read that you have an opinion about?*
Paste a link, or the path to a PDF, Word doc or Markdown file.

- **Other — paste a URL or path** (the real input; options below exist only to unstick recall)
- Nothing comes to mind → anchor re-ask: the last thing you sent someone, or argued with
- I'm done for now → clean stop · always the last non-free-text option

**Card 2 — `AskUserQuestion` · single select · header "Reaction"**

*"The case against dashboards" — where do you land?*
Title read from the page for provenance only. Nothing stored yet.

`Agree` · `Disagree` · `Other — it's complicated`

"It's complicated" routes to the **disagree** subtype and leans on the "why" to
carry the nuance — it does not invent a third subtype.

**Card 3 — `AskUserQuestion` · free text only · evidence field**

*Why? In your own words.*
This sentence is the evidence. It gets stored exactly as you write it — so write
it how you'd say it, not how you'd publish it. A line or two is plenty.

No options offered — not even examples, which anchor the answer.

**Echo before save (BP-06)**

> Saving this as your own words, verbatim:
>
> > "Dashboards answer questions nobody asked. The teams I've seen do well replaced
> > them with three numbers and someone accountable for each."
>
> `subtype alignment_of_perspective_agree` · `context example.org · sha256 …4f19`
>
> **[ Save it ]  [ Let me reword ]  [ Discard ]**

**Continue**

> Saved. That's **1 agree, 0 disagree** — the disagrees are the ones that actually
> discriminate, so they're worth the effort. Next: something you disagreed with?

---

## 6. Tier 2: the ordered categories

Values and Mission alignment reuse the same loop with an ordering discipline on
top: **con block before pro block**, and **within each block, nominee #2 is asked
about before #1**. The order is stated once, in the section explainer, and then
simply followed.

**Sequence · Values**

```
Name 3 con → why #2 → why #1 → why #3 → Name 3 pro → why #2, #1, #3
```

Ends on the pro block — the section closes warm. The naming card collects all
three at once (multi-select is wrong here; it's three free-text slots in one card,
or three quick cards — implementer's call, but never a preset list of people).

**Explainer**

> **Values.** I'll ask for three people you'd be uncomfortable being named
> alongside first, then three you'd genuinely want dinner with — and for each one,
> why, in your words. Doing the difficult list first is deliberate: it keeps the
> easy list honest, and it means we finish on the people you admire.
>
> *You've used 2 of 6 slots in this category. Public figures, colleagues, anyone —
> you can also switch to organisations if naming people feels wrong.*

**Naming card — `AskUserQuestion` · free text ×3 · `values_con`**

*Name up to three people you'd be uncomfortable being listed alongside.*
One or two is fine. Names only for now — I'll ask why afterwards, one at a time.

- three free-text slots
- I'd rather name companies → switches to `values_fallback_con` ("never buy from" instead of "named alongside")
- Skip the con list → goes straight to the pro block · nothing is required

**Guardrail · `values_con` only.** Hitler is rejected by the capture layer here,
and only here. Copy: *"That one's excluded — it's too easy a name to tell me
anything about your values. Someone closer to your own world?"* — a re-ask, not an
error. The exclusion must not be repeated in the org or company-fallback cards.

**Mission alignment · the fifth beat.** "What do you understand this
organisation's purpose to be?" is asked *before* the why, and echoed under a
**Context** label — never inside the evidence quote block. Two visually distinct
treatments, one screen apart, so the boundary is never ambiguous.

**Honest degrade · after the anchor re-ask**

> "Nothing's coming — that's a real answer, not a failure. Nothing was saved for
> this section. When an organisation does come to mind,
> `wingman interview mission_alignment_pro …` or just tell me here."

---

## 7. Question inventory

The rule that generates this table: **if the answer is stored as evidence, it is
free text.**

| Question | Control | Options | Stored as |
|---|---|---|---|
| Where to start | Single select | 4 modules + resume + Other | — |
| Source to react to | Free text | "nothing comes to mind", "done" | context |
| Agree or disagree | Single select | Agree · Disagree · it's complicated | subtype |
| Nominees (×3) | Free text ×3 | fallback switch · skip block | context |
| Understood purpose | Free text | "not sure" is acceptable, stored as-is | context |
| **Why, in your own words** | **Free text only** | **none — not even examples** | **evidence** |
| Confirm before save | Single select | Save · reword · discard | — |
| Another, or stop | Single select | Next · switch module · done | — |

---

## 8. The four open questions, resolved

**Q1 — Batch review before commit?**
**No — keep commit-on-answer, add a per-capture echo.** A batch commit invents a
transaction the storage layer doesn't have, and supersession (RFC-028) already
makes re-answering clean. What's actually missing is not deferral but *sight of the
exact string*: the echo-and-confirm in §5 buys the protection at one turn's cost.
Close each section with a read-only recap and a "change one of these" re-ask —
review without a pending state.

**Q2 — How to surface the trust ladder?**
**As one card, with the ladder expressed as option order.** The recommended step is
first and carries a "suggested first step" description; "pick up where I left off"
is promoted to first whenever prior captures exist. No option is ever disabled and
no tier is named in the UI — "Tier 2" is vocabulary for the design doc, not for the
user. Each option carries a rough time cost, which does more to sequence behaviour
than any ladder metaphor.

**Q3 — Show limits proactively?**
**Yes, in the explainer — but only once they matter.** Suppress below half the cap
("0 of 6" is noise that makes a quota feel like a chore); show from the halfway
point ("you've used 4 of 6 slots here"); at the cap the question never renders at
all — the section opens as a recap plus "replace one of these?", since supersession
means a re-answer is the real affordance. A cap should never first appear as an
error.

**Q4 — Build the anchor-first fallback?**
**Yes — but as a re-ask inside the section, not a module.** It is one extra
question fired on an empty answer: "an organisation you've actually been part of —
school, employer, club, team". It costs a branch in the tool's return, and without
it "I can't think of one" dead-ends the whole category. One re-ask only, then the
honest stop in §6 (BP-08). Generalise the same shape to Tier 1's "nothing comes to
mind" — the anchor there is the last thing you sent someone.

---

## 9. Copy rules

| Say | Never say |
|---|---|
| "Your 'why' is the only part stored." | "I'll capture your take on this article." — blurs stimulus into stance. |
| "Saving this, verbatim: …" | "So what you're saying is…" — a paraphrase that becomes evidence. |
| "That's a real answer, not a failure." | "Try harder — just one more." — coercion produces junk evidence. |
| "Doing the hard list first is deliberate." | "Tier 2 of 3 · step 4 of 9" — wizard chrome the flow doesn't have. |
| "You can stop any time; nothing is lost." | "Complete your profile to continue." — nothing gates anything. |

**The one failure mode worth naming:** a helpful model tidying a user's sentence
before passing it to `interview_react`. It reads as service and it silently
destroys the property the whole system is built on. The echo card exists for this,
and it should be the last thing removed if the flow is ever trimmed.

---

## 10. Tool contract

The implementation surface. Docstrings still carry the *how* (this codebase's
existing enforcement mechanism — `qa_capture`, `resolve_requirement`); the return
value carries the *what*, so a section's explainer, position, and next question
don't depend on the model remembering them.

```json
{
  "section":   "values_con",
  "explainer": "Values. I'll ask for three people you'd be…",   // BP-01, verbatim
  "position":  "Con 2 of 3 · 4 of 6 slots used",                // BP-05, omit if < half
  "ask": {
    "header":      "Reaction",
    "question":    "Why? In your own words.",
    "multi":       false,
    "evidence":    true,      // true => options MUST be empty (BP-03)
    "options":     [],
    "free_text":   "required" // required | offered — never absent (BP-04)
  },
  "on_answer": "interview_react",
  "echo_before_save": true                                       // BP-06
}
```

**Still stateless.** Position is computed from stored captures on every call, not
held in a session. Restarting the conversation mid-flow resumes correctly because
there is nothing to resume — the workspace is the state.

**Degrades to prose.** A client without structured questions renders `ask` as a
plain numbered list. Same protocol, worse ergonomics — which is the correct failure
direction.

---

## 11. Acceptance checks

1. The stored `detail` string is byte-identical to what the user typed, across ten
   captures — no capitalisation, punctuation, or tidying drift.
2. No question whose answer becomes evidence is ever presented with preset options,
   in any transcript.
3. A Values run asks the con block first and nominee #2 first — verified from the
   capture timestamps, not from the transcript.
4. Every module is reachable as the first act of a fresh conversation; none reports
   a prerequisite.
5. A user who answers "I can't think of anyone" twice ends with zero captures, no
   error, and a named next step.
6. Hitler is refused in `values_con` with a re-ask, and accepted in
   `mission_alignment_con` without comment.
7. The same script produces the same question sequence in Claude Code and Claude
   Desktop.

---

## 12. Out of scope

- **Company alignment (v2)** — same loop pointed at one target company during a
  live search. Inherits everything here unchanged.
- **Terminal wizard parity** — `wingman perspectives` stays as-is. It has no
  harness to ask through; scripting is its job.
- **Synthesis UX** — how captures become a stance is `build_own_pov`'s problem, and
  its own spec.

---

*Wingman · UX-0001 · v0.1 · Evidence before assertion · suggest, never gate.*
