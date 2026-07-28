Copy everything below this line into a tool or send to a person with live
web access. It's self-contained — no access to the `wingman` repo is
needed to do the research; only to file the result back.

---

I'm researching a career-tool feature design: a structured onboarding
interview that bootstraps someone's professional values/perspective profile
by asking them a small set of questions, *without* requiring them to have
pre-existing published writing (a blog, articles, etc.) to draw evidence
from. The product's existing design already has one working mechanic for
this — show the user something, have them react (agree/disagree) and
explain why in their own words, and *that explanation* (never the thing
they reacted to) becomes evidence of their own position. That part is
settled; what's being researched now is a proposed three-category structure
for what to actually ask.

## The three categories

1. **Values.** A "three people, living or dead, you'd have dinner with"
   style question, and why.
2. **Mission alignment.** Three companies whose products you'd be proud to
   buy, or where you could see yourself working. This one does double duty
   as a *fallback*: if someone can't name people directly for category 1,
   "name products you're proud to buy" is offered as a substitute.
3. **Alignment of perspective.** Reacting to specific people's
   intellectual, ethical, or professional perspectives — what you agree
   with in their thinking, and what they tend to focus on.

## Constraints your findings should be scored against

- Whatever technique you find, the *only* thing that can end up as evidence
  of the person's own position is their own words — never content they
  reacted to, quoted back as if it were their own stance. A technique that
  blurs this is a bad fit regardless of how well it otherwise works.
- No technique that pressures out a plausible-sounding but ungrounded
  answer — partial truth is preferred over a polished but unsupported one.
- This needs to work async, in a local tool, without a live human
  interviewer or a hosted assessment service. A rich technique that
  requires either is still worth citing (flag it as "not directly usable
  as-is"), just don't recommend it as a drop-in fit.
- The first-touch version of this needs to be genuinely light — a couple of
  minutes, not a guided 45-minute reflection exercise. Longer, deeper
  techniques are welcome findings, just flag them as later-stage material.

## Research questions — answer all six

1. **Prior art for the three-way split itself.** What existing frameworks —
   career coaching, executive/leadership assessment, culture-fit hiring
   interviews, personality/values instruments (e.g. Schwartz Theory of
   Basic Values, StrengthsFinder/CliftonStrengths, structured behavioral
   interviewing, culture-add vs. culture-fit hiring guides) — use a similar
   values / mission-or-organization-fit / perspective-or-worldview split?
   What do they call these dimensions, and is there an existing, better
   name or structure this design should adopt or explicitly differentiate
   from?
2. **The "dinner guest" device specifically.** Is "who would you have
   dinner with, living or dead" a validated values-elicitation technique,
   or mainly known as a cliché interview/icebreaker question that reliably
   produces rehearsed, socially-desirable answers (Gandhi/Einstein/a parent,
   per common critique)? If the latter is documented, what alternate
   phrasings or follow-up techniques are known to defeat that rehearsed-
   answer failure mode?
3. **"Products you're proud to buy" as a values proxy and as a fallback.**
   Does consumer-brand affinity validly proxy for professional/mission
   values in any established methodology (brand-values research, employer-
   branding literature), or is it mainly known to conflate consumer taste,
   price sensitivity, and aspirational purchasing with actual values? What
   failure modes are documented? Is there prior art for using this specific
   fallback when someone can't name people for a values question?
4. **Utility beyond profile-filling.** In career coaching, hiring, or
   culture-fit research, what do values/mission-fit/perspective-alignment
   answers predict or correlate with beyond "we captured some evidence" —
   e.g. job/culture fit outcomes, retention, coaching engagement, match
   quality, rapport? Is there documented evidence this kind of structured
   elicitation has real downstream predictive value, or is that claim
   mostly asserted by vendors without evidence behind it?
5. **Reacting to a company's own content as a fit signal.** Does prior art
   exist for asking a candidate to react directly to a company's public
   content/products/mission statement as a fit signal (vs. resume keyword
   matching or algorithmic similarity scoring)? How does it compare on
   validity, susceptibility to gaming (telling the interviewer what they
   want to hear), and bias, against an indirect embedding-similarity
   approach?
6. **Bias and fairness pitfalls specific to this elicitation style.** Since
   this data eventually informs job-search matching, what documented
   pitfalls exist in values/mission/perspective-based interview techniques —
   social-desirability bias, cultural specificity of devices like "dinner
   guest," or values-based screening amplifying interviewer/organizational
   homogeneity? Name the risk surface; you don't need to solve it.

## Output format

For each question, produce a table with columns: `Entry | What it is |
Concept overlap with the design above | Verdict`. Verdict must be exactly
one of: `adopt/reference`, `differentiate`, or `ignore` — no other values,
and no rationale inside the table (put rationale in a Notes paragraph under
the table). If a question turns up nothing, still include one row reading
`none found`, with the reason (searched and confirmed absent / ran out of
time / paywalled or inaccessible) in Notes.

End with an "Open gaps" section: what you couldn't answer or only answered
partially, and why.

Prefer primary sources — published assessment instruments, peer-reviewed or
practitioner literature on interview-technique validity, vendor
documentation for culture-fit/hiring tools — over general blog-post
summaries. Note the access date on everything. Label factual claims
distinctly from your own inferences.
