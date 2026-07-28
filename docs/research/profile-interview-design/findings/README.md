# Findings — submission guide

**File naming.** One file per source: `<source-slug>-findings.md`. The
slug identifies who/what produced it, e.g. `claude-findings.md`,
`gpt5-findings.md`, `dave-findings.md`. Don't overwrite another source's
file — each stays as its own independent pass.

**Format.** Copy `TEMPLATE.md` and fill it in. Don't restructure it — the
synthesis pass reads every findings file expecting the same section order
and table shape.

Two rules, non-negotiable:

- **Verdict is a closed set: `adopt/reference`, `differentiate`, or
  `ignore`.** No invented labels (no "maybe," no "adopt with caveats" as a
  verdict value — put the caveat in Notes). Pick the closest of the three
  and explain the nuance in prose.
- **No empty tables.** If a question turns up nothing, the table still
  gets one row: `none found` in the Entry column, with the reason
  (searched and confirmed absent vs. ran out of time vs. paywalled/
  inaccessible) in Notes. Silence reads as "not researched," not as "found
  nothing" — say which one it was.

**Submitting without repo write access.** Paste the completed findings
file's content back to whoever commissioned this research (or attach it as
a file). They'll commit it under this `findings/` directory using your
source slug as the filename. If you were handed `handoff-prompt.md` rather
than this repo directly, that prompt already contains everything you need —
you don't need repo access to do the research itself, only to file it.
