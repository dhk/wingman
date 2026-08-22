# Question Blocks — interview modules that read against an external rubric (proposal, not yet an RFC)

**Status.** Design recorded 2026-08-20 from an ideation session (issue #436).
**v0 is built** (2026-08-22): rubrics as packaged data, and the gap map —
`wingman gap-map` / `wingman rubrics`, and the `gap_map` / `rubrics` MCP
tools. It emits coverage and gaps, never a rung, and calls no model. v1 (the
block, and the elaboration capture mechanic) and v2 (the registry and flow
wiring) are not started. §6 was corrected during v0's implementation — see
the note there. §3's evidence audit was run
the same day against one real workspace and is the reason the build order in
§7 is what it is rather than what the session first proposed. Graduates to a
numbered `RFC.md` entry (and a `ROADMAP.md` slice) once it is referenced
elsewhere; until then this document is the working design and the thing to
revise.

**Related.** [`PROFILE-BOOTSTRAP-DESIGN.md`](PROFILE-BOOTSTRAP-DESIGN.md) —
the capture mechanics this extends.
[`UX-0001-interview-flow.md`](UX-0001-interview-flow.md) — the asking
protocol any block must obey (BP-01…BP-10). `RFC.md`: RFC-028
(supersession), RFC-049 and RFC-057 (intensity, value statement), RFC-066
(the same captures read a second time), RFC-071 (amend), RFC-073
(Observed/Endorsed evidence tiers).

---

## 1. Motivation

The worked example that started this:

> *"Help me assess my level on the Google job ladder in terms of contribution
> and impact."*

Wingman cannot answer that today, and the reason is not a missing question.
It is that the request is a different **shape** of thing from everything the
interview currently does.

| | Today's interview | What the ladder question needs |
|---|---|---|
| Grounding | None. Nine subtypes hardcoded in `application/interview.py` | An **external rubric** it is measured against |
| Subject | Other people, other organisations, other people's writing | The person themselves |
| Questions | A fixed inventory, authored once | **Derived** from the rubric's own dimensions |
| Output | A `ProfileItem` per capture | A **positioning** across dimensions, with citations |
| Growth | Add a subtype to a `set` literal, add a category tuple | Add a rubric — no code change |

Every existing subtype deliberately asks about somebody else. That is a real
design achievement and §4 explains why it must survive contact with this
feature. But it also means the interview has no mechanism at all for "measure
me against a named external standard," which is most of what a person
actually wants during a job search.

A **question block** is that mechanism. It is not "more subtypes."

---

## 2. What a question block is — three layers

Only one of the three is questions.

| Layer | What it is | Nearest existing thing |
|---|---|---|
| **Rubric** | A file. A named external standard's dimensions and rungs — e.g. a software-engineering ladder. Sourced, cited, versioned. It is the ruler, never a measurement, and **never evidence about the person**. | `company_deep_dive` findings — every finding carries the source that backs it |
| **Block** | Declarative: id, title, the rubric it reads, its dimensions, the question script per dimension, the capture mapping. This is what replaces `VALID_SUBTYPES` being a hardcoded `set`. | Nothing. This is the new part. |
| **Reading** | A per-dimension positioning, citing the `item_id`s that informed it, refusing below an evidence floor. | `application/values.py` / `my_values` — exactly this shape already |

### 2.1 What a rubric actually looks like

The word does no work without an example. A rubric is a data file — this
shape, sketched here in TOML to match `models.toml`; the serialisation is not
decided:

```toml
[rubric]
id      = "swe-ladder-google-public"
title   = "Software engineering ladder (Google) — public reconstruction"
version = "2026-08-20"

[rubric.provenance]
tier       = "reconstruction"   # first_party | reconstruction | inferred
sources    = ["https://www.levels.fyi/...", "..."]
disclaimer = "Reconstructed from public sources. Not Google's own document."

[[dimension]]
id   = "scope"
name = "Scope of impact"
asks = "How far does the effect of your work reach?"

  [[dimension.rung]]
  level      = "L4"
  descriptor = "Your own projects; effects land inside your team."

  [[dimension.rung]]
  level      = "L5"
  descriptor = "Your team's roadmap; other teams consume what you build."

  [[dimension.rung]]
  level      = "L6"
  descriptor = "Multiple teams change what they do because of your work."

[[dimension]]
id   = "ambiguity"
name = "Ambiguity absorbed"
asks = "How defined was the problem when it reached you?"

  # Asked (v1) only when this dimension has no evidence to read.
  [dimension.probe]
  question    = "What did people think the problem was, before you started?"
  attaches_to = "achievement"
```

Adding a second ladder means adding a second file. That is the whole of the
"add a rubric — no code change" claim in §1; today, adding an interview
category means editing a `set` literal, a category tuple, and a render
function.

**Three things a rubric is not.**

1. **Not a prompt.** It is data the reading cites, not instructions handed to
   a model. The model sees `descriptor` strings as text to match evidence
   against; it never sees "decide what level this person is."
2. **Not evidence about the person.** It never becomes a `ProfileItem`, never
   appears in `career.md`. It is the ruler; the profile is what gets measured.
3. **Not code.** See above.

**And what it does, on one real item.** Take an achievement already in a
workspace:

> *"Architected a financial data integrity platform supporting $100B+ in
> ledger activity with to-the-penny reconciliation across banking partners"*

Read against `scope`'s rungs, an honest reading says: *matches the L5
descriptor — other teams consume what you build — and nothing in the cited
evidence shows another team changing what it does, which is what L6 asks
for.* Plus the citation, plus the note that `ambiguity` was not scored at all
because no evidence speaks to it. The rubric supplies the rung names and the
descriptors; the profile supplies everything else; **the reading is the
join**, and every part of it is inspectable.

**The capture layer does not change.** A block's answers land through
`capture_interview_reaction` with block-scoped subtypes, so they inherit —
unchanged and for free — the "why is the only evidence" rule, RFC-028
supersession, RFC-071 amendment, per-subtype caps, persona scoping, and the
inbox note. Anything that writes its own capture path will drift from those
rules; that is precisely why `application/form_ingest.py` calls
`capture_interview_reaction` instead of writing its own, and this follows
that precedent.

The reading likewise copies `my_values` wholesale: a model **names** the
reading and groups the evidence behind each dimension, and the position is
computed **deterministically** from the cited items afterwards. The model is
never asked for the score. That separation is the only reason `my_values`
can show its work, and a ladder reading needs it more, not less.

---

## 3. The evidence audit that grounds this

This section exists because the session's first proposed build order was
wrong, and the audit is what proved it. Recording the audit rather than just
its conclusion, per invariant 9.

**Method.** One real workspace (846 source records, 287 profile items, 105
corpus documents). Read: the full rendered `career_profile` — Roles,
Achievements and Testimonials bullet-by-bullet, three testimonial evidence
bodies sampled, section sizes for the rest. Plus four targeted `evidence`
searches over the corpus. **Not** read: all 171 skill items verbatim, 16 of
19 testimonial bodies, the full footnote block.

**Finding.** There is abundant first-person career material — the worry that
a profile might be mostly values-nominations about *other* people was
unfounded. But it is the wrong **kind** of evidence for a ladder:

> The profile records **what you produced**. A ladder measures **how you
> worked**.

Against five dimensions a contribution-and-impact ladder typically uses:

| Dimension | Coverage | What is actually there |
|---|---|---|
| Technical depth | **Strong** | A granted patent; a forensic-change auditing framework; a polymorphic key pattern; a Markov sequencing system with a stated before/after; a claimed precursor to a well-known geospatial indexing scheme |
| Outcome magnitude | **Strong — but not a ladder axis** | $100B+ in ledger activity, 267% growth, 6x, 80%, 10x, 7x |
| Scope (team → org → company) | **Inferable, never stated** | "Company-wide data architecture", "set architecture and roadmap … at Facebook" — but no team sizes, no "across N teams", no org context |
| Autonomy / direction-setting | **Verbs present, substance absent** | Dozens of "Defined…", "Set…", "Led…" — and nothing anywhere saying whether the problem was *chosen* or *handed over* |
| Ambiguity | **Absent** | One corpus hit, an essay *about* ambiguous thinking. Zero achievements describe the problem's starting state |
| Influence / multiplier | **Weak, and third-party only** | Achievements: almost nothing. Testimonials: "revolutionized the way we do business", "effective manager and mentor", "forming high performance teams" |

**Surprise 1 — outcome magnitude is a trap, not a gift.** It is the densest
signal in the profile and it is not what a ladder asks for. A ladder asks
about the scope of one's *influence*, not the size of one's *numbers*. A
naive reading will read "$100B+ in ledger activity" as organisational scope.
It is not; it is the size of a system, which one person can build alone.
Any reading that does not hold these apart will systematically over-position
strong individual contributors.

**Surprise 2 — the corpus cannot fill the gaps.** 105 documents, but they are
published *writing*: newsletters, leadership essays, reflections. Searching
for work narrative returns essays *about* mentoring, not evidence *of*
mentoring. The assumption that a corpus is mixed material is wrong for at
least this workspace, and the design must not depend on it.

**Consequence for the build order.** Three of five dimensions have nothing to
read. A reading that emitted a level today would be inventing two fifths of
it. So v0's deliverable is the **gap map**, not the positioning — see §7.

---

## 4. The hard problem: a block asks about the person

Every existing subtype asks about someone else. That is not an accident of
the inventory; it is the evidence discipline working. A question that asks a
person to rate their own seniority invites self-flattery, and self-flattery
captured verbatim is still captured — the echo card (BP-06) protects the
*wording*, not the *truthfulness*.

Two ways out. Only one is acceptable:

1. **Ask directly, mark it down.** "Would you say you operate at a staff
   level?" captured as `HYPOTHESIS` at reduced confidence. Cheap, and it
   poisons the profile with exactly the assertions invariant 2 exists to keep
   out.
2. **Story-first, level-later.** Never ask for a level. Ask for a concrete
   artifact and its circumstances — *"name the largest thing you shipped
   where you set the direction, rather than someone else"* — capture the
   answer verbatim as evidence, and let the **reading** map stories onto
   rungs, citing the person's own words. The person never asserts a level;
   the rubric does, in public, showing what it read.

**(2), always.** It is the same move `my_values` already makes — never ask
for the score, compute it — and the audit supports it empirically: across 40
achievements there is not one self-assessed seniority claim. The workspace's
story-not-rating discipline is currently intact, and a block that asked
"are you an L6?" would be the first thing in it to break that.

**Copy rule, extending UX-0001 §9:**

| Say | Never say |
|---|---|
| "Name the largest thing you shipped where you set the direction." | "What level do you think you're at?" — invites the assertion the reading exists to derive. |
| "Here's what the rubric reads from your own words, and what it can't see." | "You're an L6." — a bare rung with no visible derivation. |

---

## 5. A third capture mechanic: elaborate on an existing item

The audit's most useful structural finding. The gaps are all **how**
questions, and every one of them attaches to an achievement that *already
exists* in the profile:

| Gap | The question | Attaches to |
|---|---|---|
| Autonomy | "When you took this on, how much was already decided?" | an existing achievement item |
| Influence | "Who else had to change what they were doing for this to work?" | an existing achievement item |
| Ambiguity | "What did people think the problem was, before you started?" | an existing achievement item |

That is a **third capture mechanic**, alongside the two in
`PROFILE-BOOTSTRAP-DESIGN.md`:

- **Reaction** (v0) — stimulus is fetched content; target is a URL or file.
- **Nomination** (v1) — stimulus is a name; no fetch.
- **Elaboration** (proposed) — stimulus is **an item already in this
  workspace**; target is its `item_id`.

Elaboration is better UX than either: no blank page, no recall burden, the
person is looking at something they already told us they did. It is also the
cheapest of the three to build — there is nothing to fetch and nothing to
validate beyond "does this `item_id` resolve".

**Open mechanical question.** An elaboration is evidence about the *same*
achievement, not a competing claim about it. Does it become its own
`ProfileItem` linked to the parent, or an additional `EvidenceSpan` on the
parent item? The first keeps supersession simple and is consistent with
every existing capture; the second models the relationship honestly but
means an interview answer mutating a resume-derived item, which RFC-071
explicitly refuses for achievements. Leaning to the first, with an explicit
parent reference. See §10 Q3.

---

## 6. Evidence tiering: who is vouching

**Corrected during v0's implementation.** This section originally said
"reuse RFC-073's Observed/Endorsed", and that was wrong on semantics. The
correction is recorded rather than quietly rewritten, because the mistake is
instructive: two tier vocabularies can look interchangeable and be about
completely different things.

The only influence evidence in the audited workspace came from LinkedIn
recommendations — "revolutionized the way we do business", "effective manager
and mentor". That is real evidence and it should be citable. It is also
somebody else's unfalsifiable praise, and it must not be weighed like a
shipped artifact.

RFC-073's tiers do **not** express that. `OBSERVED` vs `ENDORSED` describes
**how a note was produced** — the person's own words versus a model-drafted
synthesis they reviewed and confirmed. That is orthogonal to who is
vouching: a testimonial is verbatim, so it is `OBSERVED` under RFC-073 while
being exactly the third-party praise this distinction exists to hold at arm's
length. Borrowing the enum would have made every gap map claim something
untrue about its own evidence. (RFC-073 itself rejected reusing
`ClaimClassification` for the same class of reason.)

So the axis is **voice**, and it gets its own vocabulary
(`domain.rubric.EvidenceVoice`):

- **First-party** — achievements, roles, corpus documents, and the person's
  own elaborations. Things with an artifact or a first-person account behind
  them.
- **Third-party** — testimonials and recommendations. Cited, visibly
  distinguished, and never sufficient on their own to move a dimension.

A dimension carried **only** by third-party evidence must say so in the
reading — `Coverage.THIRD_PARTY_ONLY` is a distinct verdict from `THIN` for
this reason, since the fix differs: thin wants more of the same, third-party-
only wants a different kind. "Three people say you're a great mentor" and
"you rewrote how two teams ship" are not the same claim, and a reading that
renders them identically is polished fiction.

---

## 7. Build order

Revised by §3. The original proposal — "read existing evidence first, no new
questions" — assumed the profile would have material on all dimensions. It
does not.

**v0 — Rubric + gap map. No new questions, no positioning.**
Store one rubric with honest provenance. Read the existing profile and corpus
against its dimensions. Emit: which dimensions have evidence, which have
none, and the cited items behind each. Explicitly **do not** emit a rung.
Useful on its own (it is a "what is my profile missing, and for what
purpose" report that nothing else produces), and it is the only slice that
can ship without the capture work.

**v1 — The block, via elaboration.**
v0's gaps become the question script — the design's nicest property survives
the audit, just one slice later than proposed. Story-first (§4), elaboration
mechanic (§5), captured through `capture_interview_reaction` with
block-scoped subtypes. At the end of v1 a reading has something to read on
every dimension, and positioning becomes defensible.

**v2 — Registry and wiring.**
Generalise to a block registry, proven by a **second rubric of a different
kind** — not a second ladder. If the only rubric it ever serves is a ladder,
this should be called a ladder and the registry is over-engineering (§10 Q4).
Then wire into the flows in §8.

This ordering also honours AGENTS.md's "usable vertical slices" and the
standing rule against building automation on a rule validated once: v0
produces the evidence that tells us whether v1's questions are the right
questions.

---

## 8. Wiring into existing flows

Deferred to v2, listed here so the seams are known while v0/v1 are built.

| Surface | Wiring |
|---|---|
| `wingman_flow` | A routing destination — "measure me against something" is a distinct front-door intent from "build my profile" |
| `perspectives_start` | **Not** a sixth option on that card. It is already five, and blocks are a different intent. A block gets its own entry point that `wingman_flow` routes to (BP-09: reachable, never gated) |
| `completeness` | Unevidenced dimensions become "Things to do" entries — the report already leads with an ordered gap list, and this is exactly that shape |
| `assess_job` / fit brief | A reading becomes a citable input, the same way RFC-066's work view already is. A posting with its own levelling language is a rubric candidate (§10 Q1) |
| `answer_bank` / `pack` | Elaboration answers *are* the STAR material an interview loop asks for. Captured once, reused |
| `artifacts` / `values_chart` | A dimension reading renders on the same radar machinery, with `artifacts action='stale'` already able to say when the code that drew it has moved on |
| `overnight` / `digest` | Re-read when new evidence lands; a dimension crossing from unevidenced to evidenced is a digest-worthy change |

---

## 9. Goals and non-goals

**Goals.**

1. Measure a person against a **named, sourced, external** rubric, and show
   what was read.
2. Derive questions from the rubric's gaps rather than authoring a fixed
   inventory.
3. Reuse the existing capture pipeline unchanged — no second path to
   provenance.
4. Add a rubric without changing code.

**Non-goals.**

1. **Not a levelling oracle.** The output is a positioning with citations
   and stated gaps, not a rung handed down. See §10 Q2.
2. **Not a performance-review tool.** This reads one person's own workspace
   at their own request. Nothing here is for evaluating somebody else, and
   coaching mode's persona scoping is a containment boundary, not a
   management feature.
3. **Not a claim to reproduce any employer's internal process.** A rubric is
   a public reconstruction and says so (§10 Q1).
4. **No new capture pipeline.** If a block needs a mechanic
   `capture_interview_reaction` cannot express, that is a reason to extend
   it, not to bypass it.

---

## 10. Open questions

**Q1 — Where does rubric content come from, and how is its provenance
honest?**
A well-known employer's internal ladder is not published in full; what
circulates publicly is reconstruction. A rubric must carry that on its face —
"reconstructed from these public sources, not the employer's own document" —
or it is polished fiction with a company's name on it (invariant 9).
A strong alternative worth costing: source rubrics from **job postings' own
levelling language**, which is first-party, already flows through the
opportunity pipeline, and carries its own URL. Possibly both, with the tier
visible in every reading that cites the rubric.

**Q2 — Does a reading ever emit a rung?**
"You are an L6" that is wrong is worse than no answer, and the audit shows
the inputs are uneven enough to make it wrong often. Proposed default:
positioning plus gaps, no single rung, and a refusal below an evidence floor
— the same posture `my_values` takes. Revisit only once v1 captures exist on
every dimension.

**Q3 — Elaboration storage: linked item, or evidence on the parent?**
See §5. Leaning to a linked item with an explicit parent reference, because
RFC-071 refuses interview amendment of achievement items and the second
option walks straight into that.

**Q4 — Is "block" the right generalisation, or is this just a ladder?**
The registry only earns its complexity if a second rubric *of a different
kind* — a competency matrix, a compensation band, a domain-skills grid —
fits the same three layers without special-casing. v2 should be blocked on
demonstrating that, and if it cannot be demonstrated, this should collapse
into a single purpose-built ladder feature.

**Q5 — Do rubrics ship with the code, or live in the workspace?**
Shipped means curated, versioned with releases, and identical for every
tenant. Workspace-local means editable and per-tenant, and immediately
raises what a coached persona's rubrics are and who may write them. Not
urgent for v0 (one rubric, shipped) but it decides v2's shape.

**Q6 — What happens when a reading contradicts the person's self-image?**
This is the first wingman surface that tells somebody something unwelcome
about themselves. The tone rules in UX-0001 §9 were written for capture, not
for delivery of a result. A reading that says "nothing here evidences
cross-team influence" needs its own copy rules, and probably its own framing
as *what the evidence shows*, never *what you are*.

---

## 11. Revisit if

- A second rubric cannot be expressed in the same three layers → Q4 resolves
  toward a purpose-built ladder, and this document collapses.
- v0's gap map turns out to be empty or trivially uniform across workspaces →
  the derive-questions-from-gaps property is worthless and v1 reverts to an
  authored inventory.
- Elaboration proves to need its own capture path rather than
  `capture_interview_reaction` → the "no new pipeline" non-goal is the thing
  to re-argue, in public, before writing the path.

---

*Wingman · proposal · evidence before assertion · partial truth over polished fiction.*
