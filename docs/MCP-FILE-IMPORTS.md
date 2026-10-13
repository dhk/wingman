# Import files through MCP (client-dependent)

When your MCP client can read an attachment's bytes, it can import that file into
your connected Wingman workspace without a terminal or a server filesystem path.
Use a client-side byte encoder to supply standard base64 to the appropriate tool.
The model must not manually emit/re-encode file contents. Programmatic and local
clients with filesystem tools can do this; Claude.ai/Desktop attachment forwarding
has not been verified. A successful stdio test does not establish hosted usability.
Never ask the model to reconstruct a PDF, DOCX, or ZIP from remembered text.

| Tool | Input | Result |
| --- | --- | --- |
| `ingest_resume_file` | Resume .md, .markdown, .txt, .pdf, .docx, .tex | Canonical profile through ordinary extraction and evidence validation |
| `ingest_linkedin` | LinkedIn export .zip | Positions, skills, recommendations; excludes contacts/messages |
| `ingest_transcript` | Gemini transcript .md, .markdown, .txt, .pdf | Read-only preview; `note_for` plus `reviewed_hash` files an endorsed interaction summary |
| `corpus_add` | Writing .md, .markdown, .txt, .html, .htm, or .zip | Searchable, content-deduplicated private corpus |
| `corpus_list` | No arguments | IDs, titles, source labels, dates, word counts |

File tools take `content_base64` and `filename`. `corpus_add` also accepts
`source_type` (default `writing`). To import a writing directory, ZIP it first.
Use the same resume filename for successive versions of the same document.
Paths in filenames are stripped; tools cannot read an arbitrary server file.

Limits: 10 MiB decoded file bytes; ZIP/DOCX at most 1000 entries and 50 MiB total
expanded bytes. Archives must use stored/deflated entries without encryption.
Base64 may be line-wrapped; whitespace is ignored. Payload and archive safety
checks run before inbox storage or model calls. Format validation may stage an
inbox copy; content-rejected copies are then removed. Original files are archived in your workspace
inbox on profile/corpus import. A file whose content is rejected (nothing
importable in a LinkedIn export, no extractable resume text, a corpus upload that
adds nothing) is not kept, and the reply names the reason. Other import failures
(storage, model availability) keep the upload and preserve existing records and
any successful partial work; retrying uses the ordinary content-hash deduplication. Resume text goes to your configured extraction
provider. LinkedIn, corpus and transcript parsing make no model or network calls.
Even with opt-in telemetry enabled, these tools withhold file bytes and result
contents from the journal; only operation metadata (name, outcome, duration and
error class on failure) is recorded for these tools. Filenames,
recipient names, source labels, hashes and corpus-list results are withheld too.
Corpus INFO logs contain counts, not input paths or titles.

Transcript preview persists nothing. Review its generated-note warnings and speaker
mismatches before asking to file it against a specific watched person. Call again
with `note_for` set to the full name and `reviewed_hash` copied from that preview.
Missing/mismatched hashes refuse filing. The hash binds the exact bytes, not proof
of human review; clients must still obtain explicit approval. Filing archives the
original bytes and a source record, links that local artifact from the interaction,
and labels the note ENDORSED (Gemini-generated context reviewed by the user).
It is not a direct human quote or a profile claim and sends no message.
CLI uses the same rules with `--note-for` and `--reviewed-hash`. CLI alone supports
transcript `--url`; even URL preview persists nothing.

Some clients cannot encode attached binary files into tool arguments. For resumes
and LinkedIn exports, use the existing browser upload link returned by `my_urls`
instead. This interface does not promise native attachment access in every client.
Corpus and transcript have no browser-upload fallback yet. For clients without
a byte encoder, these remain hosted gaps; use operator-assisted local import.
For larger exports, use a smaller subset or local import.

CLI users can inspect assessed roles with `wingman opportunities list`. It uses
the same ordering and summaries as MCP `opportunities_list`, including visibly
approximate pack/answer indicators.
