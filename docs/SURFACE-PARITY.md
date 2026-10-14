# CLI and MCP parity decisions

This ledger records every leaf command in `wingman` (including aliases) and every
registered `wingman-mcp` tool. A counterpart means an existing operation or action
mode covers that command, not that argument shapes or presentation are identical.
A stated gap remains a gap; this ledger does not implement any new tools. Local
operator controls remain deliberately one-sided. The server launch entry point
`wingman-mcp` itself is not a workspace command and is outside this inventory.

Update both sides of a mapping when changing a surface. The contract test enumerates
the live Typer command tree and registered FastMCP tools, rejects missing/stale
rows, requires a reason for unmatched rows, and checks reciprocal counterparts.
CLI `--help` options and group menus are not separate executable leaf commands.

File-upload mappings are client-dependent: actual byte encoding must happen in
client software, never by model reconstruction. Claude.ai/Desktop do not forward
an attachment's bytes to a connector (see below); there is no browser fallback yet.
The transcript mapping covers local file bytes only: CLI `--url` has no MCP
counterpart. Both filing surfaces require the reviewed content hash.

| Surface | Name | Counterparts | Reason when unmatched |
| --- | --- | --- | --- |
| cli | `actions done` | `action_triage` |  |
| cli | `actions list` | `action_triage` |  |
| cli | `actions mute` | `action_triage` |  |
| cli | `actions snooze` | `action_triage` |  |
| cli | `actions unmute` | `action_triage` |  |
| cli | `admin url` |  | Deliberately local operator entry point; never grant administration through a tenant tool. |
| cli | `answers find` | `answer_bank` |  |
| cli | `answers list` | `answer_bank` |  |
| cli | `answers rm` | `answer_bank` |  |
| cli | `answers save` | `answer_bank` |  |
| cli | `answers show` | `answer_bank` |  |
| cli | `artifacts forget` | `artifacts` |  |
| cli | `artifacts list` | `artifacts` |  |
| cli | `artifacts remember` | `artifacts` |  |
| cli | `artifacts show` | `artifacts` |  |
| cli | `artifacts stale` | `artifacts` |  |
| cli | `assess` | `assess_job`, `assess_job_url` |  |
| cli | `backup` | `backup` |  |
| cli | `carve-off-persona` | `carve_off_persona` |  |
| cli | `coach-persona` | `coach_persona` |  |
| cli | `commentary find` | `commentary` |  |
| cli | `commentary list` | `commentary` |  |
| cli | `commentary remove` | `commentary` |  |
| cli | `commentary save` | `commentary` |  |
| cli | `commentary show` | `commentary` |  |
| cli | `company add-feed` | `company_feed` |  |
| cli | `company add-source` | `company_source` |  |
| cli | `company deep-dive` | `company_deep_dive` |  |
| cli | `company delete` | `company_manage` |  |
| cli | `company delete-dossier` | `company_manage` |  |
| cli | `company dossier` | `company_dossier` |  |
| cli | `company feeds` | `company_feed` |  |
| cli | `company fetch` | `company_feed` |  |
| cli | `company follow` | `company_follow` |  |
| cli | `company like` | `company_like` |  |
| cli | `company pause-source` | `company_source` |  |
| cli | `company pov` | `company_pov` |  |
| cli | `company remove-feed` | `company_feed` |  |
| cli | `company remove-source` | `company_source` |  |
| cli | `company rename` | `company_manage` |  |
| cli | `company research` | `company_research` |  |
| cli | `company score-board` | `company_score_board` |  |
| cli | `company similar` | `company_similar` |  |
| cli | `company sources` | `company_source` |  |
| cli | `company unpause-source` | `company_source` |  |
| cli | `company warm-path` | `woven_warm_path` |  |
| cli | `corpus add` | `corpus_add` | Client-dependent byte upload; native hosted attachment forwarding unverified. |
| cli | `corpus list` | `corpus_list` |  |
| cli | `criteria review` | `job_criteria` |  |
| cli | `criteria save` | `job_criteria` |  |
| cli | `criteria show` | `job_criteria` |  |
| cli | `demo` | `wingman_demo` |  |
| cli | `digest` | `digest` |  |
| cli | `doctor` |  | Deliberately local installation and credential diagnostics. |
| cli | `drive auth` | `drive_auth` |  |
| cli | `embed` | `embed` |  |
| cli | `evidence` | `evidence` |  |
| cli | `examples kinds` | `examples` |  |
| cli | `examples list` | `examples` |  |
| cli | `examples remove` | `examples` |  |
| cli | `examples show` | `examples` |  |
| cli | `export career` | `export_pdf` |  |
| cli | `export company` | `export_pdf` |  |
| cli | `export person` | `export_pdf` |  |
| cli | `feature repo` |  | Deliberately operator configuration for the external issue destination; feature_request uses the configured destination. |
| cli | `feature request` | `feature_request` |  |
| cli | `gap-map` | `gap_map` |  |
| cli | `heap add` | `heap` |  |
| cli | `heap remove` | `heap` |  |
| cli | `heap show` | `heap` |  |
| cli | `heap sort` | `heap` |  |
| cli | `host-check` |  | Deliberately local host/runtime diagnostics. |
| cli | `ingest` | `ingest_resume_file`, `ingest_resume_url` |  |
| cli | `ingest-linkedin` | `ingest_linkedin` | Client-dependent byte upload; native hosted attachment forwarding unverified. |
| cli | `ingest-transcript` | `ingest_transcript` | Client-dependent byte upload; transcript URL fetching remains CLI-only. |
| cli | `init` |  | Deliberately local workspace bootstrap; MCP requires an initialized workspace. |
| cli | `interview` | `interview_react` |  |
| cli | `keys list` |  | Deliberately local credential administration; secrets are not MCP tool arguments. |
| cli | `keys set` |  | Deliberately local credential administration; secrets are not MCP tool arguments. |
| cli | `keys test` |  | Deliberately local credential administration; secrets are not MCP tool arguments. |
| cli | `keys unset` |  | Deliberately local credential administration; secrets are not MCP tool arguments. |
| cli | `keys validate` |  | Deliberately local credential administration; secrets are not MCP tool arguments. |
| cli | `keys where` |  | Deliberately local credential administration; secrets are not MCP tool arguments. |
| cli | `log` | `relationship_log` |  |
| cli | `make-it-so` | `make_it_so` |  |
| cli | `mcp status` |  | Deliberately local process management. |
| cli | `mcp stop` |  | Deliberately local process control; stopping the serving process would sever the MCP call. |
| cli | `mcp url` | `my_urls` |  |
| cli | `miso` | `make_it_so` |  |
| cli | `motd history` | `motd` |  |
| cli | `motd set` |  | Deliberately local operator broadcast configuration/diagnostics; tenant MCP tools cannot perform this operation. |
| cli | `motd show` |  | Deliberately local operator broadcast configuration/diagnostics; tenant MCP tools cannot perform this operation. |
| cli | `objective review` | `relationship_objective` |  |
| cli | `objective save` | `relationship_objective` |  |
| cli | `objective show` | `relationship_objective` |  |
| cli | `opportunities list` | `opportunities_list` |  |
| cli | `overnight` | `overnight` |  |
| cli | `pack` | `pack` |  |
| cli | `people add` | `people_add` |  |
| cli | `people add-feed` | `feed_attach`, `feed_discover` |  |
| cli | `people brief` | `people_brief` |  |
| cli | `people deep-dive` | `people_deep_dive` |  |
| cli | `people delete` | `people_manage` |  |
| cli | `people discover` | `people_discover` |  |
| cli | `people docs` | `people_docs` |  |
| cli | `people dossier` | `people_dossier` |  |
| cli | `people evidence` | `people_evidence` |  |
| cli | `people fetch` | `people_fetch` |  |
| cli | `people fix` | `people_manage` |  |
| cli | `people import-connections` | `people_import_connections` |  |
| cli | `people like` | `people_like` |  |
| cli | `people list` | `people_list` |  |
| cli | `people log` | `relationship_log` |  |
| cli | `people news` | `people_news` |  |
| cli | `people pov` | `people_pov` |  |
| cli | `people rename` | `people_manage` |  |
| cli | `people similar` | `people_similar` |  |
| cli | `people warm-path` | `woven_warm_path` |  |
| cli | `perspectives` | `perspectives_start` |  |
| cli | `pov` | `my_pov` |  |
| cli | `profile amend` | `profile_manage` |  |
| cli | `profile clear` | `profile_manage` |  |
| cli | `profile correct` | `profile_manage` |  |
| cli | `profile list` | `profile_manage` |  |
| cli | `profile rekind` | `profile_manage` |  |
| cli | `profile rename` | `profile_manage` |  |
| cli | `profile resolve` | `profile_manage` |  |
| cli | `profile rm` | `profile_manage` |  |
| cli | `qa` | `qa_capture` |  |
| cli | `qotd answer` | `qotd` |  |
| cli | `qotd answers` | `qotd` |  |
| cli | `qotd set` |  | Deliberately local operator broadcast configuration/diagnostics; tenant MCP tools cannot perform this operation. |
| cli | `qotd show` | `qotd` |  |
| cli | `restore` |  | Deliberately local destructive whole-workspace replacement requiring terminal confirmation. |
| cli | `rubrics` | `rubrics` |  |
| cli | `search` | `search` |  |
| cli | `setup-guide` | `setup_guide` |  |
| cli | `status` | `status` |  |
| cli | `sync` | `sync` |  |
| cli | `telemetry export` |  | Deliberately local telemetry-file export; no remotely readable private transcript archive. |
| cli | `telemetry harvest-transcript` |  | Deliberately local transcript-file access and privacy boundary. |
| cli | `telemetry off` |  | Deliberately local control of telemetry collection. |
| cli | `telemetry on` |  | Deliberately local explicit opt-in to collecting private telemetry. |
| cli | `telemetry show` | `telemetry` |  |
| cli | `telemetry status` | `telemetry` |  |
| cli | `telemetry summary` | `telemetry` |  |
| cli | `tenant answers` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant form` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant ingest-form` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant keys` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant oauth-approve` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant oauth-bind` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant oauth-invite` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant oauth-invite-revoke` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant oauth-invites` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant oauth-pending` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant overnight` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant reload` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant rotate-token` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `tenant url` | `tenant_url` |  |
| cli | `tenant urls` | `tenant_urls` |  |
| cli | `tenant validate` |  | Deliberately local operator administration across tenants; tenant tools cannot grant this authority. |
| cli | `today` | `changelog` |  |
| cli | `values` | `my_values` |  |
| cli | `values-chart` | `values_chart` |  |
| cli | `watchlist add` | `watchlist` |  |
| cli | `watchlist list` | `watchlist` |  |
| cli | `watchlist remove` | `watchlist` |  |
| cli | `watchlist run` | `watchlist` |  |
| cli | `watchlist show` | `watchlist` |  |
| mcp | `action_triage` | `actions done`, `actions list`, `actions mute`, `actions snooze`, `actions unmute` |  |
| mcp | `answer_bank` | `answers find`, `answers list`, `answers rm`, `answers save`, `answers show` |  |
| mcp | `artifacts` | `artifacts forget`, `artifacts list`, `artifacts remember`, `artifacts show`, `artifacts stale` |  |
| mcp | `assess_job` | `assess` |  |
| mcp | `assess_job_url` | `assess` |  |
| mcp | `backup` | `backup` |  |
| mcp | `briefing` |  | Deliberately MCP overview for conversation, with no exact CLI operation; today reports release history. |
| mcp | `career_profile` |  | Deliberately MCP read of the canonical profile; profile CLI subcommands edit/list items. |
| mcp | `carve_off_persona` | `carve-off-persona` |  |
| mcp | `changelog` | `today` |  |
| mcp | `coach_persona` | `coach-persona` |  |
| mcp | `commentary` | `commentary find`, `commentary list`, `commentary remove`, `commentary save`, `commentary show` |  |
| mcp | `company_deep_dive` | `company deep-dive` |  |
| mcp | `company_deep_dive_save` |  | Deliberately MCP save of externally researched evidence; CLI deep-dive runs its own research. |
| mcp | `company_dossier` | `company dossier` |  |
| mcp | `company_feed` | `company add-feed`, `company feeds`, `company fetch`, `company remove-feed` |  |
| mcp | `company_follow` | `company follow` |  |
| mcp | `company_like` | `company like` |  |
| mcp | `company_manage` | `company delete`, `company delete-dossier`, `company rename` |  |
| mcp | `company_pov` | `company pov` |  |
| mcp | `company_research` | `company research` |  |
| mcp | `company_score_board` | `company score-board` |  |
| mcp | `company_similar` | `company similar` |  |
| mcp | `company_source` | `company add-source`, `company pause-source`, `company remove-source`, `company sources`, `company unpause-source` |  |
| mcp | `completeness` |  | Deliberately MCP structured setup/readiness diagnostic; no exact CLI equivalent. |
| mcp | `corpus_add` | `corpus add` | Client-dependent byte upload; native hosted attachment forwarding unverified. |
| mcp | `corpus_list` | `corpus list` |  |
| mcp | `digest` | `digest` |  |
| mcp | `drive_auth` | `drive auth` |  |
| mcp | `embed` | `embed` |  |
| mcp | `evidence` | `evidence` |  |
| mcp | `examples` | `examples kinds`, `examples list`, `examples remove`, `examples show` |  |
| mcp | `export_pdf` | `export career`, `export company`, `export person` |  |
| mcp | `feature_request` | `feature request` |  |
| mcp | `feed_attach` | `people add-feed` |  |
| mcp | `feed_discover` | `people add-feed` |  |
| mcp | `gap_map` | `gap-map` |  |
| mcp | `heap` | `heap add`, `heap remove`, `heap show`, `heap sort` |  |
| mcp | `heap_read` |  | Deliberately MCP document/image payload retrieval; heap show lists metadata. |
| mcp | `ingest_linkedin` | `ingest-linkedin` | Client-dependent byte upload; native hosted attachment forwarding unverified. |
| mcp | `ingest_resume_file` | `ingest` | Client-dependent byte upload; native hosted attachment forwarding unverified. |
| mcp | `ingest_resume_text` |  | Deliberately MCP text payload ingestion; CLI ingest takes a local file. |
| mcp | `ingest_resume_url` | `ingest` |  |
| mcp | `ingest_transcript` | `ingest-transcript` | Client-dependent byte upload; transcript URL fetching remains CLI-only. |
| mcp | `interview_react` | `interview` |  |
| mcp | `interview_status` |  | Deliberately MCP interview-progress inspection; terminal interview remains an interactive capture wizard. |
| mcp | `job_criteria` | `criteria review`, `criteria save`, `criteria show` |  |
| mcp | `make_it_so` | `make-it-so`, `miso` |  |
| mcp | `motd` | `motd history` |  |
| mcp | `my_pov` | `pov` |  |
| mcp | `my_urls` | `mcp url` |  |
| mcp | `my_values` | `values` |  |
| mcp | `opportunities_list` | `opportunities list` |  |
| mcp | `overnight` | `overnight` |  |
| mcp | `pack` | `pack` |  |
| mcp | `people_add` | `people add` |  |
| mcp | `people_brief` | `people brief` |  |
| mcp | `people_deep_dive` | `people deep-dive` |  |
| mcp | `people_deep_dive_save` |  | Deliberately MCP save of externally researched evidence; CLI deep-dive runs its own research. |
| mcp | `people_discover` | `people discover` |  |
| mcp | `people_docs` | `people docs` |  |
| mcp | `people_dossier` | `people dossier` |  |
| mcp | `people_evidence` | `people evidence` |  |
| mcp | `people_fetch` | `people fetch` |  |
| mcp | `people_import_connections` | `people import-connections` |  |
| mcp | `people_like` | `people like` |  |
| mcp | `people_list` | `people list` |  |
| mcp | `people_manage` | `people delete`, `people fix`, `people rename` |  |
| mcp | `people_news` | `people news` |  |
| mcp | `people_pov` | `people pov` |  |
| mcp | `people_similar` | `people similar` |  |
| mcp | `perspectives_start` | `perspectives` |  |
| mcp | `profile_html` |  | Deliberately MCP inline profile presentation; export career writes a different artifact. |
| mcp | `profile_manage` | `profile amend`, `profile clear`, `profile correct`, `profile list`, `profile rekind`, `profile rename`, `profile resolve`, `profile rm` |  |
| mcp | `qa_capture` | `qa` |  |
| mcp | `qotd` | `qotd answer`, `qotd answers`, `qotd show` |  |
| mcp | `relationship_log` | `log`, `people log` |  |
| mcp | `relationship_objective` | `objective review`, `objective save`, `objective show` |  |
| mcp | `resolve_requirement` |  | Deliberately MCP evidence clarification for an assessed requirement; no matching CLI operation. |
| mcp | `rubrics` | `rubrics` |  |
| mcp | `search` | `search` |  |
| mcp | `setup_guide` | `setup-guide` |  |
| mcp | `status` | `status` |  |
| mcp | `sync` | `sync` |  |
| mcp | `telemetry` | `telemetry show`, `telemetry status`, `telemetry summary` |  |
| mcp | `tenant_url` | `tenant url` |  |
| mcp | `tenant_urls` | `tenant urls` |  |
| mcp | `values_chart` | `values-chart` |  |
| mcp | `watchlist` | `watchlist add`, `watchlist list`, `watchlist remove`, `watchlist run`, `watchlist show` |  |
| mcp | `wingman_demo` | `demo` |  |
| mcp | `wingman_flow` |  | Deliberately MCP conversational next-step guidance; terminal perspectives has its own wizard. |
| mcp | `woven_warm_path` | `company warm-path`, `people warm-path` |  |

## Action-level exceptions

The inventory guard covers command/tool registration, not every string-valued
action. Human review must check action coverage when either side changes.

| Tool action | Decision |
| --- | --- |
| `examples(action="add")` | Deliberately MCP-only capture of conversational examples; CLI examples has list/show/kinds/remove but no add command. |

`people add-feed` discovers, confirms, then attaches; MCP splits that flow into
`feed_discover` and `feed_attach`. Company attachment instead accepts an already
chosen feed URL on both surfaces (`company add-feed` / `company_feed(attach)`);
`feed_discover` is an optional preparation step, not an operation performed by
that CLI command.

The deferred #598 gaps are closed for clients that can send file bytes: MCP
accepts bounded file payloads, not server paths, and directory corpus imports
use a ZIP. Claude.ai and Claude Desktop cannot: a connector's tool arguments
are written by the model, which sees an attachment as extracted text, not its
bytes. For those hosted users the file imports remain gaps until a browser
upload exists (Beads `wingman-m4l`). Transcript preview is read-only, and
filing requires the preview's review hash and the person's full name. Opportunity
listing shares its application renderer across CLI and MCP. Reciprocal mappings
are mechanically checked; their semantic accuracy still needs review.
