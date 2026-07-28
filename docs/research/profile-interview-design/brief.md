# Research Briefing: Three-Category Interview Structure for Profile Bootstrap

**Purpose.** `docs/PROFILE-BOOTSTRAP-DESIGN.md` (merged, not yet built) proposes
a "stimulus → reaction → reasoning" mechanic so a non-writer can bootstrap a
quote-backed profile without pre-existing published writing. A follow-up
ideation pass (2026-07-27) sharpened its loose "interview modules" section
into three named categories. This briefing instructs a researcher — human or
AI agent with live web access — to pressure-test that categorization against
prior art, and to look past the immediate use ("fill in a profile") toward
what else it might be worth, per the explicit direction that motivated this
research: *"there's more utility than just filling in here."*

> Container note: run this from an environment with general web egress, then
> bring findings back as a file under `findings/` in this same directory,
> following `findings/TEMPLATE.md` exactly.

---

## 1. The three categories under investigation

As proposed 2026-07-27, refining `PROFILE-BOOTSTRAP-DESIGN.md`'s "Interview
modules" section (which currently has three looser, unnamed modules: agree/
disagree pairs, the three-people question, and a company-alignment variant):

1. **Values.** A "three people, living or dead, you'd have dinner with"
   style question, and why. Reframes the design doc's existing "three
   people you'd want to be professionally associated with" (asked in
   ask-#2-first order to dodge the rehearsed front-loaded answer).
2. **Mission alignment.** Three companies whose products you'd be proud to
   buy, or where you could see yourself working. New relative to the design
   doc's existing "company-alignment variant" (which was about reacting to
   one *target* company's content during a job search) — this is broader
   and general-purpose. It is also proposed as a **fallback stimulus**: if
   someone struggles to name people directly in round one, "name products
   you're proud to buy" substitutes.
3. **Alignment of perspective.** Reacting to specific people's
   intellectual, ethical, or professional perspectives — what they agree
   with, and what those people focus on. Maps most directly onto the
   design doc's core "agree/disagree pairs" loop.

## 2. Constraints findings must be scored against

From `AGENTS.md`'s product invariants and `PROFILE-BOOTSTRAP-DESIGN.md`
itself — a technique that scores well on "elicits signal" but fails these is
not a fit, and findings should say so rather than recommend it anyway:

- **Evidence before assertion (RFC-005).** Whatever this produces must
  remain the user's own verbatim words as the only evidence for their own
  profile — never the submitted stimulus content quoted back as if it were
  the user's position (the design doc's "one hard rule").
- **No invented familiarity; partial truth over polished fiction.** A
  technique that pressures a plausible-sounding but ungrounded answer out
  of someone is a bad fit even if it "works" for engagement.
- **Local-first, human-approved, no autonomous action.** Out of scope:
  any technique that assumes a live interviewer, real-time adaptive
  branching requiring a hosted service, or third-party assessment APIs
  Wingman would need to call. Score for whether the *technique* is soundly
  adaptable to an async, deterministic-where-possible, local tool, not
  whether the platform that invented it is.
- **Light-touch, reciprocal, day-one (design doc Goals).** A technique
  requiring 45 minutes of guided reflection is real prior art worth citing,
  but should be flagged as tier-3 material, not a fit for the design's
  tier-1 zero-commitment rung.

## 3. Research questions

Each traces to a specific section of `PROFILE-BOOTSTRAP-DESIGN.md` or to the
categorization above — don't wander into general "AI onboarding" scans that
don't answer one of these.

1. **Prior art for the three-way split itself.** What existing frameworks —
   career coaching, executive/leadership assessment, culture-fit hiring
   interviews, personality/values instruments (e.g. Schwartz Theory of
   Basic Values, StrengthsFinder/CliftonStrengths, structured behavioral
   interviewing, culture-add vs. culture-fit hiring guides) — use a similar
   values / mission-or-organization-fit / perspective-or-worldview split?
   What do they call these dimensions, and does an existing, better name or
   structure exist that this design should adopt or explicitly differentiate
   from? *(→ design doc "Interview modules")*
2. **The "dinner guest" device specifically.** Is "who would you have
   dinner with, living or dead" a validated values-elicitation technique,
   or is it known mainly as a cliché interview/icebreaker question that
   reliably produces rehearsed, socially-desirable answers (Gandhi/Einstein/
   a parent, per common critique)? If the latter is documented, what
   alternate phrasings or follow-up techniques (beyond this design's
   existing ask-#2-first trick) are known to defeat that rehearsed-answer
   failure mode? *(→ design doc's ask-#2-first rationale)*
3. **"Products you're proud to buy" as a values proxy and as a fallback.**
   Does consumer-brand affinity validly proxy for professional/mission
   values in any established methodology (e.g. brand-values research,
   employer-branding literature), or is it primarily known to conflate
   consumer taste, price sensitivity, and aspirational purchasing with
   actual values? What failure modes are documented? Is there prior art for
   using this specific fallback when a values-elicitation subject can't
   name people? *(→ design doc's "no-examples fallback" open question, and
   the new mission-alignment category's stated dual role)*
4. **Utility beyond profile-filling.** In career coaching, hiring, or
   culture-fit research, what do values/mission-fit/perspective-alignment
   answers predict or correlate with beyond "we captured some evidence" —
   e.g. job/culture fit outcomes, retention, coaching engagement, match
   quality, interviewer/interviewee rapport? Is there documented evidence
   this kind of structured elicitation has downstream predictive value, or
   is that claim mostly asserted by vendors without evidence? *(→ this
   session's explicit framing: "there's more utility than just filling in
   here" — the design doc's current Motivation section only frames this as
   POV-bootstrap machinery, not as having this broader utility)*
5. **Reacting to a company's own content as a fit signal.** Does prior art
   exist for asking a candidate to react directly to a company's public
   content/products/mission statement as a fit signal (vs. resume keyword
   matching or algorithmic similarity scoring)? How does it compare on
   validity, susceptibility to gaming (telling the interviewer what they
   want to hear), and bias, against Wingman's existing indirect
   embedding-based `company_alignment()`? *(→ design doc Goals: "an
   alternative to indirect embedding-based alignment", and the "Company-
   alignment variant" interview module)*
6. **Bias and fairness pitfalls specific to this elicitation style.** Since
   this data eventually informs job-search matching, what documented
   pitfalls exist in values/mission/perspective-based interview techniques —
   social-desirability bias, cultural specificity of devices like "dinner
   guest," or values-based screening amplifying interviewer/organizational
   homogeneity? This is a risk surface to name, not necessarily to solve
   here. *(→ AGENTS.md's "no invented familiarity" and "evaluation
   precedes increased autonomy" invariants)*

## 4. Method notes

- Prefer primary sources — published assessment instruments, peer-reviewed
  or practitioner literature on interview technique validity, vendor
  documentation for culture-fit/hiring tools — over general blog-post
  summaries. Note the access date on everything.
- Label claims **[fact]** (documented, dated, sourced) vs. **[inference]**
  (your read) — same house style as `docs/RESEARCH-BRIEFING.md`'s earlier
  pass.
- Deliver findings as `findings/<source-slug>-findings.md`, following
  `findings/TEMPLATE.md` exactly — one section per question above, in
  order, each with the required table and a Notes block, plus a final
  "Open gaps" section. See `findings/README.md` for the submission format.
