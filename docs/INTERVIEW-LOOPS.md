# Interview loops — first rollout (#652)

Goals & Friction **0.3.0** works alongside the existing interview tools.
No interview migration is included. This pilot ships the engine and one
bundled loop; dossier flows and the public contribution workflow come later.

## Use from Claude

Ask to list the interview loops, then start Goals & Friction. Three tools:

- `loop_list()` shows promise, minutes, sensitivity and tier.
- `loop_start(loop_id="goals-friction")` starts or resumes the current card,
  even in an empty workspace. Show the promise and sensitivity before asking
  the first question. Present `explain`, then `ask`, including the exits.
- `loop_answer(session_id, answer=...)` returns an exact capture preview and
  a confirmation token. Show the **whole preview**, including inference,
  authorship, action owner and due date. After the person's explicit approval,
  call `loop_answer(session_id, action="confirm", confirmation=...)`.

Use `choices=["offered-id", ...]` for options, string `"1"` through `"5"`
for a rating, and `YYYY-MM-DD` for a deadline. Each call returns one decision.
The commit card asks the step and then the deadline separately; the felt
card asks the rating and then the reason separately. A missing answer is not
invented: skip or pause instead. Skipping felt records no fabricated feeling.

Every card offers `skip` and `pause`. `pause` preserves the current card.
`show` reads it back. At `want`, `action="route", answer="reactions"` returns
an explicit handoff to existing `perspectives_start`; it does not call it,
fetch anything, or advance the paused loop. There is no prerequisite loop.

The CLI has the same operations:

```sh
wingman loop list
wingman loop start
wingman loop answer SESSION_ID --answer 'My exact answer'
# Read the preview, then explicitly approve it:
wingman loop answer SESSION_ID --action confirm --confirmation TOKEN
wingman loop answer SESSION_ID --action pause
```

Do not paste private confirmation tokens into issues or logs. They contain
an encoded draft, not encrypted text. Wingman's loop telemetry withholds
arguments and output, even when the existing usage journal is enabled.
Transcript harvesting scans for loop activity before importing any events. If
found, the **whole transcript** contributes event kinds, timestamps and source
metadata only: no user/assistant text, tool inputs, shell commands or tokens.
This conservative rule avoids guessing where an interview's private turns begin
and end. The CLI explicitly reports when content was withheld. Ordinary
transcripts without loop activity retain the existing opt-in import behavior.
This applies to new imports; it does not edit previously harvested events or
the original conversation file. The host/chat client still sees what the person
types and the returned preview.

## Goals & Friction 0.3.0

The sequence is care → hard → stuck → learn → want → ladder → pick →
prescribe → commit → felt. The added `explain` on felt and `anchoring: none`
on ladder make the supplied draft internally consistent. The first card
explicitly welcomes an empty workspace.

The person chooses an optional friction category and confirms that
interpretation. Goal/problem framing is also a separate confirmed decision.
Both are stored as **inference**, never profile evidence. Stage 1 uses no
model calls to classify an answer. The ladder stops at three levels and
allows stopping sooner. Pick offers only the person's own confirmed words.

The broadened playbook covers starting, competing demands, an unclear next
step, consistency, confidence, support, and time or energy. It is a small
set of possible moves, not a clinical assessment or universal advice.
"None fits / I don't know" leads directly to choosing one's own step.
Learning preference changes presentation: move first, reasoning with the
move, or a concrete first-step example first. Examples appear only when
choosing actions, never in questions collecting the person's evidence.

Up to three moves may be selected. Only a first step with a confirmed owner
and due date becomes a commitment; other moves are queued. On re-entry,
check whether the last action happened. If not, ask what got in the way and
store that answer as new friction. Finishing a step unlocks only the next
queued move, which still needs its own first step and deadline confirmed.
A newly confirmed commitment explicitly supersedes any unfinished one in
the write preview; old records remain available in the local history.

## Storage, confirmation and trust

Bundled files live at `src/wingman/loops/*.md`, separate from the corpus.
Local files may be placed in `DATA_DIR/loops/*.md`; they are labelled
**local**, cannot shadow a bundled id, and pass the same schema checks.
Validation is an admission gate: only passing files enter the runnable
catalogue. Preset `options` and `options_from` sources are allowed only on
`type: options` cards;
free-text, scale, felt and commitment cards cannot replace their required
inputs with preset menus. `loop_list` returns `invalid_loops` separately, with filename and
field/syntax problems. One rejected file cannot block valid loops; starting
that draft returns its own error without creating a session. Open sessions
continue from their pinned definitions, even if a source becomes invalid.
Each workspace scans only its own local directory.

Authors can check a file or directory before installing it:

```sh
wingman loop validate path/to/my-loop.md
wingman loop validate src/wingman/loops
```

The command returns a nonzero exit for rejected files or an empty/missing
source. CI runs the directory form against every bundled loop, using the
same checks as runtime admission. This is a mechanical validity gate;
community trust/review and publication approval remain a later workflow.
The bundled tier is **official**. Reviewed-community distribution,
commit approval manifests, revocation UI, a full UX lint command and PR
review automation are deferred with the contribution workflow.

SQLite has `loop_sessions` (scope, pinned definition/hash, position, revision,
confirmed answers) and `goals_plan` (append-only typed captures). No goals or
preferences are inserted into the corpus, profile, answer bank, or job
criteria. Every capture carries loop id, version, card id, SHA-256 of the
file, timestamp, scope and authorship. Existing database backup covers these
tables; nothing is exported for discovery automatically.

Unconfirmed text is **not persisted**, including in session state. It lives
only in the returned signed preview token. The token survives a server
restart, but confirmation must supply it again. Without it, resume repeats
the current question. A per-session secret signs the snapshot; revision
checks reject tampering, stale confirmations and replays. Captures and the
new session position commit in one SQLite transaction. Tokens carry only
new answers, reusing confirmed history from storage. Their serialized size is
checked against the same 200,000-character limit before offering confirmation;
if a preview would exceed it, the person is asked to shorten the answer and
stays on the current card with nothing saved.

A session keeps its original definition and content hash even if its loop
file changes or disappears. Subsequent sessions use the installed version.
The fixed schema rejects unknown fields, undeclared stores, arbitrary
routes, missing explainers, free-text presets/examples (including `e.g.` with
punctuation or capitalization variants), obvious references to an
agent/assistant/model, and unmarked card links. Duplicate YAML keys
and aliases are rejected. These are mechanical checks, not a claim to
understand every malicious sentence; human review is still necessary.

All loop text is display data. The engine does not evaluate expressions,
call other tools or fetch links. Stage 1 routes only to the named existing
reaction interview; dossier registration remains for stage 3. A card marked
`display_only: true` may show a link but cannot make the engine fetch it.
The host's permissions for unrelated connectors are outside this engine.

## Coaching and rescue notes

Scope follows the existing active `coach_persona`. Shared non-operator
workspaces cannot use coaching. Persona answers default to
`coach_inference`; set `persona_authored=True` only when the person supplied
the exact words. A committed action retains the confirmed first step's
authorship and classification, with a reference to that step's capture. The
deadline's authorship and classification are recorded separately in the action
details and shown in the write preview. Supplying a deadline or confirming a
coach's suggestion does not make the suggestion the person's original words.
`owner="coach"` is allowed only in coaching mode.
No delegated access or writes to another person's workspace are introduced.

`loop_answer(..., action="rescue", answer="What intervention was needed")`
previews a coach's intervention note at the current card. Confirming it
adds a local `kind="rescue"` record without advancing. It is coach inference,
never the person's evidence. Discovery sharing remains a separate opt-in
human action; this pilot has no transmitter or aggregate felt report.

## Pilot validation

Run with the maintainer and two or three pilot users. Review the locally
retained rescue notes and felt responses with their consent before changing
the loop. The reordered, goal-agnostic friction questions and generalized
playbook have not yet been validated against the original pilot recording.
The next rollout stages are rescue/felt review and revision, the flow
registry and career dossier, and finally contribution lint and PR review.
