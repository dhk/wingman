# Findings — profile-interview-design — claude

**Researched by:** Claude (Sonnet 5, via Claude Code), web research pass on
2026-07-28. All sources below were accessed via live web search on that
date; a handful of URLs (marked below) returned HTTP 403 to this session's
fetch tool when full-text retrieval was attempted, so those are cited from
indexed search-result summaries rather than a full-text read — flagged
inline and in Open gaps.

Claims are labeled **[fact]** (documented/dated/sourced) or **[inference]**
(my read connecting a source to this design), per the brief's method notes.

## Q1 — Prior art for the three-way split itself

| Entry | What it is | PROFILE-BOOTSTRAP-DESIGN.md concept overlap | Verdict |
|---|---|---|---|
| Schwartz Theory of Basic Values (Schwartz Value Survey / Portrait Values Questionnaire) — Schwartz, "An Overview of the Schwartz Theory of Basic Values," *Online Readings in Psychology and Culture* (2012), https://scholarworks.gvsu.edu/orpc/vol2/iss1/11/; Wikipedia summary, https://en.wikipedia.org/wiki/Theory_of_basic_human_values | Cross-culturally validated (82+ countries) circular model of 10 (later 19) basic value types along two bipolar axes: openness-to-change vs. conservation, self-enhancement vs. self-transcendence | Directly names and structures what the "Values" category is trying to elicit; a free, non-proprietary reference framework, not a live instrument | adopt/reference |
| Culture-fit vs. culture-add hiring frameworks — e.g. BetterUp "Culture Fit vs Culture Add," https://www.betterup.com/blog/cultural-fit; PeopleManagingPeople hiring guide, https://peoplemanagingpeople.com/recruitment/hiring-for-culture-fit/ | Widely used HR distinction: does a candidate match the org's existing values/mission (fit), or bring something new while still aligning with mission (add)? | Maps directly onto "Mission alignment" — the design doc's target and this literature's target ("does this org's thinking actually match mine") are the same question, framed from opposite directions (org screening candidate vs. candidate self-assessing) | adopt/reference |
| McAdams' three levels of personality (dispositional traits / characteristic adaptations / narrative identity) — Dan P. McAdams, summarized via researchgate.net/publication/233083004 and related secondary sources | Established (non-hiring) personality-psychology model splitting the self into three tiers: traits, motivated-agent adaptations, and autobiographical narrative | A different three-way split of "the self," not values/mission/perspective — useful precedent that tripartite self-models are a recognized pattern, but the axes don't line up | differentiate |
| Ikigai (4-circle) / Jim Collins' Hedgehog Concept (3-circle: passion, competence, economic engine) — Sloww comparison, https://www.sloww.co/hedgehog-concept-jim-collins/ | Overlap-diagram frameworks locating purpose/fit at the intersection of values-adjacent and mission-adjacent circles | Values and mission-alignment both appear as circles, but the mechanic (find the *overlap* of static self-descriptions) is structurally different from stimulus→reaction→reasoning, and there's no "perspective/worldview" axis | differentiate |
| Simon Sinek's Golden Circle (Why / How / What) — summarized via Lucid, https://lucid.co/blog/golden-circle | Popular org-communication model: purpose (why) → process/values (how) → output (what) | A three-part framework used for organizational mission narrative, not an individual values-elicitation interview; could inform naming/framing of "mission alignment" content but isn't itself an interview technique | differentiate |
| Gallup CliftonStrengths (34 themes / 4 domains: Executing, Influencing, Relationship Building, Strategic Thinking) — https://www.strengthsschool.com/cliftonstrengths-34themes | Proprietary, paid strengths/talent assessment, delivered via a licensed online instrument | Wrong axis entirely (talent/strengths, not values/mission/perspective) and structurally incompatible with the "no third-party assessment APIs" constraint — would require calling Gallup's hosted instrument | ignore |

**Notes.** [fact] No single existing framework uses exactly this three-way
split (values / mission-or-org-fit / perspective-or-worldview) under those
names — it's a synthesis, not a lift from any one source. [inference] The
closest structural analog is Schwartz for the "Values" leg and culture-fit/
culture-add literature for "Mission alignment" — both are worth citing by
name in the design doc as validation that these are recognized, distinct
dimensions in adjacent fields (personality psychology and hiring practice,
respectively), even though neither source groups them as a triad with a
third "perspective" leg the way this design does. "Alignment of
perspective" (reacting to others' stances) doesn't have a clean named
precedent in the sources found — the closest is the general "worldview/
values screening" language in culture-fit critique pieces, which is closer
to Q6 material than to a positive framework citation. I'd recommend the
design doc state plainly that the three-way split is original and cite
Schwartz + culture-fit/culture-add as partial precedent, rather than
implying a named, adopted framework exists.

## Q2 — The "dinner guest" device specifically

| Entry | What it is | PROFILE-BOOTSTRAP-DESIGN.md concept overlap | Verdict |
|---|---|---|---|
| Career-advice consensus on "who would you invite to dinner" as a cliché interview question — targetjobs.co.uk, https://targetjobs.co.uk/careers-advice/interviews-and-assessment-centres/if-you-could-have-dinner-five-people-who-would-it-be-and-why-tricky-graduate-interview-question; Medium (Caitlin McColl), https://medium.com/dose-of-wonder/who-would-you-invite-for-dinner-c43c6e164d83 | Practitioner/career-coaching consensus that this question is well-worn, and that interviewers explicitly discount the "who" and weight the "why" because the who is expected to be rehearsed (Gandhi/Einstein/a parent, matching the brief's own prediction) | Directly confirms the design doc's premise for needing the ask-#2-first trick — this is documented, not just an assumption | adopt/reference |
| Personal Values Card Sort (Miller & Rollnick / Motivational Interviewing Network of Trainers) — https://motivationalinterviewing.org/personal-values-card-sort-instructions; peer-reviewed application, https://pmc.ncbi.nlm.nih.gov/articles/PMC11390316/ | Validated MI/ACT technique: subject sorts a fixed deck of value-word cards into "very important / important / not important" piles, then explains the top choices | A structured, async-compatible alternative to open recall that sidesteps the rehearsed-answer failure mode by constraining the answer space (card selection) rather than open free-recall, then still requires the subject's own "why" — compatible with the evidence-before-assertion rule | adopt/reference |
| Critical Incident Technique (Flanagan, 1954; modern UX/HR applications) — Wikipedia, https://en.wikipedia.org/wiki/Critical_incident_technique; NN/g summary, https://www.nngroup.com/articles/critical-incident-technique/ | Interview technique anchoring the subject to one specific, actually-remembered incident ("tell me about a specific time...") instead of a general/hypothetical prompt | A concrete alternate phrasing that defeats rehearsal by construction: a hypothetical ("who would you have dinner with") is swappable for a memory-anchored variant ("who's a specific person whose advice actually changed a decision you made"), which is harder to pre-script and stays inside the "no invented familiarity" constraint since it forces a real memory | adopt/reference |
| Question-order/anchoring research on interview and survey responses — general finding that item position affects importance ratings (PMC5784591 and adjacent secondary sources on interview follow-ups) | General evidence that order affects response weighting in surveys and interviews | Loosely supports the *logic* behind ask-#2-first (order manipulation changes what surfaces first) but nothing found specifically validates skip-to-#2 as a rehearsed-answer countermeasure — this is a plausible mechanism, not a demonstrated one | ignore |

**Notes.** [fact] The "dinner guest" question's cliché status and its
tendency to produce safe, socially-desirable answers is well and
consistently documented across career-advice sources, which corroborates
the brief's framing rather than contradicting it. [inference] The design
doc's existing ask-#2-first trick is a reasonable, self-invented
countermeasure, but two better-evidenced alternatives exist if the design
wants a validated technique instead of a novel one: swap to a Values Card
Sort (constrained choice set beats open recall for defeating rehearsal,
tier-2/3 effort) or reframe the whole device as a Critical Incident prompt
("a specific person/moment," not "who would you invite") — both are
async-compatible, don't require a live interviewer, and stay inside the
light-touch constraint for a single-question tier-1 variant. I did not find
a study that directly tests "ask about the second answer first" as a
technique by name — it appears to be original to this design, which is
fine, but the doc shouldn't claim it as an established de-biasing method.

## Q3 — "Products you're proud to buy" as a values proxy and as a fallback

| Entry | What it is | PROFILE-BOOTSTRAP-DESIGN.md concept overlap | Verdict |
|---|---|---|---|
| Laddering / Means-End Chain interviewing (Gutman, 1982; standard marketing-research method) — UXmatters primer, https://www.uxmatters.com/mt/archives/2009/07/laddering-a-research-interview-technique-for-uncovering-core-values.php; umbrex framework summary, https://umbrex.com/resources/frameworks/marketing-frameworks/means-end-chain-laddering-framework/ | Semi-structured interview method that climbs from a concrete product attribute ("why do you like it") through consequences to the personal value underneath, via repeated "why is that important to you" probes | This *is* the rigorous version of what the fallback needs: a product choice is only a valid values proxy if you ladder past it with "why," which is exactly the design's existing step 3 ("explain why," their own words) — validates doing the fallback as a reasoning-capture step, not a raw brand-name capture | adopt/reference |
| Conspicuous consumption / brand identity-signaling research — Veblen's theory (EBSCO summary, https://www.ebsco.com/research-starters/political-science/veblens-theory-conspicuous-consumption); brand-prominence signaling studies (Wiley, https://onlinelibrary.wiley.com/doi/full/10.1002/mar.21711) | Established consumer-psychology literature documenting that purchases signal status, group identity, and aspiration at least as much as, or instead of, values | This is exactly the documented failure mode the brief asked about: "products you're proud to buy" conflates price sensitivity, aspirational purchasing, and status-signaling with genuine values — real risk, well-documented, not speculative | differentiate |
| Brand affinity / NPS as a values-alignment proxy — Brandwatch, https://www.brandwatch.com/blog/brand-affinity/; SurveyMonkey, https://www.surveymonkey.com/market-research/resources/how-to-build-and-measure-brand-affinity/ | Marketing-metrics framing where "consumers seek brands that align with their personal values" is asserted and measured via advocacy scores (NPS-style) | A business metric, not a psychometric values instrument — it measures *whether a company benefits from* claiming values alignment, not whether the claim is a valid signal of the consumer's own underlying values; thin, largely vendor-asserted | differentiate |
| No direct prior art found for "if you can't name people, name products" as a specific *fallback substitution* pattern in any values-elicitation or hiring methodology | — | — | ignore |

**Notes.** [fact] Consumer-brand affinity is a documented, real thing;
[fact] the literature is equally clear that it easily conflates status,
price, and aspiration with values, which is the documented failure mode
the brief was checking for — this fallback is not a validated
values-proxy on its own. [inference] The fix implied by the laddering
literature is that "products you're proud to buy" should never be captured
as a bare product name — it only becomes evidence once it's laddered with
"why" past the product to the value underneath, which the design doc's
mechanic already forces (step 3). That means the fallback survives the
evidence-before-assertion constraint as designed, but the design doc should
be explicit that the product name itself is never evidence, only the
laddered reasoning is — worth stating as clearly as the "one hard rule" for
the person/company variants. On the fourth row: I searched specifically for
prior art on using product/brand naming as a *substitute* when someone
can't name people (the design's stated dual-role), and found none — this
appears to be original to the design, not adapted from an existing
methodology. That's not disqualifying, just worth knowing it isn't
borrowed validation.

## Q4 — Utility beyond profile-filling

| Entry | What it is | PROFILE-BOOTSTRAP-DESIGN.md concept overlap | Verdict |
|---|---|---|---|
| Person-organization (P-O) fit meta-analyses — summarized via Wiley's "Person-organization fit theory and research," https://onlinelibrary.wiley.com/doi/10.1111/peps.12581, and turnover-focused meta-analysis coverage (SAGE/PMC, https://journals.sagepub.com/doi/10.1177/00332941231219957, https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12480616/) | Meta-analytic evidence that value congruence between person and organization predicts job satisfaction (ρ≈.44), organizational commitment (ρ≈.51), and (negatively) turnover intention (ρ≈-.14 to -.35 depending on sample) | Direct evidentiary answer to "utility beyond filling in a profile": if the values/mission-alignment data this interview captures is later used for job-search matching (as Wingman intends), the underlying values-congruence construct has well-established predictive value for fit and retention outcomes, not just "we captured some evidence" | adopt/reference |
| Structured (behavioral) interview predictive-validity meta-analyses — Schmidt & Hunter lineage and Sackett et al. corrections, summarized via multiple secondary sources (e.g. https://www.eskill.com/resources/blog/how-to-use-structured-interviews); McDaniel et al. 1994 criterion-validity meta-analysis, https://home.ubalt.edu/tmitch/645/articles/McDanieletal1994CriterionValidityInterviewsMeta.pdf | Structured interviews show substantially higher criterion validity for job performance (~.51 vs. ~.38-.42 vs. ~.19-.23 for unstructured, depending on correction method) and lower adverse impact than unstructured interviews | Supports the general design choice of *structured*, evidence-anchored elicitation (stimulus→reaction→reasoning as a fixed protocol) over free-form conversation — the "structure" itself is the thing with documented predictive payoff, independent of the values/mission/perspective content | adopt/reference |
| NHS/Health Education England Values Based Recruitment evidence review — HEE, "Summary of the evidence base for Values Based Recruitment," https://www.hee.nhs.uk/sites/default/files/documents/6.%20VBR%20evidence%20summary.pdf (full-text fetch returned HTTP 403 to this session; cited from indexed search summary only) | UK NHS-commissioned literature review concluding values-based recruitment "predicts better outcomes with long-term retention and performance," while cautioning that predictive claims require ongoing validation against actual post-hire outcomes | A sector-specific (healthcare) but directly on-point precedent: a national health system explicitly adopted structured values elicitation in hiring *because of* documented retention/performance utility beyond simple screening — directly answers Q4's "is this asserted by vendors without evidence" question with a non-vendor (government) source, though I could not verify the primary claim against the full document | adopt/reference |
| Coaching engagement / rapport as a predicted outcome of values-elicitation interviews specifically | — searched, nothing on-point found — | — | ignore |

**Notes.** [fact] The strongest, best-evidenced answer to Q4 is P-O fit:
values congruence data reliably predicts satisfaction, commitment, and
turnover at meta-analytic scale, across multiple independent studies —
this is not merely vendor-asserted. [fact] Structured-interview validity
research separately shows that *how* the elicitation is structured matters
for predictive power, which is a second, independent argument for
Wingman's stimulus→reaction→reasoning discipline specifically (vs. an
unstructured "tell me about yourself"). [inference] The coaching-engagement/
rapport angle the brief specifically named ("interviewer/interviewee
rapport") turned up nothing on-point in this pass — I did not find studies
connecting *this style* of values elicitation to coaching-relationship
quality or engagement metrics, as distinct from hiring-outcome metrics.
That's a real gap, not just an omission — see Open gaps.

## Q5 — Reacting to a company's own content as a fit signal

| Entry | What it is | PROFILE-BOOTSTRAP-DESIGN.md concept overlap | Verdict |
|---|---|---|---|
| Realistic Job Previews (RJP) research — meta-analysis of 21 RJP experiments (summarized via AIHR, https://www.aihr.com/blog/realistic-job-preview/, and Villanova's RJP literature review, https://concept.journals.villanova.edu/index.php/concept/article/download/2772/2707/10433) | Decades of I/O psychology research on giving candidates unfiltered real job/org content pre-hire, showing it lowers inflated expectations and produces self-selection that reduces early turnover | Structurally the closest validated analog to the design's company-alignment variant: candidates reacting honestly to an org's *real* content (not a curated pitch) produces better fit outcomes than resume/keyword matching alone — though RJP is classically employer-authored content shown to the candidate, not candidate-chosen content reacted to, so the direction is inverted from this design's mechanic | adopt/reference |
| Embedding-based resume/job matching systems (e.g. Resume2Vec) — MDPI paper, https://www.mdpi.com/2079-9292/14/4/794 | Recent applied-ML research on neural-embedding resume-to-job matching, explicitly noting embedding-only approaches "lack precision" and can miss hard constraints that keyword/ATS methods catch | Useful counter-evidence for the specific comparison Q5 asks for: current embedding-similarity approaches (the class of technique Wingman's existing `company_alignment()` belongs to) have documented precision gaps, supporting the design doc's framing of direct reaction as a complementary alternative rather than a strictly worse or redundant path | differentiate |
| Practitioner "mission-fit interview" advice warning against reciting a company's mission statement back verbatim — Impact Opportunity, https://impactopportunity.org/blog/mastering-the-mission-fit-interview-how-to-show-real-alignment-without-sounding-like-a-brochure/ | Career-coaching content coaching candidates to anchor mission-alignment claims to specific personal history rather than parroting the company's own language | Names the gaming risk directly relevant to this module (a candidate could "read the company's content and echo it back" to game a fit score) — practitioner-level, not empirical, but a real and specific warning that the design's reasoning-must-be-the-user's-own-words rule already defends against | differentiate |
| Direct academic study comparing "candidate reacts to a company's own public content" against algorithmic/embedding similarity scoring on validity, gameability, and bias | — searched, nothing on-point found — | — | ignore |

**Notes.** [fact] No literature directly evaluates the specific comparison
the brief asks for (structured candidate-reaction-to-company-content vs.
embedding similarity, on validity/gaming/bias) — this is a genuine gap,
likely because "have the candidate react to real company content and grade
the reasoning" is closer to Wingman's own novel mechanic than to any
existing named technique. [inference] The two adjacent literatures both
point the same direction, though: RJP research shows unfiltered real
content beats curated pitches for producing accurate fit decisions, and
resume-matching research shows current embedding approaches have known
precision limits — together these support treating the company-alignment
variant as a legitimate complement to `company_alignment()`, not
redundant with it, which is what the design doc already claims (Goals:
"an alternative to indirect embedding-based alignment"). On gaming: since
the design already requires the reasoning to be the user's own words and
never a quote of the submitted content, the specific "recite the mission
statement" gaming risk named by the practitioner source is structurally
blocked already — worth noting in the design doc as a deliberate defense,
not just an incidental one.

## Q6 — Bias and fairness pitfalls specific to this elicitation style

| Entry | What it is | PROFILE-BOOTSTRAP-DESIGN.md concept overlap | Verdict |
|---|---|---|---|
| Social desirability bias in job/personality interviews — overview via ScienceDirect, https://www.sciencedirect.com/topics/psychology/social-desirability-bias; applied hiring context via Prevue HR, https://www.prevuehr.com/resources/insights/real-vs-fake-how-to-combat-social-desirability-in-hiring/ | Well-documented tendency (cited figure: ~30% of interview responses show socially-desirable distortion) for subjects to answer in ways they believe will be favorably judged | Applies to all three modules — "three people," "products you're proud to buy," and especially agree/disagree pairs are all vulnerable to answering toward a perceived-audience norm rather than genuine belief | adopt/reference |
| Acquiescence bias / "yea-saying" on agree-disagree response formats — Wikipedia, https://en.wikipedia.org/wiki/Acquiescence_bias; Springer, "Acquiescence Bias and Criterion Validity," https://link.springer.com/article/10.1007/s11109-026-10124-z | Documented tendency to default toward "agree" independent of actual content, specifically tied to the agree/disagree item format (typical effect size 10-15%) | Directly names a format-specific risk in the design's *core loop* (3 agree / 3 disagree pairs) — this isn't a generic interview-bias caveat, it's specific to the exact response format this design uses most | adopt/reference |
| Values-based/culture-fit hiring's homogeneity risk — CultureAlly, https://www.cultureally.com/blog/culture-add-vs-culture-fit; general culture-fit critique literature | Documented pattern where fit-based screening self-replicates existing team composition because "fit" implicitly means "reminds evaluators of people already there" | Names the exact risk the brief asked about ("values-based screening amplifying interviewer/organizational homogeneity") — this is about downstream *use* of the data (matching), not the elicitation step itself, but it's the mechanism by which this design's output could cause harm even if collected fairly | adopt/reference |
| ACLU model-card analysis of personality/values-construct hiring assessments (e.g. ADEPT-15) — https://assets.aclu.org/live/uploads/2024/10/Model-Cards-for-gridChallenge-ADEPT-15-and-vidAssess.pdf | Documented finding that personality/values-construct assessment tools carry elevated risk of screening out autistic and other neurodivergent candidates, because the constructs measured overlap with traits associated with those conditions | Concrete, sourced example of exactly the "fairness pitfall specific to this elicitation style" the brief asked to name — a values/perspective self-report instrument, even collected in good faith, can encode disability-correlated response patterns as if they were values differences | adopt/reference |
| Cultural specificity/individualism bias in personal-preference interview questions — general cross-cultural psychology literature (e.g. https://www.psychstory.co.uk/debates/cultural-bias-in-psychology) | Established finding that Western psychological research and interview conventions over-weight individual-choice framings, which can misrepresent or disadvantage respondents from more collectivist cultural backgrounds | [inference] extension, not a direct study: nothing found specifically testing "who would you have dinner with" or "three people" style prompts for cultural specificity, but the underlying critique (an individual-choice, name-specific-people prompt format assumes a Western frame) plausibly applies to this device by the same logic documented for other personal-preference interview questions | differentiate |

**Notes.** [fact] The strongest, most directly-actionable finding here is
acquiescence bias on the agree/disagree format, because it's not a general
interview caveat — it's a documented distortion specific to the exact
response shape the design's core loop uses, meaning some fraction of "3
agree / 3 disagree" answers will reflect format-driven defaulting rather
than genuine stance, independent of social desirability. [fact] The ACLU
model-card finding is the most concrete evidence that "collect values/
perspective self-report and feed it into a hiring-adjacent matching
system" is a documented disability-discrimination risk pathway in
practice, not merely theoretical — directly relevant since this data
"eventually informs job-search matching" per the brief's framing. This
doesn't mean the technique should be abandoned (per the constraints
section, this is a risk surface to name, not solve here), but the design
doc's eventual matching-integration work should treat values/perspective
signal as informative-but-not-scoring-eligible for anything
disability-adjacent, the same caution ADEPT-15's critics raise. The
cultural-specificity row is the one inference-heavy entry in this table —
worth a caveat that it's extrapolated from adjacent literature, not a
study of this exact device.

## Open gaps

- **Q1** — no source found that names or structures a values / mission-
  fit / perspective-or-worldview split exactly as this design proposes it;
  the design doc should present the three-way split as original synthesis,
  not as adopted from a named existing framework.
- **Q2** — no study found that directly tests the design's specific
  ask-#2-first ordering trick as a rehearsed-answer countermeasure (only
  general question-order research, which I judged too loosely related to
  cite as support — see the `ignore` row). The dinner-guest cliché itself
  is well-documented; the specific fix is not independently validated.
- **Q3** — no prior art found for the specific "can't name people → name
  products instead" fallback-substitution pattern, in career coaching,
  hiring, or brand research. I searched directly for it (branding-workshop
  and "if you can't think of anyone" queries) and found only loosely
  adjacent material (branding-workshop exercises, laddering method) — this
  appears to be original to the design.
- **Q4** — the "coaching engagement / rapport" half of Q4's question (does
  this style of values elicitation *itself*, independent of hiring
  outcomes, predict interviewer/interviewee rapport or coaching-relationship
  quality) turned up nothing on-point; the P-O-fit and structured-interview
  evidence I found both speak to downstream hiring/matching outcomes, not
  to rapport/engagement as its own predicted variable.
- **Q5** — no direct empirical study compares "candidate reacts to a
  company's own content" against embedding-based similarity scoring on
  validity, gameability, or bias specifically — I found strong adjacent
  literature (RJP research, embedding-matching limitations) and reasoned
  by analogy rather than citing a direct comparison, because none exists
  in what I could find.
- **Access limitations** — several primary/institutional sources (the NHS/
  HEE Values Based Recruitment evidence-summary and full literature-review
  PDFs, the Wikipedia pages for Schwartz's theory and acquiescence bias,
  OPM's structured-interviews page, and Equalture's values-based-
  recruitment blog post) returned HTTP 403 to this session's fetch tool on
  every attempt, despite the underlying pages being real, live, and
  publicly indexed (confirmed via search-result snippets). Those citations
  above rest on search-engine-indexed summaries of those pages rather than
  a full-text read by me; I was not able to confirm whether the 403s were
  site-side bot-blocking or a transient proxy issue on my end, and ran out
  of productive alternate routes (no cache/archive fetch attempted) within
  scope of this pass.
- **Q6** — the cultural-specificity claim for "dinner guest"-style
  prompts specifically is an inference from general individualism/
  collectivism bias literature, not a source that tests this exact device;
  flagged as `differentiate` rather than `adopt/reference` for that reason.
