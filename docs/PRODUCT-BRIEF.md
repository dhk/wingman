# Wingman — Product Brief for UX/Product Design

*Written for a designer with no prior context on this codebase. Source: this repo's `docs/RFC.md`, `docs/DESIGN.md`, `docs/PROFILE-BOOTSTRAP-DESIGN.md`, `VISION.md`, `ROADMAP.md`, `src/wingman/cli/main.py`, `src/wingman/mcp_server.py`, `src/wingman/webui.py`, as of 2026-07-28.*

---

## 1. What Wingman is, and who it's for

Wingman is a personal career-search assistant for one person at a time, run on their own machine. It reads in things about the user — a resume, a LinkedIn export, articles they've written, or (new) their reactions to other people's content — and builds a "career brain": a structured, evidence-backed profile of who this person is, what they've done, and what they believe. From that profile it does the work a sharp friend-slash-analyst would do during a job search: score how well a job posting actually fits, research a target company, track people and companies worth watching, draft the raw material for outreach and cover letters, and hand the user a daily digest of what changed overnight.

The target user is a single professional (today, essentially the product's own owner) conducting a real job search or maintaining career/relationship intelligence over time — not a recruiter, not a team, not a multi-tenant SaaS customer. Its stated ambition ("North Star," from `VISION.md`) is: *find a small number of exceptional opportunities and make the user the obviously best candidate for the best of them.* It explicitly refuses to be an autonomous applicant, a mass-application bot, or a keyword-stuffing resume optimizer.

## 2. The core loop / mental model

```
Evidence  →  Profile / POV  →  Judgment  →  Action (never sent automatically)
```

1. **Evidence goes in.** The user's own writing (essays, resume, LinkedIn), or — new — their stated reactions to other people's/companies' content. Every piece of evidence is stored with its exact source, and any claim built from it can be traced back to a verbatim quote.
2. **Evidence becomes structure.** A career profile (roles, skills, achievements, testimonials), a "POV" — the user's own point of view on topics, synthesized from their writing — and, for other people/companies being watched, their own POV cards.
3. **Structure feeds judgment.** A job posting is scored against the profile (what's a match, a partial, a gap, unknown). A company is scored for alignment against the user's POV. People and companies are tracked for relevant news and new writing.
4. **Judgment produces raw material for action** — an application pack, outreach talking points, a prioritized daily digest — but Wingman never sends anything on the user's behalf. Every output is material for the user to review, edit, and act on themselves.

The one rule that runs through everything: **nothing is asserted without a quote to back it.** If there's no evidence, the honest answer is "no stance yet — here's the command that would produce one," never a plausible-sounding guess.

## 3. Feature inventory

Organized by user-facing capability. Command names are CLI-first; nearly every capability also exists as an MCP tool used from a Claude conversation (see §4).

### Corpus / writing ingestion
**What it does:** Imports the user's own published writing (blog posts, Substack, README files, articles) as searchable, quotable evidence.
**Trigger:** `wingman corpus add <path|url|zip>` — a file, a directory, or an export zip.
**What the user gets:** Documents indexed for keyword search (and, if embeddings are on, semantic/meaning-based search); this becomes the raw material for the user's own POV.

### Resume / LinkedIn ingestion
**What it does:** Extracts achievements, skills, roles, and testimonials from a resume or a LinkedIn data export, each one tied to the exact sentence it came from.
**Trigger:** `wingman ingest <file>` (Markdown, PDF, DOCX, LaTeX, or a link-accessible Google Doc/Drive URL) or `wingman ingest-linkedin <export.zip>`. Also available through the web UI's upload form.
**What the user gets:** A structured, cited career profile (`career.md` / `career.json`) — every achievement/skill/role links back to its source sentence. Re-importing an updated version of the same document intelligently updates existing claims rather than duplicating them; genuinely conflicting claims from different sources are shown side by side for the user to resolve (`wingman profile list` / `resolve` / `rm`), never silently overwritten.

### Perspectives / Interview — profile bootstrap without existing writing (newest feature, most design-relevant)
**What it does:** Lets someone with *no* pre-existing published writing still build a real, evidence-backed profile — by reacting to content and explaining why, or by nominating people/companies they admire or don't. This is additive to the corpus path, not a replacement — someone with writing can do both.
**The mechanic, always the same three steps:**
1. The user picks a piece of content (a URL, PDF, DOCX, or text file) — or, for nominations, names a person or company.
2. They state a reaction (agree/disagree, or "I'd have dinner with them" / "I'd never buy from them," etc.).
3. They explain **why, in their own words.**
Only step 3's text ever becomes evidence of the user's own position — the submitted content itself is never quoted as if it were the user's words. This distinction (their reasoning vs. the stimulus) is the one hard rule of the whole feature.

**Three interview modules (categories):**
- **Alignment of perspective** — the entry-level module: 2–3 pieces of content the user agrees with, 2–3 they don't, one line of "why" each. This is Tier 1 — the fastest, lowest-commitment path.
- **Values (people)** — a "three people you'd have dinner with" / "three you'd be horrified to be named alongside" pro/con nomination pair, each with a "why." Con is asked before pro, and within each group the *second* nominee is asked about first (deliberately avoids the rehearsed first answer). If someone can't name people, it falls back to the same pro/con structure but with companies ("whose products are you proud/never willing to buy").
- **Mission alignment (groups)** — a parallel pro/con nomination, but about organizations one would want to work at / be proud or horrified to be associated with (companies, clubs, causes — not necessarily employers). Every nominated org also gets a quick "what do you understand this org's purpose to be" follow-up, captured as context, not evidence. If someone draws a blank, there's a memory-anchored fallback question ("an organization you've actually been part of") before the module simply skips.
- **(v2, not yet built) Company-alignment variant** — the same reaction mechanic pointed at a specific target company's own content during an active job search, as a more direct alternative to today's indirect, embedding-based company-alignment scoring.

**Trust ladder (a suggested order, never enforced):** Tier 1 (zero-commitment reactions) → Tier 2 (Values + Mission alignment) → Tier 3 (deeper, opt-in: company-alignment reactions once there's a real job-search target, resume/LinkedIn mining, optionally attaching real writing). Nothing gates anything else — a user can do any module in any order, any number of times.
**Trigger today:** `wingman interview <subtype> <target> <why>` (one capture at a time) or the guided `wingman perspectives` CLI flow (asks content-vs-interview up front, then walks Tier 1 conversationally). MCP equivalents: `interview_react` (single capture, protocol embedded in its docstring for the driving AI to follow: con-before-pro, second-nominee-first, show-before-save) and `perspectives_start` (the branching entry point for an AI-driven onboarding conversation).
**What the user gets today:** Each answer is stored as citable evidence (reviewable via `wingman profile list`) **and is already synthesized into the user's POV** — `wingman pov`/`my_pov` reads interview captures alongside any corpus writing (or on their own, with zero corpus) to build a cited stance, using the same evidence-validated machinery as the writer's path. v0 and v1 are both fully shipped; only the v2 company-alignment reaction variant remains unbuilt. There is no dedicated visual UI for any of this yet — see §6.

### People / company watchlist
**What it does:** Tracks specific people and companies the user cares about — their public writing (RSS/blog feeds), recent news, and (for companies) approved research pages (careers/blog/newsroom).
**Trigger:** `wingman people add <name>`, `people add-feed`, `company add-source`, `company follow <name>` (the one-shot "start caring about this company" command — enrolls the company, known people there, and probes conventional pages like `/careers`, `/blog`), `wingman sync` / `wingman people fetch` to pull new posts, `wingman people news <name>` for a news snapshot.
**What the user gets:** A per-person or per-company page (`export`) with their writing, a "warmth" signal (how connected the user is to them), and recent news — plus, overnight, an automated deep-refresh of everything followed (`wingman overnight`).

### POV & alignment
**What it does:** Synthesizes a "point of view" — subject areas where a person, company, or the user themself takes a position — from their stored writing, always with the exact quote behind each stance.
**Trigger:** `wingman pov` (the user's own), or the per-person/company equivalents (built into export/dossier flows).
**What the user gets:** A POV card per subject: a stated position plus its verbatim source. Company alignment scoring compares a company's POV/writing against the user's own (today via embedding similarity; Perspectives' v2 company-reaction variant will add a more direct alternative).

### Job criteria & scoring
**What it does:** Scores incoming job postings against what the user actually cares about, in three auditable layers: cheap embedding-based recall (what's worth a closer look), a model-judged score against an explicit, human-readable criteria document (`job-criteria.md` — hard filters like location/comp, weighted wants, anti-signals), and a learning loop where the user's own accept/reject verdicts propose edits to that criteria doc (never auto-applied).
**Trigger:** `wingman assess <description>` or `wingman assess --url <posting>` (fetches the posting directly); the criteria doc is seeded through a guided interview and edited via `wingman job-criteria`.
**What the user gets:** A cited fit assessment — met/partial/gap/unknown per requirement, each backed by a real profile item or a flagged gap.

### Outreach
**What it does:** Drafts the raw material for reaching out to a specific person — talking points connecting their POV to the user's own writing, plus intro bullets — and, for a specific role, an "application pack": a cited fit summary and cover-letter fodder (bullet points pairing each matched requirement with a verbatim quote from the user's evidence). Wingman never writes the final message or sends anything — RFC-006's rule — this is material for the user to compose in their own voice.
**Trigger:** `wingman people brief <name>`, `wingman pack "<role>"`, or the one-shot `wingman make-it-so <name>` ("everything end to end for a person or company").
**What the user gets:** Markdown documents under `reports/` — a brief or a pack — ready to read, print, or paste from.

### Digest
**What it does:** A daily/overnight summary of everything that changed across every followed person and company — new posts, news, research-page changes, scored job openings — plus a prioritized action list.
**Trigger:** `wingman overnight` (the "spend the tokens overnight" deep-refresh run, typically scheduled via cron/launchd on the user's own machine — Wingman itself ships no scheduler/daemon).
**What the user gets:** A dated digest under `reports/digests/`, viewable as styled HTML via the web UI. Actions the user doesn't care about can be muted or snoozed permanently (`wingman actions mute/snooze`) so the same "uninteresting" item never rolls over into tomorrow's digest.

### Other capabilities worth knowing about
- **Unified search** (`wingman search`) — one query across the corpus, watched people's writing, POV stances, news, research findings, and outreach briefs, keyword + semantic.
- **Answer bank** (`wingman answers`) — stores application-question answers the user has refined and confirmed, for reuse across applications.
- **Relationship objectives** (`wingman objective`, `wingman log`) — a per-person "why am I investing in this relationship" thesis, an interaction log, and digest reminders tied to it.
- **Heap** (`wingman heap`) — a zero-friction capture inbox for interesting leads (a URL, a screenshot reference) dropped in the moment, heat-rated, sorted later.
- **Feature requests** (`wingman feature request`) — turns "feature request: ..." said in conversation into a previewed, confirmed GitHub issue (this repo's own dogfood of the approval-gate pattern).

## 4. Current interface reality

This is the most important thing for a designer to internalize: **there is essentially no dedicated visual UI yet.** Wingman today is used in two ways:

1. **A CLI** (`wingman ...`) — dozens of Typer subcommands, run directly in a terminal.
2. **An AI assistant driving MCP tools** — the *primary*, intended way most people actually use it. Wingman exposes its tools over the Model Context Protocol; the user has a natural-language conversation with Claude (Desktop, Code, or claude.ai via a remote connector), and Claude calls the tools. Several tools carry an explicit *conversational protocol* in their own docstrings — e.g., "ask clarifying questions," "show the exact text before saving, only save after the user agrees," "ask about the second nominee first" — because the AI client itself *is* the interaction surface; there is no separate UI enforcing that flow.

There is a small **local web UI** (`webui.py`, served over a loopback HTTP listener behind a private token/tunnel), but its scope is deliberately narrow — described in its own code as "conversation is for thinking; this page is for what conversation is bad at: glancing and files." It currently offers:
- A **Digest** tab (the latest overnight digest, styled)
- A **Files** tab (browsing dossiers, packs, exports, reports — read-only file links)
- A **Changelog** tab (Wingman's own recent development activity)
- A **Connect** tab (MCP connector URLs to paste into a Claude client)
- A **Manage** tab (upload a resume or LinkedIn export zip, enter/verify API keys, restart the server)

There are **no search boxes, no triage buttons, no interactive editing, no graph browsing** in the web UI today — that is a stated, deliberate scope boundary (RFC-033), not an oversight: today's whole interactive surface is the AI conversation.

**Implication for design:** a designer working on this product is very likely designing a **new primary UI surface from close to scratch**, not restyling an existing app. The existing web UI is a "read and upload" utility, not a foundation to extend into an interactive product UI — extending it that way would cut against an explicit design decision in this codebase, so any such move should be a conscious call, not an assumption.

## 5. Design-relevant constraints and invariants

These come from the product's stated invariants (`VISION.md`) and hold regardless of what UI gets built on top:

- **Evidence must always be inspectable.** Every claim, score, or stance a UI shows the user must be traceable to the exact source text it came from — never presented as a bare AI assertion. A UI should make "show me why" a first-class, cheap interaction, not a hidden feature.
- **No invented familiarity.** The product will not claim a relationship, a referral, or an achievement that isn't backed by real evidence. A UI should never soften a "no stance yet — here's what would produce one" into something that reads like a real answer.
- **Human approval before any external action.** Wingman never sends a message, applies to a job, or posts anything on the user's behalf. Anything that looks like a "send"/"apply"/"post" button is out of scope unless it's explicitly a copy-to-clipboard/export, not a live send.
- **Local-first, private by default.** All data lives in one file on the user's own machine; there's no cloud account, no multi-tenant backend. A UI shouldn't imply an account/login system beyond what's needed to reach the user's own local instance (today: a private capability-token URL).
- **"Additive, not gated" in Perspectives.** Every interview module (Alignment of perspective, Values, Mission alignment) and every ingestion path (corpus, resume, interview) is independently usable in any order; the suggested trust-ladder ordering is a UX nudge, never an enforced gate. A UI should let a user jump straight to any module, skip ahead, or come back later without penalty.
- **Honest, visible degradation.** When something is missing or a fetch/step fails, the product says so plainly and names the fix ("no stance yet — run X") rather than hiding the gap or guessing. A UI should preserve that legibility rather than papering over empty states with generic placeholders.
- **Deterministic scoring, model judgment only where it adds value.** Scores (job fit, alignment) are built from an auditable set of components, not an opaque single number a UI would have to just trust.

## 6. Where UX attention would matter most right now

1. **Perspectives / onboarding — the newest and clearest gap.** This is the feature most likely to get real design attention first, and it has essentially nothing visual today: it's either raw CLI prompts (`wingman perspectives`, one question at a time in a terminal) or a chat-driven MCP flow where an AI assistant is manually enforcing ordering rules (con-before-pro, ask-the-second-nominee-first, primary-purpose follow-ups) that exist only as instructions in a tool's docstring. A dedicated UI could make the trust-ladder (Tier 1 → 2 → 3), the pro/con nomination flow, and the reaction-capture mechanic (submit content → react → explain why) feel like a purpose-built interview experience rather than a chatbot doing its best to follow written rules. Under the hood it's fully wired end-to-end — captures already synthesize into a real POV — so a UI here is pure surface work, not waiting on unfinished plumbing.
2. **The digest/action-triage loop.** The digest is the product's daily habit-forming surface (it explicitly exists to be "glanced at"), and it already has real interaction concepts behind it (mute/snooze/keep verdicts, per-item stable keys, a suppression count) that today only exist as CLI/MCP actions — the web UI shows the digest read-only, with no triage buttons at all (a stated, deliberate scope cut). This is a strong second candidate for a real UI treatment.
3. **Evidence inspection generally.** Because "evidence must be traceable" is a core invariant, and almost everything the product produces (profile items, POV cards, job-fit verdicts, outreach bullets) carries a citation back to a quote, a good "inspect the evidence behind this claim" pattern, designed once, would pay off across nearly every feature area — profile, POV, scoring, outreach — rather than needing to be reinvented per feature.
4. **Company/people research and job-opening scoring** are data-rich (dossiers, alignment scores, fit verdicts) but currently only exist as generated Markdown documents — functional, but not designed as an interactive product experience.

---

*Everything above is grounded in this repository as read on 2026-07-28. Where the design docs describe planned-but-unbuilt behavior (e.g., the v2 company-alignment reaction variant), that is called out explicitly rather than presented as shipped.*
