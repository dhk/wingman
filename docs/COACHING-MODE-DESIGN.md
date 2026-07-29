# Coaching Mode — Design (proposal, not yet an RFC)

**Status.** Design recorded 2026-07-29, from a scoping discussion. Coach-mediated
confirmed as the shape (no separate coachee login/token); the persona
mechanic, evidence-classification treatment for coach-authored answers, and
what's shared vs. scoped are resolved below. Not built — no code in this
repository implements any part of this document. Graduates to a numbered
`RFC.md` entry once a v1 slice ships.

## Motivation

The owner wants to use their own wingman instance — same corpus, same
watchlist, same everything they've already gathered — to run the same
analyses (POV, job scoring, career profile) on behalf of other people they
coach, not just themselves. Not a separate instance per person
(`docs/MULTI-INSTANCE-DESIGN.md`'s Shape A) and not a separate Unix account
per person (Shape B, built for lobster's dhk/Trent split) — one instance,
one coach, multiple people's work kept properly separate within it, the
same way a real coaching conversation naturally keeps one client's answers
from bleeding into another's.

## Goals

- One instance serves the coach's own work and N coachees' work.
- The coach sees everything — that's correct, not a bug: it mirrors sitting
  in the room for a real conversation.
- A given piece of work (profile, POV, job criteria, interview captures)
  stays scoped to the person it's about.
- The coach's shared material — corpus, watchlist, company research — is
  available as background for any coachee's session, without automatically
  becoming that coachee's *own* evidence.
- Coach-mediated only: the coach always drives every tool call. No separate
  coachee-facing login or access token in v1.

## Non-goals (v1)

- No coachee-facing auth/access control. A real, larger feature if ever
  wanted (its own new security surface — wingman's MCP/HTTP server today is
  one capability token per instance, all-or-nothing, RFC-017) — deliberately
  out of scope now. See "Revisit if."
- No re-architecture of "one workspace = one person" into true
  multi-tenancy. Personas live *inside* the coach's own workspace as a
  scoping dimension, not separate instances or accounts.
- No automatic promotion of the coach's own corpus/POV into a persona's
  evidence. Explicit only.

## The mechanic: personas

A new domain concept, **Persona** — someone the coach is coaching.
Deliberately *not* the existing `Person` type (`domain/person.py`: "the
watchlist of humans whose thinking the user follows" — someone whose public
writing is fetched *for the coach's own benefit*). A persona is the
opposite relationship: the *subject* of the coach's own tools, run on their
behalf. `domain/person.py`'s own docstring states the invariant this design
extends to a second axis rather than inventing a new one: "'my evidence'
and 'their point of view' never mix." Coaching mode needs that same
discipline to also hold between the coach's own evidence and a persona's,
and between one persona's and another's.

A persona is a name plus optional notes for v1 — nothing more, keeping
scope narrow per direction. Any persona-aware record (a `ProfileItem`, an
interview capture, a job-criteria set, a POV card) carries a `persona_id`.
`null` means "the coach's own work" — the default, and the *only* value
that exists in every workspace today, so this is fully backward compatible
with zero migration: every row already in production is implicitly the
coach's own.

**"Acting as coach for Mike."** MCP tools in this codebase hold no session
state between calls (the established stateless posture — see
`docs/UX-0001-interview-flow.md` §10, "position is computed from stored
captures on every call, not held in a session"). A single persisted "active
persona" pointer — one row, the same shape as `doctor_deep`'s own persisted
step-cursor (`infrastructure/doctor_deep.py`) — is the mechanism: a
`coach_persona` tool sets it ("act as coach for Mike"), clears it, or
reports who's currently active. Every persona-aware tool call defaults to
whatever's active unless given an explicit override, so "set it once, keep
going" works the way it was described, while a one-off cross-persona call
always stays possible without switching the active pointer.

**Evidence, and "how would Mike answer this."** When the coach types an
answer *speculating on the persona's behalf* — their own best
reconstruction of what Mike would say, not Mike sitting there typing it
himself — that is not a verified first-person statement. Today's interview
captures are stored as `ClaimClassification.FACT` ("first-person statement;
there is no better source" — `application/interview.py`). A coach's guess
on someone else's behalf is the opposite of that. Classify it
`ClaimClassification.HYPOTHESIS` (already in `domain/provenance.py`,
unused by any capture path today) with `extracted_by="coach"` instead of
`"user"`, and render it distinguishably wherever it surfaces ("Coach's
understanding of how Mike would answer," never presented as Mike's own
words). If a persona is ever literally in the room typing their own
answers, that's `FACT`/`extracted_by="persona"` instead — same mechanic,
different provenance, and the two stay distinguishable in the data forever,
not just at capture time.

## What's shared vs. persona-scoped

**Shared, exactly as today, no scoping needed:**
- Corpus documents, watchlist (people/companies tracked), company
  research/dossiers.
- `search` — already spans the whole workspace (corpus, watchlist, POV
  stances, news, research). This alone satisfies "all my material is
  available to them": the coach (or a persona-scoped session) can search
  and reference it for context. Nothing here becomes a persona's own
  evidence just by being visible.

**Persona-scoped (`persona_id`, `null` = the coach's own, unchanged
default):**
- Interview/Perspectives captures (`ProfileItem` kind=`INTERVIEW`)
- Career profile items generally (achievement/skill/role/testimonial)
- The job-criteria document (RFC-035) — one per persona, plus the coach's
  own, instead of one per workspace
- `build_own_pov`'s output — a persona's stance, built only from *that
  persona's own* scoped evidence, never the coach's corpus or POV unless
  explicitly attached under that persona
- Outreach briefs, application packs, and company alignment scores computed
  against a persona's POV specifically

## Explicit attachment, not automatic inclusion

No new "reference tier" of data needed. The coach's shared corpus stays
visible/searchable for any persona's context via the existing `search`
tool — that's the "everything I know is available to them" half. If the
coach wants something to become part of a *persona's own* evidence (not
just visible background), that's the same capture mechanism used today,
just tagged under that persona's id — an explicit act, never automatic.
Nothing crosses from the coach's own POV into a persona's synthesis on its
own, matching "my own perspectives/POVs aren't important here — unless we
specifically do that."

## Cross-referencing the coach's own People graph

Confirmed, and it fits the existing shared/scoped split without needing a
new mechanism: when working on a persona's behalf, the coach's *entire*
People watchlist — everyone tracked, warm-path scoring, all of it — stays
visible for finding candidates. "Who among everyone I know might be
valuable for Mike" is exactly the "all my material is available to them"
principle, applied to people instead of writing. Nothing new needed for
*visibility*; `search`/`people_similar`/`woven_warm_path` already span the
whole workspace today.

What's new is the *action*: designating a specific person as relevant to a
specific persona is an explicit coach decision, not a suggestion engine —
the same "explicit attachment, not automatic" principle as evidence above,
just applied to People instead of `ProfileItem`s. A lightweight join
(persona ↔ person, optionally carrying the coach's own note on *why* this
person might matter for this persona) is the natural v1 shape. The coach
does the selecting; nothing here auto-suggests overlaps.

## Deferred: a persona's own LinkedIn graph (Woven)

If a persona (e.g. Mike) ever gets *their own* LinkedIn connections export
uploaded — directly, or through Woven — that's a materially different
question from anything else in this document: whose graph is it, does it
merge with or stay separate from the coach's own People watchlist, and what
"warm path" even means once two people's networks are both in play.
Explicitly out of scope here, per direction — noted so it isn't lost, not
designed. Revisit alongside Woven's own roadmap, not as part of this v1.

## Web UI: a persona tab

A tab to select a persona, then view the same kind of pages wingman already
renders (profile, POV, job matches) — scoped to that persona instead of the
coach's own. This reuses existing rendering parameterized by persona; it
isn't new visual design, just an existing view plus a filter dimension and
a picker. `webui.py` today is deliberately narrow (RFC-033: "glancing and
files," no interactive editing) — this stays consistent with that posture:
browsing a persona's scoped view, not a new editing surface.

## Digest

Deliberately narrow for v1: a "Coaching" section naming each active persona
with a one-line status (something changed since you last looked / nothing
new), rather than fully expanding the overnight scoring/news pipeline to
run once per persona. That expansion is a real cost multiplier (embeddings,
model calls all scale with persona count) worth its own follow-up once this
ships, not v1 scope.

## Architecture — what already exists to reuse

- `ProfileItem.classification`/`extracted_by`/`confidence` — already
  exactly the fields the coach-speculation-vs-persona's-own-words
  distinction needs. No new fields, just new, correct values for the
  coaching case.
- `Person` vs. `Persona` kept deliberately separate concepts (see "The
  mechanic" above) — reuse the same underlying storage/evidence pattern
  (`SourceRecord` + `ProfileItem`), not the `Person` type itself.
- `search` — already spans the whole workspace; no changes needed for
  "reference material available to a persona's session."
- `doctor_deep`'s persisted-cursor pattern — the template for "active
  persona" state: one row, explicit set/clear, never silently assumed.

## Open questions

1. **Naming.** This doc uses "Persona," matching how it was described
   ("modeled as a persona flag"). Confirm, or "Coachee"/"Client" if that
   reads better once it's a real UI label.
2. **How long does "active persona" stay set?** Until explicitly cleared,
   or does it expire (end of MCP session, a time window)? Leaning toward
   "until explicitly cleared" for simplicity, but worth confirming given
   the failure mode is misattributed evidence if the coach forgets who's
   active.
3. **Company alignment for a persona** — reuses the coach's own
   already-fetched company research (shared, per "What's shared" above),
   computed fresh against that persona's own POV. Confirming this is the
   intended shape, not something requiring its own re-fetch per persona.
4. **Job-criteria seeding** — does a fresh persona start with an empty
   criteria doc (the existing seeding interview, run under that persona's
   scope), or is there ever a reason to clone the coach's own as a
   starting point? Leaning toward always empty/fresh — a coachee's job
   criteria shouldn't inherit the coach's.

## Phasing

- **v1 (this design's scope).** Persona domain type; persisted
  active-persona state; persona-scoped interview captures, profile items,
  job-criteria, and `build_own_pov`; the `coach_persona` MCP tool (and CLI
  equivalent); the web UI persona tab (browse existing views, scoped); the
  digest's narrow "Coaching" section.
- **v2 (deferred).** Full per-persona overnight scoring/digest expansion;
  coachee-facing auth/access, if ever wanted; a richer persona profile
  beyond name + notes.

## Revisit if

- A coachee ever needs *their own* direct access, not coach-mediated —
  that's the trigger for the harder access-control design this document
  deliberately keeps out of scope.
- The persisted "active persona" pointer proves error-prone in practice
  (the coach forgets which persona is active and misattributes evidence)
  — trigger to require an explicit persona argument on every call instead
  of defaulting to a background pointer.
- Coachees end up wanting to see each other's scoped work, or explicitly
  must not be able to (this design assumes only the coach ever sees
  everything; if personas themselves ever get any direct visibility, their
  mutual privacy becomes a real question this doc hasn't addressed).
