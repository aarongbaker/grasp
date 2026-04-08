# Phase 3: Security Surface Closure - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-08
**Phase:** 03-security-surface-closure
**Areas discussed:** Session rate limiting, Kitchen config validation, RAG ownership and cache policy

---

## Session Rate Limiting

| Option | Description | Selected |
|--------|-------------|----------|
| Per authenticated user, keep `30/minute` | Minimal behavior change; weakest tightening | |
| Per authenticated user, tighten to `10/minute` | Stronger authenticated throttle without fallback logic | |
| Hybrid: per authenticated user plus IP fallback when auth is missing | Strongest scoped hardening for both authenticated and unauthenticated cases | ✓ |
| Other | Custom ceiling or burst model | |

**User's choice:** Hybrid policy.
**Notes:** Follow-up choices locked the ceilings at `10/minute` per authenticated user and `5/minute` for unauthenticated IP fallback.

---

## Kitchen Config Validation

| Option | Description | Selected |
|--------|-------------|----------|
| Numeric bounds only | Ceiling checks only | |
| Bounds plus cross-field invariants | Adds relational validation such as burner-count consistency and second-oven rules | ✓ |
| Full strict model validation | Adds bounds, cross-field invariants, and extra descriptor-level strictness beyond the scoped requirement | |
| Other | Custom validation policy | |

**User's choice:** Bounds plus cross-field invariants.
**Notes:** Follow-up decisions locked `max_burners` at `10`, max equipment count at `20`, and invalid relational data must be rejected with validation errors rather than normalized.

---

## RAG Ownership And Cache Policy

| Option | Description | Selected |
|--------|-------------|----------|
| Drop mismatched-owner chunks and log server-side | Preserves graceful degradation and keeps suspicious chunks out | ✓ |
| Drop, log, and attach a recoverable pipeline error | More visible but expands error surface | |
| Fail the whole enrichment step | Strongest posture, highest user impact | |
| Other | Custom ownership handling policy | |

**User's choice:** Drop mismatched-owner chunks and log server-side.
**Notes:** Follow-up decision locked cache scope to one pipeline run, keyed by session/query context rather than shared across sessions.

---

## the agent's Discretion

- Exact slowapi implementation details for hybrid user/IP keying
- Exact validator placement between request models and persisted kitchen config models
- Exact in-memory cache helper shape inside the enricher path

## Deferred Ideas

None.
