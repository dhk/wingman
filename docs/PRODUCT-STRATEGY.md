# Wingman: Product Strategy and Guardrails

**Working draft — 16 August 2026**

## Executive summary

Wingman began as a local-first job-search assistant. The implemented product is now substantially broader: it builds an evidence-backed model of a person, researches the people and organizations around them, prepares them for consequential professional interactions, captures what happened, and recommends what should happen next.

The job search remains an important use case, but it is no longer an adequate definition of the product.

The user-facing promise comes first:

> **Wingman helps you prepare for the professional interactions that matter—and learn from what happens afterward.**

This is why a person cares and what they may buy. The product definition explains how Wingman fulfills that promise:

> **Wingman is a personal intelligence system that builds an evidence-backed understanding of you, the people and organizations you encounter, and what happens between you.**

The promise and the product definition serve different purposes. The promise should lead user-facing explanations. The product definition should guide architecture, scope and feature decisions.

Wingman operates in a person's **professional, public and intellectual life**. It is not intended for intimate, romantic, familial or spiritual relationships.

Its deepest promise is not contact management, application volume or conversational affirmation. It is better judgment and better participation:

> Understand me → understand others → find alignment or useful disagreement → prepare me to engage → learn from what happens → foster the right next step.

The immediate adoption wedge is narrower and easier to experience:

> **Help me prepare for this consequential interaction now.**

A strong meeting briefing—public background, revealing anecdotes, expressed values, useful points of alignment, respectful disagreement and relevant conversation openings—can demonstrate value before Wingman has a rich model of the user. After the interaction, Wingman asks what happened, ingests notes or a transcript, and closes the learning loop.

## 1. What Wingman is today

As implemented in the repository through v0.6.0, Wingman is a local-first and hosted-capable system with the following feature families.

### 1.1 A canonical, evidence-backed model of the person

- Ingests resumes in multiple formats, LinkedIn exports, the user's writing, interview responses, forms and call transcripts.
- Builds a structured professional profile covering roles, skills, achievements and testimonials.
- Requires consequential claims to trace to source evidence.
- Surfaces conflicts rather than silently choosing a convenient version.
- Supports correction, amendment, reclassification and source lineage.
- Synthesizes the user's point of view and value dimensions.
- Provides separate readings of expressed values and ways of working.
- Maintains job criteria, refined application answers and other first-person evidence.

### 1.2 Opportunity and role assessment

- Ingests job descriptions from files or URLs.
- Extracts requirements with citations.
- Assesses each requirement as met, partial, gap or unknown against actual profile evidence.
- Produces fit briefs and application packs.
- Scores roles against explicitly elicited job criteria.
- Retains and recalls refined answers across applications.

### 1.3 Intelligence about people

- Tracks selected people and their public writing through feeds, publications and websites.
- Builds evidence-validated point-of-view cards.
- Searches stored writing and identifies semantic similarity.
- Builds public-background and deep-dive dossiers.
- Finds warm paths using explicit relationship signals.
- Tracks interaction history and relationship objectives.
- Prepares purpose-specific outreach and conversation material without sending it.
- Surfaces recent public developments and relevant anecdotes.

### 1.4 Intelligence about companies

- Follows approved company sources, feeds, careers pages and newsrooms.
- Detects changes and new links as potential signals.
- Builds dated dossiers with fact, inference and hypothesis labels.
- Synthesizes company themes from citable evidence.
- Compares companies with the user or with other companies.
- Finds warm-path coverage through people already known.

### 1.5 Action and continuity

- Runs a full research-and-preparation sequence for a selected person or company.
- Maintains watchlists and followed companies.
- Produces overnight digests and action lists.
- Supports mute, snooze and reinstate decisions for recurring suggestions.
- Captures unsorted leads in a heap and helps route them deliberately.
- Records interaction outcomes, relationship goals and likely next moves.
- Provides unified keyword and semantic search across the workspace.

### 1.6 Reflection, perspectives and coaching

- Conducts guided interviews about values, admired or rejected examples, organizations and professional criteria.
- Supports coaching personas while distinguishing the person's own evidence from a mentor's interpretation.
- Can carve a coached persona into a separate workspace.
- Supports hosted tenants, guided forms and operator prompts.
- Retains assistant commentary in a quarantined store that cannot masquerade as first-person evidence.

### 1.7 Delivery, privacy and operations

- Provides CLI, MCP and limited web views.
- Supports self-operated, BYOK and hosted deployment shapes.
- Uses separate workspaces and capability tokens for hosted tenants.
- Keeps external actions behind a human approval boundary.
- Supports backups, managed upgrades, diagnostics, artifact records and optional local telemetry.
- Exposes provider-neutral interfaces rather than binding the product to one model vendor.

## 2. What the feature inventory reveals

The feature list looks heterogeneous if Wingman is described as a job-search tool. It becomes coherent when viewed as a person-centric intelligence loop.

Wingman maintains four related models:

1. **The person:** identity, experience, values, beliefs, interests, criteria and evolving views.
2. **The environment:** people, organizations, communities, opportunities and public ideas.
3. **The relationship:** history, objectives, mutual relevance, promises, tensions and possible next steps.
4. **The session:** the user's immediate goal, desired operating mode, constraints and evidence of drift.

It then performs five kinds of work:

1. Gather and preserve evidence.
2. Synthesize what the evidence may mean.
3. Compare the person with an opportunity, role archetype, organization or other person.
4. Prepare the person for a consequential decision or interaction.
5. Learn from the outcome and foster an intentional next step.

This is the common product beneath job assessment, values synthesis, meeting preparation, relationship objectives, coaching, daily digests and reflection.

## 3. What Wingman should become

Wingman should become a durable companion for a person's professional, public and intellectual development.

It should help answer:

- Who am I, and how is that changing?
- What do I value, believe and find interesting?
- What have I actually demonstrated?
- Where do I align with a role, person, organization or community?
- Where is disagreement meaningful and worth exploring?
- How can I make this interaction valuable to both parties?
- What did I learn from what happened?
- What should happen next—and when is pause, completion or closure the right answer?

### 3.1 The primary experience loop

**Before:** Understand the session goal and prepare from the smallest credible seed.

**During:** Keep the work aligned with the stated goal; flag drift and renegotiate the mode when necessary.

**After:** Ask what happened, ingest notes or a transcript, identify promises and learning, and propose the next step.

**Over time:** Retain longitudinal working memory, periodically step back, surface changes and contradictions, and invite the user to promote durable conclusions into canonical memory.

### 3.2 Memory model

Wingman should remember by default but promote by consent.

- **Observed:** something appeared in a session or source.
- **Inferred:** Wingman interpreted or synthesized it.
- **Endorsed:** the person accepted it as part of their durable model.

Working memory should be durable and searchable even when unendorsed. It should not silently become authoritative identity. Canonical views should be versioned so that changes in beliefs, values and priorities remain visible over time.

Wingman should maintain a reflection counter based on elapsed active hours, sessions, turns, unresolved material and recurring themes. At an appropriate threshold it should invite a reflection checkpoint similar to a thoughtful periodic coaching review.

### 3.3 Session contract and operating modes

For consequential work, Wingman should ask:

- What sort of session should this be?
- What are the goals?
- What would a useful outcome look like?
- What constraints or decisions are already fixed?
- What degree and form of challenge would be useful?

Modes may include reflection, synthesis, exploration, advice, preparation, research, recommendation, steelmanning and challenge. The goal matters more than the label.

Wingman should notice drift from the session contract. It may propose a mode change, but it should make the change explicit rather than silently altering the engagement.

### 3.4 Strong, respectful pushback

Wingman must not confuse support with agreement.

> **Wingman is loyal to the person, not to every proposition the person advances.**

It should begin with curiosity, synthesize fairly, and flag meaningful contradiction or stuckness when supported by evidence. Pushback should be strong, respectful, specific and proportionate. It should show its basis and allow the user to correct the record.

### 3.5 People as trajectories

Wingman should understand people through signals of varying strength:

- what they say;
- what they do;
- who and what they affiliate with;
- how these signals change over time.

It should distinguish association from endorsement and current contradiction from historical change. When a person's position changes, Wingman should surface the change and the supporting evidence without pretending to know whether it represents growth, decline or opportunism.

> **People are trajectories, not profiles.**

## 4. The adoption wedge

### 4.1 Consequential-interaction briefing

The fastest credible demonstration of Wingman's value is preparation for an upcoming professional interaction.

Input should be minimal:

- a name;
- a screenshot, pasted biography or known affiliation;
- a personal or professional website when identifiable;
- optionally, why the meeting is happening and what would make it worthwhile.

Wingman should resolve identity from public evidence and ask one focused clarification only when necessary. It should always degrade honestly: a thinner but credible result is better than invented richness.

The output should answer:

- Who is this person professionally and intellectually?
- What do they appear to care about?
- What revealing anecdotes are supported by public evidence?
- What have they said, done and affiliated with?
- Where might the user align with them?
- Where might respectful disagreement be productive?
- What could the user contribute?
- What questions or conversation openings are genuinely relevant?

The Mark meeting is the archetypal proof: the briefing enabled a richer conversation, and the other person valued that thoughtful preparation had taken place.

### 4.2 The follow-through

After the meeting, Wingman asks what happened and whether notes or a transcript are available. It compares the outcome with the original goal, extracts promises and learning, and proposes:

- a useful follow-up;
- a resource or introduction promised;
- a collaboration idea;
- an unresolved question worth revisiting;
- another conversation;
- a pause;
- completion; or
- closure.

Clear obligations should become proposed ticklers requiring quick confirmation, not silently created commitments.

### 4.3 Progressive enrichment

> **Seed lightly. Harvest immediately. Enrich progressively.**

Wingman should earn the right to ask for more information by returning value from what the user has already provided. The personal model should be able to emerge through real work, not only through a lengthy upfront interview.

## 5. Existing and prospective use cases

### 5.1 Existing or substantially implemented

| Use case | Value |
| --- | --- |
| Job-fit assessment | Understand fit, gaps and uncertainty using real evidence. |
| Application preparation | Assemble cited experience, company context and recalled answers. |
| Company research | Build a dated, transparent view of a company and its signals. |
| Person research | Understand someone's published ideas, activity and professional context. |
| Meeting preparation | Enter a consequential conversation with relevant context and anecdotes. |
| Relationship objectives | State why a relationship matters and what a useful next move could be. |
| Warm-path discovery | Identify credible routes through real relationship signals. |
| Values and working-style synthesis | Articulate recurring themes in the person's own evidence. |
| Career-transition mentoring | Help a person articulate criteria, evidence and possible directions. |
| Daily/overnight review | Turn changing information into a manageable action list. |
| Hosted guided use | Let nontechnical people use Wingman without operating infrastructure. |

### 5.2 Near-term prospective use cases

| Use case | Product fit |
| --- | --- |
| Post-meeting review | Core: closes the learning and relationship loop. |
| Transcript-derived next steps | Core, provided every suggestion remains inspectable and user-approved. |
| Reflection checkpoints | Core: examines longitudinal change, contradiction and priorities. |
| Session contracts and drift detection | Core interaction architecture. |
| Role-archetype comparison | Core when used to give the person context, not a rank. |
| Professional-community discovery | Core when grounded in interests, values and reciprocal relevance. |
| Respectful-disagreement preparation | Core for public and intellectual engagement. |
| Evolving belief/values timeline | Core longitudinal intelligence. |
| Collaboration preparation | Core extension of consequential-interaction support. |

### 5.3 Longer-term possibilities

- Carefully approved transmission of factual follow-ups, promised resources or collaboration summaries.
- Stronger longitudinal outcome learning across relationships, opportunities and stated goals.
- Opt-in, privacy-preserving role cohorts once sample sizes justify genuine comparison.
- Richer professional-community mapping and affiliation context.
- Shared collaboration workspaces where every participant understands the model and retains agency.

These are future gates, not current promises.

## 6. Product surfaces and line extensions

These should initially be treated as surfaces of one product, not prematurely separated businesses.

### 6.1 Wingman Briefing

**Promise:** Prepare me for this consequential professional interaction.

This is the acquisition wedge and most immediate demonstration of value.

### 6.2 Wingman Follow-Through

**Promise:** Help me learn from what happened and foster the right next step.

This is the first compounding extension and should be prioritized ahead of exhaustive profile onboarding.

### 6.3 Wingman Career

**Promise:** Help me understand which roles, organizations and transitions fit who I am and what I want.

This contains the original job-search capabilities without defining the whole product.

### 6.4 Wingman Reflection

**Promise:** Help me examine how my thinking, priorities and professional practice are evolving.

This includes longitudinal memory, periodic reflection, contradiction detection and canonical-memory promotion.

### 6.5 Wingman Guided

**Promise:** Help someone get started or navigate a career transition with a trusted mentor facilitating the process.

This is a delivery mode, not a coach-centric product. The modeled person remains the subject and beneficiary. A mentor may prompt, challenge and reflect but must never silently author that person.

### 6.6 Wingman Hosted

**Promise:** Receive the full Wingman experience without running software or infrastructure.

Near-term monetization should focus on convenience, continuity, maintenance and modest service fees. BYOK should be supported through a humane interface. The open core should remain genuinely useful rather than deliberately crippled.

## 7. External capability boundaries

Wingman should integrate specialist systems without swallowing their identity.

### Minority Report

Minority Report researches external context through multiple independent perspectives. It can construct transparent role archetypes, map agreement and disagreement, and pressure-test claims.

Wingman applies that research to the person:

- What does this context mean for me?
- Where do I align?
- Is this a demonstrated gap, a preference difference or an unknown?
- What should I examine or do next?

Minority Report should remain a separate research service with an integrated Wingman experience. Wingman owns personal judgment; Minority Report supplies researched external context.

The same boundary applies to Crucible-like debate machinery: adversarial or steelman modes belong when they serve a legitimate Wingman session goal. Generic multi-agent debate is infrastructure, not itself part of the product promise.

## 8. Non-negotiable product boundaries

### 8.1 Professional, public and intellectual—not intimate

Wingman does not target romantic, familial, intimate or spiritual relationships.

### 8.2 It works for the person, not on them

Wingman must not become:

- candidate screening;
- recruiter-side culture-fit scoring;
- covert personality or values assessment;
- employee surveillance;
- founder or investor profiling used to make hidden decisions;
- automated reputational scoring;
- inference of protected or intimate characteristics; or
- sale of personal models to third parties.

It may help a user understand another person from public evidence for respectful engagement. It may not covertly evaluate that person for employment, investment or access decisions.

### 8.3 The person controls external communication

For now, Wingman prepares; the person communicates. No message is sent without the person reviewing and deliberately sending it.

This is a product gate rather than a permanent prohibition. Future automation must remove clerical friction without automating attentiveness, accountability or care.

### 8.4 Evidence before assertion

Wingman must distinguish:

- first-person evidence;
- public statements;
- observed behavior;
- affiliation;
- credible third-party reporting;
- model inference; and
- user-endorsed interpretation.

It must not turn absence of evidence into evidence of absence, or a plausible narrative into a fact.

### 8.5 Context, not ranking

Role comparisons should use transparent archetypes or defensible cohorts. Until adequate population data exists, Wingman must not use percentile language or imply that a synthetic reference profile represents an empirical average.

### 8.6 Closure is valid

Wingman should distinguish continue, pause, complete and close. A longitudinal record remains valuable even when a relationship or opportunity has run its course.

## 9. Feature-admission test

When considering a new feature, answer these questions in order.

### A. Does it serve the product promise?

1. Does it help the person understand themselves, another person, an organization, an opportunity or a relevant community?
2. Does it improve judgment, preparation, reflection or the quality of a professional/public/intellectual interaction?
3. Does it create a meaningful next step or help the user consciously choose pause, completion or closure?

If none apply, it is probably outside Wingman's scope.

### B. Is the person still the subject and beneficiary?

4. Is the feature working for the modeled person rather than enabling someone else to judge or control them?
5. Does the person retain agency over interpretation, memory and external action?
6. Would the feature remain acceptable if its operation were fully visible to everyone it concerns?

If not, reject it.

### C. Is it epistemically honest?

7. Can important claims be traced to evidence?
8. Are speech, action, affiliation and inference kept distinct?
9. Does it preserve temporal change rather than flattening a person into a static profile?
10. Does it disclose uncertainty, missing evidence and degraded results?

If not, redesign it before proceeding.

### D. Is it mindful rather than mechanistic?

11. Does it increase relevance or value for the other party—not merely the user's efficiency?
12. Is it automating clerical friction rather than automating care?
13. Does it avoid generic engagement, immortal follow-up queues and AI-bloated messaging?

If not, it is likely harmful to the product promise.

### E. Does it belong inside Wingman?

14. Is personal context and judgment central to the capability?
15. Could the capability be a separate specialist service whose output Wingman consumes?
16. Is this a core capability, an acquisition surface, a delivery mode or merely infrastructure?

Integrate adjacent capabilities without automatically absorbing them.

### F. Does it improve adoption now?

17. Can a nontechnical thoughtful professional understand and use it?
18. Does it return value before demanding extensive setup or data?
19. Does it strengthen the loop from immediate value to continued use?
20. Will it help drive use, forks, modest paid service or strategic product interest?

If the feature passes the product tests but not the adoption test, it may belong later rather than now.

## 10. Strategic priorities

### Now

1. Make the consequential-interaction briefing work from minimal information.
2. Build the post-interaction feedback loop: outcome, transcript, learning and proposed next step.
3. Make the experience usable by nontechnical thoughtful professionals through hosted access.
4. Support BYOK and a modest paid service sufficient to cover operations.
5. Preserve everything provisionally and make promotion to canonical memory explicit.

### Next

1. Add session contracts, drift detection and explicit mode negotiation.
2. Add reflection checkpoints based on accumulated work rather than calendar alone.
3. Build transparent role-archetype comparisons using Minority Report research.
4. Strengthen temporal models of people, beliefs, affiliations and relationships.
5. Improve professional-community discovery and respectful-disagreement preparation.

### Not now

1. Autonomous messaging or generic follow-up.
2. Exhaustive upfront onboarding before any value is returned.
3. Population percentile claims without defensible data.
4. Recruiter, employer or investor assessment of non-participating people.
5. Expansion into intimate personal domains.
6. Folding every research or orchestration capability into Wingman itself.

## 11. Adoption and business model

Near-term validation should be measured by:

1. People using Wingman.
2. People forking and extending it.
3. People paying modestly for features or service.
4. People expressing interest in buying the product or business.

The strategy is open-core adoption:

- Preserve a useful, forkable core.
- Design the primary experience for nontechnical professionals, not developers.
- Offer BYOK without exposing infrastructure complexity.
- Charge initially for hosted convenience, continuity, maintenance and support.
- Avoid reserving the product's ethical or intellectual quality only for paid users.

The repository remains valuable as proof of work and as a reference architecture for evidence-first personal AI. It should demonstrate the quality of the product thinking without forcing the primary user to understand its technical machinery.

## 12. Promise, product and short external explainer

### The promise: why a person cares

> **Wingman helps you prepare for the professional interactions that matter—and learn from what happens afterward.**

People will adopt or buy Wingman for this outcome, not because they want a “personal intelligence system.”

### The product: what Wingman does

> **Wingman builds an evidence-backed understanding of you, the people and organizations you encounter, and what happens between you. It uses that understanding to improve preparation, judgment, reflection and follow-through.**

This definition belongs in product strategy, architecture and collaborator explanations. It is not the lead sales proposition.

### Short external explainer

> Wingman helps you prepare for the professional interactions that matter—and learn from what happens afterward.
>
> Give it as little as a name, a screenshot or a website and it will build a credible briefing: who this person is, what they care about, revealing anecdotes, where you may align and where useful disagreement may exist. After the meeting, Wingman asks what happened, can ingest your notes or transcript, and helps you decide what should happen next.
>
> Over time it builds an evidence-backed understanding of your experience, values, beliefs and interests. That makes every future briefing, opportunity assessment and recommendation more personal—not because Wingman acts for you, but because it helps you show up better informed, more relevant and fully accountable for how you engage.

## 13. The compact product constitution

1. Wingman is centered on the person using it.
2. It serves professional, public and intellectual life.
3. It helps people become better participants, not better manipulators.
4. It is loyal to the person, not to every proposition they advance.
5. It begins consequential sessions with an explicit purpose.
6. It remembers by default and promotes identity claims by consent.
7. It treats people as evolving trajectories rather than static profiles.
8. It distinguishes what people say, do and affiliate with.
9. It makes evidence, inference, uncertainty and change visible.
10. It starts with the smallest credible seed and enriches progressively.
11. It closes the loop after interactions and always considers the right next state.
12. Continue, pause, complete and close are all valid outcomes.
13. It prepares; for now, the person communicates.
14. It works for the modeled person, never covertly on them.
15. It integrates specialist services without becoming an indiscriminate platform.
16. It optimizes first for meaningful adoption and learning.
