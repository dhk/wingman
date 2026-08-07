# Contributing to Wingman

The authoritative engineering rules, product invariants, working method, test
commands, and definition of done are in [AGENTS.md](AGENTS.md). Read it before
making a change. Product intent lives in [VISION.md](VISION.md), current delivery
status in [ROADMAP.md](ROADMAP.md), architecture in
[docs/DESIGN.md](docs/DESIGN.md), and durable decisions in
[docs/RFC.md](docs/RFC.md).

Keep contributions to one complete vertical slice, preserve privacy and
provenance invariants, and update documentation with behavior. Run the standard
secret-free checks before opening a pull request:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Live-provider evaluations are excluded from the default test suite and must not
be run casually. Never include secrets or identifiable private fixture data.
Durable architectural choices require an RFC entry following
[the ledger instructions](docs/RFC.md#how-to-add-an-entry).
