---
phase: 02
slug: correctness-fixes
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-08
---

# Phase 02 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest -q tests/test_status_finalisation.py tests/test_api_routes.py tests/test_embedder.py -o addopts='' -k 'finalise or cancel or embed'` |
| **Full suite command** | `pytest -m "not integration" -v` |
| **Estimated runtime** | ~90 seconds |

---

## Sampling Rate

- **After every task commit:** Run the plan-specific targeted `pytest -q ... -o addopts=''` command.
- **After Wave 1:** Re-run the status/cancel targeted command before moving to embedder work.
- **After Wave 2:** Run `pytest -m "not integration" -v`.
- **Before `/gsd-verify-work`:** Full suite must be green.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | BUG-02 | T-02-01 | `finalise_session()` acquires a row lock before checking for `CANCELLED` and does not fall back to `db.get()` | targeted pytest | `pytest -q tests/test_status_finalisation.py -o addopts=''` | ✅ | ⬜ pending |
| 02-01-02 | 01 | 1 | BUG-02 | T-02-02 | `cancel_pipeline()` locks the row before writing `CANCELLED` and remains idempotent for already-terminal sessions | targeted pytest | `pytest -q tests/test_status_finalisation.py tests/test_api_routes.py -o addopts='' -k 'cancel or finalise'` | ✅ | ⬜ pending |
| 02-02-01 | 02 | 2 | BUG-01 / PERF-01 | T-02-03 | `embed_and_upsert_chunks()` opens one `AsyncOpenAI` client per invocation and reuses it across batch + fallback paths | targeted pytest | `pytest -q tests/test_embedder.py -o addopts='' -k 'client or context'` | ✅ | ⬜ pending |
| 02-02-02 | 02 | 2 | PERF-01 | T-02-04 | Fallback embedding uses bounded gather semantics so partial failures leave `None` holes without aborting successful upserts | targeted pytest | `pytest -q tests/test_embedder.py -o addopts='' -k 'fallback or partial'` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Existing infrastructure can run the new targeted status/cancel and embedder suites.

---

## Manual-Only Verifications

- Inspect the final diff to confirm `app/core/status.py` no longer uses `db.get(Session, session_id)` inside `finalise_session()`.
- Inspect the final diff to confirm `app/ingestion/embedder.py` contains exactly one `AsyncOpenAI(` construction in `embed_and_upsert_chunks()`.

---

## Validation Sign-Off

- [ ] All tasks have targeted `pytest` verification commands
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all missing references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
