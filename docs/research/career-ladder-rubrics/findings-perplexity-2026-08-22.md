# Career ladder rubrics: what is actually published, and under what terms

**Status.** Findings, 2026-08-22. Commissioned to resolve Q1 of
[`../QUESTION-BLOCKS-DESIGN.md`](../QUESTION-BLOCKS-DESIGN.md) — where
rubric content comes from and how its provenance stays honest.

**Source.** Perplexity, run against a brief asking specifically for
first-party vs. reconstruction vs. leaked provenance, dimension
convergence, and redistribution terms. Kept verbatim below, including its
own citations.

**Independently verified.** One claim carries the whole resolution and was
checked at the primary source rather than taken on trust:
`github.com/dropbox/dbx-career-framework` is owned by Dropbox, Inc. and
licensed **Apache-2.0**, and its README describes the `docs` directory as
the public version of the internal `drl/eng-career` framework.

**NOT verified.** The CC BY / CC BY-SA claims for Pleo, Inviqa, Medium and
InfraCloud, and the absence of a reuse licence on GitLab's handbook. Treat
these as leads, not conclusions — and note the finding below that a public
catalogue listed Dropbox as unlicensed when the Dropbox-owned repository is
Apache-2.0. Catalogue licence metadata that is wrong about a checkable case
should not be trusted for the rest. Verify at the primary source before any
rubric carries one of these frameworks' text.

---

For a citation-grade tool, treat **Dropbox and GitLab as primary-source frameworks**, and treat the “Big Six” named in Q1 as **not publicly published in full** unless you are explicitly storing a reconstruction or an unauthorized leak as a separate provenance class. I found no first-party, level-by-level engineering ladder for Google, Meta, Amazon, Apple, Netflix, or Microsoft; public job listings and culture pages are not equivalent to a career framework. The Meta and Netflix pages below provide limited first-party context, but not complete promotion/leveling criteria.[^1][^2]

## 1. Publication status

| Company | First-party engineering ladder / criteria? | What first-party material actually says | Reconstruction | Leaked material | Safe conclusion |
| :-- | :-- | :-- | :-- | :-- | :-- |
| Google | **No first-party source found.** | Google Careers publishes role-specific minimum/preferred qualifications, but I found no official public, level-by-level software-engineering expectations or promotion rubric. [^3] | Commonly circulated level mappings and expectations are third-party reconstructions. | Internal leveling/promotion material may circulate online, but it is not company-authorized publication. | **No first-party public engineering ladder located.** Do not cite Google reconstructions as Google policy. |
| Meta | **No full first-party ladder found.** | Meta has publicly said careers are measured by “larger scope” and “real impact,” but this is a cultural statement, not a level-by-level framework or calibrated rubric. [^1] | Levels.fyi, Blind discussions, recruiter guides, and ex-employee accounts reconstruct levels/expectations. | Internal career matrices sometimes circulate without authorization. | **No first-party public engineering ladder located.** The scope/impact statement is citeable, but insufficient for a ladder. |
| Amazon | **No first-party ladder found.** | Amazon Jobs publishes position-specific qualifications, such as years of experience and architecture/design experience for SDE roles; those postings do not publish a cross-level promotion framework. [^4][^5] | Third-party “Amazon engineering ladder” matrices exist, including a Progression entry, but are not Amazon publication. [^6] | Some internal documents may circulate, but they are not authorized sources. | **No first-party public engineering ladder located.** |
| Apple | **No first-party ladder found.** | Apple’s careers pages identify disciplines and openings, for example software/services and hardware work areas; they do not provide level expectations or a promotion rubric. [^7][^8] | Recruiter, ex-employee, and compensation-site level mappings are reconstructions. | Unauthorized internal material is not a usable first-party publication. | **No first-party public engineering ladder located.** |
| Netflix | **No first-party engineering ladder found.** | Netflix’s own work-life philosophy says the company does not use “set bands and grades” to define its talent market; that is evidence against assuming a conventional public ladder, not evidence of no internal expectations whatsoever. [^2] | Third-party descriptions of Netflix seniority/compensation are reconstructions. | Any circulating internal documents are not authorized publication. | **No first-party public engineering ladder located.** |
| Microsoft | **No first-party ladder found.** | Microsoft careers expose job titles such as Software Engineer II and Senior Software Engineer, while Microsoft Learn offers training “career paths”; neither is a public company leveling rubric. [^9][^10] | Widely cited level mappings and “62–67” style ladders are third-party reconstructions. | Internal level guides may circulate without authorization. | **No first-party public engineering ladder located.** |

### Provenance rule for your product

Use a hard source-type field, not a single “published” boolean:

- **First-party public:** the employer published the framework itself, in an official handbook, company-controlled GitHub organization, career site, or official engineering publication.
- **Third-party reconstruction:** someone assembled it from job postings, interviews, compensation data, or experience. It may be useful, but should never be represented as the employer’s rubric.
- **Leaked / unauthorized:** even if authentic, label it unauthorized and avoid redistributing it; it is neither a public framework nor a safe text corpus.
- **Unknown:** use this when you cannot establish provenance. That is the appropriate status for many familiar Big-Tech ladders.


## 2. Genuinely public frameworks

These are materially different from an article saying “Company X has levels.” Each links to a primary document or company-controlled source.


| Organization | Actual primary framework | Coverage and detail | Reuse status |
| :-- | :-- | :-- | :-- |
| Dropbox | [Engineering Career Framework](https://dropbox.github.io/dbx-career-framework/) and its [official GitHub repository](https://github.com/dropbox/dbx-career-framework) | Full public framework, including IC and management paths, role-specific craft expectations, level expectations, and behavioral responsibilities. The site describes level expectations as the “what” and core/craft responsibilities as the “how.” [^11] | Apache License 2.0 in the Dropbox-owned repository. [^12] |
| GitLab | [Engineering Career Framework](https://handbook.gitlab.com/handbook/engineering/careers/matrix/) | Public handbook matrix with engineering competencies by job title; job-family pages cover staff and distinguished engineering as well as numerous adjacent roles. [^13][^14] | GitLab’s handbook pages should be treated as copyrighted web content unless the relevant page/repository grants reuse rights. I did not verify a framework-specific permissive content license in the retrieved sources; link and quote limited excerpts rather than bulk-copy. |
| InfraCloud | [Career Ladders site](https://career-ladders.infracloud.io/docs/) / [source repository](https://github.com/infracloudio/career-ladders) | Company-published website source, with the framework kept in a public repository. The result confirms this is the source for its Career Ladders website. [^15] | CC BY 4.0. Attribution required; commercial reuse and adaptation are permitted subject to the license. [^15] |
| Pleo | [Engineering Career Pathways](https://progression.fyi/f/pleo) | Public company framework for engineering pathways, catalogued as a 2022 Copenhagen framework. The retrieval establishes the framework and its license, but not enough detail to characterize every criterion precisely. [^16] | CC BY-SA 4.0: attribution plus ShareAlike for adaptations. [^16] |
| Inviqa | [Engineering Progression Framework](https://progression.fyi/f/inviqa) | Levels 2–6, from Engineer 1 through Principal Engineer, across five skill areas. [^17] | CC BY-SA 4.0. [^17] |
| Medium | [Engineering Growth Framework](https://progression.fyi/f/medium) | Public set of assessment tools, posts, and frameworks described by the catalogue as unusually in-depth. [^18] | CC BY-SA 4.0. [^18] |
| Healx | [Career Growth Framework](https://progression.fyi/f/healx) | Cross-functional framework spanning Engineering, Data, and R\&D IC pathways. [^19] | No license specified in the catalogue: link to it; do not assume redistribution permission. [^19] |
| Carta | [Engineering levels](https://progression.fyi/f/carta) | Eight full-time engineering levels plus an intern level. [^20] | No license specified: link/limited quotation only unless Carta grants permission elsewhere. [^20] |
| Khan Academy | [Engineering Career Development](https://progression.fyi/f/khan-academy) | Compact public model organized around **Skills, Scope, and Experience**. [^21] | I did not retrieve a primary-license statement; do not assume broad text reuse. |
| CircleCI | [Engineering Competency Matrix](https://progression.fyi/f/circle-ci) | Six-level engineering-focused framework. [^22] | License not verified from the retrieved record; use as linked source unless separately confirmed. |

A key caveat: **Progression.fyi is a catalogue, not proof that every listed artifact is first-party.** It usefully preserves licensing metadata and links, but the authoritative source should be the employer-controlled original where available. Its own index distinguishes, for example, CC BY-SA frameworks from Dropbox’s prior “no license specified” listing.[^23]

## 3. Dimensions and convergence

Dropbox is unusually explicit. Its level expectations use:

- **Scope** — “Area of ownership and level of autonomy / ambiguity.”
- **Collaborative Reach** — “Organizational reach and extent of influence.”
- **Impact Levers** — “Technical levers typically exercised to achieve business impact.”[^24]

Its behavioral/Core dimensions are **Results, Direction, Talent, Culture**, and its per-role technical component is **Craft**.[^11]

GitLab’s public engineering materials do not present the identical three-axis schema in the retrieved engineering matrix summary, but they repeatedly assess scope/complexity, organizational influence, impact, technical expertise, and role-specific competencies. A GitLab Principal Engineer is expected to solve problems of the “highest scope, complexity, and ambiguity” for a sub-department, while a Distinguished Engineer must have measurable impact on teams across the company and a “wide sphere of influence.”[^14][^25]

A particularly clear GitLab cross-functional example is its public TPM framework, whose explicit axes are: **Ambiguity; Scope \& Influence; Execution; Communication; Impact; Tech Expertise; Problem Solving**.  This is not an engineering-IC ladder, but it strongly supports that the dimensions form a reusable job-framework structure rather than an engineering-only construct.[^26]

Your proposed five are therefore mostly real, but need careful normalization:


| Proposed dimension | Supported as a distinct public dimension? | Best-supported wording |
| :-- | :-- | :-- |
| Scope of impact | **Yes.** | Dropbox separates Scope, Collaborative Reach, and business impact; GitLab separately states scope and outcomes in its PM ladder. [^24][^27] |
| Autonomy / direction-setting | **Yes.** | Dropbox embeds autonomy in Scope and has a Direction pillar; GitLab’s TPM ladder moves from guidance to independently defining problems and setting direction. [^24][^26] |
| Ambiguity absorbed | **Yes.** | Explicit in Dropbox Scope and GitLab’s Ambiguity axis. [^24][^26] |
| Technical depth | **Yes, but not universal as a standalone axis.** | Dropbox calls it Craft and names Domain Expertise as an impact lever; GitLab uses technical expertise and specialized technical competencies. [^24][^28] |
| Influence on others | **Yes.** | Dropbox calls it Collaborative Reach; GitLab uses Scope \& Influence and explicitly includes mentoring/knowledge sharing in SRE contribution areas. [^24][^26][^28] |

The important correction is that “scope of impact” collapses at least two concepts in Dropbox: **scope/reach** and **business impact**. Your model should avoid combining them into one score.

## 4. The three hard-to-evidence dimensions

### Handling ambiguity

Dropbox defines ambiguity as part of **Scope**, not as a vague seniority proxy. At Staff level, it expects engineers to tackle “open-ended problems that require difficult prioritization,” including “defining both the what and how of things to be done.” It also expects them to “navigate ambiguity by focusing on the greater purpose, goals, and desired impact to move forward one step at a time.”[^24]

At Principal, the test becomes more technical and strategic: Dropbox expects systems requiring research into what is possible, with a “significant portion of the challenge” being creation of an appropriately staged validation plan; it also expects technical choices with “no one clearly correct answer.”[^29]

GitLab’s TPM framework makes progression highly concrete: Junior work has well-defined tasks; senior levels “bring order to chaos”; Staff defines problems and solutions independently; Senior Staff “thrives in ambiguity” and “sets direction”; Principal defines strategy under “extreme ambiguity” and “creates clarity.”[^26]

**Evidence your tool should request:**

- Original problem statement showing what was unknown, conflicting, or unbounded.
- The framing process: hypotheses, alternatives, trade-off criteria, staged experiments, validation plan, and risk register.
- Evidence that the person chose a path amid conflict or incomplete information, rather than merely delivered a pre-specified plan.
- Resulting decision, learning, and downstream change in roadmap, architecture, or operating practice.


### Autonomy and choosing the problem

Dropbox’s Staff descriptor explicitly requires “defining both the what and how.” That is a stronger claim than independently implementing a manager-assigned project.[^24]

It also expects Staff engineers to “identify and execute on opportunities that have area/group-wide impact” and proactively refocus a team when work is not moving business or customer goals.  At Principal, the requirement expands to identifying significant company/group opportunities and working with business owners to build the right business roadmap.[^29][^24]

GitLab’s public TPM rubric expresses the progression in equally operational language: an entry-level TPM follows existing processes and escalates blockers; a Staff TPM defines problems/solutions independently; a Principal TPM defines strategy and creates clarity in extreme ambiguity.[^26]

**Evidence your tool should request:**

- A before/after showing that the candidate identified the opportunity rather than inherited a ticket.
- Decision records showing problem selection, rejected alternatives, opportunity cost, and ownership boundaries.
- Sponsor/stakeholder evidence that the engineer changed priorities, scope, or roadmap.
- Measurable customer, reliability, cost, or delivery outcome tied to that chosen problem.


### Influence and multiplier effect

Dropbox separates influence from impact. Its Staff-level **Collaborative Reach** requires influencing other teams’ roadmaps and favoring wider engineering priorities over locally optimal outcomes.  Its **Talent** expectations include coaching/mentoring, spreading knowledge through talks, blog posts, or documentation, and helping calibrate or hire senior talent.[^24]

At Principal, Dropbox expects an engineer to influence group technical strategy, partner with directors and senior leadership, “transcend organizational boundaries,” and “rally” the organization behind major technical choices using rationale and vision.[^29]

GitLab’s Distinguished Engineer description similarly requires a track record of “growing and influencing others,” measurable impact on teams across the company, and a “wide sphere of influence.”  GitLab’s SRE materials explicitly name knowledge sharing and mentoring under “Influence and Maturity.”[^28][^14]

**Evidence your tool should request:**

- Adoption data: teams using a platform, standard, API, design pattern, process, or documentation the person introduced.
- Artifacts that made others faster or safer: reusable systems, templates, RFCs, libraries, tooling, training, operational practices.
- Named stakeholders and evidence of changed decisions/roadmaps, especially without direct managerial authority.
- Growth evidence: mentoring outcomes, technical leadership developed in others, hiring/calibration contribution, and evidence that impact persisted without the candidate’s constant direct intervention.

Do not infer multiplier effect from audience size alone. A presentation is weak evidence unless it changed adoption, behavior, decisions, or capability.

## 5. Scope versus magnitude

Your distinction is explicitly supported by Dropbox’s public framework.

Dropbox labels **Collaborative Reach** as “organizational reach and extent of influence,” while **Impact Levers** are the technical means used to achieve business impact.  Separately, its impact guidance says career growth is anchored on **business impact**, a function of “scope of impact” and execution; it gives outcomes such as reducing costs, proving out a solution, or achieving learning goals.[^30][^24]

That means these are separately assessable claims:

- “Multiple teams changed what they do” is evidence of **collaborative reach / organizational influence**.
- “A system processed \$100B” is potentially evidence of **business magnitude**, but only after attribution is established: what changed because of this engineer, what counterfactual existed, and whether the business volume reflects the engineer’s work rather than the pre-existing product.

Dropbox does not appear, in the retrieved material, to use your exact phrase “organizational reach versus outcome size.” So that exact formulation is **your inference**, not a quotation. But the underlying separation is not invented: Dropbox’s framework intentionally puts organizational reach/influence in one category and business impact/levers in another.[^30][^24]

GitLab’s PM ladder makes the split especially explicit even outside engineering: **Scope** is defined in team reach—single group, 1–2 teams, 3–5 teams, then 5+ teams—while **Outcomes** are separately stated as launches/metrics, material acquisition/adoption/retention improvement, business impact, or durable competitive advantage/ARR/margin expansion.  This is strong evidence that a framework of this kind should represent reach and magnitude as separate fields.[^27]

## 6. Redistribution and licensing

For software-tool ingestion, separate **metadata**, **short attributed excerpts**, and **full-text redistribution**.


| Framework | What the retrieved source supports | Practical tool treatment |
| :-- | :-- | :-- |
| Dropbox | The official repository says its contents are Apache License 2.0 unless otherwise noted. [^12] | You can redistribute/adapt the repository content subject to Apache 2.0 requirements, including retaining license/notices. Preserve source URL, version, file path, and attribution. |
| InfraCloud | Its public repository is CC BY 4.0. [^15] | Redistribution/adaptation is permitted with attribution; identify modifications. |
| Pleo | Catalogue states CC BY-SA 4.0. [^16] | You may reuse/adapt subject to attribution and ShareAlike. If your tool distributes adapted framework text, carefully assess whether its output/distributed dataset is an Adapted Material under CC BY-SA. |
| Inviqa | Catalogue states CC BY-SA 4.0. [^17] | Same attribution + ShareAlike caution. |
| Medium | Catalogue states CC BY-SA 4.0. [^18] | Same attribution + ShareAlike caution. |
| GitLab | No framework-specific reuse license was verified in this research. [^13] | Default to link plus short quotation/fair-use analysis appropriate to jurisdiction; do not bulk copy into your product without confirming rights. |
| Healx, Carta, Dropbox as displayed in Progression | The catalogue indicates “No license specified” for these listed records. [^23][^20][^19] | “Publicly accessible” is not “licensed for redistribution.” For Dropbox, rely on the official Apache repository rather than the catalogue listing. For the others, link rather than reproduce unless permission is obtained. |

This is operational guidance, not legal advice. For a commercial product, retain the original license text, source snapshot/version, author/company attribution, and a source/provenance record per descriptor.

## 7. Non-engineering frameworks

Yes. The same broad structure can work for data, product, and design, but the **craft evidence and outcome types must be role-specific**.

- **Data / ML:** Healx publicly includes Engineering, Data, and R\&D individual-contributor pathways, making it a useful first-party example of a shared framework extending beyond software engineering.  GitLab also lists an ML engineering family spanning Associate through Staff roles.[^19][^31]
- **Product:** GitLab has a public product-manager ladder with separate columns for **Scope**, **Outcomes**, time horizon, and level progression from a focused feature set for one group through company-level platforms/business lines across 5+ teams.[^27]
- **Design:** GitLab publicly documents Product Designer and Distinguished Product Designer roles. Its description positions product designers at the source of product decisions “where direction gets set,” while Distinguished Product Designers are strategic design leaders who shape product direction.[^32]
- **Program management:** GitLab’s TPM framework is the cleanest shared-axis example: ambiguity, scope/influence, execution, communication, impact, technical expertise, and problem solving.[^26]

The transferable meta-model is therefore plausible:

$$
\text{Level evidence} =
\{\text{reach},\ \text{outcome magnitude},\ \text{autonomy/ambiguity},\ \text{domain craft},\ \text{influence/multiplier}\}
$$

But do not apply a single engineering rubric verbatim. For example, “technical depth” for a Product Manager should likely become product judgment, market/customer understanding, and decision quality; for design, it should become research/design craft, systems thinking, and product-direction shaping. GitLab’s PM scope/outcome separation is evidence for this structural reuse, while its product/design job families show that the craft layer changes by discipline.[^32][^27]

For your system, I would store each framework as: `publisher`, `source_class`, `source_url`, `license`, `framework_version/date`, `role_family`, `axis_name`, `axis_definition`, `level_descriptor`, `verbatim_quote`, and `evidence_artifact_types`. That design prevents an unauthorized reconstruction from silently becoming “Google’s ladder,” and preserves the distinction between reach, magnitude, autonomy, ambiguity, craft, and multiplier effects.

<div align="center">⁂</div>

[^1]: https://www.metacareers.com/blog/why-your-career-wont-plateau-at-facebook/

[^2]: https://jobs.netflix.com/work-life-philosophy

[^3]: https://careers.google.com/jobs/results/97762067657695942-data-center-plant-engineer/

[^4]: https://amazon.jobs/jobs/3187761

[^5]: https://www.amazon.jobs/en/jobs/10466814/software-development-engineer-ii

[^6]: https://progression.fyi/f/amazon

[^7]: https://www.apple.com/careers/us/work-at-apple/teams/hardware.html

[^8]: https://www.apple.com/careers/us/work-at-apple/teams/software-and-services.html

[^9]: https://careers.microsoft.com/professionals/us/en/l-vancouver

[^10]: https://learn.microsoft.com/en-us/training/career-paths/developer

[^11]: https://dropbox.github.io/dbx-career-framework/

[^12]: https://github.com/dropbox/dbx-career-framework

[^13]: https://handbook.gitlab.com/handbook/engineering/careers/matrix/

[^14]: https://handbook.gitlab.com/job-description-library/engineering/development/management/distinguished/

[^15]: https://github.com/infracloudio/career-ladders

[^16]: https://progression.fyi/f/pleo

[^17]: https://progression.fyi/f/inviqa

[^18]: https://progression.fyi/f/medium

[^19]: https://progression.fyi/f/healx

[^20]: https://progression.fyi/f/carta

[^21]: https://progression.fyi/f/khan-academy

[^22]: https://progression.fyi/f/circle-ci

[^23]: https://progression.fyi/

[^24]: https://dropbox.github.io/dbx-career-framework/ic5_staff_software_engineer.html

[^25]: https://handbook.gitlab.com/job-description-library/engineering/development/management/principal-engineer/

[^26]: https://handbook.gitlab.com/job-description-library/engineering/technical-program-management/

[^27]: https://handbook.gitlab.com/job-description-library/product/product-manager/

[^28]: https://handbook.gitlab.com/job-description-library/engineering/infrastructure/site-reliability-engineer/

[^29]: https://dropbox.github.io/dbx-career-framework/ic6_principal_software_engineer.html

[^30]: https://dropbox.github.io/dbx-career-framework/what_is_impact.html

[^31]: https://handbook.gitlab.com/job-description-library/engineering/development/data-science/machine-learning/

[^32]: https://handbook.gitlab.com/job-description-library/product/product-designer/

[^33]: https://dropbox.tech/culture/our-updated-engineering-career-framework

[^34]: https://github.com/bmoeskau/engineering-ladders

[^35]: https://github.com/jorgef/engineeringladders

[^36]: https://github.com/dropbox/dbx-career-framework/security

[^37]: https://github.com/orgs/community/discussions/51386

[^38]: https://www.reddit.com/r/ExperiencedDevs/comments/oj94lm/dropbox_shares_their_engineering_career_framework/

[^39]: https://progression.fyi/f/etsy

[^40]: https://progression.fyi/f/dropbox

[^41]: https://swyx.io/writing/career-ladders

[^42]: https://progression.fyi/f/gitlab

[^43]: https://dropbox.github.io/dbx-career-framework/cr_clarifications_and_myths.html

[^44]: https://scalingfunds.notion.site/Growth-Frameworks-for-Engineers-63caaa58edcb41c0ba28a0cf929f24db

[^45]: https://about.google/belonging/diversity-annual-report/2024/

[^46]: https://careers.google.com/jobs/results/110758776134345414-associate-data-center-facilities-technician/

[^47]: https://www.metacareers.com/

[^48]: https://www.metacareers.com/blog/life-at-facebook-as-an-engineering-manager/

[^49]: https://www.metacareers.com/profile/job_details/1187958502273564/

[^50]: https://www.metacareers.com/teams/technology/software-engineering/

[^51]: https://www.metacareers.com/profile/job_details/2282367178830920/

[^52]: https://www.metacareers.com/profile/job_details/978903851762450/

[^53]: https://www.metacareers.com/profile/job_details/2100171950572222/

[^54]: https://www.metacareers.com/profile/job_details/1075389717795087/

[^55]: https://www.metacareers.com/profile/job_details/1512065736047495/

[^56]: https://aws.amazon.com/careers/building-careers-in-the-aws-cloud-no-tech-experience-required/

[^57]: https://aws.amazon.com/blogs/training-and-certification/zero-to-hero/

[^58]: https://aws.amazon.com/careers/life-at-aws-from-tpm-to-technical-advisor-how-amazons-culture-fueled-my-nontraditional-career-journey/

[^59]: https://aws.amazon.com/careers/life-at-aws-helping-early-career-professionals-open-new-doors/

[^60]: https://aws.amazon.com/blogs/training-and-certification/reimagining-entry-level-tech-careers-in-the-ai-era/

[^61]: https://podcasts.apple.com/us/podcast/engineering-success-the-engineering-career-podcast/id1569193027

[^62]: https://www.amazon.jobs/en/jobs/3177934/software-development-engineer-2026-us

[^63]: https://podcasts.apple.com/us/podcast/the-happy-engineer-career-success-for-engineering/id1576582987?l=ru

[^64]: https://www.amazon.jobs/en/jobs/10408763/software-development-engineer-2026

[^65]: https://podcasts.apple.com/us/podcast/soft-skills-engineering/id1091341048

[^66]: https://amazon.jobs/en/jobs/10442900/senior-software-development-engineer-p-s-engineering-services

[^67]: https://careers.microsoft.com/v2/global/en/locations/bengaluru.html

[^68]: https://jobs.netflix.com/careers/new-grads

[^69]: https://careers.microsoft.com/v2/global/en/locations/bay-area.html

[^70]: https://careers.microsoft.com/professionals/us/en/l-atlanta

[^71]: https://jobs.netflix.com/careers/product

[^72]: https://careers.microsoft.com/v2/global/en/datacenters.html

[^73]: https://jobs.netflix.com/locations/london

[^74]: https://careers.microsoft.com/v2/global/en/professions.html

[^75]: https://learn.microsoft.com/en-us/training/career-paths/ai-engineer

[^76]: https://jobs.netflix.com/locations/mumbai

[^77]: https://careers.microsoft.com/professionals/us/en/military

[^78]: https://leap.microsoft.com/en-US/pathways/engineering/software-engineer/

[^79]: https://handbook.gitlab.com/job-description-library/marketing/product-manager-marketing/

[^80]: https://handbook.gitlab.com/job-description-library/marketing/product-designer-ux-marketing/

[^81]: https://handbook.gitlab.com/job-description-library/product/product-design-management/

[^82]: https://handbook.gitlab.com/job-description-library/sales/professional-services-engineer/

[^83]: https://handbook.gitlab.com/job-description-library/engineering/development/management/senior-manager/

[^84]: https://handbook.gitlab.com/job-description-library/marketing/digital-experience/

[^85]: https://handbook.gitlab.com/job-description-library/engineering/engineering-management/

[^86]: https://handbook.gitlab.com/job-description-library/product/service-designer/

[^87]: https://handbook.gitlab.com/job-description-library/product/ux-researcher/

[^88]: https://progression.fyi/f/meetup

[^89]: https://progression.fyi/f/brandwatch

[^90]: https://progression.fyi/f/jorge-fioranelli

[^91]: https://progression.fyi/f/sarah-drasner

[^92]: https://progression.fyi/f/liefery

[^93]: https://www.education.nh.gov/sites/g/files/ehbemt326/files/inline-documents/sonh/state-board-meeting-materials-compact-10-08-20.pdf

[^94]: https://www.deel.com/blog/career-progression-examples/

[^95]: https://www.deel.com/blog/develop-career-progression-framework/

[^96]: https://tessl.io/registry/testland/career-ladder-author

[^97]: https://andrewmurphy.io/stdlib/d047b65d-bcee-4d61-bf39-b09861aa22f8

[^98]: https://habr.com/ru/articles/932932/

[^99]: https://theartofcto.com/guides/engineering-career-ladder-builder-guide

[^100]: https://jobs.correlationvc.com/companies/reclaim-ai/jobs/65442183-staff-infrastructure-software-engineer-metadata

[^101]: https://crackingwalnuts.com/sitemap

[^102]: https://www.techprep.app/blog/dropbox-interview-process

[^103]: https://prachub.com/resources/software-engineer-hiring-manager-interview-questions-signals-and-seniority

[^104]: https://news.ycombinator.com/item?id=27817519

[^105]: https://handbook.gitlab.com/job-description-library/finance/integrations-engineer/

[^106]: https://handbook.gitlab.com/handbook/engineering/careers/matrix/staff/

[^107]: https://handbook.gitlab.com/job-description-library/security/security-engineer/

[^108]: https://handbook.gitlab.com/handbook/engineering/careers/matrix/senior-staff/

[^109]: https://handbook.gitlab.com/job-description-library/engineering/

[^110]: https://handbook.gitlab.com/job-description-library/marketing/fullstack-engineer-marketing/

[^111]: https://handbook.gitlab.com/job-description-library/engineering/support-engineer/

[^112]: https://handbook.gitlab.com/job-description-library/marketing/integrated-marketing/

[^113]: https://blog.worktugal.com/gitlab-jobs-europe-2026/

[^114]: https://builtin.com/articles/shipping-code-and-career-growth-engineers-journey-gitlab

[^115]: https://arxiv.org/html/2511.13656v1

[^116]: https://www.github.careers/careers-home/jobs/5481?lang=en-us

[^117]: https://github.com/posquit0/awesome-engineering-ladders

[^118]: https://github.com/gab0gomes/awesome-career-paths

[^119]: https://github.com/swyxio/swyxdotio/issues/359

[^120]: https://github.com/vitorsr/cc

[^121]: https://github.com/vijayvenkatesh/engineering_ladders

[^122]: https://github.com/ORNL/intersect-architecture/blob/main/LICENSE

[^123]: https://github.com/jorgef/engineeringladders/blob/master/README.md

[^124]: https://gist.github.com/ssebelius/43c030ec6668b1f83613d8fc00c9b1a1

[^125]: https://www.linkedin.com/posts/jessicaolivertechconnect_github-is-one-of-those-tools-your-team-can-activity-7379541816859131904-QaJl

[^126]: https://ben.balter.com/2023/01/10/manage-like-an-engineer/

[^127]: https://blackgirlbytes.dev/developer-relations-is-an-all-company-effort

[^128]: https://progression.fyi/f/spotify

[^129]: https://progression.fyi/f/brad-fults

[^130]: https://progression.fyi/f/planet-argon

[^131]: https://skillpanel.com/blog/career-framework-guide/

[^132]: https://www.rnbglobal.edu.in/assets/pdfs/annual-reports/Annual Report 2018-19.pdf

[^133]: https://dropbox.github.io/dbx-career-framework/ic5_staff_reliability_engineer.html

[^134]: https://www.reddit.com/r/EngineeringManagers/comments/1qnbm2g/how_do_you_actually_track_promotion_readiness_for/

[^135]: https://dropbox.github.io/dbx-career-framework/m4_engineering_manager.html

[^136]: https://www.remocate.app/?a7cc3918_page=64

[^137]: https://dropbox.github.io/dbx-career-framework/ic5_staff_security_engineer.html

[^138]: https://www.linkedin.com/posts/shreyanaik24_in-early-2020-when-the-travel-industry-collapsed-activity-7399676098948632576-qjQo

[^139]: https://dropbox.github.io/dbx-career-framework/ic3_machine_learning_engineer.html

[^140]: https://www.career.com/job/executiveplacements-com/senior-analytics-engineer/j202511300905054059518

[^141]: https://www.linkedin.com/posts/nana-kwesi-amponsah_vaultly-defining-the-problem-activity-7440038235252080641-AULl

