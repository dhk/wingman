# Development Runbook

## Setup

```bash
uv sync
```

## Run the CLI

```bash
uv run wingman --help
uv run wingman status
```

## Quality Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## Before Committing

- inspect the diff;
- confirm no private data or secrets were added;
- run relevant tests;
- update documentation;
- record any known limitation.
