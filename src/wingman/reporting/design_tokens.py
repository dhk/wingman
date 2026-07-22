"""The shared design-token block (issue #95 spec §3, wired by #118 §9).

One Python constant feeds every surface — the web UI (`webui._UI_CSS`), the
PDF/export stylesheet (`export.WINGMAN_PDF_CSS`), and everything composed
from them (digest HTML twin, pack sheets, person tabs) — so Wingman and the
DHK design system stop drifting: change a value here and every artifact
picks it up. Light is the default; dark rides `prefers-color-scheme` with
no class toggle and no JavaScript. Nothing is fetched at render time —
fonts degrade through their stacks (RFC-009).
"""

from __future__ import annotations

DESIGN_TOKENS_CSS = """\
/* wingman design-system tokens — Electric Cobalt (issue #95 spec, canonical).
   Light default; dark via prefers-color-scheme. No external requests: fonts
   degrade through their stacks. Legacy variable names are kept so every
   existing component reads the new palette without edits. */
:root {
  --bg: #f5f6fa; --bg2: #eceef5; --bg3: #e2e6f0;
  --border: #c8cde0; --border-light: #dfe3ee;
  --text: #14181f; --text-head: #14181f; --text-muted: #444b5c; --text-dim: #7b8298;
  --accent: #2b50e8; --accent-hv: #1a3bd4; --accent-blue: #2b50e8;
  --teal: #0e9591; --accent-purple: #6b3fd4; --accent-orange: #d1660a;
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-cond: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: "DM Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --border-radius: 4px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1120; --bg2: #151a2e; --bg3: #1d2440;
    --border: #2a3350; --border-light: #222a45;
    --text: #e7ebf7; --text-head: #e7ebf7; --text-muted: #b3bad4; --text-dim: #8891b5;
    --accent: #5b7cff; --accent-hv: #7d97ff; --accent-blue: #5b7cff;
    --teal: #3fd0c4; --accent-purple: #9d7dff; --accent-orange: #f0954a;
  }
}
"""
