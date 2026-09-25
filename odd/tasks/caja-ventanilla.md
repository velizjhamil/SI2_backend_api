# Feature: Caja de ventanilla (CU-W10, CU-W11, CU-W13)

Source: `/home/yimy/proyectos/si2/casosDeUso.md` (ignore "Responsables").

## Objective
Cashier (Cajero) web flows: open a cash register session with an initial cash amount (CU-W10), process cash deposits into savings accounts (CU-W11), and process internal transfers between accounts of the same cooperative (CU-W13).

## Current state (2026-09-24)
- Tables `caja(id, nombre, estado)` and `control_caja(id, monto_apertura, monto_cierre, saldo_sistema, fecha_apertura, fecha_cierre, estado, caja_id, usuario_id)` exist in `bd.sql`; no SQLAlchemy models, no endpoints. `caja` has no `cooperativa_id` and no limit.
- `transaccion.control_caja_id` already exists.
- `POST /api/v1/ahorros/cuentas/{id}/depositos` exists but is not tied to a cash session and has no depositor data.
- `POST /api/v1/ahorros/transferencias` is socio self-service only (own accounts, canal MOVIL). Helpers `_registrar_transferencia` and the ascending-id `FOR UPDATE` locking pattern live in `app/api/v1/endpoints/ahorros.py`.
- Frontend `src/modules/cajero/pages/CajaPage.jsx` is a 5-line placeholder; route `/cajero/caja` already exists.

## Decisions (orchestrator)
- New router `app/api/v1/endpoints/caja.py` mounted at `/api/v1/caja`. Auth: `require_operaciones`; tenant scope = user's `cooperativa_id` (SUPERADMIN excluded from operating a caja).
- Migration `migrations/009_sprint4_caja_ventanilla.sql`, idempotent, style of 004–007:
  - `caja.cooperativa_id BIGINT REFERENCES cooperativa(id)` (nullable), `caja.monto_maximo_efectivo NUMERIC(12,2) NOT NULL DEFAULT 50000.00` (the parametrized limit).
  - Partial unique index: one `control_caja` with `estado='ABIERTA'` per `usuario_id`, and one per `caja_id`.
  - `transaccion.depositante_nombre VARCHAR(150)`, `transaccion.depositante_ci VARCHAR(20)` (nullable).
- Cash-desk operations (deposit, transfer) require the operator to have an open `control_caja`. Deposit increases `control_caja.saldo_sistema`; transfer does not move cash.
- Receipts: the API returns all receipt data as JSON; the frontend renders a printable receipt (`window.print()`), no PDF in v1.
- Transfer via ventanilla: any two ACTIVE accounts of the same cooperative, same currency, different ids; canal `VENTANILLA`; reuse `_registrar_transferencia` by adding a `canal` parameter defaulting to `'MOVIL'` (existing behavior unchanged).
- Out of scope: closing the caja (cierre/arqueo), withdrawals, currency conversion, caja CRUD for admins.

## API contract (backend and frontend MUST follow exactly)
All amounts are decimal strings/numbers with 2 decimals. Errors: `{"detail": "<mensaje en español>"}`.

1. `GET /api/v1/caja/cajas` → `[{id, nombre, estado, monto_maximo_efectivo}]` cajas of the user's cooperative.
2. `GET /api/v1/caja/sesion-actual` → `200 {id, caja_id, caja_nombre, monto_apertura, saldo_sistema, fecha_apertura, estado}` or `404` if no open session.
3. `POST /api/v1/caja/aperturas` body `{caja_id, monto_apertura}` → `201` same shape as (2).
   - 400 if user already has an open session; 400 if the caja is already open by someone else; 400 if `monto_apertura <= 0` or `> monto_maximo_efectivo`; 404 if caja not in user's cooperative. Sets `caja.estado='ABIERTA'`, `saldo_sistema = monto_apertura`, `fecha_apertura = now()`. Bitácora modulo `CAJA`, accion `APERTURA`.
4. `GET /api/v1/caja/cuentas/buscar?q=<numero o CI>` → `[{id, numero, estado, saldo_disponible, moneda: {id, codigo_iso, nombre, simbolo} (MonedaOut), titular: {socio_id, nombre_completo, ci}}]` accounts of the user's cooperative matching exact account number or socio CI.
5. `POST /api/v1/caja/depositos` body `{cuenta_id, monto, depositante_nombre, depositante_ci}` → `201 {numero_operacion, fecha_hora, cuenta_numero, titular, monto, moneda, saldo_actualizado, depositante_nombre, depositante_ci, caja_nombre}`.
   - 400 without open session; 400 if account not ACTIVE or monto <= 0; 404 if account outside cooperative. Row in `transaccion` (tipo `DEPOSITO`, canal `VENTANILLA`, `control_caja_id`, depositor fields); `numero_operacion` = transaccion id. Bitácora `CAJA`/`DEPOSITO`.
6. `GET /api/v1/caja/transferencias/preview?origen=<numero>&destino=<numero>` → `{origen: {cuenta_id, numero, titular, moneda, saldo_disponible}, destino: {cuenta_id, numero, titular, moneda}}`; 404/400 with the same validations as (7) except amount.
7. `POST /api/v1/caja/transferencias` body `{cuenta_origen_id, cuenta_destino_id, monto, glosa?}` → `201 {numero_operacion, fecha_hora, origen: {numero, titular}, destino: {numero, titular}, monto, moneda, saldo_origen_actualizado, glosa}`.
   - 400: same account, not ACTIVE, different currency, insufficient funds, monto <= 0, no open session. 404 if any account outside cooperative. Atomic, ascending-id `FOR UPDATE`. `numero_operacion` = id of the TRANSFERENCIA_SALIDA row.

## Tasks
Backend — Codex (repo `SI2_backend_api`, branch `feat/caja-ventanilla`):
- [x] B0 Diagnose baseline test failures (26/32 fail with 401 at login in `tests/test_auth.py:47`); fix only test fixtures/env, never business logic; report cause — coopDB had only `socio@test.com` among the expected fixture users; the documented idempotent `app.db.seed_users` seed added the missing test users. Guarded `main` imports in DB-backed test modules so unavailable PostgreSQL skips collection instead of raising during import. Verified after seeding: 27 passed, 5 unrelated pre-existing test failures.
- [x] B1 Migration 009 + apply to local `coopDB`, idempotent (run twice) — `migrations/009_sprint4_caja_ventanilla.sql` applied twice successfully; second run skipped existing columns/indexes.
- [x] B2 Models `Caja`, `ControlCaja` + schemas — `Caja`/`ControlCaja` and caja/ventanilla request/response schemas added; availability test RED→GREEN.
- [x] B3 CU-W10 apertura + sesion-actual + cajas (RED→GREEN) — 4 focused tests cover no-session 404, opening, listing, and max-cash rejection.
- [x] B4 CU-W11 buscar cuentas + depósito (RED→GREEN) — 3 focused tests cover search by account/CI, session requirement, receipt, ledger and cash balance updates.
- [x] B5 CU-W13 preview + transferencia (RED→GREEN, incl. concurrency) — 4 focused tests cover preview, session requirement, receipt/paired rows/no cash movement, and concurrent overspend prevention.

Frontend — Antigravity (repo `SI2_frontend_web`, branch `feat/frontend-redesign`):
- [x] F1 `src/core/api/cajaApi.js` following `savingsApi.js` / `client.js` patterns
- [x] F2 `src/modules/cajero/pages/CajaPage.jsx`: session state (open form vs. active session card), tabs Depósito / Transferencia
- [x] F3 Deposit flow: search by número/CI, amount + depositor, printable receipt
- [x] F4 Transfer flow: origen/destino by number, preview with both titulares, confirm, printable receipt

Orchestrator — Claude:
- [x] V1 Review both diffs, run checks, end-to-end pass against local backend, commit per work unit

## Checks
- Backend TDD: strict. Runner: `.venv/bin/python -m pytest` (pytest installed into `.venv` on 2026-09-24; `requirements.txt` untouched). New tests in `tests/test_caja.py`, same style/skipif as `tests/test_transferencias.py`.
- Known environmental failures (baseline, before any change): 26 failed / 6 passed.
- Frontend: `pnpm build` OK; `oxlint` 0 errors, no warnings beyond the 8 pre-existing ones. No test runner.

## Route
Delegated direct: backend writer Codex, frontend writer Antigravity (writer + mapping triggers: 4+ files per side). Orchestrator reviews and commits.

## Progress
- 2026-09-24: exploration done, contract defined, branch `feat/caja-ventanilla` created in backend; delegated.
- 2026-09-24: B0–B5 completed in order. The new `tests/test_caja.py` suite passed 13/13 against local coopDB; existing transfer tests passed 10/10. Final `.venv/bin/python -m pytest -q`: 40 passed, 5 failed. The remaining failures are unrelated legacy expectations: `test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` expects 403 but receives 200, and four `tests/test_savings.py` cases omit currently-required `monto_apertura`, `numero_titulos`, and/or `valor_unitario` fields (422/KeyError).
- 2026-09-24 (orchestrator review): F1–F4 delivered by Antigravity (only new files + `CajaPage.jsx`; no router/client/package changes). Contract gap fixed by orchestrator: responses return `titular` as object `{socio_id, nombre_completo, ci}`; frontend rendered it as a string (React crash) in `TransferenciaTab.jsx` and `ReciboCajaModal.jsx` → now renders `titular.nombre_completo`. Frontend build OK, oxlint 8 pre-existing warnings / 0 errors.
- Backend verified: the 5 remaining failures reproduce identically on HEAD code (git archive of HEAD) → pre-existing. Changes to `ahorros.py` keep defaults (`canal='MOVIL'`, `modulo='AHORROS'`).
- E2E over HTTP (uvicorn :8011, cajero@test.com, temporary caja): no session 404 → open over max 400 → open 201 → double open 400 → search by CI → deposit 201 (receipt, saldo +200, saldo_sistema 1200) → preview same-account 400 / different currency 400 / OK → transfer insufficient 400 / OK 201. Test data removed and balances restored afterwards.
- Data gap (not code): existing `caja` id 1 "Ventanilla 1 - Central" has `cooperativa_id` NULL (invisible to every cajero) and a stale open `control_caja` id 1 from seed data. No admin UI to manage cajas (out of scope).
- Delivery: backend and frontend caja work committed on feature branches.
- 2026-09-24: frontend↔backend wiring verified through the Vite proxy (:5173 → :8000): all 7 caja endpoints consumed by cajaApi.js, payloads match Pydantic schemas, CORS preflight OK. Local data fixed: caja 1 assigned to cooperativa 1 and stale control_caja 1 closed (dev DB only; production may need the same backfill).
