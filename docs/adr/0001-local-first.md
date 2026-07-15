# ADR 0001: Local-First Private Data

## Status

Accepted

## Context

Wingman will process career history, contacts, interactions, calendar data, and potentially email-derived information.

## Decision

Private user data is stored locally by default. Remote persistence and telemetry require separate explicit decisions.

## Consequences

- local setup is slightly more involved;
- privacy boundaries are easier to understand;
- connectors should begin read-only;
- backups and migration need deliberate design.
