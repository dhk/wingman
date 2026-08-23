# Dropbox Engineering Career Framework — retained notice and modifications

Copyright (c) 2021 Dropbox, Inc.

Licensed under the Apache License, Version 2.0. The full licence text is in
[`LICENSE`](LICENSE) beside this file.

**Source.** <https://github.com/dropbox/dbx-career-framework>, at commit
`6469a45a10cd61f9ce399e1c4ee1e57f26f0084d` (2023-04-08), `docs/` directory.
The published site built from that repository is at
<https://dropbox.github.io/dbx-career-framework/>.

**Used in.** `../../dbx-swe-ladder.toml`.

## Statement of modifications

Apache-2.0 §4(b) requires that modified files carry prominent notice of the
changes. `dbx-swe-ladder.toml` is a derivative of the framework's software-
engineering IC pages. What was changed:

1. **Format.** HTML pages were converted to one TOML file. No wording was
   altered in the process.
2. **Selection.** Only the software-engineering IC track (IC1–IC7) is
   carried, and only three of its sections: `Scope`, `Collaborative Reach`,
   and `Impact Levers`. The Core Responsibilities sections (Results,
   Direction, Talent, Culture), the manager track (M-levels), and the other
   engineering disciplines (data, ML, reliability, security, quality, SDET,
   technical program management) are **not** included.
3. **Concatenation.** Where a level states several sentences under one
   section, those sentences are joined into a single `descriptor` string,
   in their original order. Nothing was dropped, reworded, or reordered.
4. **Additions that are NOT Dropbox's words.** Three fields per dimension
   were written by Wingman and are not part of the framework:
   - `signals` — search terms this tool matches profile text against.
   - `probe` — the question Wingman would ask to close an evidence gap.
   - `confusable_with` — Wingman's warning about a misreading.

   Dropbox authored none of these three and does not endorse this tool.

## What is not asserted

Dropbox's levels are Dropbox's. They do not map onto any other employer's
ladder, and this file makes no claim that they do. Wingman's gap map does
not place anybody on one of these rungs — it reports which dimensions a
person's evidence speaks to, and which it does not.
