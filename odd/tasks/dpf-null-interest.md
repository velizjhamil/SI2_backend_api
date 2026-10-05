# DPF Portfolio Null Net Interest

## Objective
Prevent the DPF portfolio from returning HTTP 500 when legacy rows have no `interes_neto`, and present missing financial data honestly in the UI.

## Problem and motivation
`listar_dpf` formats `dpf.interes_neto` as a float even though the model and migration allow `NULL`. That crashes the entire portfolio response. Treating missing interest as zero would fabricate a financial value.

## Scope and constraints
- Authorized: backend API serialization and regression test; frontend portfolio and certificate displays for null net interest; this task document.
- Do not query or modify the deployed Render service or its database.
- Preserve null as unavailable; do not silently backfill or coerce to zero absent an approved domain rule.
- Inspect sibling DPF serializers for the same nullable-field failure and fix only proven affected paths within scope.
- TDD: enabled by session configuration. RED → GREEN → REFACTOR; preserve exact test commands.
- Branches: `fix/dpf-null-interest` in backend and frontend repositories.
- Delivery strategy: `ask-on-risk`; estimated diff below 400 authored lines, one work-unit commit per repository if review/delivery gates permit.

## Tasks
- [ ] DPF-NULL-01 — Added regression coverage that sets a persisted DPF's `interes_neto` to SQL NULL; backend serialization now preserves null in portfolio and certificate serializers. Verification is pending because pytest collection is blocked by missing FastAPI.
- [x] DPF-NULL-02 — Portfolio cards and the certificate modal render nullish net interest as `—`; the certificate maturity total is omitted unless principal and interest are both known finite values. Added Node coverage for nullable formatting and maturity-total calculation.
- [ ] DPF-NULL-03 — Verification and native RDD gates are incomplete; changes remain unstaged and uncommitted.

## Acceptance criteria
- A DPF portfolio containing a null net-interest value returns successfully.
- The API response preserves absence/null rather than presenting a made-up numeric amount.
- Portfolio and certificate views display an explicit unavailable marker for missing net interest.
- The certificate does not display a numeric maturity total when required financial inputs are missing.
- Non-null interest values and other portfolio fields remain unchanged.

## Verification
- Backend: `pytest tests/test_dpf.py` (include exact observed result; if fixture/database prevents it, report that failure honestly).
- Frontend: `npm test`, `npm run lint`, and focused tests in the existing Node test harness.
- `git diff --check` in each repository.
- Runtime deployment test: N/A; no remote access or deployed mutation is authorized.

## Progress
- [x] Confirmed the traceback reaches `listar_dpf` formatting `interes_neto` and that the field is nullable in model/migration.
- [x] Created isolated feature branches before source edits.
- [x] Backend implementation: `listar_dpf` (and the sibling certificate serializer) now returns JSON null for missing net interest instead of formatting null as a float.
- [x] Frontend TDD regression: focused test first failed because the null-aware maturity-total helper was absent; after implementation, `node --test tests/dpfUtils.test.mjs` — 2 passed.
- [x] Frontend checks: parent spot-check `npm test` — 22 passed, 0 failed; `npm run lint` — exit 0 with existing warnings in unrelated admin/savings pages.
- [x] `git diff --check` — exit 0 in both backend and frontend repositories.
- [ ] Backend verification: system `pytest tests/test_dpf.py` cannot collect because `fastapi` is missing; `.venv/bin/pytest` cannot import `app` unless `PYTHONPATH=.` is set, after which all 9 tests are skipped because PostgreSQL is unavailable. The regression has not executed against a database.
- [ ] Native RDD: status requires explicit selection of the untracked task tracker (`odd/tasks/dpf-null-interest.md`); the exact `external.select_intended_untracked` capture tool is unavailable. No staging or commit was attempted. Deployment check: N/A (remote access unauthorized).

## Next step
Resume with an available supported native untracked-selection capture, then complete the RDD lifecycle before any stage/commit. Run the backend regression in an environment with FastAPI and PostgreSQL available. Do not access the deployment or production database.
