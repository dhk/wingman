# ADR 0002: Provider-Neutral Model Layer

## Status

Accepted

## Context

Model capabilities, pricing, and product names change rapidly.

## Decision

Wingman domain and application code depend on internal interfaces, not provider SDKs. Provider-specific implementations live under `src/wingman/providers/`.

## Consequences

- switching providers is easier;
- tests can use fakes;
- provider-specific features require explicit adapters;
- initial implementation requires a small abstraction layer.
