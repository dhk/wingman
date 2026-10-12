# Import files through MCP

When your MCP client can read an attachment's bytes, it can import that file into
your connected Wingman workspace without a terminal or a server filesystem path.
Ask it to encode the actual file as standard base64 and call the appropriate tool.
Never ask the model to reconstruct a PDF, DOCX, or ZIP from remembered text.

| Tool | Input | Result |
| --- | --- | --- |
| `ingest_resume_file` | Resume .md, .markdown, .txt, .pdf, .docx, .tex | Canonical profile through ordinary extraction and evidence validation |
| `ingest_linkedin` | LinkedIn export .zip | Positions, skills, recommendations; excludes contacts/messages |
| `ingest_transcript` | Gemini transcript .md, .markdown, .txt, .pdf | Read-only preview; explicit `note_for` files a local interaction summary |
| `corpus_add` | Writing .md, .markdown, .txt, .html, .htm, or .zip | Searchable, content-deduplicated private corpus |
| `corpus_list` | No arguments | IDs, titles, source labels, dates, word counts |

File tools take `content_base64` and `filename`. `corpus_add` also accepts
`source_type` (default `writing`). To import a writing directory, ZIP it first.
Use the same resume filename for successive versions of the same document.
Paths in filenames are stripped; tools cannot read an arbitrary server file.

Limits: 10 MiB decoded file bytes; ZIP/DOCX at most 1000 entries and 50 MiB total
expanded bytes. Archives must use stored/deflated entries without encryption.
Invalid inputs are rejected before inbox storage or model calls. Original files
are archived in your workspace inbox on profile/corpus import. Import failures
preserve existing records and any successful partial work; retrying uses the
ordinary content-hash deduplication. Resume text goes to your configured extraction
provider. LinkedIn, corpus and transcript parsing make no model or network calls.
Even with opt-in telemetry enabled, these tools withhold file bytes and result
contents from the journal; ordinary invocation metadata is still recorded.

Transcript preview persists nothing. Review its generated-note warnings and speaker
mismatches before asking to file it against a specific watched person. The client
then calls again with `note_for` set to that name. The resulting note is a local
summary, not a profile claim, and sends no message. It does not infer a recipient
from the transcript.

Some clients cannot encode attached binary files into tool arguments. For resumes
and LinkedIn exports, use the existing browser upload link returned by `my_urls`
instead. This interface does not promise native attachment access in every client.
For larger exports, use a smaller subset or ask your operator about local import.

CLI users can inspect assessed roles with `wingman opportunities list`. It uses
the same ordering and summaries as MCP `opportunities_list`, including visibly
approximate pack/answer indicators.
