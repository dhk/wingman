# Competitive Landscape: Wingman vs. the Field

**Research date:** 2026-07-18  
**Input briefing:** `RESEARCH-BRIEFING.md`  
**Scope:** Product strategy and integration research for Wingman v0.3–v0.5. Local-first storage and explicit-egress architecture are intentionally excluded from scoring.

## Executive conclusion

Wingman should **not** become a general-purpose job-search suite, personal CRM, feed reader, page monitor, resume builder, or outbound automation platform.

Those markets are already mature, inexpensive, and operationally demanding. Teal and Huntr provide polished application tracking and resume workflows; Jobscan and Rezi specialize in ATS-oriented optimization; Readwise Reader and Feedly provide mature content ingestion and triage; Visualping and Distill provide substantially more reliable page monitoring; Clay, folk, Monica, and sales-intelligence products provide deeper relationship-management infrastructure.

Wingman's defensible product is considerably narrower:

> **An evidence-constrained opportunity intelligence and preparation system that turns a user's real work, writing, relationships, and approved research into defensible judgments and human-authored action.**

The product advantage is not data collection by itself. It is the chain of custody from source evidence to recommendation:

1. a claim is grounded in a verbatim source;
2. unknowns remain unknown rather than being smoothed over;
3. composition is deterministic where practical;
4. external action remains with the user;
5. opportunity quality outranks application throughput.

That combination is uncommon among the reviewed tools. Most competitors optimize speed, volume, convenience, engagement, or sales conversion. Many generate plausible prose; few expose strong claim-level provenance. The evidence discipline therefore survives contact with the market—but only if Wingman stops rebuilding commodity workflow components around it.

## Strategic verdict

| Wingman area | Verdict | Core reason |
|---|---|---|
| A. Profile and corpus | **BUILD, narrowly** | Evidence-backed canonical career knowledge is strategic; resume design, generic PKM, and broad document management are not. |
| B. Opportunity assessment | **BUILD (versus)** | Quote-backed requirement verdicts are the strongest differentiated wedge. |
| C. People intelligence | **BUILD the evidence layer; integrate the CRM layer** | POV provenance and relationship-to-opportunity reasoning differentiate; contact management and activity logging do not. |
| D. Company intelligence | **BUILD synthesis; keep collection thin** | Dated, quote-backed themes and explicit fact/inference separation are valuable; crawling and page monitoring are commodity infrastructure. |
| E. Outreach support | **BUILD, with a hard boundary** | Evidence-linked talking points for human composition differ from automated sequence tools. Do not send. |
| F. Delivery and orchestration | **KEEP THIN / INTEGRATE** | MCP is a useful integration surface, but tool count, rendering, backup, scheduling, and monitoring are not the product. |

## 1. Market map

### 1.1 Job-search operating systems: Teal, Huntr, Simplify, Careerflow

**[Fact, accessed 2026-07-18]** Teal combines unlimited resume creation, job tracking, keyword matching, resume analysis, email templates, cover-letter generation, and interview practice. Its paid plan is currently offered at $13 weekly, $29 monthly, or $79 quarterly.

**[Fact]** Huntr combines a job tracker, contact tracker, application autofill, tailored resumes, cover letters, interview tracking, and job-match scoring. Its Basic tier tracks up to 100 jobs; Pro is $40 monthly, $90 quarterly, or $160 for six months.

**[Fact]** Simplify positions its paid product around resume tailoring, application writing, networking, outreach, job targeting, and a tracker.

**Assessment.** These products have already won the "job-search workspace" category. They have browser extensions, polished UI, kanban tracking, reminders, templates, application autofill, and high-frequency workflow affordances that would consume multiple Wingman versions to reproduce.

Their dominant model is **throughput and workflow completion**: save jobs, tailor materials, apply, and track. Wingman's north star is deliberately different. This is not a reason to compete on their surface; it is a reason to interoperate.

**Decision.**

- Do not build application-stage tracking, kanban boards, autofill, reminders, or a browser clipping ecosystem.
- Export an opportunity packet in a stable JSON/Markdown shape that can be attached to a Teal/Huntr record.
- Consider a one-way import from common job-tracker CSV exports.
- Treat "the user pairs Wingman with a tracker" as the default workflow, not a product failure.

### 1.2 Resume and ATS optimization: Jobscan, Rezi, Kickresume

**[Fact]** Jobscan explicitly scores resumes against job descriptions, prioritizing hard skills, education where required, job title, soft skills, and other keywords. It markets ATS-specific parsing and optimization.

**[Fact]** Rezi offers resume construction, scoring, keyword targeting, AI editing, interview practice, PDF export, and a human review in its $29/month Pro tier; it also offers a $149 lifetime plan.

**[Fact]** Kickresume offers templates, AI resume and cover-letter writing, ATS checking, career planning, and personal-site generation. Current standard pricing is $24 monthly, $18/month quarterly, or $8/month annually.

**Assessment.** Resume formatting, ATS templates, keyword surfacing, and generic bullet rewriting are mature and inexpensive. Wingman should not attempt to prove that its resume is more ATS-friendly than specialized tools.

However, the market's optimization model creates the opening for Wingman. Keyword-match systems generally answer, "How can this document look more aligned?" Wingman can answer the prior and more consequential question: **"What can the user truthfully claim, what evidence supports it, and what remains a gap?"**

That is a different object. A high match score can be created by lexical alignment. A defensible candidacy assessment requires provenance, contradiction handling, and abstention.

**Decision.**

- **Drop** resume keyword scoring as a product ambition.
- Keep a minimal deterministic resume renderer only because Wingman's cited profile must be exportable.
- Add a structured "evidence packet" that can accompany external resume tailoring:
  - requirement quote;
  - verdict;
  - supporting career evidence;
  - confidence;
  - missing evidence;
  - prohibited unsupported wording.
- Permit import/export to resume tools, but never silently accept AI-generated resume claims back into the canonical profile.

### 1.3 Personal CRM: Clay.earth, Dex, folk, Monica

**[Fact]** Monica is an open-source personal CRM with contacts, relationship data, activities, reminders, tasks, and journaling. It can be self-hosted for free; the hosted paid tier is $9/month or $90/year.

**[Fact]** folk models people and companies, groups, custom fields, interaction history, dashboards, enrichment, and API access; its higher tiers expose deeper history and integrations.

**Assessment.** Contact records, reminders, relationship notes, interactions, synchronization, deduplication, and timeline UI are an established category. Wingman's current three-signal warmth score is immature against even basic CRM products.

But a conventional CRM asks, "What interactions have occurred?" Wingman can ask, "Why might this person be relevant to this opportunity, and what source evidence justifies the proposed conversational bridge?" The second question depends on the user's corpus, the person's cited POV, company themes, and opportunity requirements. That cross-domain composition is strategic.

**Decision.**

- Do not build a general contact database UI, reminders, birthdays, tasks, or full interaction logging.
- Define a `relationship_signal` import interface for:
  - connected/not connected;
  - last interaction date;
  - interaction count by channel;
  - shared organizations;
  - user-supplied relationship notes.
- Retain deterministic warmth, but rename or decompose it. A single score hides distinct facts. Present a signal vector and an optional ranking:
  - direct connection;
  - recency;
  - frequency;
  - shared context;
  - evidence relevance;
  - relationship confidence.
- Build the **opportunity-specific relationship brief**, not the CRM.

### 1.4 Sales intelligence and outbound automation: Clay.com, Apollo, Sales Navigator, Waalaxy/Dripify class

**[Fact]** Clay.com provides multi-provider enrichment, company and people data, job-change and company signals, AI web research, CRM synchronization, HTTP APIs, webhooks, and native or integrated email campaigns. Its Launch plan begins around $167/month and Growth around $446/month under the current action/credit model.

**[Fact]** LinkedIn's current User Agreement prohibits scraping profiles and other service data, unauthorized browser plugins, automated access, automated contact downloading, automated messages, and other inauthentic engagement. LinkedIn also warns that invitation volume and suspected automation can cause restrictions.

**Assessment.** This category is almost the inverse of Wingman's manifesto. Its economics favor list size, enrichment coverage, sequence conversion, and automated activity. Competing would both dilute the product and create substantial ToS exposure.

Wingman's "never sends" invariant is strategically useful. It allows rich preparation without becoming an engagement bot. It also reduces the pressure to invent familiarity: an automated sequencer must fill every field; a human preparation system may correctly say, "There is not enough evidence for a credible approach."

**Decision.**

- No LinkedIn scraping, browser automation, invitations, messages, or sequencing.
- LinkedIn ingestion should remain limited to user-provided exports and user-approved public URLs where lawful.
- Do not build email discovery, phone enrichment, or bulk prospecting.
- Build exportable, cited talking points and let the user compose and send in the native channel.
- Make "insufficient basis for outreach" a first-class successful outcome.

### 1.5 Company and market intelligence: Crunchbase, PitchBook, Harmonic, Google Alerts

**Assessment.** Structured company facts—funding, headcount, investors, acquisitions, categories, and executive changes—are data-provider territory. Wingman cannot economically reproduce their coverage or entity resolution.

Wingman does have a useful derivative role: taking a deliberately bounded collection of company material and producing a dated, source-visible view of themes, claims, changes, and relevance to the user.

**Decision.**

- Integrate structured company facts when a licensed/user-provided source is available.
- Avoid positioning dossiers as comprehensive company intelligence.
- Preserve `[fact]` versus `[inference]`, dates, authorship, and source coverage.
- Every dossier should include a visible **coverage statement**: sources consulted, source dates, excluded/failed sources, and freshness.
- Company alignment should expose contributing documents and people rather than only an embedding score.

### 1.6 Page-change monitoring: Visualping, Distill, changedetection.io

**[Fact]** Visualping's free tier currently includes five pages and 150 checks per month; Personal begins at $10/month and Business at $100/month. API access, webhooks, and Zapier are available on every plan. Visualping exposes scoped API keys, REST operations for monitors and changes, and an MCP endpoint. It renders JavaScript-heavy pages and supports selectors, summaries, and importance filtering.

**[Fact]** Distill supports local and cloud monitors, visual element selection, macros, version history, push/email/SMS alerts, and webhooks carrying monitor metadata, current text, timestamps, and raw HTML.

**Assessment.** Wingman's "one GET, hash, link-set diff" is auditable but operationally thin. It will continue to fail on client-rendered pages, cookie flows, redirects, authentication, layout noise, and anti-bot systems. These are not edge cases; they are the core engineering burden of monitoring.

The invariant-bearing portion is not the fetch. It is what Wingman does with a dated change after receipt: preserve the source, label fact versus inference, connect the change to company themes and opportunity relevance, and avoid overclaiming.

**Decision: KEEP THIN, then integrate.**

- Retain the current fetcher as a zero-dependency baseline for static approved pages.
- Add a monitor-provider abstraction:
  - create/update/delete monitor;
  - poll or receive changes;
  - fetch before/after text or snapshots;
  - retain provider metadata and timestamps.
- Make Visualping the first managed adapter because it exposes API, webhook, and MCP surfaces even on its free tier.
- Consider changedetection.io as the self-hosted adapter.
- Do not build browser rendering, selector tooling, anti-bot adaptation, or a monitoring scheduler.

### 1.7 Feed reading and monitoring: Feedly, Inoreader, Readwise Reader

**[Fact]** Readwise Reader ingests articles, newsletters, RSS, PDFs, EPUBs, videos, and other document types; provides search, highlights, annotations, offline clients, exports, a public API, and webhooks. It costs $9.99/month annually or $12.99 monthly after a 30-day trial.

**[Fact]** Feedly's AI features summarize articles and highlight important sentences, and its established product includes feeds, filtering, boards, and team distribution.

**Assessment.** Wingman should not become a reader. Feed discovery, OPML, article extraction, read/unread state, mobile clients, triage, annotation, newsletters, and retention are mature capabilities.

Wingman needs only a bounded inbound interface for documents selected because they relate to watched people and companies.

**Decision.**

- Support OPML import/export and a generic "document arrived" interface.
- Add Readwise Reader import/API support before expanding Wingman's own feed UI.
- Keep source attribution and exact quotations in Wingman even when the upstream system supplies an AI summary.
- Never treat an upstream summary as evidence; fetch/store the underlying text or mark the item unavailable for claim generation.
- Keep feed discovery thin. Drop aspirations for a general-purpose reading queue.

### 1.8 PKM and corpus tools: Obsidian, Notion, Readwise, Rewind/Limitless

**Assessment.** Wingman's personal corpus overlaps PKM only at the storage/search layer. It should not compete on general note-taking, graph navigation, collaborative documents, or life logging.

Its differentiated corpus function is **career-scoped evidentiary recall**: finding passages from the user's own work that justify a claim, stance, example, or outreach bridge.

**Decision.**

- Build corpus connectors and indexers, not editors.
- Offer a folder/Markdown contract compatible with Obsidian.
- Support explicit provenance down to file, heading, and span.
- Add source-quality and temporal metadata.
- Unified search should privilege auditable retrieval over "chat with everything."

### 1.9 MCP and agentic integration

**[Fact]** The MCP ecosystem is already large and heterogeneous. A July 2026 study reports 2,297 validated MCP projects after filtering a larger GitHub candidate set.

**[Fact]** Recent research on MCP architecture reports that tool-selection accuracy can decline as the number of simultaneously exposed tools grows, with thresholds varying by model; the paper observed degradation below 90% in the low tens of tools for tested models.

**Assessment.** Wingman's 33-tool MCP surface is an integration advantage but also a usability risk. "CLI parity" is not automatically the right agent interface. Agents need task-shaped tools with stable schemas and bounded outputs, not a mirror of every internal operation.

**Decision.**

- Keep MCP as a primary interface.
- Reduce the default advertised surface to 8–12 task-level tools; expose specialist capability groups dynamically.
- Prefer composite tools such as:
  - `assess_opportunity`
  - `prepare_company_brief`
  - `prepare_person_brief`
  - `find_career_evidence`
  - `prepare_outreach_evidence`
  - `refresh_approved_research`
  - `search_wingman`
  - `export_packet`
- Keep lower-level tools available behind capability discovery.
- Add output schemas that distinguish `facts`, `inferences`, `unknowns`, `evidence`, and `prohibited_claims`.
- Do not make Wingman an MCP marketplace or generic agent host.

## 2. Maturity matrix

Scale: 1 = rudimentary, 3 = credible, 5 = mature category leader. Wingman scores refer to the current v0.2.x briefing.

| Capability | Wingman | Mature comparator | Market maturity | Interpretation |
|---|---:|---|---:|---|
| Canonical career profile | 3 | Teal/Huntr/Rezi | 4 | Wingman is less polished but more provenance-oriented. |
| Resume formatting/templates | 2 | Kickresume/Rezi | 5 | Integrate/export; do not compete. |
| ATS keyword optimization | 1–2 | Jobscan | 5 | Drop as a product bet. |
| Job tracking | 1 | Teal/Huntr | 5 | Deliberately external. |
| Requirement assessment | 4 | Jobscan/Teal | 3–4 | Wingman's verdict provenance is differentiated. |
| Contact management | 2 | folk/Monica/Dex | 4–5 | Import signals; do not build CRM breadth. |
| Person POV evidence | 4 | Sales/CRM tools | 2–3 | Defensible if quotes and coverage remain visible. |
| Warmth/relationship scoring | 2 | CRM and sales tools | 4 | Replace opaque score with imported signal vector. |
| Company structured facts | 2 | Crunchbase/PitchBook | 5 | Integrate licensed/user-provided facts. |
| Company theme synthesis | 4 | General AI research tools | 3 | Strong if evidence enforcement is real. |
| Page monitoring | 2 | Visualping/Distill | 5 | Adapter, not rebuild. |
| Feed ingestion | 2–3 | Feedly/Readwise/Inoreader | 5 | Keep thin and interoperable. |
| Corpus evidence retrieval | 3 | Obsidian/Readwise/Notion | 4 | Unified ranked search is the immediate priority. |
| Outreach generation | 3–4 | AI writers | 4 | Wingman differentiates through abstention and evidence, not prose fluency. |
| Sending/sequencing | 0 | Clay/Apollo/Waalaxy class | 5 | Remain out of scope. |
| PDF/report export | 3 | commodity renderers | 4 | Maintain as delivery, not a strategic pillar. |
| MCP integration | 4 | emerging ecosystem | 2–4 | Strategic surface; simplify exposed tools. |
| Interview preparation | 1 | Teal/Huntr/general AI | 3–4 | High-fit adjacent build using existing evidence. |
| Action prioritization | 1 | trackers/AI coaches | 3 | Build only as opportunity-quality ranking, not task management. |

## 3. What the market cannot easily copy

Competitors can add citations cosmetically. The harder-to-copy product behavior is a set of constraints that reduce apparent capability:

1. **Claim survival requires evidence.** The system must be willing to return nothing.
2. **Contradictions are retained.** New evidence does not simply overwrite the convenient profile.
3. **Unknown is a valid verdict.** It is not automatically converted into a plausible bridge.
4. **The canonical profile is not a generative draft.** It is an evidence graph.
5. **Every outward-facing suggestion is traceable to both sides:** what the other person/company said and what the user actually wrote or did.
6. **The product does not optimize activity volume.** It ranks opportunities and next actions by expected quality.
7. **Human action is structurally required, not merely recommended in UI copy.**

These constraints should become testable product invariants with regression fixtures, not only manifesto language.

## 4. Recommended product architecture

### 4.1 Separate collection, evidence, judgment, and delivery

```
Connectors / imports
        ↓
Immutable source documents + snapshots
        ↓
Evidence index and entity resolution
        ↓
Deterministic / constrained judgments
        ↓
Packets, briefs, search results, MCP resources
        ↓
Human-controlled external action
```

No connector-provided AI summary should enter the evidence layer as if it were primary source text.

### 4.2 Make the evidence object the central primitive

Suggested minimum schema:

```json
{
  "claim_id": "...",
  "claim_text": "...",
  "claim_type": "fact|inference|stance|career_item",
  "subject_id": "...",
  "source_document_id": "...",
  "quote": "...",
  "source_span": {"start": 0, "end": 0},
  "source_date": "YYYY-MM-DD",
  "retrieved_at": "...",
  "author": "...",
  "confidence": 0.0,
  "status": "supported|contradicted|stale|unknown",
  "supersedes": [],
  "derivation_rule": "..."
}
```

### 4.3 Search is not merely another feature

Unified search is the correct next build because every differentiated workflow depends on it. It should rank across document passages, career evidence, people, companies, POV claims, research snapshots, and opportunities while preserving type and provenance.

Recommended retrieval stack:

1. lexical retrieval (FTS/BM25);
2. vector retrieval;
3. reciprocal-rank fusion;
4. entity and recency boosts;
5. evidence-status filters;
6. bounded reranking;
7. grouped result presentation with exact supporting passages.

Do not return an undifferentiated semantic list. A query such as "healthcare operational redesign" should return labeled groups: the user's writing, career evidence, watched-person statements, company material, and open opportunities.

## 5. Version-shaping memo

### v0.3 — Retrieval and evidentiary integrity

**Bet 1: Unified evidence search.**  
Build hybrid ranked search across corpus, career profile, people, companies, POV claims, approved research, news, and opportunities. Every result includes type, source, date, exact span, and evidence status.

**Bet 2: Evidence graph hardening.**  
Create first-class support, contradiction, staleness, and unknown states. Add coverage statements and prohibited-claim output. Regression-test the manifesto invariants.

**Bet 3: MCP surface reduction.**  
Replace the default 33-tool presentation with task-shaped composite tools and dynamic capability groups.

**Explicitly not building:** job tracking, ATS scoring, resume-template breadth, outbound automation, a feed-reader UI, or browser-grade monitoring.

### v0.4 — Preparation that compounds the evidence base

**Bet 1: Interview preparation.**  
Generate an evidence-constrained interview packet:
- likely requirement themes;
- supported examples;
- gaps and honest framing;
- company/person quotations;
- questions derived from unresolved evidence;
- no invented STAR stories.

**Bet 2: Opportunity-quality ranking.**  
Rank a deliberately small set of opportunities using transparent components:
- requirement fit;
- evidence strength;
- strategic alignment;
- relationship access;
- information freshness;
- uncertainty/effort.
Show the arithmetic and allow user-adjustable weights.

**Bet 3: Connector contracts.**  
Add stable adapters/imports for:
- one job tracker;
- one personal CRM/contact source;
- Readwise Reader or OPML;
- Visualping and changedetection.io.

**Explicitly not building:** a generic CRM, calendars/reminders, application autofill, a mobile reader, or a proprietary company database.

### v0.5 — Decision and action briefs

**Bet 1: Next-best-action briefs.**  
For each high-quality opportunity, recommend one bounded human action—research, seek an introduction, reconnect, apply, or decline—with evidence and uncertainty. Do not turn this into a task manager.

**Bet 2: Relationship-to-opportunity reasoning.**  
Combine imported interaction signals with cited POV/company relevance. Explain why a person is relevant; do not imply closeness from weak signals.

**Bet 3: Longitudinal research diffs.**  
Turn imported monitor events into dated company/person change narratives with source-visible before/after evidence and explicit inference labels.

**Explicitly not building:** automated outreach, sequencing, social scraping, broad enrichment, or multi-tenant sales intelligence.

## 6. Integration priorities

| Priority | Integration | Why |
|---:|---|---|
| 1 | Visualping or changedetection.io | Removes a known immature infrastructure burden and supports reliable dated research diffs. |
| 2 | Readwise Reader / OPML | Avoids rebuilding feed and document triage while expanding approved source intake. |
| 3 | Teal or Huntr CSV/workflow export | Lets Wingman remain opportunity intelligence while users retain a mature tracker. |
| 4 | Generic contacts/interactions import | Improves relationship signals without building a CRM. |
| 5 | Structured company-data import | Useful when the user already has lawful access; not required for the core product. |

## 7. ToS and operating boundaries

1. Treat user-provided LinkedIn exports as imports, not permission to automate LinkedIn.
2. Do not ship browser extensions that scrape or modify LinkedIn.
3. Do not automate connection requests, messages, comments, or engagement.
4. Do not depend on unofficial LinkedIn profile APIs or scraped data brokers.
5. Preserve source ownership and attribution for public writing.
6. For managed monitoring, respect robots/access controls and retain provider provenance.
7. Make connector-derived data revocable and re-importable.

## 8. Final product statement

Wingman is not an applicant bot and not a job-search CRM.

It is an **evidence operating system for selective career moves**:

- it knows what the user can substantiate;
- identifies unusually strong opportunities;
- distinguishes fit, gap, and uncertainty;
- finds the people and company ideas that genuinely matter;
- prepares the user to act in their own voice;
- and stops before external action.

That is sufficiently distinct from the field. The next versions should deepen that chain rather than widen the surrounding workflow.

## Sources consulted

Accessed 2026-07-18 unless otherwise noted.

- Teal: product, pricing, and Teal vs. Teal+ documentation
- Huntr: product pages and May 2026 plan/pricing documentation
- Simplify: Simplify+ features and pricing documentation
- Jobscan: product and January 2026 scoring-method documentation
- Rezi: product and pricing documentation
- Kickresume: product and pricing pages
- Monica: product, documentation, pricing, and API/privacy pages
- folk: pricing and developer documentation
- Clay.com: pricing and feature documentation
- LinkedIn: User Agreement effective 2025-11-03, prohibited software guidance, API terms, crawling terms, and invitation restrictions
- Visualping: pricing, API documentation, monitoring/MCP documentation
- Distill: pricing and webhook documentation
- Readwise Reader: product, pricing, API, and documentation
- Feedly: AI summarization documentation
- Rodrigues & Vas, "MCP Server Architecture Patterns for LLM-Integrated Applications," 2026
- Toeppe, Barrak & Ksontini, "A Large-Scale Dataset of MCP Implementations on GitHub," 2026

## Research limitations

This was a documentation-led market review, not a paid hands-on trial of every named product. Vendor claims about efficacy were not treated as independently proven. Pricing and feature gates change frequently. PitchBook, Harmonic, Apollo, Dex, Careerflow, Inoreader, and some LinkedIn-assistant products were assessed primarily at category level rather than through a complete feature-by-feature trial. Before drafting an implementation RFC for a specific integration, verify its current authentication, rate limits, export schema, contractual restrictions, and availability on the intended subscription tier.
