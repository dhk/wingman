# Wingman

Wingman is a local-first, AI-assisted career intelligence system.

It helps a human:

- build a canonical, evidence-backed professional profile;
- assess opportunities against real experience;
- research companies and hiring signals;
- identify credible warm introduction paths;
- prepare differentiated outreach and interviews;
- decide which actions deserve attention next.

Wingman is **not** an autonomous job application bot. It is a human-in-the-loop decision-support system. No message, application, calendar invitation, or external write occurs without explicit approval.

## Status

This repository is at **Phase 2: Opportunity Inbox** (first slice).

Assess a job description against your profile and get a cited fit brief:

```bash
wingman assess path/to/job.md
```

Requirements are extracted with verbatim quotes from the posting; each is
assessed against your profile as met / partial / gap / unknown, citing only
real profile items — an unsupported "met" is downgraded to "unknown" by
deterministic validation. The brief lands in the workspace `reports/`.

The Phase 1 product proof works end to end:

```text
resume.md
   ↓
validated ingestion (SourceRecord, content-hashed, deduplicated)
   ↓
model extraction (extract_fast capability class) + deterministic evidence validation
   ↓
career.json
   ↓
career.md with evidence references
```

```bash
wingman ingest path/to/resume.md
```

Every accepted claim carries verbatim quotes from the source; claims whose
quotes do not appear in the source are rejected and reported, never stored.
Model routing is configured in the workspace `models.toml` (written by
`wingman init`; set `ANTHROPIC_API_KEY` to use the default mapping).

## Repository Guide

- [`VISION.md`](VISION.md): why Wingman exists and what good looks like
- [`ROADMAP.md`](ROADMAP.md): phased delivery plan
- [`AGENTS.md`](AGENTS.md): engineering and agent instructions
- [`docs/DESIGN.md`](docs/DESIGN.md): overall architecture — what the system is
- [`docs/RFC.md`](docs/RFC.md): engineering decisions and rationale
- [`docs/EVALUATION.md`](docs/EVALUATION.md): how we measure that Wingman is getting better
- [`docs/`](docs/): product notes

## Install

```bash
uv tool install git+https://github.com/dhk/wingman
wingman init
wingman doctor
```

The workspace lives in `$WINGMAN_DATA_DIR` if set, otherwise the platform user
data directory (e.g. `~/.local/share/wingman` on Linux).

## Quick Start (development checkout)

```bash
uv sync
export WINGMAN_DATA_DIR=./data   # keep workspace data inside the repo checkout
uv run wingman --help
uv run wingman init
uv run wingman status
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## Design Principles

The binding list is the Product Invariants in [`VISION.md`](VISION.md#product-invariants).

## License

MIT — see [`LICENSE`](LICENSE).
