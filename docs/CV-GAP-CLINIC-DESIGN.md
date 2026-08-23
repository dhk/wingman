# The CV gap clinic — a shareable slice, and what it may honestly learn (proposal, not yet an RFC)

**Status.** Proposed 2026-08-22 from an ideation session (issue #452). Nothing is built.
Depends on the gap map (#436, PRs #449/#450) being merged. This document
exists mainly to record the **reframe** in §2 and the constraints in §5–§7,
because the version of this idea that occurs to you first is the one that
fails.

**Related.** [`QUESTION-BLOCKS-DESIGN.md`](QUESTION-BLOCKS-DESIGN.md) — the
gap map this shares, and Q2 (does a reading ever emit a rung), which this
document must not quietly resolve on its own.
[`research/career-ladder-rubrics/`](research/career-ladder-rubrics/README.md)
— which frameworks are actually publishable.

---

## 1. What this is

A narrow, shareable slice of wingman, offered to a professional community —
Lenny's product-manager group was the prompting example — to get real
feedback on whether rubric-based reading of a career actually works on
people who are not its author.

The pitch, as it should be worded:

> **"Send me your CV. I'll tell you which dimensions of a published career
> framework it can and cannot evidence — and the questions that would close
> the biggest gaps."**

Not the whole tool. No workspace, no install, no account. One document in,
one gap map out.

---

## 2. Why it does NOT say "what level are you"

The idea's natural first form is *"I'll work out which ladder you're on and
where you sit on it."* That form is rejected, for four reasons that compound:

1. **It pre-empts an open question.** `QUESTION-BLOCKS-DESIGN.md` Q2 is
   unresolved, and the gap map deliberately emits no rung. Shipping a
   level to a community would settle Q2 by press release, on *less*
   evidence than the workspace that motivated the caution.
2. **The feedback would be worthless for the stated purpose.** If the tool
   hands somebody a level, their response sorts by whether they liked it.
   Flattering results read as "accurate"; correct-but-unwelcome results read
   as "broken". That measures agreeableness, not validity. *"Is this gap
   real?"* is falsifiable by the one person who knows. *"Is L5 right?"* is
   not.
3. **It would be cited as a credential.** "Assessed at staff level by X" is
   a claim nobody running this has standing to confer and no employer
   recognises. A level travels; a gap map does not.
4. **It is emotionally loaded, at scale, to strangers.** Q6 of the design
   doc flags this for a single consenting user. A community adds
   comparison, screenshots and group chat.

**Rule.** No rung, no score, no ranking, no percentile, no "you look
senior". If a participant asks directly, the honest answer is that the tool
does not do that and why.

---

## 3. What a participant gets

1. A gap map against a **named, published** framework, with its provenance
   tier and licence shown (today: Dropbox, `first_party`, Apache-2.0).
2. Per dimension: what their CV evidences, what it does not, and the
   citations behind each.
3. For each gap, the **probe** — the question that would close it.
4. The standing caveats, unedited: a CV records what you produced, a
   framework measures how you worked; a dimension carried only by
   third-party praise is reported as exactly that.

That is the whole product. It is useful, it is novel, and it promises
nothing it cannot show its working for.

---

## 4. The default path stores nothing

The strongest privacy design is the one where the interesting question does
not arise. **By default, a run holds no personal data at all**: the CV is
read, the gap map is returned, and nothing is retained.

Contribution is a **separate, explicit, second act** — never a checkbox on
the first screen, and never the default.

### What a contribution may contain

| Collected | Never collected |
|---|---|
| Role family, self-declared (e.g. "product management") | The CV itself |
| Years-of-experience band, self-declared | Employer names, dates, titles |
| Per dimension: coverage verdict, and first/third-party counts | Patent numbers, URLs, publication titles |
| Rubric id and version it was read against | Names, emails, any contact detail |
| Optionally, probe answers — only after a verbatim review step | Anything the participant did not see on screen first |

The contributed record is derived, small, and structurally incapable of
carrying a career history.

**Why this matters more than it sounds.** A CV is close to maximally
re-identifiable — employer, title, dates and one distinctive achievement
identify a person outright. One real profile read during this project's
audit contains a granted US patent number, which identifies exactly one
human being. Stripping names is not anonymisation, and k-anonymity over
employment histories is fragile. The mitigation is not better redaction; it
is **not holding the document**.

---

## 5. Consent, and the obligations that come with it

Holding other people's career data makes the operator a data controller,
whatever the project's size. Consent is necessary and not sufficient.

- **Purpose stated before collection**, in one sentence, and never widened
  afterwards without re-consent.
- **Deletion on request**, with a contact route that works, and a stated
  turnaround.
- **Retention limit** decided up front, not "until we think of something".
- **No onward sharing** of individual records — aggregates only.
- **Show the exact record** that would be contributed, before it is, in the
  same echo-before-save spirit as `interview_react` (UX-0001 BP-06).

If any of these cannot be honoured, the contribution path should not ship.
The gap map alone still works and still gets feedback.

---

## 6. The crowdsourcing is circular unless the input is un-rubricked

The tempting version — assess people against a rubric, then aggregate the
results into a ladder for that role — **cannot work**. The output would be
contaminated by the input: reading a hundred PMs against a rubric and
aggregating their placements measures the rubric, not the profession, and
would present that as independent corroboration.

**The fix is to collect the wrong-looking thing.** What can honestly be
aggregated:

- **Which dimensions people can and cannot evidence.** "78% of product
  managers who ran this could not evidence ambiguity" is a real finding
  about how PMs describe their work, and it does not presuppose a ladder.
- **Probe answers** — the *how* stories. These are raw material a ladder
  could later be derived from, precisely because they were not produced by
  one.

What must never be aggregated into a ladder: level placements, coverage
verdicts treated as scores, or anything the tool itself decided.

There is a second-order version of the same trap: if the rubric shapes the
probes, and the probes shape the stories, the stories inherit the rubric's
frame. That is weaker contamination than aggregating verdicts, but it is
real, and any published finding should say which probes produced it.

---

## 7. What may and may not be claimed about the sample

Lenny's community is paid, self-selecting, ambitious, seniority-skewed,
heavily US/tech, and English-speaking. Any aggregate describes **that
population**. Presenting it as "the product-manager ladder" is how a tool
quietly encodes whose career counts as senior.

Requirements on any published aggregate:

- State the population and how it was recruited, in the same breath as the
  finding.
- Publish **n**, per cell, and suppress small cells rather than rounding
  them.
- Call it a community aggregate, never a framework, and never brand it with
  a company name.
- Recruit outside one community before claiming anything about a role
  generally.

---

## 8. Where it runs — not inside wingman

Wingman's invariant 3 is *local-first private data*. A service that receives
other people's CVs inverts that. This is not a wingman feature with a web
front end; it is a **separate deployment with its own threat model**, which
imports the gap map as a library.

Consequences:

- Its own repository, or at minimum its own deployable, with its own
  secrets, logging policy, and retention story.
- No tenant workspace, no `ProfileItem`, no writes into anybody's wingman.
- Wingman's own tests and invariants must not be relaxed to accommodate it.

An honest lighter-weight start: **run it by hand.** People send a CV, the
operator runs the gap map locally, replies with the report. No service, no
storage, no consent machinery — and it answers the actual question (does
this land for other people?) before anything is built.

---

## 9. What would have to be built

Ordered, cheapest first. Steps 1–2 are enough for the manual version.

1. **Gap map from a document, without a workspace.** Today's path requires
   an ingested profile in a tenant database. This needs
   `text → dimension coverage` with no persistence — a real gap in the
   current code, and the only strictly necessary piece.
2. **A shareable rendering.** The Markdown report is fine for a manual
   round; a self-contained HTML page is nicer and reuses the existing
   design tokens.
3. *(Only if the manual round works)* an intake path, the contribution
   record of §4, the consent flow of §5, and aggregate reporting with
   suppression.

Note that #451 — the gap map's signals are miscalibrated, and `ambiguity`
matched nothing at all in a real 240-line profile — **lands directly on
this**. Running a lexically blind matcher over strangers' CVs would
manufacture gaps that are artifacts of vocabulary and then ask people
questions about them. #451 should be resolved, or its limits stated on
every report, before anyone outside this project sees output.

---

## 10. Open questions

**Q1 — Manual round first, or build the intake?** Strong prior for manual:
it tests the hypothesis with zero privacy surface and zero code beyond §9.1.

**Q2 — Which framework do PMs get read against?** Dropbox's SWE ladder is
wrong for them. GitLab publishes a product-manager ladder, but its licence
is unverified (see the research). Resolving that licence is a prerequisite
for a PM-facing round.

**Q3 — Who is the controller, legally?** An individual operator, a company,
or nobody-because-nothing-is-stored. §4's default makes the third answer
available and it is by far the cheapest.

**Q4 — What is the falsifiable claim?** Suggested: *"a gap map tells a
professional something true about their CV that they did not already know."*
Measurable by asking exactly that, once, per participant.

**Q5 — What happens when the report is wrong about somebody?** It will be.
There needs to be a stated route for "this is wrong", and a habit of
treating those as the most valuable responses rather than as complaints.

---

## 11. Kill criteria

Stop, rather than iterate, if:

- Participants consistently want the level and find the gap map
  unsatisfying without one — the honest conclusion is that the market wants
  a thing this project declines to build.
- The gaps reported turn out to be dominated by vocabulary rather than
  substance (#451) and the matcher cannot be fixed to the point where a
  stranger's report is defensible.
- The consent, retention or deletion obligations of §5 cannot be met by
  whoever is actually operating it.

---

*Wingman · proposal · evidence before assertion · partial truth over polished fiction.*
