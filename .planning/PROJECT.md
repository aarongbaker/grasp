# GRASP — Deployment & Production Readiness

## What This Is

A web-based dinner party planning tool for private chefs and home cooks. Wraps LLM-driven menu generation, RAG-backed recipe retrieval, DAG-based schedule optimization, and food costing into a single interface with a Gantt chart timeline view.

## Core Value

The cook can see at a glance what to do and when — every step visible, accurately timed, in one unified view.

## Current Milestone: v1.1 Deploy to Production

**Goal:** Get GRASP running on a public URL so 2-5 friends can try it out.

**Target features:**
- Containerized deployment (Docker)
- Hosted on free-tier platform with public URL
- PostgreSQL + pgvector, Redis, Celery worker all running
- Environment/secrets management for API keys
- Basic production hardening (CORS, HTTPS, error handling)

## Requirements

### Validated

- ✓ Every TimelineEntry renders as a visible bar in its recipe lane — v1.0
- ✓ Bar widths reflect step durations proportionally — v1.0
- ✓ Bar positions reflect step start times accurately — v1.0
- ✓ Buffer uncertainty zones shown visually on bars — v1.0
- ✓ X-axis displays absolute clock times at sensible intervals — v1.0
- ✓ Prep-ahead restricted to long-lead tasks (brining, marinating, stock-making) — v1.0
- ✓ Renderer time-gate filters by hours/days window — v1.0
- ✓ All steps in single chronological timeline — no separate prep-ahead section — v1.0
- ✓ Inline prep-ahead tags for steps that can be done ahead — v1.0
- ✓ Gantt renders all steps with gaps and parallel tasks visible — v1.0
- ✓ Backend returns unified timeline list — v1.0
- ✓ Session delete from dashboard — v1.0-hotfix
- ✓ API error surfacing on dashboard and session detail — v1.0-hotfix

### Active

(Defined in REQUIREMENTS.md)

### Recently Validated (v1.1)

- ✓ Auth bypass removed, JWT-only endpoints, CORS lockdown — Phase 4
- ✓ Docker image builds on linux/amd64, dual-mode API + Celery — Phase 5
- ✓ .env.example documents all required environment variables — Phase 5

### Out of Scope

- Drag-to-reschedule — visualization only, not a scheduling editor
- Custom domain setup — platform subdomain is fine for now
- CI/CD pipeline — manual deploy is fine for 2-5 users
- Auto-scaling — single instance sufficient for small group
- Monitoring/alerting — not needed at this scale

## Context

Shipped v1.0 with 1,667 net lines across 39 files (Python backend + React frontend).
Tech stack: React with CSS Modules, FastAPI, LangGraph pipeline, PostgreSQL + pgvector, Redis, Celery.
Full pipeline: generator → enricher → validator → dag_builder → dag_merger → renderer.
138 tests passing (unit + fixture-based).
JWT auth with 60-minute access tokens and refresh rotation.

## Constraints

- **Budget**: Free or near-free hosting tiers only
- **Tech stack**: React with CSS Modules, FastAPI, no new application dependencies
- **Audience**: 2-5 friends, platform subdomain acceptable
- **Secrets**: Anthropic API key + Pinecone API key must be securely managed
- **Database**: Needs pgvector extension for RAG pipeline

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use `clock_time` field for x-axis | Already provided by backend, gives absolute times | ✓ Good |
| Keep lane-per-recipe layout | Steps as individual bars grouped by recipe | ✓ Good |
| Unified timeline (v1.0 Phase 3) | Prep-ahead steps depend on day-of steps; separate section misleading | ✓ Good |
| MERGE_GAP_MINUTES = -1 | Each step gets own bar for clear timing visibility | ✓ Good |
| Platform subdomain | Custom domain not needed for small friend group | — Pending |
| Free-tier hosting | Budget constraint, sufficient for 2-5 users | — Pending |

---
*Last updated: 2026-03-21 after Phase 5 (Containerization) complete*
