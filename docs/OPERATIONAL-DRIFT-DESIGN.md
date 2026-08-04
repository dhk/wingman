# Operational drift detection — Design (proposal, not yet an RFC)

**Status.** Design recorded 2026-08-03, per #212. Prompted by two real
incidents in one night standing up RFC-048's shared multi-tenant
process: a `systemctl --user` bus-connection failure
(`wingman-provision-shared.sh` didn't carry forward the
`XDG_RUNTIME_DIR` fix `infrastructure/upgrade_all.py` already needed for
the identical reason, RFC-042), and a crash loop from a pidfile written
into a directory the running account couldn't write to. Both were
discovered only by running the affected command live and reading a
traceback — exactly what #212 asks `wingman upgrade`/`wingman doctor` to
catch *before* that point. Graduates to a numbered RFC entry once built
and the manifest format has actually been exercised against a real
upgrade; until then this is the working design, not yet implementation.

## The one-sentence design

A small, version-controlled manifest names the operational facts *this
version of wingman's code* assumes about the host it's running on —
systemd unit shape, wrapper script presence, config file layout — and
`wingman doctor` (not `wingman upgrade` itself — see below) checks the
live host against it, auto-correcting only what's safe and reversible,
reporting everything else.

## Being honest about what this would and wouldn't have caught

Worth naming directly, since it changes the manifest's actual value:
**neither of tonight's two incidents would have been auto-fixed** by
this design — both are exactly the "systemd/sudo/cross-account" tier
#212's own acceptance criteria say must be report-only, never silently
applied. What the manifest buys is *earlier, calmer discovery*: running
`wingman doctor` before standing up a new box would have said "this
account's systemd unit doesn't match the XDG_RUNTIME_DIR shape newer
code expects" or "the configured pidfile location isn't writable by
this account" as a named finding, instead of a crash loop and a live
traceback read under time pressure. That's real value — turning a
production incident into a pre-flight check — but it's not automatic
healing, and the design shouldn't be sold as more than it is.

## What's already covered, and not this design's job to duplicate

Host **config file layout** (RFC-046's `wingman.env`/`secrets.env`
split, RFC-047's global-secrets tier) already has its own
drift-detection: `wingman doctor` reports the current layout on every
run, and `migrate_legacy_host_file` auto-corrects the one migration
path that's actually safe (moving off the old flat file). That
machinery is the existing precedent for "safe, reversible, auto-applies
with a printed summary" — this design extends the same *shape* to
systemd units and wrapper scripts, which currently have no equivalent
check at all.

## What's in the manifest

A small, plain-data structure (not a new file format — a Python module
under `infrastructure/`, mirroring how `KNOWN_KEYS`/`HOST_SETTINGS`
already are plain, table-driven data rather than parsed config) naming,
per systemd unit `wingman` ships a template for:

- The unit's expected `ExecStart` shape (does it reference the
  currently-installed binary path, e.g. `%h/.local/bin/wingman-mcp`).
- Whether the unit's *invoking* pattern (root running `sudo -u <user>
  systemctl --user ...`, the RFC-042 case) requires `XDG_RUNTIME_DIR`
  and whether the caller's own script/unit sets it.
- The wrapper script's (`wingman-ctl`) expected location and that it's
  actually reachable via the account's `wg` alias (docs/SERVER.md §1).
- Which host config files (`wingman.env`, `secrets.env`,
  `global-secrets.env`) this version's code reads, cross-checked against
  what `wingman doctor` already reports.

Deliberately NOT in the manifest: anything about *tenant*-level state
(the RFC-048 registry, individual workspace contents) — this is host/
account-operational-surface only, one layer below any tenant's own data.

## Where it lives, and who runs the check

The manifest is code, shipped in the same commit as whatever change it
describes — no separate versioning scheme, no drift between "the
manifest" and "the code" being possible by construction (the same
reason `KNOWN_KEYS` lives in `keys.py` next to the code that reads it,
not in a separate config file).

**`wingman doctor`, not `wingman upgrade`, runs the check.** #212's own
title says `wingman upgrade`, but `doctor` is the existing, established
home for "is this host's state correct" reporting (RFC-039's guided
diagnostic ladder, the host-config-layout check already there) — adding
a *second* place that reports host health would recreate exactly the
"which file is actually in effect" confusion RFC-046 exists to prevent,
one layer up. `wingman upgrade`/`wingman-ctl upgrade` can *suggest*
running `doctor` after a version bump, the same way it already prints a
version line today, rather than duplicating the check inline.

## Tiered apply behavior (per #212's own acceptance criteria)

- **Safe, reversible, file-only** (a host config file whose current
  content is unambiguously the old shape, e.g. a future config-layout
  migration analogous to RFC-046's): auto-applies, prints a summary —
  the existing `migrate_legacy_host_file` precedent, unchanged.
- **Anything touching a systemd unit, sudo, or cross-account state**:
  reported only, with the specific fix command printed (mirroring how
  `docs/SERVER.md`'s troubleshooting entries already read — named
  problem, named fix, operator runs it by hand). Never silently
  rewrites a unit file a human may have hand-edited for a reason the
  manifest doesn't know about.

## Alternatives considered

- **A separate, human-edited manifest file** (TOML/YAML, versioned
  independently of the code) — rejected: the whole point is the
  manifest never drifts from what the code actually needs, which only
  holds if it ships in the same commit as the code, not as a document
  someone has to remember to update separately.
- **Checking in `wingman upgrade` directly** — rejected per the "where
  it lives" section above: `doctor` already owns this kind of reporting;
  splitting it would recreate the two-sources-of-truth problem RFC-046
  fixed for host config specifically.
- **Auto-applying systemd unit fixes too** (not just file-layout ones) —
  rejected outright, matches #212's own acceptance criteria: a unit file
  is exactly the kind of thing an operator may have hand-tuned (a custom
  `Restart=` policy, an extra `EnvironmentFile=`), and silently
  overwriting it on every `doctor` run is a worse failure mode than the
  drift it would be fixing.

## Rationale

Tonight's two incidents share a shape: both were *known, nameable*
classes of problem (the RFC-042 XDG_RUNTIME_DIR fix already existed in
one codepath and needed carrying to a new one; the pidfile-location
constraint was knowable from `/etc/wingman/`'s own documented
permissions, RFC-047) that nonetheless surfaced live, under time
pressure, via a crash traceback. A manifest doesn't need to be
exhaustive or clever to close that gap — it needs to say, in one place,
"here's what correct looks like for this version," checkable *before*
the operation that would otherwise fail.

## Revisit if

- The manifest format, once built, turns out too coarse to express a
  real drift case (discovered by trying to encode a THIRD incident, not
  by speculation) — extend the schema then, not preemptively now.
- `wingman doctor`'s existing report format can't cleanly accommodate
  systemd/wrapper-script findings alongside host-config-layout ones —
  worth a smaller design pass on `doctor`'s own output shape first.
- A real *third* incident of this exact class occurs before this is
  built — strengthens the case for prioritizing it, doesn't change the
  design itself.
