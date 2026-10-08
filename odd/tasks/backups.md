# Database Backups (manual and automatic)

## Objective
Let the SUPERADMIN create full-database backups on demand and have the platform create them automatically on a schedule, storing every dump outside the application host.

## Problem and why
SI2 holds the financial data of every cooperative in one Supabase Postgres database. There is no backup feature today. Render's filesystem is ephemeral, so dumps must live in external storage. Render free instances sleep, so an in-process scheduler is unreliable.

## Decisions (user-approved, 2026-10-07)
- Option A: full-database `pg_dump` (all tenants), SUPERADMIN only. No per-cooperative backup.
- `pg_dump` runs inside the backend; the backend deploys on Render via a new `Dockerfile` that installs `postgresql-client` (major version configurable, default 17, which can dump Postgres 15 and 17 servers).
- Dumps go to a private Supabase Storage bucket.
- The automatic backup is a GitHub Actions scheduled workflow that calls the backend with a shared secret token.
- Restore stays a manual, out-of-app operation (`pg_restore`), documented only.

## Scope
Backend only (Codex). The web screen is a later, separate task (Antigravity).

## API contract (`/api/v1/backups`)
| Method | Path | Auth | Result |
|---|---|---|---|
| POST | `/backups` | JWT, SUPERADMIN | 202 `{id, tipo:"MANUAL", estado:"EN_PROCESO", iniciado_en}`; 409 if a backup is already `EN_PROCESO` |
| POST | `/backups/automatico` | header `X-Backup-Token` = `BACKUP_CRON_TOKEN` (no JWT); 401 on missing/wrong token, 503 if the token is not configured | 202, same body with `tipo:"AUTOMATICO"`; 409 if one is running |
| GET | `/backups?tipo=&estado=` | JWT, SUPERADMIN | list, newest first |
| GET | `/backups/{id}` | JWT, SUPERADMIN | detail; 404 if missing |
| GET | `/backups/{id}/descarga` | JWT, SUPERADMIN | `{url, expira_en}` short-lived signed URL; 409 unless `COMPLETADO` |

Backup item fields: `id, tipo, estado, archivo, tamano_bytes, checksum_sha256, error, usuario_id, usuario_nombre, iniciado_en, finalizado_en`.

## Data
Migration `migrations/030_backups.sql` (029 is taken by the CU-W23 seed on another branch): table `backup` with
`tipo` (MANUAL|AUTOMATICO), `estado` (EN_PROCESO|COMPLETADO|FALLIDO), `archivo` (storage object path), `tamano_bytes`, `checksum_sha256`, `error`, `usuario_id` (nullable FK, null for automatic), `iniciado_en`, `finalizado_en`. Model in `app/models/models.py`. Not tenant-scoped.

## Behavior
- Execution runs as a background task: `pg_dump --format=custom` into a temp file → SHA-256 → upload to `<bucket>/<YYYY>/<MM>/si2_<timestamp>_<tipo>.dump` → row `COMPLETADO`; any failure → `FALLIDO` with a sanitized error (never a connection string, password, or key). The temp file is always removed.
- A row left `EN_PROCESO` for longer than `BACKUP_STALE_MINUTES` (default 60) is marked `FALLIDO` before the concurrency check, so a restart never blocks backups forever.
- Retention: after a successful automatic backup, keep the newest `BACKUP_RETENTION_AUTOMATIC` (default 7) automatic `COMPLETADO` backups and delete older ones from storage and the table. Manual backups are never auto-deleted.
- Each start, completion, failure, and retention deletion writes a bitacora entry using the existing audit helper.
- `pg_dump` and storage sit behind small ports (dumper and storage adapter) so tests inject fakes; Storage uses the Supabase Storage REST API through `httpx` (already a dependency).

## Configuration (Settings, all optional with safe defaults)
`BACKUP_DATABASE_URL` (direct or session-mode connection; falls back to `DATABASE_URL`), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `BACKUP_BUCKET` (default `backups`), `BACKUP_CRON_TOKEN`, `BACKUP_RETENTION_AUTOMATIC`, `BACKUP_STALE_MINUTES`, `BACKUP_SIGNED_URL_SECONDS` (default 300).

## Deploy artifacts
- `Dockerfile` + `.dockerignore`: Python slim base, `postgresql-client-${PG_MAJOR}` from the PGDG apt repo, `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`.
- `.github/workflows/backup-cron.yml`: daily at 06:00 UTC (02:00 Bolivia) plus `workflow_dispatch`; `curl` POST to `${{ secrets.BACKUP_API_URL }}/api/v1/backups/automatico` with `X-Backup-Token: ${{ secrets.BACKUP_CRON_TOKEN }}`, retrying long enough for a Render cold start; fail on non-2xx.
- README section: Render Docker switch, Supabase connection string and bucket, env vars, GitHub secrets, and the manual restore command.

## Tasks
- [x] BK-01 — Migration, model, settings, and backup service with dumper/storage ports, stale handling, retention, and bitacora. Done; RED was not observed for the original implementation (TDD exception recorded), RED observed for the review correction. Route: delegated (Codex).
- [x] BK-02 — `/backups` endpoints with SUPERADMIN and cron-token auth, router registration, and endpoint tests. Done; same TDD exception as BK-01. Route: delegated (Codex).
- [x] BK-03 — Dockerfile, .dockerignore, GitHub Actions workflow, and README operator docs. Done; the Docker image build is unverified locally and is verified in BK-05 on Render. Route: delegated (Codex).
- [x] BK-04 — Orchestrator review, full suite, commits. Review removed the dead `run()` path whose tests bypassed production code, added redacted pg_dump diagnostics, moved the password out of argv, and aligned Python to 3.13. Evidence: `pytest tests/test_backups.py` 20 passed; full suite 81 failed / 435 passed, with the identical 81 failures reproduced on `main` 5609029 (pre-existing local-data failures). Route: inline (Claude).
- [ ] BK-05 — User actions: Render to Docker, Supabase bucket and connection string, env vars, GitHub secrets; then an end-to-end manual and automatic backup.
- [ ] BK-06 — Web Backups screen (Antigravity), separate task.

## Acceptance criteria
- Non-SUPERADMIN users get 403; a wrong or missing cron token gets 401.
- A manual and an automatic backup each end `COMPLETADO` with size and checksum, and the file exists in the bucket (verified in BK-05).
- A failure leaves `FALLIDO` with a sanitized error and no temp file.
- Concurrency, stale recovery, and retention behave as specified.
- No test touches a real database dump, Supabase Storage, or the network.

## Checks
`pytest tests/test_backups.py -q`, then the full suite when safe. For the bounded correction, record observed RED before GREEN per corrected behavior; the original BK-01–BK-03 behaviors still have incomplete strict-TDD evidence, as detailed below.

## Progress
- 2026-10-07: branch `feat/backups` created from `main` (5609029); document written.
- 2026-10-07: BK-01 implemented in `migrations/030_backups.sql`, `app/models/models.py`, `app/core/config.py`, and `app/services/backups.py`. The migration enforces one active backup with a partial unique index; API creation also serializes start checks with a transaction advisory lock. Service tests use injected fake dumper/storage/repository, with no dump, Storage, network, or database access. Sanitized error text is fixed and does not persist tool output. Initial attempted RED run via system `pytest` produced 4 import failures because that interpreter lacked project dependencies; after switching to the project's `.venv`, the first run exposed test harness errors (fake repository not returning persisted row, route inclusion shape) rather than a valid pre-implementation behavior RED. Strict RED-before-GREEN evidence is therefore incomplete and not claimed.
- 2026-10-07: BK-02 registered `app/api/v1/endpoints/backups.py` at `/api/v1/backups`; manual/list/detail/download require SUPERADMIN, automatic trigger uses constant-time shared-token comparison and rejects unconfigured token with 503. Direct endpoint tests cover wrong/missing token (401), unset token (503), and router paths. Existing bitacora schema requires non-null `usuario_id`; automatic audit uses a captured active SUPERADMIN actor while backup ownership remains null (see audit fail-closed follow-up below).
- 2026-10-07: BK-03 added Docker image with configurable PostgreSQL client major (17 default), daily 06:00 UTC GitHub Actions workflow with bounded retry, Docker exclusions, and Render/Supabase/secrets/restore operator documentation in README.
- Focused verification: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_backups.py -q` — 9 passed, 1 existing Pydantic config deprecation warning. `python3 -m py_compile app/services/backups.py app/api/v1/endpoints/backups.py app/models/models.py app/core/config.py app/api/v1/router.py` — passed.
- Full suite remains pending: existing suite contains database-backed integration tests, and this task explicitly forbids connecting to any local or remote database; no full-suite run was attempted.
- Pattern note: automated backups have a nullable owner by contract, but audit records cannot because the existing `bitacora.usuario_id` is NOT NULL. The actor ID is now captured at request start and carried into the background worker.
- 2026-10-07 follow-up: independent verification found that looking up an active SUPERADMIN again inside the background worker could silently skip audit when no actor existed or the actor became inactive. `_start` now fails closed with 503 before creating/updating backups if it cannot resolve an audit actor, writes stale/start audit events with that actor, and passes the actor's user ID into the background task. The worker uses that captured ID for completion/failure/retention audit without requiring the actor to remain active; audit calls are no longer conditional. The start audit row itself prevents deletion under the existing non-cascading bitacora FK.
- Follow-up RED: before production changes, `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_backups.py -q` — 4 failed, 12 passed. Failures observed: no 503 when system actor missing; actor ID not carried to scheduled task; background execute rejected captured actor arg in both fake-success and storage-failure paths. Other added coverage (automatic retention preserving manual row, signed URL status/path, and route-level non-SUPERADMIN denial) already passed against existing implementations, so no false RED is claimed for them.
- Follow-up GREEN: after production changes and test refinements, the same focused command — 16 passed, 2 dependency deprecation warnings. The fake background tests verify exact dump checksum/size, completion audit despite no currently-active actor lookup, sanitized storage failure/failure audit/temp cleanup, and automatic retention that leaves manual rows untouched. Tests make no real dump, database, storage, or network calls.
- Current syntax check: `python3 -m py_compile app/services/backups.py app/api/v1/endpoints/backups.py tests/test_backups.py` — passed. Full suite remains intentionally unrun due module-level DB access found by independent verification.
- 2026-10-07 bounded correction: removed duplicate `BackupService.run()`, `make_backup_service()`, and its TypeError fallback; endpoint `_service()` is the sole factory and service tests use the production `_start`/`execute` paths. `PgDumper` now strips URI password/query credentials from argv, supplies password via `PGPASSWORD`, and logs bounded stderr/exit-code diagnostics after redacting complete database URLs and password values. Docker base is `python:3.13-slim`.
- Correction RED: after test rewrites and before production edits, `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_backups.py -q` — 3 failed, 17 passed. Valid observed failures: pg_dump argv contained the password and did not set `PGPASSWORD`; pg_dump failure had no useful redacted diagnostic log; Dockerfile used Python 3.11. A prior attempt also exposed a test-only missing `monkeypatch` fixture; it was fixed before this recorded RED.
- Correction GREEN: same focused command after implementation — 20 passed, 2 dependency deprecation warnings. Coverage now runs stale/409 checks through `_start`, async success/failure and retention through `execute`/real `_apply_retention`, and includes retention-cleanup failure, manual-row preservation, signed URL guard/path, non-SUPERADMIN 403, pg_dump fake argv/env/log redaction, and Docker base assertion.
- TDD boundary: actor audit fail-closed and original backup behavior still lack a complete original RED-before-GREEN sequence; several rewritten tests already passed against existing behavior. BK-01/02/03 remain unchecked rather than overstating strict-TDD completion. The user confirmed 81 full-suite failures also occur on main; no full suite was rerun.
- 2026-10-07: BK-01..BK-04 complete; next is BK-05 (user setup) and then BK-06.
