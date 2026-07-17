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

This repository is at **Phase 0: Foundation**.

The first product proof is:

```text
resume.md
   ↓
validated ingestion
   ↓
career.json
   ↓
career.md with evidence references
```

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
