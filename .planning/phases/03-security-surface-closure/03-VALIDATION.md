---
phase: 03
slug: security-surface-closure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-08
---

# Phase 03 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest -q tests/test_api_routes.py tests/test_middleware.py tests/test_enricher_integration.py -o addopts='' -k 'create_session or rate limit or kitchen or rag or context or owner or cache'` |
| **Full suite command** | `pytest -m "not integration" -v` |
| **Estimated runtime** | ~120 seconds |

---

## Sampling Rate

- **After every task commit:** Run the plan-specific targeted `pytest -q ... -o addopts=''` command.
- **After Wave 1:** Re-run the session and kitchen targeted checks before starting RAG/cache work.
- **After Wave 2:** Run `pytest -m "not integration" -v`.
- **Before `/gsd-verify-work`:** Full suite must be green.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | SEC-01 | T-03-01 | `create_session()` throttles authenticated callers by user identity at the configured ceiling | targeted pytest | `pytest -q tests/test_api_routes.py tests/test_middleware.py -o addopts='' -k 'create_session or rate limit'` | ✅ | ⬜ pending |
| 03-01-02 | 01 | 1 | SEC-01 | T-03-02 | unauthenticated fallback requests use IP-based throttling at a tighter ceiling without changing route contracts | targeted pytest | `pytest -q tests/test_api_routes.py tests/test_middleware.py -o addopts='' -k 'create_session or rate limit'` | ✅ | ⬜ pending |
| 03-02-01 | 02 | 1 | SEC-02 | T-03-03 | kitchen config rejects burner/rack values above the selected ceilings | targeted pytest | `pytest -q tests/test_api_routes.py -o addopts='' -k 'kitchen or burner or equipment'` | ✅ | ⬜ pending |
| 03-02-02 | 02 | 1 | SEC-02 | T-03-04 | cross-field invariants reject impossible burner and second-oven combinations instead of normalizing them | targeted pytest | `pytest -q tests/test_api_routes.py tests/test_phase6_unit.py -o addopts='' -k 'kitchen or burner'` | ✅ | ⬜ pending |
| 03-03-01 | 03 | 2 | SEC-03 | T-03-05 | mismatched-owner Pinecone chunks are logged and dropped before enrichment uses them | targeted pytest | `pytest -q tests/test_enricher_integration.py -o addopts='' -k 'owner or rag'` | ✅ | ⬜ pending |
| 03-03-02 | 03 | 2 | PERF-02 | T-03-06 | repeated RAG retrievals within one pipeline run reuse a per-run cache rather than issuing duplicate Pinecone queries | targeted pytest | `pytest -q tests/test_enricher_integration.py -o addopts='' -k 'cache or context or rag'` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Existing route, middleware, and enricher test harnesses can exercise the new targeted regressions without external API keys.

---

## Manual-Only Verifications

- Inspect the final diff to confirm the authenticated session-rate-limit path keys by user identity rather than remote address alone.
- Inspect the final diff to confirm kitchen-config invalid relational input is rejected instead of silently normalized.
- Inspect the final diff to confirm the RAG cache is scoped to a single pipeline run rather than a cross-session global store.

---

## Validation Sign-Off

- [ ] All tasks have targeted `pytest` verification commands
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all missing references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
