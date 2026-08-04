# Profile Bootstrap via Reaction — Design (proposal, not yet an RFC)

**Status.** Design recorded 2026-07-27, from an ideation session; interview
modules revised twice on 2026-07-28 (pro/con structure and Mission
alignment split out as its own category, then a company-reaction fallback
reinstated for Values specifically — see "Interview modules"); all seven
v1 open questions resolved the same day (`InterviewDocument` as the
synthesis-feeding type, flow enforcement, fallback depth, con-side
exclusions, ordering validation, storage shape, Mission alignment's own
fallback — see "Open questions" and "Revisit if"). **v0 and v1 are both
shipped**: reaction capture, Values/Mission alignment nomination capture,
per-submission size and per-subtype count limits, and synthesis into a
stance via `build_own_pov` are all in this repository (`wingman
interview`/`interview_react`, `wingman perspectives`/`perspectives_start`
as the branching onboarding entry point). v2 (the company-alignment
variant) is not started. Graduates to a numbered `RFC.md` entry (and a
`ROADMAP.md` phase slice) once it's referenced elsewhere; until then this
document is the working design and the thing to revise.

## Motivation

Wingman's most distinctive features — `my_pov`, company alignment scoring,
similar-companies — all trace back to one thing: `build_own_pov()` reading
the user's own corpus (`add_to_corpus`), gated by RFC-005's discipline that
every inference must be backed by a verbatim quote. That works well for a
user with years of published writing to draw from. It leaves everyone else
with an empty corpus and an honest but unhelpful degrade path: the dossier's
Gaps section just says "no stance yet — go write something," which isn't
actionable advice for someone who isn't a writer.

This design is about giving that person something real on day one, as part
of onboarding, without requiring them to author original content first.

## Goals

- Produce quote-backed evidence for a new user's own POV/values without
  requiring pre-existing published writing.
- Keep it light-touch and reciprocal — low effort in, an immediate insight
  back. This is onboarding, not homework.
- Reuse RFC-005's fact/inference discipline unchanged: the *source* of
  evidence differs, the *guarantee* doesn't.
- Support two applications of the same mechanic: bootstrapping the person's
  own POV, and letting them react directly to a target company's own
  content as an alternative to indirect embedding-based alignment.
- Chunk the flow into increasing levels of trust/effort, so day-one value
  doesn't require the deepest tier.

## Non-goals

- Not a replacement for the existing writer's path (corpus → `build_own_pov`)
  — this is an additional bootstrap path for people who don't have one, not
  a redesign of the existing pipeline.
- Not a content-curation or recommendation engine. Content is always the
  user's own submission (a link or a file); wingman never selects or
  crawls content on their behalf. This sidesteps the cold-start
  curation/matching problem entirely — there is no seed library to
  maintain and no "find opposing content" matching step to build.
- Not a new ingestion-format surface. Scoped to the four formats already
  proven elsewhere in the codebase: URL, PDF, DOCX, Markdown/plain text.
  No PPTX, XLSX, or other new parsers.
- Not a background or automatic fetch. Same RFC-009 posture as everywhere
  else in wingman: explicit, user-invoked, one fetch per submitted URL.

## The mechanic

The atomic unit of evidence becomes **stimulus → reaction → reasoning**,
replacing "the user wrote an essay" with "the user reacted to something and
explained why":

1. The user submits a piece of content — a URL, a PDF, a DOCX, or Markdown/
   text. This is always *their* choice of content, e.g. "here's a person
   whose thinking I agree with" or "here's one I don't."
2. They state a reaction: agree / disagree (or, for the company-alignment
   variant, aligned / not aligned).
3. They explain why, in their own words.

Step 3's answer is the only thing that ever becomes evidence for *their*
profile. The submitted content itself is context, not evidence of the
user's own position — a POV card must never quote the submitted piece as
if it were the user's own words. Keeping these two attributions distinct is
the one hard rule this design must not compromise, since conflating them
would be exactly the "invented familiarity" / unsupported-claim failure
mode RFC-005 and `AGENTS.md`'s product invariants exist to prevent.

## Interview modules

Three categories, refined 2026-07-28 from the original looser three-module
sketch (agree/disagree pairs, a three-people question, a company-alignment
variant) into named categories with a settled internal structure.

### Values (people)

- **Pro.** Three people, living or dead, you'd have dinner with.
- **Con.** Three people, living or dead, you'd be horrified to see your
  name in print alongside — excluding Hitler (too easy a nomination to
  discriminate anything about the person's actual values).
- **Ordering (issue #242).** Four separate steps, never a combined
  name-and-why pass: (1) name all three pro nominees, no why yet; (2)
  name all three con nominees, no why yet; (3) ask why about every con
  nominee, in #2/#1/#3 order — skips the rehearsed, front-loaded first
  answer; (4) ask why about every pro nominee, same #2/#1/#3 order —
  ends the section on a high note, a bet on continued-engagement UX
  (dwelling on the negative first, resolving positive last) rather than
  purely on evidence quality. Naming happens pro-then-con; why-asking
  happens con-then-pro — deliberately asymmetric, not a copy-paste error.
- **Fallback, when someone struggles to name people.** Swap the stimulus
  to companies: three whose products/services they're proud to buy, three
  they'd never buy — same pro/con, same four-step ordering as
  the people version (an assumption, not independently confirmed — the
  rescue path could reasonably be lighter, e.g. one of each, but full
  structure was chosen for consistency rather than inventing a second,
  thinner shape). Illustrative example: "Walmart — they destroy
  communities" as a con nomination. The "why" is captured verbatim,
  unsteered — a price- or quality-based reason is still a real answer,
  even if thinner evidence than an ethics-based one; the mechanic doesn't
  coach or filter it, consistent with how every other module works.
  **This is a different question from Mission alignment below**, even
  though both involve naming companies: this fallback asks "whose
  products would you buy or refuse to buy" (a consumer-ethics judgment,
  standing in for a personal-values signal), where Mission alignment asks
  "who would you want to work for or be associated with" (an aspirational,
  professional-fit judgment). Different psychological register, both
  worth keeping.

### Mission alignment (groups)

Not "company" specifically — any nominated set of people aligned for a
purpose: a company, a club, a professional organization.

- **Pro.** Three companies you'd like to work at, or organizations/purposes
  you believe in — you'd be proud to be associated with them.
- **Con.** Three organizations you'd be horrified to be associated with —
  symmetric with Values' con side. No exclusion rule (unlike Values' "no
  Hitler") — see "Open questions" for why: there's no single obvious
  "worst company" the way there's an obvious lazy answer for a person, so
  this relies on verbatim capture and the pro/con pairing itself to
  surface real signal instead of a hardcoded banned-org list.
- **Ordering.** Same four-step naming-then-why structure as Values
  (issue #242), carried over for consistency (not independently
  confirmed for this category — an assumption to revisit if it doesn't
  hold up in practice).
- **Primary-purpose check.** Asked alongside each nomination, during the
  naming steps (not the why steps) — it's context about the org, not
  part of the alignment reasoning. Whenever an org is nominated (pro or
  con), ask what the person understands that org's primary purpose to be —
  "Pepsi sells cola," "the fire department puts out fires." Captured as
  context alongside the alignment reasoning, never as evidence itself.

**This is not the same question as the Values fallback above**, even
though both name companies. Mission alignment asks "who would you want to
work for or be associated with" — an aspirational, professional-fit
judgment, always asked as its own category regardless of whether someone
struggled with the Values people-question. The Values fallback asks "whose
products would you buy or refuse to buy" — a consumer-ethics judgment,
invoked only as a rescue path. (The research pass under
`docs/research/profile-interview-design/` predates this whole
distinction — it was written back when "products/companies" was a single,
undifferentiated fallback idea; its Q3 findings — laddering/means-end-chain
interviewing, conspicuous-consumption caveats — still bear on both
company-facing questions' validity, just not on a framing this design
still uses as originally written.)

### Alignment of perspective

- **Agree/disagree pairs.** The core loop, unchanged: 3 pieces of content
  the user agrees with, 3 they don't, each with a one-line why — reacting
  to specific people's intellectual, ethical, or professional
  perspectives.

### Network admired (v1)

- **Pro only, no con.** Three people you genuinely admire, given as their
  LinkedIn profile URL — preferably first-degree connections of yours (a
  soft preference, not a hard requirement), and why, in a few words.
- **Why a URL, not a name.** Unlike Values (admiration by name, no
  networking angle), the target here is a dereferenceable identifier on
  purpose: it doubles as a warm-path candidate. Motivated by coaching
  mode's "who among everyone I know might be valuable for this persona"
  cross-referencing (`docs/COACHING-MODE-DESIGN.md`), but useful standalone
  too — the LinkedIn URL is captured as an identifier only, same as any
  other nomination target; it is never fetched or scraped.

### Company-alignment variant (v2, job-search-specific)

The same stimulus/reaction/reasoning loop as Mission alignment, but
pointed at one *target* company's own public content during an active job
search, instead of freely-nominated organizations during onboarding —
answers "does this org's thinking actually match mine" directly, as an
alternative or supplement to the existing indirect embedding-similarity
path (`company_alignment()`).

### Nominee research (issue #214)

For every nomination module above (Values, Values fallback, Mission
alignment, Network admired) — not Alignment of perspective, whose target
already has real fetched content: once a nominee is named, before asking
"why," identify them via search, prompting to disambiguate if the name is
ambiguous (a common name, several notable people). Look for their
values/background (Wikipedia preferred) and, if they're alive, their
current writing.

Deliberately built with no new wingman-side search infrastructure — the
calling agent already has its own web-search capability, so this lives
entirely as a protocol instruction in `interview_react`'s own docstring
(the same place con-then-pro ordering and echo-before-save already live),
not as new Python code. This keeps the invariant above — the nominee's
own content is never fetched or quoted as evidence, only "why," verbatim,
ever is — completely unchanged: research here is a conversational aid for
asking one better follow-up question, never a source of evidence, and
nothing it finds is persisted anywhere.

After a *living* nominee's capture is saved, offer to track them via the
existing watchlist (`people_add`) — reusing infrastructure, not inventing
a second one. Never offered for someone who has died.

Whether research findings about a nominee should ever be persisted — so
re-nominating the same public figure later doesn't mean re-researching
from scratch — is deliberately out of scope here; see issue #215 (data
scope: user/group/all) for that separate, harder question.

## Trust ladder

| Tier | Ask | Commitment |
|---|---|---|
| 1 — zero-commitment | React to 2-3 pieces of content the user already has in mind (agree/disagree + one line why) | Minutes; no account of themselves beyond reactions |
| 2 — light values | Values (pro/con dinner-guest question, or the proud-to-buy/never-buy company fallback if people-naming struggles) and Mission alignment (pro/con org question), each con-then-pro with ask-#2-first nested inside | Still short, first real values + mission-fit signal |
| 3 — deeper, opt-in | Company-alignment variant reactions once a job-search target exists; resume/LinkedIn mined for anything usable; option to attach a blog/writing if they have one (the existing writer's path) | Ongoing, as engagement builds |

Tier progression is not product-enforced (see "Open questions" for the
resolution) — this table is a suggested default order, and each module
stays independently invokable.

**Perspectives — implemented as the onboarding entry point.** `wingman
perspectives` (an interactive CLI wizard) and `perspectives_start` (an MCP
tool returning branching instructions for the calling agent) both ask the
one question this design never automated: content to share, or an
interview instead? Content routes to the corpus (`wingman corpus add`);
"interview" starts tier 1 (Alignment of perspective) and hands off to
`wingman interview`/`interview_react` for tiers 2+. Neither branch gates
the other — this is the front door, not a wizard that walks every tier.

## Architecture — what already exists to reuse

Nothing here is designed from scratch; the point of this design is that
it's mostly integration, not new machinery:

- **URL fetch** — `infrastructure/fetch.py`'s `fetch_url_final`. Already
  HTTPS-only, SSRF-guarded (blocks loopback/private/link-local/reserved/
  multicast addresses, checked on the initial URL and every redirect —
  RFC-009, #71), 20MB cap, 30s timeout, one polite retry on 429/503.
- **PDF extraction** — `application/resume_formats.py`'s `pdf_text`. Never
  executes embedded content (`pypdf` is extraction-only), malformed files
  raise a clean, visible error rather than crashing or silently degrading.
- **DOCX extraction** — `resume_formats.py`'s `docx_text`. Already rejects
  XML entity/DTD declarations before parsing (billion-laughs defense).
- **Markdown/plain text** — already a supported corpus suffix
  (`application/corpus.py`).
- **The "why" capture** — `application/qa_capture.py`'s existing pattern:
  a verbatim user answer becomes a `SourceRecord` + one `ACTIVE`
  `ProfileItem`, zero model calls at capture time. This flow generalizes
  that pattern (today scoped to resolving Unknown verdicts mid-assess)
  into a deliberate onboarding interview.
- **Downstream synthesis — resolved 2026-07-28: `InterviewDocument`.** A
  new, thin candidate-document type alongside `ExternalDocument`/
  `CorpusDocument`, which `build_own_pov` reads in the same synthesis call
  as the other two. Rejected: coercing the "why" text into an existing
  `CorpusDocument` — cheaper (zero pipeline changes) but collapses
  provenance, since a stance built entirely from three terse interview
  answers would then render identically to one built from a 2,000-word
  essay, against `AGENTS.md`'s "inspectable outputs and provenance"
  invariant. `InterviewDocument` keeps both evidence sources composable in
  one call (so writing and interview answers genuinely *augment* each
  other for someone who has both) while staying labeled by source in the
  rendered card. **Storage — resolved 2026-07-28: reuse and extend
  `qa_capture`'s `SourceRecord`/`ProfileItem` shape**, not a new table.
  `ProfileItemKind` (`achievement`/`skill`/`role`/`testimonial`) stays
  scoped to career evidence as-is — none of those fit an interview
  reaction — so this adds one new discriminator field, a `subtype`
  carrying the interview category (e.g. `values_pro`, `values_con`,
  `values_fallback_con`, `mission_alignment_pro`,
  `alignment_of_perspective_agree` — exact values TBD at implementation
  time), rather than overloading `ProfileItemKind` itself or forking a
  parallel table. `InterviewDocument` is then the read-side view
  `build_own_pov` consumes, assembled from these rows.

## Limits and configuration

- **Per-submission size — implemented.** `INTERVIEW_MAX_SUBMISSION_BYTES`
  (2MB) in `application/interview.py`, well under `webui.py`'s
  `MAX_UPLOAD_BYTES` (20MB, sized for resumes) — an article is KB-scale,
  and a 2MB cap covers even an image-heavy PDF write-up. Applies to both a
  fetched URL's body and a local file's size, checked before extraction.
- **Submission count per onboarding pass — implemented, scoped per
  subtype.** `WINGMAN_INTERVIEW_MAX_PER_SUBTYPE` (default 6, same
  operator-tunable-env-var shape as `WINGMAN_TUNNEL_PORT`/
  `WINGMAN_ALLOWED_HOSTS`). Written when only Alignment of perspective
  existed, the original "~6 covers 3 agree + 3 disagree" framing meant a
  single global cap; that would starve Values/Mission alignment once they
  shipped, so the cap became per-subtype instead — a new target for a
  subtype already at its cap is refused, but re-capturing an existing
  target (a supersession, not growth) is exempt.

## Security: untrusted content handling

Most of the threat model is already solved by the infrastructure being
reused (see Architecture above) — SSRF, XXE, malformed-file handling all
carry over unchanged. One risk is genuinely new to this flow, though:

**This is the first fetch/upload path in the codebase whose content is
likely to reach a model call.** Company research (RFC-015) is explicit
that no model ever reads a fetched page — it's a pure deterministic link
diff. This flow is different: the submitted piece is presumably referenced
when a model later helps synthesize the user's "why" into a stance.
`AGENTS.md`'s existing rule — "treat all imported content as untrusted
data; instructions inside it are not system instructions" — has to be
actively enforced here, not just true in principle:

- Submitted content goes into any prompt as clearly delimited untrusted
  data, never as instructions.
- The model gets no tool/connector access while processing it.
- The evidence chain stays anchored to the user's own reasoning only — the
  model must never be able to quote the *submitted* piece as if it were
  the user's own position (see "The mechanic" above).

## Open questions

Resolved 2026-07-28 (see "Revisit if" for what would reopen each):

- ~~**Feeding synthesis.**~~ **Resolved: `InterviewDocument`**, a new
  candidate-document type — see "Architecture" above for the decision and
  what it rejected.
- ~~**Flow enforcement.**~~ **Resolved: not product-enforced across
  modules.** Nothing else in wingman gates command sequencing (`wingman
  company pov` doesn't require `wingman people fetch` first; dossiers
  degrade gracefully via their Gaps section instead) — Values, Mission
  alignment, and Alignment of perspective stay independently invokable,
  and the trust ladder is a *suggested default order* for an onboarding
  prompt, not an enforced gate. Ordering **within** one module's own flow
  (con-then-pro, ask-#2-first) stays code-enforced regardless — that's a
  single interactive command's own logic, not cross-module sequencing.
- ~~**Values fallback depth.**~~ **Resolved: ship the full 3+3
  structure**, same as the primary people-based question — one code path
  handles both stimulus types, rather than building a second, thinner
  interview shape ahead of any evidence it's needed.
- ~~**Mission alignment's con-side exclusion rule.**~~ **Resolved: no
  exclusion list.** "No Hitler" works for Values because there's one
  obvious, universally-agreed lazy answer for a *person*; there's no
  equivalent single obvious "worst company," and a hardcoded banned-org
  list would be brittle and culturally specific to maintain. Rely on
  verbatim capture + the pro/con pairing itself to surface real signal;
  add a rule only if real usage actually produces a problem.
- ~~**Does Mission alignment's ordering hold up for organizations?**~~
  **Resolved: ship the carried-over assumption, observe.** One shared
  ordering implementation for both Values and Mission alignment rather
  than two; this is empirical and can't be resolved by design discussion
  — see "Revisit if."
- ~~**Storage shape for `InterviewDocument`.**~~ **Resolved: reuse and
  extend `qa_capture`'s shape**, with a new `subtype` field carrying the
  interview category — see "Architecture" above.
- ~~**Mission alignment's own no-examples fallback.**~~ **Resolved:
  anchor first, then skip gracefully.** If someone can't name a company or
  organization they'd want to work at or be horrified by, first re-ask
  anchored to something they've actually lived — "an organization you've
  actually been part of: school, an employer, a club, a volunteer group —
  proud or not, and why" (Critical Incident Technique — a memory-anchored
  prompt beats open hypothetical recall for someone already stalling, per
  the research pass's Q2 finding). If that still comes up empty, the
  module just doesn't fire — same honest-degrade pattern the dossier's
  Gaps section already uses elsewhere in wingman ("no stance yet — run
  X"), rather than forcing an answer. Naturally fills in later once real
  job-search targets exist (tier 3's Company-alignment variant). Rejected:
  deriving a company indirectly from whoever they already named in
  Values ("you said you'd have dinner with X — what org is X associated
  with?") — reintroduces exactly the cross-module indirection this design
  just spent effort removing by keeping Values and Mission alignment as
  separate, direct questions.

All seven v1 open questions are now resolved.

## Phasing

- **v0.** Tier-1 mechanic only: submit 3 agree + 3 disagree links/files,
  capture the "why" per item. Reactions are stored and reviewable but not
  yet synthesized into a stance.
- **v1.** Model-synthesized stance from the captured reactions, using the
  same evidence-validated machinery `build_own_pov`/`build_company_pov`
  already use; adds the Values and Mission alignment modules (pro/con,
  con-then-pro ordering, nested ask-#2-first).
- **v2.** Company-alignment variant — react to a target company's own
  content — as an alternative to the embedding-based `company_alignment()`
  path.

## Revisit if

- The `InterviewDocument` storage-shape question turns out to matter for
  something else already planned (e.g. RFC-036's Q&A lineage machinery) —
  resolve them together rather than diverging.
- Real usage shows the trust ladder's tiers don't match how people actually
  move through onboarding (e.g. everyone stops after tier 1) — that's a
  product-flow finding, not a reason to redesign the underlying mechanic.
- The model-exposure risk above proves harder to contain than expected
  once v1 is built (e.g. a submitted piece's content leaks into a
  synthesized stance despite the delimiting) — that is the trigger to
  reconsider whether synthesis should read the submitted content at all,
  versus only ever reading the user's own "why" text.
- Someone actually nominates a genuinely problematic org for Mission
  alignment's con side and the no-exclusion-list decision turns out to be
  wrong in practice — that's the trigger to name a specific rule, not
  building one on spec now.
- Real usage shows Mission alignment's con-then-pro/ask-#2-first ordering
  doesn't land the same way it does for Values (e.g. it reads as arbitrary
  for organizations, where it reads as a deliberate technique for people)
  — that's the trigger to give organizations their own ordering, not
  assume the carried-over one is permanent.
- The anchor-first fallback for Mission alignment (Critical Incident
  Technique — "an org you've actually been part of") turns out to *also*
  come up empty often enough that skip-gracefully is doing most of the
  work — that's the trigger to reconsider whether Mission alignment needs
  a company-free fallback of its own (parallel to Values'), not just a
  two-step version of the same question shape.
