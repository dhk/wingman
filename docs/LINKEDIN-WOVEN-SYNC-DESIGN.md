# LinkedIn/Woven sync — Design (proposal, not yet an RFC)

**Status.** Design recorded 2026-08-28, from a scoping discussion triggered
by a real gap: refreshing a LinkedIn export in wingman does nothing for
[Woven](https://github.com/dhk/woven) (a separate, sibling project — "find
your warmest path to anyone, using everyone's network," combining multiple
contributors' LinkedIn exports into one shared graph). Not built — no code
in either repository implements any part of this document. This is a
**cross-repo** design: the mechanic below touches `dhk/wingman` and
`dhk/woven`. It lives here because the triggering gap and the RFC-028
lineage machinery it extends both live in wingman; Woven's own maintainers
already flagged the matching gap on their side (see "What exists today,"
Woven section). Graduates to a numbered `RFC.md` entry once a v1 slice
ships, same as `docs/COACHING-MODE-DESIGN.md`'s convention — which itself
already anticipated and deliberately deferred the adjacent "a persona's own
LinkedIn graph" question to this document (see that doc's "Deferred:
a persona's own LinkedIn graph (Woven)" section).

## Motivation

Two systems both care about the same raw material — a person's LinkedIn
export — and neither updates the other:

- Wingman ingests a LinkedIn export two different ways (Connections.csv
  into the watchlist, a full "Complete" export into profile claims), each
  scoped to whichever workspace/tenant you ran the command against.
- Woven combines multiple contributors' own Connections exports into one
  shared graph, browser-only, with no automated ingestion path at all.

Today, "refresh my LinkedIn data" means separately, manually repeating two
unrelated processes, in two different tools, with no shared identity
between them and no guarantee either one preserves what it already knew.
The immediate trigger: after refreshing a LinkedIn export in wingman, the
same person's slice of Woven's graph was three months stale, and there was
no reproducible way to fix it without also re-uploading a second
contributor's raw export (whose data must never be touched by someone
else's refresh).

## Goals

- One reproducible input — **(person's name, LinkedIn export path — full
  or partial)** — updates that person's data in both wingman and Woven.
- **Upsert, not replace.** A thinner or partial new export never erases
  richer data already on file; a genuine change (title, company, a new
  connection) is captured. This applies to *other people's* fields a given
  export happens to know about (e.g. a mutual connection's company). It
  does **not** apply to a contributor's **own** connection list — their
  fresh export is authoritative for who *they* are currently connected to,
  and replaces their own edge set wholesale. "Partial" export support
  covers the LinkedIn "Basic" vs. "Complete" export distinction (fewer
  fields, same connections list), not a hand-trimmed subset of rows.
- Safe to re-run. Running the same export through the process twice changes
  nothing the second time.
- No new person-identity system. "Person's name" resolves to *existing*
  identity in each system — a wingman tenant, a Woven contributor slug —
  never invents a third canonical identity store.

## Non-goals (v1)

- Not building Woven's persistent multi-user security/auth model — its own
  roadmap already scopes that as separate, larger future work
  ("Persistent team graphs," `docs/roadmap.md`), gated on "a new security
  and privacy design" the roadmap explicitly says this project hasn't done.
- Not extending Coaching Mode to give a *persona* their own LinkedIn/Woven
  graph — `docs/COACHING-MODE-DESIGN.md` already named and deferred that
  question; this document is about existing wingman tenants (real people
  with their own workspace) and existing Woven contributors, not personas.
- Not changing wingman's share-nothing multi-tenant architecture (RFC-048).
  "Upsert person X" means "run against X's existing tenant workspace,"
  never a new shared table keyed by person name.
- Not a live/automatic sync. A manually-triggered, reproducible batch
  process — cron or a standing watcher is a later question, not this one.
- Not a rewrite of Woven's browser app or its dedup/scoring heuristics —
  reuse their existing merge logic (see "Architecture" below), don't
  replace it.

## What exists today

### Wingman

Two unrelated import paths, each wrong for this goal in a different way:

- **`seed_from_connections`** (Connections.csv → `Person` watchlist rows,
  `application/people.py`): on a name collision, it **skips outright** —
  `company`/`position`/`connected_on`/`linkedin_url` on the existing record
  are never touched, richer or not. No deltas are ever captured this way.
  Contrast with `add_person` (manual add, same file), which already does a
  correct fill-blank-wins per-field merge on collision — that policy
  exists in the codebase, just not wired to the connections-import path.
- **`import_linkedin`** (full "Complete" export's `Positions.csv`/
  `Skills.csv`/`Recommendations_Received.csv` → `ProfileItem`s via RFC-028
  document-lineage supersession, `application/linkedin.py`): the opposite
  problem. `document_key` is derived from the filename alone, so every
  `Positions.csv` you ever import collapses to the same lineage, and a
  retirement sweep (`application/profile_store.py`'s `persist_items`)
  silently marks `SUPERSEDED` any existing `ProfileItem` the new file
  doesn't restate. There is **no thinness guard** — a subset re-import
  quietly hides real history, indistinguishable in the code from "the user
  actually deleted that role." This is the mechanism issue
  [#420](https://github.com/dhk/wingman/issues/420) ("Versioned imports
  should show a before/after, not just land") already names.
- No shared identity parameter anywhere in either path — a `ProfileItem`
  is implicitly "whoever's workspace this call is running against."
  Multi-tenant routing (RFC-048) is share-nothing: each tenant is a
  separate `data_dir`/database, resolved by capability token or
  `WINGMAN_DATA_DIR`, never by an in-band person-name argument. "Upsert
  person X" therefore means "run the ordinary single-tenant import against
  X's own workspace," not a new cross-tenant call shape.
- `ingest-linkedin` (the full-export path) is CLI-only today — no MCP tool
  wraps it, so a remote tenant's only documented route in is uploading
  through their own web UI.

### Woven

Browser-only, and there is a hard wall between "view an existing snapshot"
and "add to it":

- All CSV parsing lives inline in `index.html` (no Node/CLI ingestion
  exists anywhere in the repo — confirmed by their own
  `docs/woven-mcp-server-plan.md`: *"No LinkedIn CSV import through MCP
  yet"* and *"Add `import_snapshot` or `reload_graph` for swapping graph
  files"* under "Later Extensions," i.e. explicitly unbuilt).
- `parseConnections` already does a correct same-session upsert: exact-id
  match on a normalized name, union `sources[]`, fill `company`/`position`
  only if currently blank. `chooseKeep`/`mergeNodes` (fuzzy near-duplicate
  detection) score "richer" by owner status, primary-user status,
  connection count, has-company, has-position, and fill blanks from the
  losing node — a real "prefer richer" heuristic, already written.
- **But it only operates within one in-browser build.** Loading the
  existing seeded snapshot (`loadSeedData`, view-only hydration) and
  uploading a fresh CSV (`buildGraph`, which unconditionally clears
  `nodeMap`/`adjacency`/`edgeData`/`fileOwners` before rebuilding from
  only the files uploaded *this session*) are incompatible code paths.
  Today, uploading one contributor's fresh export after opening the seeded
  page **destroys every other contributor's data** rather than merging.
- `graph/seed.json` is a single flat structure — `nodes` carry a
  `sources: string[]` owner tag (the right building block: it's exactly
  what the MCP's `sourceOwner` search filter already matches against), but
  `adjacency` and `edgeData` are global maps with no per-owner partition.
  30 of 3720 nodes in the current graph are tagged with *both* current
  contributors (mutual connections) — any selective-replace logic must not
  drop the other owner's half of those.
- The MCP server (`mcp/src/*.ts`) is strictly read-only — no write path
  exists, and the snapshot is loaded once at process start, never
  live-reloaded; a rebuilt snapshot needs every running server process
  restarted to take effect.

## The mechanic

### Field-merge policy (one rule, both systems)

Per field: **the new value wins if it's non-empty; otherwise, keep the
existing value.** This is not a new invention — it's the fill-blank
pattern wingman's `add_person` and Woven's `parseConnections`/`mergeNodes`
already independently arrived at; the work here is applying it where it's
currently missing (`seed_from_connections`) and extending it across a
persisted-snapshot boundary (Woven, which today only has it within one
in-memory build).

**Exception, both systems:** a contributor's own outgoing edges (who *they*
are connected to) are replaced wholesale by a fresh export of theirs, never
merged field-by-field. A person's current connections list is a complete
statement of the truth as of that export, not a possibly-thinner data
point to be merged against history.

### Wingman changes

1. **`seed_from_connections` upserts instead of skipping** on a name
   collision — reuse `add_person`'s existing merge shape rather than
   reinventing it. Genuinely new rows still insert as new `Person` records,
   unchanged. A person missing from a thinner re-export is never deleted.
2. **A thinness guard on `import_linkedin`'s retirement sweep** — before
   retiring items resting entirely on a superseded `document_key` lineage,
   report what *would* be retired rather than doing it silently; only
   proceed with explicit confirmation (or an explicit flag for a scripted
   run that's already vetted the export). This is the natural home for
   issue #420's before/after surfacing — the guard and the visibility fix
   are the same piece of work, not two.
3. **Tenant dispatch** — resolve "person's name" to that tenant's own
   `data_dir` (local `WINGMAN_DATA_DIR`, or a remote target the way this
   session already drove lobster's `~/.config/wingman/secrets.env` over
   SSH) before running the ordinary single-tenant CLI commands against it.
   No new server-side concept; the dispatch table is a small, explicit
   local lookup (name → tenant target), not a new registry.

### Woven changes

1. **A new headless Node script** (nothing like it exists today — this is
   net-new engineering, not a small patch): reads the existing
   `graph/seed.json`, parses one named contributor's fresh CSV (port the
   parsing/merge logic already proven in `index.html`'s `parseConnections`
   into reusable Node code — a CSV library stands in for the browser
   dependency on PapaParse, nothing else about the algorithm needs to
   change), replaces that contributor's own edge set wholesale, and merges
   other-people node fields with the fill-blank policy above. Writes an
   updated `graph/seed.json` with a fresh `exportedAt`.
2. **Correctly handle dual-owner nodes** — a node tagged with both the
   refreshed contributor and another contributor must keep the other
   contributor's fields/edges untouched; only the refreshed contributor's
   own contribution to that node updates.
3. **Say so about running servers** — after writing a new snapshot, name
   the running `server.js` processes reading the old one (no live reload
   exists) and either offer to restart them or state plainly that a
   restart is required, rather than leaving a refreshed file silently
   unread by anything already running.

## Architecture — what exists to reuse

- Wingman: `add_person`'s fill-blank merge (`application/people.py`) — the
  field policy this document specifies already lives in the codebase.
- Wingman: RFC-028's `document_key`/`persist_items` lineage machinery
  (`application/profile_store.py`) — the thinness guard is additive to
  this, not a replacement of it.
- Woven: `parseConnections`'s same-session upsert and `chooseKeep`/
  `mergeNodes`'s richer-wins scoring (`index.html`) — port the algorithm,
  don't reinvent it; the gap is only that it never runs across a
  persisted-snapshot boundary today.
- Woven: `sources: string[]` per-node owner tagging, already exactly what
  `sourceOwner`-filtered search depends on — the natural boundary for
  "which nodes did this contributor's own export touch."

## Open questions

Resolved 2026-08-29 from a scoping discussion:

- ~~**Exact policy for `connected_on`.**~~ **Resolved: first-seen wins,
  always** — except when the stored value looks invalid (e.g. an
  impossible date). An insane existing value is never silently kept or
  silently overwritten; it's surfaced for a human to confirm.
- ~~**Woven contributor identity vs. wingman tenant identity.**~~
  **Resolved: 1:1, in their own space.** Every wingman tenant is assumed
  to eventually get their own Woven presence, but never by silently
  merging them into an existing combined graph — a new tenant gets their
  own separate single-user graph. Combining specific people's networks
  (today: dave and trent) stays a deliberate, separate, manual action.
- ~~**Local vs. remote dispatch.**~~ **Resolved, and this reframed the
  whole shape of Phase 4 — see below.** Wingman is never local: every
  tenant (dave, trent, jason, bob) lives on lobster's one shared
  multi-tenant instance (RFC-048), addressed by tenant slug/data_dir, not
  a separate login per person. Woven stays local to the operator's own
  machine. **wingman and Woven stay architecturally separate — no
  automated bridge gets built.** Integration is manual and session-driven
  (a Claude session drives both, by hand, coordinated in-session) until a
  real migration/shared-infrastructure plan for Woven exists, which is
  explicitly a separate, later decision, not scoped here.
- ~~**Does the thinness-guard warning block by default, or just
  report?**~~ **Resolved: block by default, but as a separate dry-run
  step, not an in-band prompt.** A blocking interactive confirmation
  doesn't work over a non-interactive channel (e.g. a scripted SSH
  invocation against the shared lobster instance). The workable shape:
  the tool always runs a read-only dry-run first ("here's what would be
  retired"), that's shown to the human, and only a second, explicit,
  already-confirmed invocation actually retires anything.

## Phasing

- **Phase 1 (wingman).** `seed_from_connections` upsert. Tracked:
  [#478](https://github.com/dhk/wingman/issues/478).
- **Phase 2 (wingman).** `import_linkedin` thinness guard + before/after
  surfacing — this *is* issue
  [#420](https://github.com/dhk/wingman/issues/420), not a separate
  follow-on.
- **Phase 3 (woven).** The headless Node upsert script, net-new. Tracked:
  [dhk/woven#92](https://github.com/dhk/woven/issues/92). Still needs to
  correctly preserve dual-owner nodes in the *existing* combined
  `graph/seed.json` (dave+trent) even though this phase never performs a
  fresh combine of its own — the file it's upserting into already merges
  two contributors' data today.
- **No automated Phase 4.** Originally scoped as a unifying dispatch
  wrapper; superseded by the resolved local-vs-remote question above —
  wingman and Woven stay separate, integration is manual/session-driven.
  [#479](https://github.com/dhk/wingman/issues/479) now tracks documenting
  that manual procedure, not building automated dispatch.
- **Deferred, out of this document's scope.** Moving Woven to lobster and
  giving it real multi-user hosting (identity, auth, tenant isolation) —
  a separate, later design effort, not scoped or scheduled here.

## Revisit if

- Woven's own roadmap builds real multi-user persistence/auth — the
  headless script from Phase 3 should then sit behind whatever identity
  model that introduces, rather than writing `graph/seed.json` directly.
- A wingman tenant wants their *own* Woven contributor identity that
  isn't 1:1 with their wingman tenant slug — the dispatch table in Phase 4
  needs to become a real mapping rather than an assumed identity match.
- This process wants to run unattended (a schedule, not a manual trigger)
  — the thinness-guard's default-to-block posture needs revisiting for a
  context where nobody's watching to answer a confirmation prompt.
- A persona (Coaching Mode) ever gets their own LinkedIn/Woven graph —
  `docs/COACHING-MODE-DESIGN.md`'s deferred section is the trigger to
  connect that work to this document instead of treating them separately.
