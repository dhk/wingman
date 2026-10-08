# OAuth and multi-tenant server support — a considered "not yet"

**Superseded first by RFC-048 and now, for authentication, by RFC-081**
(`docs/RFC.md`). RFC-048 acted as of the fourth tenant (`jason`, `bob` joining
`dhk`/`trent`) — exactly this document's own named trigger,
*"a third user appears… three friends is a product,"* now real and acted
on explicitly rather than inferred from operational pain. RFC-048 did
not overturn this document's reasoning about the tenant-count-vs-overhead
trade-off, which held correctly at n=2; it records that the owner made
the multi-tenancy call in daylight, as its own decision, the way this
document's own "Revisit if" asked for. RFC-081 later revisited the auth-model
conclusion for a narrower invite-only launch: externally issued OAuth bearer
tokens may select an operator-approved tenant, while existing capability paths
remain available. It does not adopt public signup or a Wingman-run auth
system. Left in place, not deleted, per this repo's convention for superseded
entries (e.g. RFC-040/RFC-046).

**Status.** Reflection recorded 2026-07-24, prompted by a night of hands-on
lobster operation that surfaced four concrete pains: a `pgrep` pattern
matching a substring shared across two different Unix users' processes
(nearly signaling another user's live server); a missing
`~/.config/wingman.env` causing `wingman-overnight.service` to fail
silently; a port-rebind race after `wingman mcp stop`; and capability-token
URLs nobody can remember. This is not an RFC — it recommends *not* building
anything here yet, and records why, so the question doesn't get re-litigated
from scratch next time it comes up. Follows `MULTI-INSTANCE-DESIGN.md`'s
shape (problem, decision, alternatives, rationale, revisit-if), since this
is the same kind of standing decision, not a numbered implementation RFC.

## The question

Four real incidents in one night, all in the deployment layer. Is that
evidence the current model — one person = one Unix account = one workspace
= one port = one capability token — is hitting a real ceiling, where OAuth
and multi-tenant server support (one process, one database, many people,
logged in with real accounts) would actually be the fix? Or is it four
tooling gaps sitting on top of a model that is otherwise sound?

## Decision

**Not yet.** Every one of tonight's four pains traces to missing or
immature *tooling*, not to a limitation of per-account isolation — and
three of the four are already fixed as of tonight's other work. Walked
through individually:

1. **The `pgrep` collision.** This is exactly the failure mode a per-account
   model is supposed to prevent, and it *did* prevent the actual harm —
   "only OS permissions prevented harm" is the boundary working as
   designed, catching a bug in the tooling sitting on top of it. The fix
   (#137/#138, shipped tonight as `wingman doctor --deep`) is a
   cross-account-aware, `ss`-based port check that identifies a process by
   what it's actually bound to, not a loose name match — strictly better
   tooling around the same model, not a reason to replace the model. A
   multi-tenant rewrite would not have prevented this class of bug; it
   would have made the blast radius of a *successful* mistake larger (one
   shared process instead of two separately-privileged ones).

2. **The missing env file.** A configuration-discoverability gap (which of
   two plausible files was actually in effect), already fixed tonight
   (#122, `~/.config/keys.env` as one canonical host key file wingman
   itself reads, `wingman doctor` naming the winning source; later split
   into `~/.config/wingman/{secrets.env,wingman.env}` by RFC-046). This
   class of bug is not tenancy-shaped: a multi-tenant server still needs
   secrets configured somewhere, and a misconfigured *shared* secrets store is a
   worse incident (affects every tenant at once) than a misconfigured
   *per-account* one.

3. **The port-rebind race.** A pure timing bug in stop/restart sequencing.
   Orthogonal to tenancy — the same race can exist in a single-tenant or a
   multi-tenant listener alike. Worth its own fix (a longer wait, or a
   readiness probe instead of a fixed sleep, in `wingman-ctl`'s
   `cmd_start`/`scripts/wingman-ctl`); not evidence for this document's
   question either way.

4. **Capability-token URLs nobody can remember.** The one pain in this list
   that's a real, structural weakness of RFC-017's current shape — and it
   already has its own issue (#135) with a *non-OAuth* proposed fix: a
   reverse proxy routing by subdomain, so the hostname alone says which
   instance you're looking at, while the token stays the credential
   ("everything *around* the token, not eliminating it" — #135's own
   framing). That fix is available without touching the tenancy model at
   all.

So: one of four pains is genuinely about the credential's ergonomics, and
its own issue already proposes a solution that doesn't require OAuth. The
other three are tooling maturity, now substantially addressed. None of the
four argue that per-account isolation itself is the problem.

## Why not build it anyway, given the direction seems plausible eventually

**The isolation is currently free, and multi-tenancy would make wingman
respinsible for a boundary the OS gives away for nothing.** Today, Trent
reading dhk's job search requires a bug in the *kernel's* UID separation.
Under one shared process serving both, it requires a bug in *wingman's own*
authorization code — a strictly larger, self-authored attack surface, for
a system whose entire value proposition (`VISION.md`, `AGENTS.md` invariant
3: "Local-first private data") is that this exact data never needs to trust
anyone's app-level access-control code. Every additional line of
multi-tenant authz is a line that has to be right forever; the current
model needs zero such lines.

**Two users is nowhere near the number where per-account overhead pays for
multi-tenancy.** `MULTI-INSTANCE-DESIGN.md` already names its own trigger
for revisiting shape B: *"Upgrade coordination hurts (N instances of shape
B want one `wingman-ctl upgrade --all-users` story; today each user
upgrades their own)."* That trigger is about **operational tooling across N
isolated instances** (issue #125, still open) — not about collapsing those
instances into one shared tenant. The number of users where "one Unix
account, one systemd unit, one port, one token" per person becomes a real
operational burden is much closer to dozens than to two.

**This repo has already decided, repeatedly, not to build a second auth
system.** RFC-033's own alternatives-considered section, written when the
web UI first shipped, rejected exactly this: *"a security surface wingman
deliberately doesn't have; the capability token is already the session, and
a second auth system would be the most dangerous code in the repo."*
Nothing about tonight changes that calculus — if anything, watching a
misconfigured *file* cause an eight-hour outage is a data point for how
much can go wrong with something far simpler than an OAuth implementation
(token issuance, refresh, revocation, session storage, an IdP dependency or
a hand-rolled equivalent).

**Multi-tenancy is a prerequisite for a product decision this repo has
explicitly parked, not made.** `docs/SERVER.md`'s opening line: *"local-first
with a longer extension cord, not hosting (RFC-002 holds; the hosted tiers
remain a separate, parked decision)."* Building multi-tenant infrastructure
now would be pre-building for a business decision nobody has made, for the
benefit of zero current users (dhk and Trent are both already served, each
by their own isolated instance).

## Alternatives considered

- **OAuth as a login UX improvement, keeping one-instance-per-account
  unchanged.** A real, separable idea: replace the capability-token-in-URL
  with a proper login flow for a *single* still-isolated instance, purely
  to fix "the URL is unmemorable and un-revocable-by-glancing-at-it,"
  without touching who can reach whose data. This is a smaller, more
  defensible increment than "become multi-tenant" — but it still adds a
  session/credential system where a rotatable capability token
  (`wingman-mcp --rotate-token`) already exists and already works, and
  issue #135's subdomain-routing proposal solves the actual complaint
  (illegible, port-riddled URLs) without adding one. Parked behind #135;
  revisit only if #135's fix turns out insufficient in practice.
- **Multi-tenancy without OAuth (shared process, still capability-token
  gated per tenant).** Doesn't remove any of the app-level-authz risk
  named above, and removes the free OS-level isolation for no offsetting
  benefit — strictly worse than shape B at the current user count.
- **Doing nothing about tonight's pains at all, on the theory that a future
  architecture change would obsolete today's tooling investment.** Rejected
  implicitly by shipping #122/#133/#137/#138 tonight: the fixes are cheap,
  general-purpose (they'd still be useful even if wingman *did* eventually
  go multi-tenant — better diagnostics and one canonical key file are not
  shape-B-specific ideas), and address real, already-occurred incidents
  instead of a hypothetical future one.

## Rationale

The four incidents felt architectural in the moment — hours of downtime,
a near-miss on another person's live server — but a careful pass through
each one lands on "tooling wasn't ready for two accounts on one box," not
"two accounts on one box is the wrong shape." The tells are consistent:
every fix that actually resolved an incident tonight (#122, #137, #138,
#133) made the *existing* model's boundaries more visible and more
respected (name the winning key source, verify the port not just the pid,
detect cross-account squatting explicitly, restart only your own instance
with only your own token) rather than dissolving those boundaries. That's
the opposite of what evidence-for-multi-tenancy would look like.

## Revisit if

- **A third user appears.** `MULTI-INSTANCE-DESIGN.md`'s own trigger:
  *"three friends is a product; the hosted-tiers assessment stops being
  parked."* At that point this document's calculus should be redone from
  scratch, not assumed to still hold — it explicitly does not conclude
  "never," only "not at n=2, not from tonight's evidence."
- **Per-account operational overhead becomes the actual bottleneck** —
  concretely, if issue #125 (a scheduled upgrade-all-users service) and
  further tooling like tonight's still leave upgrading/monitoring N
  instances meaningfully painful as N grows, that's the real signal, and
  the answer is likely still "better ops tooling across N isolated
  instances" before "collapse them into one shared tenant."
- **A deliberate, explicit hosted-product decision gets made** — i.e.
  RFC-002's "hosted tiers" stop being parked, on purpose, with a real
  business reason, not as a side effect of chasing tonight's operational
  pain. That decision should be made in daylight, as its own RFC, weighing
  the authz-surface trade-off named above directly — not inferred
  retroactively from a rough night running two instances by hand.
- **Someone wants OAuth purely for login UX** on an otherwise-unchanged
  single-tenant instance. That is a much smaller, separable proposal from
  "multi-tenancy" and could be evaluated on its own terms against #135's
  subdomain-routing alternative — but only once #135 ships and is found
  wanting, not preemptively.
