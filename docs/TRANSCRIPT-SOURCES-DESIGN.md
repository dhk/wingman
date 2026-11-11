# Transcript sources — find, fetch, summarize, extract (DRAFT)

Status: draft for owner review. Nothing here is implemented.
Decided by owner (2026-11-11): the scheduled sweep stages proposals; nothing is filed until a human approves.

## Problem

Real conversations are the richest career signal Wingman never sees. Today
`ingest_transcript` takes one Gemini export, one file at a time, with the
client supplying the bytes. The user wants Wingman to go and *find* new call
transcripts in the apps they already use (Wispr Flow, Gemini meeting notes in
Drive, Zoom, ...), summarize them, and pull out what matters — and to say once,
in configuration, where their transcripts come from.

## The constraint that shapes everything

Connectors (Wispr Flow, Google Drive, Zoom) are authenticated **in the Claude
client**. The Wingman server (lobster, the shared multi-tenant process) has no
credentials for them, and AGENTS.md invariants 3 and 4 (local-first, least
privilege) argue against giving it any in v1. So the feature splits:

| Layer | Owner | Responsibility |
| --- | --- | --- |
| Sweep orchestration | Claude client (a skill, `transcript-sweep`) | Read config, call each source's connector tools, hand text to Wingman |
| Config, ledger, parsing, extraction, filing | Wingman server | Deterministic everything; model only for extraction |

This also means the existing 06:00 host-side overnight run **cannot** do the
fetch. A scheduled Claude task runs the sweep instead (see Triggering).

## User configuration

New per-tenant setting, `transcript_sources`, managed by a `transcript_sources`
MCP tool (`list | add | update | remove | test`) and `wingman transcripts
sources ...` in the CLI (surface parity: docs/SURFACE-PARITY.md).

```yaml
transcript_sources:
  - id: wispr-main
    kind: wispr_flow            # wispr_flow | gemini_drive | zoom | file_drop
    enabled: true
    lookback_days: 14           # first-run window; afterwards the cursor wins
    locator:                    # kind-specific, never a secret
      folder: "Calls"           # Wispr Flow folder name/id; optional
  - id: gemini-notes
    kind: gemini_drive
    enabled: true
    locator:
      drive_query: "title contains 'Notes by Gemini'"
  - id: zoom-cloud
    kind: zoom
    enabled: false              # v2
```

- No credentials in config. Connector auth stays in the client.
- `test` is a dry run: lists what a sweep *would* fetch (titles, dates,
  counts), persists nothing.
- Each source carries a cursor (last-seen modified time) so a sweep is
  incremental.
- Adding a new app = one adapter (below), no schema change.

## Normalization: adapters to one `Transcript`

`application/transcript.py` is Gemini-shaped today (Notes + Transcript tabs,
`Invited` line). Generalize with per-kind adapters producing the same
`Transcript` structure:

- `gemini_drive` — existing parser, fed Doc text from Drive instead of a file.
- `wispr_flow` — `get_meeting` returns transcript text, attendee list and
  calendar link; prefer the transcript over the auto-summary (the Wispr
  tool descriptions say the same: summaries are lossy).
- `zoom` — VTT/cloud transcript (v2).

Carry forward the existing hard rules: only transcript turns are anybody's
words; notes/summaries are the tool's paraphrase and stay labelled as such;
speaker labels that don't match a known participant are reported, never
reconciled.

## Ledger and dedupe

New table `transcript_sources_ledger(source_id, external_id, content_hash,
fetched_at, status, person_ids)`. Dedupe key is `(source_id, external_id)`,
with `content_hash` detecting an edited upstream transcript (re-propose, never
silently overwrite — AGENTS.md data rule). Reuses `SourceRecord` and the
`find_transcript_filing` check so a call that arrives via two sources (Zoom
call that Gemini also noted) is flagged as a probable duplicate by
participants + start time.

## Extraction

One `extract_fast`-class model call per transcript, schema-bound, producing:

1. **Summary** — a few sentences, plus decisions and open questions.
2. **Action items** — owner, text, due date if stated; each with the turn it
   came from.
3. **People and company facts** — each must cite a verbatim turn (timestamp +
   speaker). Facts without a supporting quote are dropped by a deterministic
   validator, not trusted. Same evidence-validation shape as resume ingest.
4. **Job-search signals** — opportunities mentioned, intros offered, next
   steps, compensation or timeline statements; routed to opportunities /
   company POV as *proposals*.

Everything model-generated is tiered **ENDORSED only after human approval**;
before that it is a proposal. Transcript text is untrusted input (AGENTS.md):
instructions inside a transcript are data. Provider, model, prompt version and
token use are recorded per the model-use rules.

## Where results land (all as proposals first)

| Extracted | Destination | Gate |
| --- | --- | --- |
| Summary + decisions | `relationship_log` entry for the matched person | approve |
| Action items | `action_triage` queue (Todoist push stays a client step) | approve |
| Facts about people/companies | evidence proposals on person/company records | approve each |
| Job signals | `opportunities_list` / company POV proposals | approve |
| Unmatched participants | listed, **not** auto-created (invariant 8: no invented familiarity) | explicit add |

Participant matching reuses `match_speakers` and the invite/attendee emails;
ambiguous matches go to a clarifying question, never a guess.

## Triggering

- **On demand:** "process my calls" runs the skill over enabled sources.
- **Scheduled:** a scheduled Claude task runs the same sweep nightly and
  *stages* a review queue. It never files. Next morning the digest shows
  "N calls waiting for review". This preserves invariant 1 (human approval
  before any write that matters) while meeting "just have it done".
- Host-side overnight cannot fetch (no connector auth). Server-side pollers
  with their own OAuth are an explicit later decision, not v1.

## Privacy

Calls contain *other people's* words. Local-first applies: originals archived
in the workspace inbox; transcript bytes and extraction output withheld from
telemetry (same as `ingest_transcript` today). Extraction sends transcript
text to the configured model provider — say so in the source `test` output
and in setup docs. Per-source `enabled` plus per-sweep preview gives the user
a hard stop. Consider a `redact_participants` option and a note about
recording-consent norms.

## Vertical slices

1. Config tool + ledger, with a `file_drop` source and the `test` dry run.
2. Generalized `Transcript` + Wispr Flow adapter + extractor + proposal queue.
3. Gemini-in-Drive discovery through the same path.
4. `transcript-sweep` skill + scheduled staging task + digest line.
5. Zoom adapter.

Each slice ships something usable alone.

## Open questions

- Phone calls proper (Twilio recordings) vs. meeting recorders: in or out for
  v1? This draft assumes out.
- Review UX: one batch card per sweep, or per-transcript preview using the
  existing `reviewed_hash` flow? This draft assumes per-transcript hash, batched
  presentation.
- Retention: keep originals forever, or a configurable window?
- Multi-tenant: config is per tenant; does the extraction spend count against
  the existing spend-tracking (RFC-084) per tenant? Assumed yes.
