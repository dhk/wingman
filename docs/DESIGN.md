# Wingman Design Specification

## Overview

Wingman is a local-first career intelligence platform that helps users discover, evaluate, and pursue high-quality career opportunities. It is explicitly **not** an autonomous job application system.

## Goals

- Canonical career profile
- Opportunity assessment
- Company intelligence
- Relationship intelligence
- Daily prioritization
- Interview preparation
- Human-approved drafting

## Non-goals

- Automatic applications
- Automatic messaging
- Mass resume optimization
- Autonomous networking

## Architecture

Presentation (CLI/Web)
    |
Application Services
    |
+-----------------------------+
| Agents                      |
| - Profile Curator           |
| - Opportunity Analyst       |
| - Company Researcher        |
| - Connector                 |
| - Operator                  |
| - Writer                    |
| - Critic                    |
+-----------------------------+
    |
Domain Model
    |
Infrastructure
    |- SQLite
    |- Local Files
    |- Provider Adapters
    |- Connectors

## Agent Contracts

Each agent defines:
- Mission
- Inputs
- Outputs
- Allowed tools
- Forbidden actions
- Validation
- Evaluation fixtures

## Data Model

Entities:
- CareerProfile
- Company
- Opportunity
- Person
- Interaction
- Recommendation

All records include provenance metadata.

## Model Strategy

Build:
- Codex (GPT-5.5) for implementation, tests, and maintenance.

Runtime:
- Deterministic code where possible.
- Small models for extraction.
- Balanced models for synthesis.
- Frontier reasoning models for high-consequence recommendations.

Never hard-code provider-specific assumptions.

## Privacy

- Local-first storage
- Human approval before outbound actions
- Least-privilege connectors
- Evidence required for recommendations

## Phases

0. Foundation
1. Career Brain
2. Opportunity Inbox
3. Company Research
4. Relationship Intelligence
5. Daily Operator
6. Drafting
7. Interview Coach
8. Controlled Connectors

Every phase must deliver an independently useful capability.
