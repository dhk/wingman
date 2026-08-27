# `dashboard_state()` — the read contract for personalised surfaces

**Status:** design, not implemented. Companion to RFC-075.
**Issue:** the Kneeboard surface (personalised dashboard over the workspace).

## Why this exists

A personalised UI needs the workspace's state in one read. Today it has two
options and both are wrong.

It can call six tools and parse their Markdown. That is what the first
Kneeboard build does, and it means the *rules* — which opportunity matters,
what counts as neglected, which action is a chore and which is a commitment —
live in a browser page. Change the rule and you change the UI; write a second
UI and the two disagree silently. It also means the page re-derives things the
workspace already knows: `focus.py` stamps every action with a typed key and
the page throws that away and guesses from whether a title contains a score.

Or the workspace can render the UI itself, which is the `webui.py` road and
deliberately not taken (RFC-033): "conversation is for thinking; this page is
for what conversation is bad at."

The third option is a contract. The workspace owns facts and the judgments it
draws from them. The UI owns nothing but paint.

## Shape

One tool, `dashboard_state()`, read-only, no model call, returning JSON with
two top-level halves.

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-08-27T21:14:03Z",
  "workspace": { "...": "..." },

  "facts":    { /* what is true, with provenance */ },
  "readings": { /* what the workspace concludes, citing facts */ },
  "capabilities": [ /* what this workspace cannot currently see */ ]
}
```

### Why `facts` and `readings` are separate

`commentary` already draws this line in this codebase — its rows are "the
assistant's readings — never evidence." The same word, the same discipline.

- **`facts`** are records. No ranking, no opinion, no threshold. Dates are
  dates. A UI that disagrees with every rule below can still render from here.
- **`readings`** are the workspace's judgments — the ledger, the neglect
  ranking, the candidate next moves. Every reading names the rule that
  produced it and the fact ids it used, so a reading is always traceable back
  to something checkable (invariants 2 and 6).

This is the separation the surface actually asked for. Rules move to the
workspace, where they are testable in Python without a browser; the UI is free
to be beautiful and opinionated about *presentation* and nothing else. A
second UI that wants different rules recomputes from `facts` and says so.

### `facts`

Every collection is a list of typed records. Fields that are exact are exact;
fields that are approximations say so **in the data**, not in a docstring —
`opportunities.py` already documents that `company`, `pack_composed` and
`answers_matched` are best-effort, and today a caller has to read the source to
find out.

```jsonc
"facts": {
  "overnight": {
    "date": "2026-08-27", "processed": 16, "failed": 11,
    "targets": [ { "name": "Ramp", "kind": "company", "ok": false,
                   "failures": ["themes: nothing attributable"] } ]
  },

  "actions": [
    { "id": "opening:anthropic:https://…/5390770008",
      "kind": "opening",              // from the key prefix — see below
      "what": "Applied AI Architect, Industries at Anthropic",
      "why": "…", "who": "Anthropic", "score": 72,
      "evidence": ["…"], "url": "https://…",
      "triage": null }                // "muted" | {"snoozed_until": "…"} | null
  ],

  "opportunities": [
    { "id": "ea93623c", "title": "…", "company": "Anthropic",
      "company_exact": false,          // best-effort, per opportunities.py
      "url": "https://…",
      "verdicts": { "met": 11, "partial": 0, "gap": 0, "unknown": 0 },
      "next_action": "…",
      "assessed_at": "2026-07-19T…",
      "pack_composed": true, "pack_composed_exact": false,
      "pursuit": null }                // ← requires RFC-076; see Dependency
  ],

  "objectives": [                      // NEW EXPOSURE — closes a real gap
    { "person": "…", "person_key": "…",
      "goal": "…", "thesis": "…", "next_move": "…",
      "updated_at": "2026-06-02T…",
      "last_logged_at": "2026-06-02T…",   // newest relationship_log entry
      "days_quiet": 86 }
  ],

  "heap":      [ { "id": "…", "heat": "hot", "ref": "…", "note": "…", "added_at": "…" } ],
  "artifacts": [ { "kind": "completeness", "url": "…", "stale": true,
                   "stale_reason": "…", "rebuild": "wingman completeness" } ],
  "criteria":  { "exists": true, "path": "…", "reviewed_at": "…" }
}
```

Two notes on `actions`. First, `kind` is derived from the key prefix that
`focus.py` already stamps — `opening:`, `relationship-review:`, `relationship:`,
`research:`, `fix-source:`, `company-posts:`, `person-posts:`, `heap-hot`,
`criteria-review`. That taxonomy is already data and no consumer can currently
see it; publishing it is most of the value of this contract. Second, `triage`
carries the RFC-031 verdict, so a surface can show what was muted rather than
silently omitting it.

`objectives` is the other gap this closes. `Storage.list_objectives()` exists;
no tool exposes it. Today an objective only becomes visible to any caller after
it has been quiet 45 days and `_relationship_review_actions` raises a digest
nudge — so **a live commitment is invisible until it is already late.** That is
backwards for a surface whose job is accountability.

### `readings`

```jsonc
"readings": {
  "prepared_not_shipped": [
    { "rule": "pack_composed_without_pursuit",
      "subject": "opportunity:ea93623c",
      "what": "Pack composed for Applied AI Architect · Anthropic",
      "since": "2026-07-19", "days": 39,
      "confidence": "inferred",        // ← not "known"; see Dependency
      "basis": "no pursuit state exists; age since assessment is the only signal",
      "move": "Send it or kill it." }
  ],
  "stalled":  [ { "rule": "…", "subject": "…", "what": "…", "days": 0 } ],
  "neglected":[ { "rule": "objective_quiet", "subject": "person:…",
                  "what": "…", "days": 86, "confidence": "known" } ]
}
```

**The ledger row is the interchange type, and wingman is only one producer.**
`dashboard_state()` covers this workspace's own state and reaches nothing else —
Todoist, the calendar and Alexandria's run store are other systems, and having
the workspace call them would break local-first and least-privilege for no gain.
Instead the row schema above is the shared shape: Alexandria can emit
`prepared_not_shipped` rows for runs it never promoted, and the surface merges
same-shaped rows from every producer. One rule, several producers, no coupling.

### `capabilities`

```jsonc
"capabilities": [
  { "name": "woven_warm_path", "available": false,
    "reason": "WINGMAN_WOVEN_URL is not set — the Woven integration is inert",
    "unlocks": "reachable people, company coverage, warm introduction paths" },
  { "name": "embeddings", "available": true, "provider": "voyage/voyage-4" }
]
```

A surface should be able to tell you what it cannot see without a human having
hand-written that list — which is what the current Kneeboard does, and it will
rot the first time a setting changes. Reporting one's own blind spots is the
same discipline as invariant 9 applied to configuration rather than content.

## Dependency: opportunities have no lifecycle

`OpportunityStatus` has exactly one member, `ASSESSED`. An opportunity can
never be anything else. So nothing anywhere records that an application was
sent, declined, or answered.

This matters more than it sounds. The ledger's whole claim is *finished and not
delivered* — and with no pursuit state that claim is unfalsifiable. It cannot
distinguish an application you sent in July from one you forgot. Every row it
prints is a guess dressed as an accusation, which is precisely the polished
fiction invariant 9 exists to forbid.

Two honest ways forward, and the design takes both:

1. **Ship the contract now** with `pursuit: null` and every affected reading
   carrying `confidence: "inferred"` plus a `basis` string saying exactly what
   was and was not observed. A surface can then say "no pursuit state exists"
   rather than implying one was checked.
2. **Add the lifecycle separately** (RFC-076, not written): `OpportunityStatus`
   grows `PURSUING` / `APPLIED` / `DECLINED` / `CLOSED`, set only by an explicit
   human act, never inferred. Then `confidence` becomes `"known"` and the ledger
   means what it says.

The contract must not wait on the lifecycle, and the lifecycle must not be
faked inside the contract.

## What this deliberately does not do

- **No writes.** Triage, journalling and objective revision stay conversational,
  behind their existing confirm gates. A dashboard that mutes an action on a
  click has skipped the gate that makes muting the user's judgment.
- **No model call, no network.** Pure read over SQLite plus the reports
  directory, so it is cheap enough to call on every page open and safe to call
  offline.
- **No `completeness` passthrough.** Its "Things to do" is the natural intent
  supplier and is currently unusable on a real workspace: the LinkedIn import
  put 2,701 people in scope, so its top two actions read "build POV cards for
  2,628 watched people" and "log what happened with 2,669 people". Until people
  carry a curated-vs-imported distinction, piping that in imports a wall.
- **No merging of other systems' data**, per the interchange-type note above.

## Open questions

1. Does `readings` belong in this tool at all, or in a sibling `dashboard_read()`
   so a caller can take facts without opinions? One call is simpler and the
   halves are already separable by key; splitting is easy later and hard to undo.
2. Should `capabilities` be its own tool? It answers a different question
   ("what is switched on") and other surfaces would want it.
3. Thresholds — 45 days quiet, 21 days recent, 14 days cooling — are currently
   constants in `focus.py` and the UI. If they are rules they belong in config
   and should appear in the payload so a surface can show its own workings.
