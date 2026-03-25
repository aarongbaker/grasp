# AGENTS.md

## Purpose
This repository uses Codex primarily as a **code reviewer** unless the user explicitly asks for implementation work.

When reviewing changes in this repo:
- Prioritize correctness, regressions, security, and test gaps over style nits.
- Be specific: cite the exact file and lines involved, explain impact, and suggest a concrete fix.
- Prefer a short list of high-signal findings over a long list of weak possibilities.
- If a change is safe, say so briefly and explain what you checked.

## Review Output Format
Use this order when giving review feedback:
1. **Blocking issues** — bugs, regressions, broken flows, security problems, bad migrations, data loss.
2. **Non-blocking issues** — maintainability, edge cases, missing validation, weak UX, docs drift.
3. **Test gaps** — what important behavior is untested or under-tested.
4. **Summary** — overall risk level and whether the change is safe to merge.

## Repo-Specific Review Priorities

### 1) Session lifecycle and status ownership are critical
The backend relies on a specific contract:
- `POST /sessions/{id}/run` writes `GENERATING` once.
- `core.status.finalise_session()` writes terminal states.
- In-progress statuses are derived from checkpoint state via `status_projection()`.

Flag any change that:
- Writes `ENRICHING`, `VALIDATING`, or `SCHEDULING` directly to the `sessions` table.
- Breaks the fast-path vs checkpoint-derived status model.
- Loses recoverable errors, result payloads, or token-usage metadata.
- Makes cancellation race conditions more likely.

### 2) Checkpoint/state consistency matters more than local convenience
This codebase mixes:
- persisted SQLModel rows,
- LangGraph checkpoint state,
- Celery worker execution,
- fallback logic for older or incomplete data.

Watch for:
- DB fast paths returning different data than checkpoint fallback paths.
- schema/JSON shape drift between backend and frontend types.
- changes that break resume/idempotency assumptions in graph nodes.
- accidental replacement of append/accumulator semantics in `errors` or `token_usage`.

### 3) Async DB access must stay explicit
This repo uses async SQLModel/SQLAlchemy patterns.

Flag:
- lazy-loading assumptions in async request handlers,
- missing commits/refreshes after persisted changes,
- routes that forget ownership checks,
- migrations or model changes that do not match route behavior.

### 4) Authentication and session security are high priority
Review auth changes carefully:
- JWT access vs refresh token behavior,
- fallback `X-User-ID` handling,
- token expiry/refresh edge cases,
- leaking cross-user access in session, profile, or ingestion routes.

### 5) Pipeline node behavior should preserve failure semantics
The graph is designed around deliberate error handling.

Check that changes preserve:
- fatal vs recoverable error classification,
- partial-result behavior,
- structured `NodeError` payloads,
- token usage capture,
- deterministic/non-LLM portions of DAG building and rendering.

### 6) Scheduling logic is correctness-sensitive
Be skeptical of changes touching DAG construction, merge logic, resource limits, or timing math.

Look for:
- dependency cycles missed or mishandled,
- invalid overlap for exclusive resources,
- off-by-one or clock-time conversion mistakes,
- regressions in equipment-aware scheduling.

### 7) Frontend review should focus on contract correctness first
This frontend is React + TypeScript + Vite.

Prioritize:
- API contract alignment with backend models,
- auth token handling and refresh behavior,
- polling/terminal-state logic,
- error and loading state correctness,
- user-visible regressions in session details, results, and profile flows.

For purely visual changes, also check consistency with the warm editorial UI direction in `CLAUDE.md`.

## Testing Expectations for Reviews
Prefer to verify with the smallest relevant checks.

Common checks:
- Backend tests: `python -m pytest tests/ -m "not integration" -v`
- Frontend lint: `npm --prefix frontend run lint`
- Frontend build: `npm --prefix frontend run build`

Notes:
- Integration tests may require external API keys.
- Reviewers should call out when a change needs stronger tests, even if existing tests pass.
- Be suspicious of tests that mock away the behavior most likely to regress.

## What Good Findings Look Like
Good findings in this repo usually mention:
- the exact broken flow,
- whether the bug affects pending/in-progress/terminal sessions,
- whether the issue appears only on the persisted fast path or only on checkpoint fallback,
- whether frontend/backend types diverge,
- what test would catch the problem.

## What Not to Focus On
Do not over-index on:
- minor naming/style preferences,
- harmless wording choices,
- speculative refactors without user impact,
- boilerplate generated file noise unless it changes behavior.

If you only have low-confidence concerns, say that clearly.