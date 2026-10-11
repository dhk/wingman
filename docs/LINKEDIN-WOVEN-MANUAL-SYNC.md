# Manual LinkedIn / Woven refresh

A session coordinates two separate systems: one authenticated tenant on
Wingman's shared server, and the operator's authorized Woven graph on their
Mac. Wingman can call Woven read-only via `woven_warm_path` (RFC-043);
that lookup does not import or synchronize either database. This is the procedure for #479;
[#467](https://github.com/dhk/wingman/issues/467) remains open and does not
replace it with a working bridge.

## Availability and stop conditions

Baseline checked on 2026-10-09 against Wingman main and Woven main
[`a04f6e8`](https://github.com/dhk/woven/tree/a04f6e8fcbe68f01f36e749427fd0b0df9d90a5f):

- Wingman has profile import (`ingest-linkedin`), contact import
  (`people_import_connections` / `people import-connections`) and tenant-scoped
  MCP requests. Contact upsert and email backfill are tracked by
  [#478](https://github.com/dhk/wingman/issues/478) and
  [#422](https://github.com/dhk/wingman/issues/422); verify those changes are
  deployed before relying on repeated imports or updated/unchanged counts.
  The baseline reports Created / Already known instead.
- The profile before/after and thinness dry-run required by the sync design
  is not implemented. [#420](https://github.com/dhk/wingman/issues/420) is open;
  `wingman ingest-linkedin --help` has no dry-run option, and uploading a zip
  imports it immediately. An upload is not a preview.
- [dhk/woven#92](https://github.com/dhk/woven/issues/92) is open. Woven main
  has no headless contributor-upsert script, root script package, or MCP
  import tool. Its MCP server is read-only. There is therefore no verified
  Woven write command to invoke or document yet.

**A complete two-system refresh is blocked on those last two prerequisites.**
The read-only preparation below is usable now. Stop before the write sequence
until a deployed Wingman dry-run and a reviewed Woven #92 importer exist.
Do not substitute a browser upload into the combined seeded graph: that
rebuilds from the session's uploaded files and can discard other owners.
Do not guess a `--dry-run` flag or a Woven script filename.

## Prepare the session and identify the owner

1. Obtain the owner's fresh LinkedIn export zip and keep its original bytes.
   Record its local SHA-256 and export date privately. Do not put the zip,
   contact addresses, graph snapshot or credentials in an issue or PR.
2. Connect to that owner's Wingman tenant on the shared instance using their
   existing authenticated connector. Call `status()` and `career_profile()`;
   have the owner verify that this is their career record. Authentication
   chooses the tenant: these tools have no tenant-name argument. A contributor
   name in a CSV is not authority to select somebody else's workspace.
3. Call `my_urls()` if the owner needs their upload address. Use only the
   address actually returned for this session. A capability URL is a secret;
   OAuth sessions may instead require operator-assisted upload. See
   [SERVER.md](SERVER.md) for the current authentication and upload behavior.
4. On the Mac, select an authorized Woven snapshot and confirm the contributor
   identity with its owner. Keep a new tenant's graph separate unless the
   contributors explicitly agree to combine their networks. A matching
   Wingman slug and Woven contributor name is a mapping to verify, not an
   instruction to merge graphs.
5. Register Woven as a peer MCP server. The verified entrypoint, after building
   its `mcp` package, is:

   ```text
   claude mcp add woven -- node /absolute/path/to/woven/mcp/dist/src/server.js
   ```

   Configure `WOVEN_GRAPH_PATH` for the authorized snapshot using Woven's
   [install guide](https://github.com/dhk/woven/blob/main/docs/mcp-install.md).
   Call `get_graph_stats` and verify the graph and contributor coverage before
   any refresh. Follow that guide's logging controls for private queries.

Wingman's optional read-only lookup uses `WINGMAN_WOVEN_URL` pointing at a
reachable, authorized Woven streamable-HTTP MCP endpoint. A peer stdio entry
alone does not configure that endpoint. Set it in the environment of the
Wingman process that should make the lookup, then reload that process using
its deployment procedure. Do not restart unrelated tenants or services.

## Write sequence, once both prerequisites ship

1. Run the deployed Wingman **read-only** import preview against the exact
   zip and the selected tenant. Show before/after counts and every retirement
   to the owner. If it is unexpectedly thin, rejected or ambiguous, stop:
   neither Wingman nor Woven should change. The eventual #420 interface must
   supply the real preview command; none exists in the checked version.
2. Before any import, create and verify a private Wingman backup for the
   selected tenant, with no cloud upload unless separately approved:

   ```bash
   WINGMAN_DATA_DIR=/absolute/tenant-workspace wingman backup /absolute/private/backups --no-drive
   ```

   Preserve the printed archive path and a copy of the authorized Woven
   snapshot. Recovery uses `wingman restore <archive>` in the same tenant
   environment; restoring over a live database requires `--force` and the
   owner's explicit approval because it replaces current workspace state.
   Stop the affected workspace's writers before restoring. Verify with
   `status()` and `career_profile()` afterward.

   After approval of the preview, import the profile into that same Wingman
   tenant. The existing server-side command is shown below for an authorized
   operator running as the workspace owner. Both paths are on the server,
   not the Mac; resolve the tenant directory from the operator's registry.

   ```bash
   WINGMAN_DATA_DIR=/absolute/tenant-workspace wingman ingest-linkedin /absolute/private/export.zip
   ```

   Alternatively the tenant's upload UI imports the profile. Use one route,
   then inspect accepted/updated/retired/conflict results and `career_profile()`.
   Do not move on merely because a file uploaded successfully.
3. Import contacts separately; profile upload does not seed the address book.
   In the same tenant's MCP session, with the exact zip already staged on the
   server at an operator-confirmed private path. An authorized operator must
   copy the exact zip into that tenant's private inbox with workspace-owner
   permissions; confirm its SHA-256 matches the original. The upload UI also
   stores a zip in the inbox but immediately imports the profile, so it is
   not a staging-only or preview route:

   ```text
   people_import_connections(export_path="/absolute/private/export.zip")
   ```

   The equivalent server-side command is:

   ```bash
   WINGMAN_DATA_DIR=/absolute/tenant-workspace wingman people import-connections /absolute/private/export.zip
   ```

   Inspect created/updated/unchanged counts and a few owner-selected records
   through `people_list()`. Confirm non-empty new fields landed, blank fields
   did not erase known values, and a thinner export did not delete people.
   Contact values remain private and must not be published in verification notes.
4. **Only after both Wingman imports are confirmed** invoke the reviewed
   Woven #92 headless importer locally, using its documented contributor name,
   CSV path and snapshot arguments. Extract the Connections.csv from the same
   zip; do not use a different export for the graph. Record the exact command
   and importer revision privately. Before this step becomes executable,
   update this runbook with the real script path, flags and dry-run behavior
   from the merged Woven implementation.
5. Verify Woven preserved other contributors' outgoing edges and shared-node
   fields, replaced only the refreshed owner's edge set, and is idempotent for
   the same export. Compare contributor counts and known mutual connections
   against the pre-refresh snapshot. Restart/reconnect only the Woven MCP
   instance reading that snapshot, then call `get_graph_stats` again: the
   current server reads its graph at startup and does not live-reload it.
6. Record a private completion note: tenant, contributor, export hash, deployed
   revisions, before/after counts, each system's outcome, and whether Woven
   was reloaded. No automatic outreach, publishing or graph sharing is part
   of this procedure.

## If one side fails

If Wingman rejects the preview or either import fails, do not write Woven.
If Wingman succeeds and Woven fails, report that exact partial state and keep
both the export and pre-refresh graph snapshot. Retry only the Woven operation
after repair, verifying the same contributor and input hash. Do not erase the
successful Wingman import or claim the systems are synchronized.

Revisit automation only after a real Woven hosting/identity migration is
agreed. The obsolete HTTP bridge discussed in #467 is not a substitute for
this session-level coordination.
