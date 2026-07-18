# Comparator Rubric — Raw Data (companion to competitive-landscape-2026-07-18-reality-check.md)

**Access date for all claims below: 2026-07-18**, unless a source's own publish date is noted. House style: **[fact]** = vendor docs, observed product behavior, or dated third‑party technical documentation; **[inference]** = analyst's read/extrapolation. No undated claims are included below; where a source did not carry a clear access-relevant date, the fetch date (2026-07-18) is used as the observation date.

This file is the per-tool backing data for the comparison matrix in `competitive-landscape-2026-07-18-reality-check.md`. Rubric dimensions, per the briefing: (1) Evidence discipline, (2) Autonomy posture, (3) Feature overlap A–F with 1–5 maturity vs Wingman, (4) Interfaces (API/MCP/export), (5) Price & model, (6) ToS friction.

---

## Job-search CRMs (overlap: B, E, (A))

### Teal (Teal HQ)
1. **Evidence discipline:** No claim-provenance model — it is a manual/AI-assisted resume and tracker tool, not a claims-verification system; not applicable in Wingman's sense **[inference]**.
2. **Autonomy posture:** No auto-apply and no autofill product as of the reviewed date; "every application is submitted manually" **[fact]** ([Teal HQ Review, resumly.ai, 2026](https://www.resumly.ai/answers/teal-review)). Teal separately ships an **Autofill** Chrome-extension beta layered on its resume builder, still requiring the user to review and submit **[fact]** ([Teal HQ Autofill product page](https://www.tealhq.com/tools/autofill-job-applications)).
3. **Feature overlap:** B (job tracker, keyword match scoring) — maturity 4/5, mature multi-year product with Kanban tracker and resume-JD keyword matching, but matching is keyword/heuristic, not verdict-with-verbatim-quote reasoning **[fact]**+**[inference]** ([Teal pricing](https://www.tealhq.com/pricing)). E (email templates, AI cover letters) — maturity 2/5 vs Wingman's POV-cited briefs; Teal's outreach is templated, not corpus-quote-cited **[inference]**. A (resume builder/import) — maturity 3/5; imports resume/LinkedIn but no personal-corpus FTS5 or multi-format ingestion (LaTeX, Google Docs) **[inference]**.
4. **Interfaces:** No public API or MCP server found as of 2026-07-18 **[fact — absence confirmed by search, not just unchecked]**. Chrome extension only.
5. **Price & model:** Free tier with unlimited job tracking, base resume builder, 10 templates; Teal+ paid ~$9–$29/month gates AI resume generation, unlimited tailored resumes, and Resume Curation auto-select **[fact]** ([Teal HQ Review, loopcv.pro, 2026](https://blog.loopcv.pro/teal-hq-review/); [Teal pricing](https://www.tealhq.com/pricing)).
6. **ToS friction:** Low — no LinkedIn scraping/automation; LinkedIn import is user-initiated export/paste **[inference]**.

### Huntr
1. **Evidence discipline:** N/A — deterministic keyword/qualification matching, not claims-with-quotes **[inference]**.
2. **Autonomy posture:** "Application Autofills" are a paid feature but require the user to open and submit each application; no auto-apply found **[fact]** ([Huntr pricing](https://huntr.co/pricing)).
3. **Feature overlap:** B — maturity 4/5; full keyword + "responsibility and qualification" matching, resume scoring, unlimited job tracking on paid tier **[fact]** ([Huntr pricing](https://huntr.co/pricing)). More automated than Wingman's verdict system but not evidence-quoted — it optimizes match score, not quote-backed met/partial/gap/unknown reasoning **[inference]**. A — resume builder with base/tailored resume versions, maturity 3/5.
4. **Interfaces:** No public API/MCP found as of 2026-07-18 **[fact]**.
5. **Price & model:** Free tier (2 tailored resumes, 100 tracked jobs, basic scoring); Pro $40/mo (or ~$26.66–$30/mo on longer terms) unlocks unlimited AI resume generation, unlimited tailoring, advanced job matching **[fact]** ([Huntr pricing](https://huntr.co/pricing)).
6. **ToS friction:** Low; Chrome "Job Clipper" saves listings, no LinkedIn automation found **[inference]**.

### Simplify (Simplify.jobs / Copilot)
1. **Evidence discipline:** N/A — autofill is field-mapping, not claim generation. AI resume tailoring on Simplify+ is generative without a quote-verification gate **[inference]**.
2. **Autonomy posture:** Explicitly **not** auto-apply. "You still click Submit on every application… There is no autonomous or mass-apply mode — the human stays in the loop on every single submission" **[fact]** ([Simplify Jobs Review, resumly.ai, 2026-06-13](https://www.resumly.ai/answers/simplify-jobs-review)). 200,000,000+ applications "submitted" via the tool, but submission remains a manual click **[fact]** ([Simplify.jobs](https://simplify.jobs/)).
3. **Feature overlap:** B (autofill, ATS score, keyword gaps) — maturity 4/5 on autofill breadth (100+ ATSs, 20,000+ career pages) but 0/5 on evidence-quoted verdicts — it's field-fill, not requirement-by-requirement assessment **[fact]**+**[inference]** ([Simplify Copilot](https://simplify.jobs/copilot)). E (networking/outreach tool identifies hiring managers, drafts emails) on Simplify+ — maturity 2/5, generative drafting without corpus-quote citation **[inference]**.
4. **Interfaces:** Browser extension (Chrome/Firefox) only; no public API/MCP found **[fact]**.
5. **Price & model:** Free tier: autofill, job-matching feed, tracker, referral tools, unlimited autofill volume. Simplify+ $39.99/month gates AI resume tailoring, AI cover letters, the outreach tool, and stronger long-form question handling **[fact]** ([Simplify Jobs Review, aceapp.ai, 2026](https://aceapp.ai/blog/simplify-review); [Simplify Jobs Review, remotejobassistant.com, 2026](https://www.remotejobassistant.com/blog/simplify-jobs-review)).
6. **ToS friction:** Low for job-board autofill (first-party form filling, user-initiated); privacy policy reportedly unchanged since June 2021 despite feature growth — a data-handling transparency concern, not a LinkedIn ToS one **[fact]** ([Simplify Jobs Review, remotejobassistant.com, 2026](https://www.remotejobassistant.com/blog/simplify-jobs-review)).

### Careerflow (Careerflow.ai)
1. **Evidence discipline:** N/A — LinkedIn/resume optimization is keyword-score based, not quote-verified claims **[inference]**.
2. **Autonomy posture:** Product docs describe autofill, tracking, and an "AI Email Writer" for outreach, but do not state that Careerflow submits applications or sends messages without user action **[fact]** ([Careerflow: How It Works](https://www.careerflow.ai/how-it-works)).
3. **Feature overlap:** B (ATS score checker, job tracker Kanban, autofill) — maturity 3/5. C-adjacent (Networking CRM: hiring-manager finder, contact tracker, follow-up reminders) — maturity 2/5 vs Wingman's watchlist+POV+warmth system; Careerflow's CRM is a tracker with reminders, not a deterministic warmth score or POV-card system **[fact]**+**[inference]** ([Careerflow Premium](https://www.careerflow.ai/premium)). E (AI email writer, cover letters) — maturity 2/5, generative without corpus-quote citation.
4. **Interfaces:** Chrome extension; no public API/MCP found as of 2026-07-18 **[fact]**.
5. **Price & model:** Free: 1 resume, basic ATS score, up to 10 tracked jobs, limited Chrome extension. Premium/Premium Plus (price not published on fetched pages) gate unlimited resumes, One-Click Optimizer, AI Cover Letter Generator, Email Writer, Elevator Pitch, full extension, AI Assistant; Mock Interview is Premium‑Plus‑only **[fact]** ([Careerflow: Free vs. Premium](https://help.careerflow.ai/en/articles/10605570-free-vs-premium-access)). Third-party reviews cite ~$23.99/month for Premium **[fact]** ([Careerflow Review, remotejobassistant.com, 2026](https://www.remotejobassistant.com/blog/careerflow-review)).
6. **ToS friction:** Moderate — LinkedIn Optimizer and profile tools operate via browser extension reading/writing the user's own LinkedIn profile; not third-party scraping in the prohibited sense, but LinkedIn's Prohibited Software policy is broad enough to cover any automated LinkedIn interaction **[inference]**.

---

## Resume/ATS optimizers (overlap: A, B)

### Jobscan
1. **Evidence discipline:** N/A — keyword/match-rate scoring engine, not a claims system **[inference]**.
2. **Autonomy posture:** No autonomous application or sending behavior; it is an analysis tool **[inference]**.
3. **Feature overlap:** B — maturity 4/5 for resume-to-JD keyword/match-rate scoring specifically (the single feature it's known for), but 1/5 on Wingman's B definition of verbatim-quote requirement extraction into met/partial/gap/unknown verdicts — Jobscan reports a percentage match score and keyword gaps, not a structured, quote-cited requirement ledger **[fact]**+**[inference]** ([Is Jobscan Worth It, atsresumeai.com, 2026-06-29](https://www.atsresumeai.com/compare/is-jobscan-worth-it)).
4. **Interfaces:** Consumer web app; no public developer API or MCP found as of 2026-07-18 **[fact]**.
5. **Price & model:** Free: 5 scans/month, basic match rate. Premium $49.95/mo (or $89.95 per quarter, ≈$29.98/mo) unlocks unlimited scans, one-click AI optimization, cover letter generator, LinkedIn optimizer, job tracker **[fact]** ([Is Jobscan Worth It, atsresumeai.com, 2026-06-29](https://www.atsresumeai.com/compare/is-jobscan-worth-it)).
6. **ToS friction:** Low; LinkedIn "optimizer" feature is advisory (profile-copy suggestions), not automation **[inference]**.

### Rezi
1. **Evidence discipline:** N/A — AI resume writer/editor is generative; no verbatim-quote gate on generated bullet content **[inference]**.
2. **Autonomy posture:** Chrome extension does autofill + job tracking + LinkedIn import + "personalized interview prep," but framed as assistive, not autonomous submission **[fact]** ([Rezi Chrome Extension docs](https://www.rezi.ai/rezi-docs/rezi-chrome-extension)).
3. **Feature overlap:** A/B — maturity 3/5; strong resume generation and ATS-style scoring ("Rezi Score"), but no evidence-validated extraction pipeline comparable to Wingman's career.md, and no personal corpus/FTS5 concept. Notably ships a **remote MCP server** (see Area F).
4. **Interfaces:** Rezi ships `rezi-mcp`, a hosted MCP server at `https://api.rezi.ai/mcp`, streamable-HTTP transport, OAuth-style session login (browser sign-in on first tool use, session token refreshed automatically, not persisted to disk), gated behind an active Rezi subscription. Five tools: `list_resumes`, `read_resume`, `write_resume`, `search_jobs`, `get_job_details` **[fact]** ([rezi-mcp — MCP.Directory](https://mcp.directory/servers/rezi-mcp)). No auto-apply or auto-send tool is exposed **[fact]**.
5. **Price & model:** Free: 1 resume, 1 AI interview, 3 PDF downloads. Pro $29/month: unlimited resumes/interviews/downloads, Rezi Score, keyword targeting. Enterprise $99/mo per 200 users adds SSO, webhooks, domain restriction, team roles **[fact]** ([Rezi pricing](https://www.rezi.ai/pricing)).
6. **ToS friction:** Low-moderate; LinkedIn import is user-authorized paste/connect, not scraping **[inference]**.

### Kickresume
1. **Evidence discipline / Autonomy / overlap:** Templated AI resume/cover-letter builder; overlap area A only, maturity ~2/5 vs Wingman (no evidence-validated extraction, no corpus) **[inference]** — pricing page fetched but yielded only cookie-consent boilerplate content, so feature claims here rely on secondary sources and are marked accordingly; no primary vendor feature/pricing text was retrievable on 2026-07-18 **[fact: fetch limitation]**.
4. **Interfaces:** No API/MCP found.
5. **Price & model:** Third-party sources report tiered monthly/lifetime plans with a 20%-off promotion running as of the access date; exact current price not confirmed from a primary source on this pass **[inference — unconfirmed]**.
6. **ToS friction:** Low.

---

## Personal CRM / relationship management (overlap: C — warmth, watchlist)

### Clay.earth (rebranded **Mesh**)
1. **Evidence discipline:** N/A — relationship data is captured from connected accounts (email/calendar/social), not model-inferred claims; Mesh surfaces "career moves, news mentions, and life updates" automatically **[fact]** ([clay.earth](https://clay.earth/)).
2. **Autonomy posture:** Captures and surfaces information; does not send messages on the user's behalf per its own marketing copy — positions itself as a memory/context layer, not an outreach agent **[fact]**+**[inference]** ([clay.earth](https://clay.earth/)).
3. **Feature overlap:** C — maturity 3/5 vs Wingman's warmth score + POV cards + watchlist. Mesh auto-aggregates contacts and news/job-change signals (comparable to Wingman's news snapshots + some warmth signals) but has no deterministic, disclosed warmth-scoring formula, no POV/stance extraction with quotes, and no recommendation-graph discovery **[fact]**+**[inference]** ([clay.earth](https://clay.earth/)).
4. **Interfaces:** Consumer app with integrations (email, calendar, social); no public developer API found as of 2026-07-18 **[fact]**.
5. **Price & model:** Free "Personal" plan up to 1,000 contacts with core import/reminders; Pro $10–$20/month (discount vs list price) for unlimited contacts and CSV import; Team $40–$49/seat/month; Enterprise custom with SSO/SOC 2 **[fact]** ([Clay Earth pricing, TrustRadius, 2025](https://www.trustradius.com/products/clay-earth/pricing); [Clay Earth review, toolsforhumans.ai](https://www.toolsforhumans.ai/ai-tools/clay)).
6. **ToS friction:** Low-moderate; imports from LinkedIn are described as user-connected imports, not scraping **[inference]**.

### Dex
1. **Evidence discipline / Autonomy:** Manual relationship notes + reminders; no model-claim layer to verify **[inference]**.
2. **Feature overlap:** C — maturity 2/5; reminders to reach out, contact notes — no warmth score, no POV cards, no feeds/news layer comparable to Wingman **[inference]**.
4. **Interfaces:** No public API/MCP found as of 2026-07-18 **[fact]**.
5. **Price & model:** 7-day free trial; paid tiers reported at roughly $12/month and $20/month by third-party reviews; Dex's own pricing page loaded with minimal text on this fetch (`$12/month… $20/month`) **[fact — partial, from vendor page]** ([getdex.com/pricing](https://getdex.com/pricing/)).
6. **ToS friction:** Low.

### folk
1. **Evidence discipline / Autonomy:** Standard CRM; no claims/evidence layer — N/A **[inference]**.
2. **Feature overlap:** C-adjacent as a generic relationship CRM rather than a warmth/POV system — maturity 2/5 vs Wingman's purpose-built warmth+POV+feeds stack **[inference]**.
4. **Interfaces:** Has a documented **developer API and webhooks** (Folk Developer Portal: create/list webhooks, events & payloads) plus Zapier integration — the most integration-ready of the personal-CRM set **[fact]** ([Folk Developer Portal](https://developer.folk.app/); [folk Webhooks by Zapier](https://zapier.com/apps/folk/integrations/webhook)).
5. **Price & model:** Standard $24/member/month ($288/yr), Premium $48/member/month ($576/yr), Enterprise $80/member/month ($960/yr), billed monthly; annual billing shown as discounted per-month equivalents on the pricing page; 2-week free trial, no card required **[fact]** ([folk pricing](https://www.folk.app/pricing)).
6. **ToS friction:** Low; general-purpose CRM, no LinkedIn-specific automation claimed.

### Monica (OSS)
1. **Evidence discipline:** N/A — pure user-entered data store, no AI claims layer **[fact]** ([monicahq.com](https://www.monicahq.com/)).
2. **Autonomy posture:** Never sends anything; explicitly "not a social network," a private data store the user owns **[fact]** ([monicahq.com](https://www.monicahq.com/)).
3. **Feature overlap:** C — maturity 1/5 vs Wingman's C; Monica is a manual personal-relationship logbook (birthdays, notes, reminders) with zero automated intelligence — no warmth score, no feeds, no POV cards **[fact]**+**[inference]**.
4. **Interfaces:** Open source (GitHub, `monicahq/monica`), self-hostable; has a documented API (typical of the OSS CRM class) — the natural INTEGRATE-OR-DROP anchor for "I don't want to build a generic contacts DB" **[fact]** ([GitHub: monicahq/monica](https://github.com/monicahq/monica)).
5. **Price & model:** Free/open-source, self-hosted; no vendor lock-in **[fact]**.
6. **ToS friction:** None — self-hosted, no third-party platform dependency.

---

## Sales intelligence / prospecting (overlap: C, D, E)

### Clay.com
1. **Evidence discipline:** Enrichment is sourced from 150+ third-party data partners via "waterfalls," plus a generative agent ("Claygent"); no verbatim-quote requirement gate on generated fields — enrichment can include AI-written summary cells that are not source-quoted by default **[fact]**+**[inference]** ([Clay pricing](https://www.clay.com/pricing)).
2. **Autonomy posture:** Growth-tier+ includes "webhook automation" and CRM auto-sync that can trigger outbound sequences in connected tools (e.g., email sender platforms) — this is oriented toward autonomous outbound execution once wired up, the closest thing in this comparator set to "sends on the user's behalf," albeit through the user's own connected tools rather than natively **[fact]** ([Clay pricing](https://www.clay.com/pricing)).
3. **Feature overlap:** D (company/person enrichment, signals) — maturity 4/5 on breadth (150+ partner sources, job-change/signal tracking) but the enrichment is assembled from paid third-party lookups rather than Wingman's deterministic embeddings-over-owned-corpus approach — a categorically different sourcing model **[fact]**+**[inference]**. E (Clay Sequencer for email) — maturity 3/5, templated/AI-personalized outbound, not corpus-quote-cited and explicitly built to send, unlike Wingman **[fact]**.
4. **Interfaces:** REST/HTTP API and webhooks available from the **Growth tier ($495/mo)** up; full "Clay API access" reserved for Enterprise. Data delivered as enrichment "credits" per lookup (two meters: Data Credits for enrichment, Actions for workflow/platform operations) **[fact]** ([Clay pricing 2026 overhaul](https://databar.ai/blog/article/clay-pricing-2026); [Clay pricing](https://www.clay.com/pricing)).
5. **Price & model (post–March 2026 overhaul):** Free ($0, 100 Data Credits + 500 Actions/mo, ≤200 rows/table); Launch ($185/mo, 2,500 Data Credits + 15,000 Actions, ≤50,000 rows/table, no HTTP API); Growth ($495/mo, 6,000 Data Credits + 40,000 Actions, HTTP API + webhooks + CRM auto-sync); Enterprise (custom, 100,000+ credits, full API, SSO/RBAC) **[fact]** ([Clay pricing overhaul, databar.ai, 2026-07-08](https://databar.ai/blog/article/clay-pricing-2026)).
6. **ToS friction:** Real but indirect — Clay itself is a data aggregator, not a LinkedIn automation tool; friction depends on which of its 150+ partners are used for LinkedIn-adjacent enrichment **[inference]**.

### Apollo.io
1. **Evidence discipline:** N/A — B2B contact/company database with AI-assisted lead scoring; not a verbatim-quote claims system **[inference]**.
2. **Autonomy posture:** Explicitly built to send — "Sequences," "Deliverability Suite & Email Warmup," and automated multi-touch outreach are core, paid features; this is the opposite end of the spectrum from Wingman's never-sends invariant **[fact]** ([Apollo.io pricing](https://www.apollo.io/pricing)).
3. **Feature overlap:** C/D (contact + company data, intent signals) — maturity 4/5 on breadth (waterfall enrichment, 6–12 intent topics depending on tier); E (sequences, dialer) — maturity 5/5 on send-capable outreach automation, which is precisely the invariant Wingman rejects **[fact]** ([Apollo.io pricing](https://www.apollo.io/pricing)).
4. **Interfaces:** REST API, consumption/credit-based billing tied to plan tier; fixed-window rate limiting (example given: 200 requests/60-second window for a plan at that tier); exact per-tier numeric limits are surfaced only via an account's own "usage stats" endpoint, not published generally **[fact]** ([Apollo Rate Limits docs](https://docs.apollo.io/reference/rate-limits)). Chrome/Gmail/Salesforce extensions; no MCP server found from Apollo itself, though third-party community MCP wrappers exist **[fact]**.
5. **Price & model:** Free (AI Assistant capped at 5 chats, 2 sequences, basic filters); Basic, Professional, Organization tiers add unlimited sequences, waterfall enrichment, more intent topics, CRM integrations, dialer credits; add-ons like "Inbound" ($119/team/month) sold separately **[fact]** ([Apollo.io pricing](https://www.apollo.io/pricing)).
6. **ToS friction:** Moderate-high for LinkedIn-adjacent use — Apollo's browser extension and enrichment touch LinkedIn-sourced data; LinkedIn's Prohibited Software policy targets exactly this class of tool broadly, though Apollo is an established, long-running vendor rather than a fly-by-night scraper **[inference]**.

### LinkedIn Sales Navigator
1. **Evidence discipline:** First-party LinkedIn data — the most "grounded" of the sales-intel comparators since it is the platform of record, not a scrape; no separate claims-verification layer needed for its own data **[inference]**.
2. **Autonomy posture:** Generates "personalized outreach with AI" and supports InMail sending (50/month across tiers) — this is LinkedIn's own first-party send capability, and by definition inside LinkedIn's rules, unlike third-party automation **[fact]** ([LinkedIn Sales Navigator: Compare Plans](https://business.linkedin.com/sell/sales-navigator/compare-plans)).
3. **Feature overlap:** C/D — maturity 5/5 for first-party LinkedIn relationship/signal data (job changes, real-time buyer signals, warm-intro paths through the team's trusted connections) — categorically stronger raw data access than Wingman can have without LinkedIn's own data, but zero evidence/quote discipline and explicitly a sales (not career-seeker) tool **[fact]** ([LinkedIn Sales Navigator: Compare Plans](https://business.linkedin.com/sell/sales-navigator/compare-plans)).
4. **Interfaces:** No public developer API for individual/team tiers (enterprise CRM sync exists via managed integrations to Salesforce/HubSpot); no MCP server from LinkedIn itself **[fact]**.
5. **Price & model:** Three tiers ("Individual sellers," "Sales teams," "Sales teams using integrated CRM"); exact prices not shown on the fetched compare page, but third-party pricing guides report the range as roughly $99–$1,600+/year depending on tier **[inference — third-party, not primary]** ([LinkedIn Sales Navigator Pricing, cleanlist.ai, 2026-05-08](https://www.cleanlist.ai/blog/2026-05-08-linkedin-sales-navigator-pricing-guide)).
6. **ToS friction:** N/A for using it as licensed; but any *third-party tool* that scrapes or automates against Sales Navigator or LinkedIn generally is squarely prohibited (see LinkedIn ToS section below).

### Humantic AI / Crystal Knows
1. **Evidence discipline:** Both infer DISC/Big-Five personality profiles from public data and user-submitted data. Humantic's own ToS is explicit that it makes **no warranty of currency, accuracy, or completeness** for "Humantic Data" or "Humantic Insights," has **no obligation to validate, update, or refresh** its data, and states insights are **"only suggestive in nature"** with usage risk borne solely by the customer **[fact]** ([Humantic AI Terms of Service](https://humantic.ai/tos)). This is the sharpest documented contrast with Wingman's evidence-before-assertion invariant: Humantic explicitly disclaims the very guarantee Wingman treats as a non-negotiable.
2. **Autonomy posture:** Personality insights feed human sales reps' own messaging; not itself an autonomous sender, but exists to make third-party outreach (which is sent) more effective **[inference]**.
3. **Feature overlap:** C (person intelligence) — maturity 2/5 vs Wingman's POV cards: Humantic/Crystal produce a personality *type* prediction (DISC/Big Five) from aggregated public data, not a quote-cited stance/values/attitude card; fundamentally probabilistic-inference rather than evidence-extraction **[fact]**+**[inference]**.
4. **Interfaces:** Humantic: native integrations with Gmail, Google Calendar, Outlook, Salesforce, Outreach; Chrome extension. Humantic's ToS explicitly **prohibits** scraping/data-extraction to source "Customer Data," and prohibits using Humantic Insights/Data "to analyse or train... machine learning... models" — a hard blocker on the "integrate" path for any pipeline that would touch Humantic's raw personality data with an LLM **[fact]** ([Humantic AI Terms of Service](https://humantic.ai/tos)). Crystal: Chrome extension operating on LinkedIn profile pages **[fact]** ([Crystal Chrome Extension Help Center](https://docs.crystalknows.com/crystal-chrome-extension)).
5. **Price & model:** Humantic: pricing gated by seat/usage, page confirms a paid "Personality AI Assistant" tier structure but without published numeric tiers on the fetched page **[fact — partial]** ([Humantic AI pricing](https://humantic.ai/start/pricing)). Crystal: Free profile (6 assessments) forever; Premium $99/yr; Sales from $59/mo, Hiring from $49/mo, Coaching from $99/mo, each with Basic/Pro/Max sub-tiers up to $199/mo **[fact]** ([Crystal Knows pricing](https://www.crystalknows.com/pricing)).
6. **ToS friction:** High. Both tools' core value proposition is inferring personality from LinkedIn-adjacent public profile data; LinkedIn's own Prohibited Software policy (see below) targets scraping/automated extraction broadly, and Crystal's own help docs describe browser-extension use directly on LinkedIn pages **[fact]** ([Crystal Chrome Extension on LinkedIn](https://docs.crystalknows.com/how-do-i-use-the-crystal-chrome-extension); [LinkedIn Prohibited Software policy](https://www.linkedin.com/help/linkedin/answer/a1341387)).

---

## Company signals / market intel (overlap: D)

### Harmonic
1. **Evidence discipline:** Sources from "legal filings and dozens of public data sources," refreshed regularly; presented as a data platform, not a claims-with-citations system in Wingman's [fact]/[inference] sense **[fact]** ([Harmonic pricing](https://harmonic.ai/pricing)).
2. **Autonomy posture:** No sending; a research/sourcing platform for investors **[inference]**.
3. **Feature overlap:** D — maturity 4/5 on raw coverage/freshness (35M+ companies, 195M+ people, network mapping, "AI diligence reports") but built for VC/PE deal sourcing, not job-seeker company dossiers; no [fact]/[inference]-labeled dossier format **[fact]**+**[inference]** ([Harmonic pricing](https://harmonic.ai/pricing)).
4. **Interfaces:** Developer-friendly API (enrich any company/person, discover new companies/people), Chrome Extension, bulk data delivery via S3/BigQuery/Snowflake refreshed weekly, no-code CRM sync **[fact]** ([Harmonic pricing](https://harmonic.ai/pricing)). No MCP server found.
5. **Price & model:** No public numeric pricing; Enterprise console, API access, and Bulk Data are three distinct product tiers, each requiring a sales conversation ("Chat with the team") **[fact]** ([Harmonic pricing](https://harmonic.ai/pricing)).
6. **ToS friction:** Low for Wingman's purposes (aimed at institutional investors, not career-seekers) but a plausible acquisition-cost blocker regardless **[inference]**.

### Crunchbase
1. **Evidence discipline:** Structured, sourced company/funding database; the platform of record for company facts, not an inference/claims engine **[fact]**.
2. **Autonomy posture:** No sending; a read/data platform, though Pro includes "automated prospecting" workflows built on its data **[fact]** ([How much does Crunchbase cost?](https://support.crunchbase.com/hc/en-us/articles/360019482733-How-much-does-Crunchbase-cost)).
3. **Feature overlap:** D — maturity 4/5 for company facts (funding, acquisitions, people) at consumer-accessible pricing, but no page-diff research and no author-attributed theme synthesis; closer to a structured facts database that Wingman's dossiers could *cite* than a competing dossier product **[fact]**+**[inference]**.
4. **Interfaces:** REST API v4, token-based auth via `user_key` (URL param) or `X-cb-user-key` (header); **rate limit 200 calls/minute** (basic/ODM tier limited further, 25 calls/minute); full API requires Enterprise or "Applications" license, Basic-tier customers get a separate, more limited Basic API **[fact]** ([Using the API — Crunchbase](https://data.crunchbase.com/docs/using-the-api); [Crunchbase API FAQ](https://support.crunchbase.com/hc/en-us/articles/32319290128019-Crunchbase-API-FAQ); [Crunchbase Basic API FAQ](https://support.crunchbase.com/hc/en-us/articles/8327794377235-Crunchbase-Basic-API-FAQ)). This is a strong, well-documented **INTEGRATE** candidate for RFC-018 — clean auth model, published rate limit, JSON entity shapes.
5. **Price & model:** Crunchbase Pro $49/month billed annually (10 free contacts/month, 7-day trial); Enterprise and Data Licensing are custom-priced; full API access requires Enterprise/Applications license **[fact]** ([How much does Crunchbase cost?](https://support.crunchbase.com/hc/en-us/articles/360019482733-How-much-does-Crunchbase-cost)).
6. **ToS friction:** Low; Crunchbase is a licensed data provider, not a LinkedIn-adjacent scraper.

### PitchBook
1. **Evidence discipline:** Proprietary, analyst-curated private-market data — treated as authoritative by the market but is vendor-proprietary, not independently source-quoted the way Wingman's [fact]/[inference] labels require **[fact]**+**[inference]** ([pitchbook.com](https://www.pitchbook.com/)).
2. **Autonomy posture:** No sending; a research/data terminal **[inference]**.
3. **Feature overlap:** D — maturity 4/5 for private-market coverage depth (companies, deals, investors, funds) but priced far above what a career-intelligence tool would justify, and is not company-*culture*/POV-oriented the way Wingman's themes/dossiers are **[fact]**+**[inference]**.
4. **Interfaces:** REST API, auth via `Authorization: PB-Token {API_KEY}` header; **credit-based billing per call** (each endpoint has a defined credit cost, purchased from an account manager — no self-serve signup); typical account cost reported by third parties at **$12,000–$70,000/user/year** **[fact]** ([PitchBook API docs, Postman](https://documenter.getpostman.com/view/5190535/TzCV1iRc); [PitchBook Pricing 2026, costbench.com](https://costbench.com/software/financial-data-terminals/pitchbook/)).
5. **Price & model:** No self-serve tier; enterprise sales-only, custom pricing, credit-metered API on top of the base license **[fact]**.
6. **ToS friction:** Low (licensed data), but cost alone rules it out as an integration for an individual-user tool like Wingman **[inference]**.

### Google Alerts
1. **Evidence discipline:** Raw search-result surfacing, no synthesis or claims — the most "just facts" of any comparator; closest philosophically to Wingman's evidence-first stance because it makes no inferential claim at all, just delivers matched pages **[fact]**.
2. **Autonomy posture:** Delivers alerts only; no send capability **[fact]**.
3. **Feature overlap:** D/C (news monitoring) — maturity 2/5 vs Wingman's Google-News-RSS-based person snapshots with relevance filtering: Google Alerts has no relevance-scoring beyond keyword match and no per-entity dossier structure **[inference]**.
4. **Interfaces:** No API; RSS feed output per alert is the closest thing to an integration surface **[fact]** ([Google Alerts](https://www.google.com/alerts)).
5. **Price & model:** Free, no tiers **[fact]**.
6. **ToS friction:** None.

---

## Page-change monitoring (overlap: D — RFC-015 research)

### Visualping
1. **Evidence discipline:** N/A — mechanical diff tool; "Premium AI" adds custom prompts to filter whether a change is "important," which is itself a model judgment layered on top of a deterministic diff **[fact]** ([Visualping pricing](https://visualping.io/pricing)).
2. **Autonomy posture:** No sending; monitoring + alerting only **[fact]**.
3. **Feature overlap:** D (RFC-015 approved-source research) — maturity 4/5 as a *general* page-monitoring product (mature, many years in market, Zapier/API integrations shipped Q1 2026) but not job/company-context-aware — it has no concept of "N new links since date" framed as a company-research finding; that framing is Wingman-specific **[fact]**+**[inference]** ([Visualping Q1 2026 Release Recap](https://visualping.io/blog/q1-2026-release-recap)).
4. **Interfaces:** API + Zapier webhook integration shipped as of the Q1 2026 release; per-plan page/check quotas **[fact]** ([Visualping Q1 2026 Release Recap](https://visualping.io/blog/q1-2026-release-recap); [Visualping Webhooks by Zapier](https://zapier.com/apps/visualping/integrations/webhook)).
5. **Price & model:** Personal $50/mo ($600/yr), Business $100/mo ($1,200/yr), and a $3,000/yr custom "Solutions" tier with Premium AI, custom plans, dedicated support **[fact]** ([Visualping pricing](https://visualping.io/pricing)). No free tier surfaced on the fetched pricing page.
6. **ToS friction:** Low; monitors publicly accessible pages the user designates, same GET-based approach as Wingman's RFC-015 **[inference]**.

### Distill.io
1. **Evidence discipline / Autonomy:** Same category as Visualping — deterministic diff + alerting, no sending **[fact]**.
2. **Feature overlap:** D — maturity 4/5 as a general tool; free tier alone (25 monitors, 1,000 checks/month, 6-hour interval) could plausibly cover a light RFC-015 workload **[fact]** ([Distill.io pricing](https://distill.io/pricing/)).
4. **Interfaces:** Webhook & email alerts on all paid tiers; "Macros" for pre/post-check actions on Professional+ ($35/mo) and Flexi ($80+/mo) **[fact]** ([Distill.io pricing](https://distill.io/pricing/)).
5. **Price & model:** Free ($0, 25 monitors, 1,000 checks/mo, 6-hr interval); Starter $15/mo (50 monitors, 30,000 checks, 10-min interval); Professional $35/mo (150 monitors, 100,000 checks, 5-min interval); Flexi $80+/mo (500+ monitors, 200,000+ checks, 2-min interval) **[fact]** ([Distill.io pricing](https://distill.io/pricing/)).
6. **ToS friction:** Low, same GET-based model as Visualping.

### changedetection.io (OSS)
1. **Evidence discipline:** Deterministic text-hash + diff view by default; optional LLM connector ("AI change detection rules") for plain-language summaries of what changed, which is an *optional* generative layer on top of a deterministic core — closer to Wingman's philosophy than any other comparator in this category **[fact]** ([GitHub: dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)).
2. **Autonomy posture:** No sending; monitors and alerts via Discord/Email/Slack/Telegram/webhook **[fact]** ([GitHub: dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)).
3. **Feature overlap:** D (RFC-015) — maturity 5/5 as a general-purpose, self-hostable change-detection engine with a documented RSS Reader Mode, per-monitor trigger/extract-text filters, and full version history/diff archive — this is functionally a superset of RFC-015's "one GET each, snapshot = text hash + link set" design, already built, free, and open-source **[fact]** ([changedetection.io RSS Reader Mode tutorial](https://changedetection.io/tutorial/changedetectionio-can-be-your-new-favourite-rss-reader)). This is the single strongest KEEP-THIN-vs-INTEGRATE tension in the whole inventory (see main memo).
4. **Interfaces:** Self-hosted (Docker, pip, or $8.99/month hosted-with-proxies option); no formal MCP server found, but webhook output + RSS reader mode make it trivially pipeable into any downstream system, including a Wingman ingestion job **[fact]** ([GitHub: dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)).
5. **Price & model:** Free/open source (self-hosted, unlimited monitors limited only by host resources); optional $8.99/month hosted tier with proxies/support **[fact]** ([GitHub: dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)).
6. **ToS friction:** None inherently — monitors whatever URLs the user configures, same posture as Wingman's approved-source model.

---

## Feed reading / monitoring (overlap: C — feeds, news)

### Feedly (+ Leo/AI Feeds)
1. **Evidence discipline:** AI Feeds are explicitly **model-based**, not deterministic: "uses smart AI Models to filter content," "over 30,000 AI Models," concept-tagging via ML — the antithesis of Wingman's deterministic-composition invariant for the equivalent job (news relevance filtering) **[fact]** ([What is an AI Feed? — Feedly docs](https://docs.feedly.com/article/764-what-is-an-ai-feed-feedly)).
2. **Autonomy posture:** No sending; a reading/monitoring tool **[fact]**.
3. **Feature overlap:** C (news/feed relevance) — maturity 4/5 on sophistication of relevance filtering (concept-based tagging beats simple keyword match, directly addressing Wingman's "common-word companies remain hard" known-immature item) but is a generic content-intelligence product (built out for threat intel, market intel, biopharma) rather than person/company-specific dossiers **[fact]**+**[inference]** ([Feedly plan comparison docs](https://docs.feedly.com/article/140-what-is-the-difference-between-feedly-basic-pro-and-teams)).
4. **Interfaces:** Consumer/enterprise web app; Enterprise plan tier exists for "Threat Intelligence, Market Intelligence, Biopharma Research, Competitive Intelligence" workflows, implying an API/integration layer at that tier, though a public self-serve API was not confirmed on the fetched pages **[fact — partial]** ([Feedly plan comparison docs](https://docs.feedly.com/article/140-what-is-the-difference-between-feedly-basic-pro-and-teams)).
5. **Price & model:** Free, Pro, Pro+ (AI Feeds/RSS Builder), Enterprise; Pro+ reported around $99/year by third-party trackers **[fact]**+**[inference]** ([Feedly Pro Pricing vs Readless, readless.app, 2026](https://www.readless.app/blog/feedly-pro-pricing-vs-readless-2026)).
6. **ToS friction:** Low; reads publicly syndicated feeds.

### Inoreader
1. **Evidence discipline:** Similar to Feedly — has "Monitoring feeds" (people/brands/companies/trends) and AI "Intelligence" summarization features layered on top of deterministic RSS following; the base following mechanism is deterministic, the summarization layer is generative **[fact]** ([Inoreader pricing](https://www.inoreader.com/pricing)).
2. **Feature overlap:** C — maturity 4/5; explicitly supports "Web feeds & Track changes" for sites without RSS (directly relevant to Wingman's "feed discovery fails on JS-only sites" known-immature item) plus password-protected feed auth and Google News alerts as a first-class feature **[fact]** ([Inoreader pricing](https://www.inoreader.com/pricing)).
4. **Interfaces:** Has a documented API (used by third-party apps); Enterprise tier for custom deployments.
5. **Price & model:** Free forever tier; Pro ≈€6.67/month billed annually (or €8.99/month); Custom/Enterprise plans **[fact]** ([Inoreader pricing](https://www.inoreader.com/pricing)).
6. **ToS friction:** Low.

### Readwise Reader
1. **Evidence discipline / Autonomy:** Read-later + highlight-resurfacing tool; no claims layer, no sending **[fact]**.
2. **Feature overlap:** A/C (personal corpus of saved reading + highlights) — maturity 2/5 vs Wingman's People/Company intelligence; overlaps more with "personal corpus" (Area A) than with People intelligence, since it's about the user's own reading, not a per-person watchlist **[inference]** ([readwise.io/pricing](https://readwise.io/pricing)).
4. **Interfaces:** Has a public API (used by many third-party PKM integrations, e.g., Obsidian plugins) — not directly confirmed on the fetched pricing page but well-documented in the ecosystem **[inference — not verified on this pass]**.
5. **Price & model:** 30-day free trial, then monthly subscription (~$9.99/mo cited by third parties); student discount 50% **[fact]**+**[inference]** ([readwise.io/pricing](https://readwise.io/pricing); [Readwise Reader Pricing 2026, readless.app](https://www.readless.app/blog/readwise-reader-pricing-2026)).
6. **ToS friction:** Low.

---

## AI outreach writers / LinkedIn automation (overlap: E)

### Waalaxy
1. **Evidence discipline:** N/A — templated/generative outreach sequences **[inference]**.
2. **Autonomy posture:** Explicitly automates LinkedIn connection invitations and follow-ups at volume (300–800 invites/month depending on tier) — this is automated, unattended sending, the direct opposite of Wingman's never-sends invariant **[fact]** ([Waalaxy pricing](https://www.waalaxy.com/pricing)).
3. **Feature overlap:** E — maturity 5/5 on outreach-automation capability (which Wingman deliberately does not build) but 0/5 on Wingman's evidence-quote/corpus-citation requirement — Waalaxy sequences are templates with variables, not POV-stance-cited talking points **[fact]**+**[inference]**.
4. **Interfaces:** API access from Advanced tier ($32/user/month) up; Make/Zapier/N8N modules; CRM sync (2,000+ tools) **[fact]** ([Waalaxy pricing](https://www.waalaxy.com/pricing)).
5. **Price & model:** Pro $16/user/month (300 invites/mo); Advanced $32/user/month (800 invites/mo, API access); Business $55/user/month (adds cold email, multichannel) **[fact]** ([Waalaxy pricing](https://www.waalaxy.com/pricing)).
6. **ToS friction:** High — this is precisely the class of tool LinkedIn's Prohibited Software policy and Automated Activity policy target ("bots or other unauthorized automated methods to... send or redirect messages... or otherwise drive inauthentic engagement") **[fact]** ([LinkedIn Prohibited Software policy](https://www.linkedin.com/help/linkedin/answer/a1341387)).

### Dripify
1. **Evidence discipline:** N/A — same class as Waalaxy **[inference]**.
2. **Autonomy posture:** Automates LinkedIn connection requests, messages, profile views, endorsements, post likes, and follows at daily quotas; markets "human behavior simulation" and "access from unique, local IP-address" and "cloud-based performance" explicitly as anti-detection features — i.e., its own marketing acknowledges it is built to evade LinkedIn's automated-activity detection **[fact]** ([Dripify pricing](https://dripify.com/pricing/)).
3. **Feature overlap:** E — maturity 5/5 on automation breadth, 0/5 on evidence discipline, same profile as Waalaxy.
4. **Interfaces:** Webhook & Zapier integration on Pro tier and up ($59–$79/user/month); CSV export **[fact]** ([Dripify pricing](https://dripify.com/pricing/)).
5. **Price & model:** Basic $39/user/month (1 campaign, limited quotas); Pro $59/user/month (unlimited campaigns, full quotas, webhooks); Enterprise custom **[fact]** ([Dripify pricing](https://dripify.com/pricing/)).
6. **ToS friction:** Very high — self-described anti-detection posture ("human behavior simulation") is the clearest evidence in this whole rubric of a comparator built specifically to violate LinkedIn's Automated Activity policy while evading enforcement **[fact]**+**[inference]**.

---

## PKM + AI (overlap: A, C)

### Obsidian (+ AI plugins)
1. **Evidence discipline:** Varies entirely by plugin; Obsidian core itself is a plain local-markdown notes tool with no AI claims layer. Community AI plugins (chat, summarization) inherit whatever hallucination risk their underlying LLM has, with no platform-level verbatim-quote enforcement **[inference]**.
2. **Autonomy posture:** No sending; a notes app **[fact]**.
3. **Feature overlap:** A (personal corpus) — maturity 3/5 as a *general* PKM tool (full-text search, backlinks, graph view) but with no purpose-built resume/career-evidence extraction, no FTS5-equivalent cited-retrieval pipeline out of the box — that would have to be custom-built via plugins **[inference]**.
4. **Interfaces:** Fully local, plugin API (community plugin ecosystem), Markdown files on disk — the most "integrate-friendly" of the PKM set since Wingman's own corpus is already Markdown-based **[inference]**.
5. **Price & model:** Free for personal use; paid sync/publish add-ons **[inference]**.
6. **ToS friction:** None.

### Notion AI
1. **Evidence discipline:** Documented, real-world fabrication risk. A widely discussed user report describes Notion AI inventing links/sources when asked to summarize and cite "thought leaders'" content: fabricated URLs prefixed with real site names leading to 404s, less than 50% of "cited" TED-talk links resolving to real talks, and a fabricated attribution (a movement's founder) with sources that "have never been real" per Wayback Machine checks **[fact]** ([Notion AI hallucination reports, Reddit r/Notion](https://www.reddit.com/r/Notion/comments/10wtbeu/anyone_else_have_notion_ai_just_make_things_up/)). This is the single most concrete, real-world illustration in this rubric of exactly the failure mode Wingman's evidence-before-assertion invariant (verbatim-quote-or-nothing-stored) is designed to prevent.
2. **Autonomy posture:** No autonomous sending; a workspace/notes AI feature **[fact]**.
3. **Feature overlap:** A/C (notes + AI drafting) — maturity 2/5 vs Wingman on evidence discipline specifically (documented fabrication), though 4/5 on general notes/workspace maturity as a product **[fact]**+**[inference]**.
4. **Interfaces:** Full Notion API (databases, pages, blocks) — mature, well-documented, widely integrated **[inference — well-established, not re-verified this pass]**.
5. **Price & model:** Free tier includes a "Trial of Notion AI"; full AI capability requires paid plans (Plus/Business/Enterprise) **[fact]** ([Notion pricing](https://www.notion.com/pricing)).
6. **ToS friction:** None specific to LinkedIn; general SaaS ToS only.

### Rewind AI / Limitless
1. **Evidence discipline:** Records/transcribes what the user says and sees; the underlying capture is deterministic (transcription), but "AI features like notes and summaries" layered on top are generative and carry standard LLM summarization risk **[fact]** ([Limitless pricing plans](https://help.limitless.ai/en/articles/9129649-pricing-plans)).
2. **Autonomy posture:** No sending; a personal memory/recording tool, closer to Wingman's "personal corpus" concept than any other PKM comparator, since it is building a first-person evidentiary record (transcripts) rather than third-party inferred claims **[fact]**.
3. **Feature overlap:** A (personal corpus, but audio/meeting-derived rather than document-derived) — maturity 3/5; interesting adjacent-data-source idea (interaction history) that could one day feed Wingman's known-immature "no interaction history" warmth-score gap, but not a competing product in Wingman's document-ingestion sense **[inference]**.
4. **Interfaces:** Web/Mac/Windows apps + optional Pendant hardware; no public developer API confirmed on the fetched page **[fact — not found]**.
5. **Price & model:** Free plan: 1,200 minutes (20 hrs)/month transcription, AI notes/summaries included free; Pro and Unlimited paid tiers scale transcription minutes; Rewind Pro bundles with Limitless Pro **[fact]** ([Limitless pricing plans](https://help.limitless.ai/en/articles/9129649-pricing-plans)).
6. **ToS friction:** None LinkedIn-specific; general recording-consent considerations apply (out of scope for this rubric).

---

## MCP-native / agentic career tools (overlap: F — the integration surface)

This category is the fastest-moving and most directly relevant to Wingman's own F (33-tool MCP server, RFC-017). As of 2026-07-18, the MCP job/career ecosystem has grown to an estimated "fewer than 10" dedicated servers out of "over 3,000" MCP servers overall in the registry as of an April 2026 count **[fact]** ([Best Free MCP Servers for Job Search in 2026, workopia.io, 2026-04-09](https://workopia.io/blog/best-free-mcp-servers-job-search-2026)).

### JobGPT (via 6figr.com) MCP server
1. **Evidence discipline:** N/A / not disclosed — a job-search automation platform, not an evidence-verification system **[inference]**.
2. **Autonomy posture:** **Directly contradicts Wingman's core invariant.** The server explicitly exposes auto-apply as a tool category: "Job Hunts: Create and manage saved searches with auto-apply," "Applications: ...auto-apply, import jobs by URL," ships with "5 free auto-apply credits" on signup, and an outreach category that finds recruiters and "send[s] personalized outreach emails" **[fact]** ([JobGPT MCP setup guide, 6figr.com, 2026-03-05](https://6figr.com/blog/jobgpt-mcp-claude-code-646)). Whether outreach emails are sent with or without per-message approval is not stated on the page, but auto-apply is explicitly named and credited as a feature, not a hypothetical **[fact]**.
3. **Feature overlap:** B, E, F — maturity 4/5 on breadth (34 tools spanning search, profile, hunts, applications, resume, outreach — matching Wingman's own 33-tool count almost exactly) but this is the clearest categorical divergence in the entire comparator set: JobGPT's MCP surface is built explicitly to automate the actions Wingman's manifesto rules out **[fact]**+**[inference]**.
4. **Interfaces:** Hosted remote MCP server at `https://mcp.6figr.com/mcp`, Bearer-token auth (API key generated from account settings, prefixed `mcp_`), also runnable locally via `npx jobgpt-mcp-server` with an env-var API key; Claude Code one-liner setup (`claude mcp add jobgpt -t http ...`) **[fact]** ([JobGPT MCP setup guide, 6figr.com, 2026-03-05](https://6figr.com/blog/jobgpt-mcp-claude-code-646)).
5. **Price & model:** MCP server itself described as "free and open source"; underlying JobGPT platform actions (auto-apply credits) appear to be metered/credited, consistent with typical freemium job-tool models **[fact]**+**[inference]**.
6. **ToS friction:** Job-board-adjacent auto-apply carries ToS risk with individual employer ATSs/job boards depending on their own terms; not LinkedIn-specific but the same family of risk **[inference]**.

### Rezi MCP server
See full entry under "Resume/ATS optimizers" above. Notable here as the most Wingman-*compatible* MCP posture found: read/write resume tools and job search/detail tools, explicitly **no** auto-apply or auto-send tool, session-scoped OAuth-style auth tied to the user's own Rezi subscription **[fact]** ([rezi-mcp — MCP.Directory](https://mcp.directory/servers/rezi-mcp)).

### Dice MCP server
1. **Evidence discipline:** N/A — a job listings database exposed via MCP; the value is direct, current, first-party listing data (a "curated database of technology jobs"), arguably itself a form of grounding (no fabricated jobs) even without an explicit claims-verification layer **[fact]** ([Dice Launches MCP Server, dice.com, 2026-01-12](https://www.dice.com/career-advice/dice-launches-mcp-server-for-ai-powered-job-search)).
2. **Autonomy posture:** Read-only search surface; no apply/send tools described **[fact]**.
3. **Feature overlap:** B (job search only, not assessment) — maturity 3/5 as a pure listings-search MCP; supports "13+ filter parameters" (location radius, workplace type, employment type, visa sponsorship, posting recency) via natural language **[fact]** ([Dice Launches MCP Server, dice.com, 2026-01-12](https://www.dice.com/career-advice/dice-launches-mcp-server-for-ai-powered-job-search)).
4. **Interfaces:** MCP server connecting Claude/ChatGPT/Gemini directly to Dice's job database; setup guide and "Technical Documentation" referenced but full auth/rate-limit spec not captured on this pass **[fact — partial]**.
5. **Price & model:** Free to connect and query (Dice's core job-search product is free to job seekers) **[inference]**.
6. **ToS friction:** None — first-party data, first-party MCP server.

### Four-Leaf / Clover MCP server
1. **Evidence discipline:** `match_score` runs "a real scoring algorithm against a resume and a job description" (deterministic-scoring claim, notable since it's explicitly framed as algorithmic rather than purely generative) returning a 0-100 fit score with skills/experience/role-alignment breakdowns — directionally similar in spirit to (but less evidentiary than) Wingman's verdict system, since it produces a score rather than quote-cited met/partial/gap/unknown verdicts **[fact]** ([How we built a job search assistant MCP, dev.to, 2026-06-04](https://dev.to/fourleaf/how-we-built-a-job-search-assistant-mcp-for-claude-cursor-and-chatgpt-13d2)).
2. **Autonomy posture:** Two paid tools (`start_voice_mock_interview`, `tailor_resume`) return deep-links into the four-leaf.ai app rather than acting server-side — i.e., they hand control back to the user in-app rather than autonomously executing, a partial approval-gate pattern **[fact]** ([How we built a job search assistant MCP, dev.to, 2026-06-04](https://dev.to/fourleaf/how-we-built-a-job-search-assistant-mcp-for-claude-cursor-and-chatgpt-13d2)).
3. **Feature overlap:** B — maturity 3/5; `search_jobs` over a "nightly-scraped pool of 180,000+ active postings" from Greenhouse/Lever/Ashby/Workday, `get_role_intelligence`/`list_roles` expose a structured 24-role catalog with scoring rubrics — a real, if generic, alternative to Wingman's requirement-extraction approach **[fact]**.
4. **Interfaces:** Hosted MCP server at `four-leaf.ai/api/mcp`, plus an MIT-licensed "Skill" wrapper on GitHub; listed on the Official MCP Registry, Glama, Smithery, PulseMCP, and skills.sh — a well-distributed, multi-registry-listed server, useful as a distribution-strategy comparator for Wingman's own future MCP registry listing decision **[fact]** ([How we built a job search assistant MCP, dev.to, 2026-06-04](https://dev.to/fourleaf/how-we-built-a-job-search-assistant-mcp-for-claude-cursor-and-chatgpt-13d2)).
5. **Price & model:** Eleven tools total; two are explicitly paid (voice mock interview, resume tailoring) with the rest presumably free — a freemium-inside-MCP model **[fact]**+**[inference]**.
6. **ToS friction:** Job-board-scraping risk on the sourcing side ("nightly-scraped pool"), not LinkedIn-specific **[inference]**.

### LinkedIn-scraping MCP servers (community, unofficial)
Several community-built MCP servers (`Hritik003/linkedin-mcp`, `stickerdaniel/linkedin-mcp-server`) expose LinkedIn profile/job/feed access via **unofficial LinkedIn API methods / browser scraping**, explicitly stated in their own READMEs ("Uses Unofficial LinkedIn API Docs," "Scrape LinkedIn profiles and companies") **[fact]** ([Hritik003/linkedin-mcp](https://github.com/Hritik003/linkedin-mcp); [stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server)). These sit squarely inside the conduct LinkedIn's Prohibited Software and User Agreement Section 8.2 forbid (crawlers/bots that scrape or copy the Services) **[fact]** ([LinkedIn Prohibited Software policy](https://www.linkedin.com/help/linkedin/answer/a1341387)). Relevant to Wingman only as a cautionary boundary-marker: any future Wingman LinkedIn integration must stay on the *user's own exported data* side of this line, exactly as it already does per the briefing (LinkedIn export ingestion, not live scraping).

**Overall F-category read [inference]:** the MCP career-tool ecosystem in mid-2026 is small (<10 servers), heavily weighted toward auto-apply/scraping-style tools (JobGPT, LinkedIn-scraping servers, Workopia, Career-Ops) with a minority of more conservative, read/write-scoped servers (Rezi, Dice, Four-Leaf's deep-link pattern). No comparator found combines Wingman's specific mix of (a) never auto-applies/sends, (b) evidence-quote discipline, and (c) a 30+-tool MCP surface with CLI parity and a capability-token remote-auth model (RFC-017). Wingman's F-area architecture is not duplicated by any comparator surveyed; the closest partial analogs (Rezi MCP, Four-Leaf's deep-link gating) validate the *direction* (session-scoped auth, no auto-send tools) rather than compete on breadth.

---

## LinkedIn Terms of Service — reference findings (cross-cutting, affects C/D/E ToS-friction scoring above)

**[fact]**, per LinkedIn's own policy pages, fetched 2026-07-18:

- **Prohibited Software and Extensions** policy bars any third-party "crawlers," bots, browser plug-ins/extensions that scrape LinkedIn, modify its appearance, or automate activity on the site; also bars fake accounts/engagement and algorithm-manipulation tools ([LinkedIn Help: Prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387)).
- **User Agreement Section 8.2** enumerates specific prohibitions relevant to this rubric: no scraping/copying via crawlers or plugins; no bots/automated methods to access the service, add/download contacts, or send/redirect messages, likes, comments, or shares; no overriding security features or bypassing access controls; no unauthorized deep-linking; no interference/load abuse ([LinkedIn Help: Prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387)).
- Consequence stated: violating accounts risk restriction or shutdown, and prohibited tools "may become non-operational without notice" as LinkedIn improves its defenses — i.e., this is an actively enforced and evolving policy, not a static one **[fact]**.
- **Implication for the rubric:** every LinkedIn-adjacent comparator surveyed sits somewhere on a spectrum from "first-party and compliant" (LinkedIn Sales Navigator itself) through "extension reading/writing the user's own profile, gray-zone" (Crystal, Careerflow's LinkedIn Optimizer) to "explicitly built to automate sending/engagement and evade detection" (Waalaxy, Dripify, community LinkedIn-scraping MCP servers). Wingman's own posture — ingesting a user-exported LinkedIn archive rather than live-scraping or automating — sits outside this risk spectrum entirely, which the main memo treats as a structural advantage worth preserving rather than trading away for feature parity.
