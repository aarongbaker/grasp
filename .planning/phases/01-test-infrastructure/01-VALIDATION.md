---
phase: 01
slug: test-infrastructure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-08
---

# Phase 01 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest -q tests/test_admin_invites.py tests/test_invite_gating.py tests/test_state_machine.py tests/test_deploy_readiness.py tests/test_api_routes.py tests/test_ingestion_tasks.py tests/test_pipeline_tasks.py tests/test_kitchen_edge_cases.py -o addopts=''` |
| **Full suite command** | `pytest -m "not integration" -v` |
| **Estimated runtime** | ~90 seconds |

---

## Sampling Rate

- **After every task commit:** Run the plan-specific targeted `pytest -q ... -o addopts=''` command
- **After every plan wave:** Run `pytest -m "not integration" -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | TEST-01 | T-01-01 | Admin and invite suites use shared fixtures without bypassing auth | targeted pytest | `pytest -q tests/test_admin_invites.py tests/test_invite_gating.py tests/test_state_machine.py tests/test_deploy_readiness.py -o addopts=''` | ✅ | ⬜ pending |
| 01-02-01 | 02 | 2 | TEST-02 / TEST-05 | T-01-02 | `/health` and equipment CRUD return the documented HTTP contracts | targeted pytest | `pytest -q tests/test_api_routes.py -o addopts='' -k 'health or equipment'` | ✅ | ⬜ pending |
| 01-03-01 | 03 | 2 | TEST-03 / TEST-04 | T-01-03 | Celery task failures are captured without leaking unhandled exceptions | targeted pytest | `pytest -q tests/test_ingestion_tasks.py tests/test_pipeline_tasks.py -o addopts=''` | ✅ | ⬜ pending |
| 01-04-01 | 04 | 2 | TEST-06 | T-01-04 | Scheduler edge cases fail safely and remain regression-covered for Phase 4 | targeted pytest | `pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts='' -k 'kitchen or equipment'` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

All phase behaviors have automated verification.

---

## Validation Sign-Off

- [ ] All tasks have targeted `pytest` verification commands
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all missing references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

