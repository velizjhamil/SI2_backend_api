# Feature: Amortization schedule (CU-W24) and credit disbursement (CU-W26)

Plan: W20 ✅ → W21 ✅ → W23 p1 ✅ → **W24 + W26** → W27 → W23 part 2. Grouped because the schedule is generated and persisted at disbursement. Autonomous run authorized by the user.

## User stories (fictional, drafted by the orchestrator)
**CU-W24 Generación de Tablas de Amortización** — Actor: Oficial de crédito.
> Como oficial de crédito quiero generar el plan de pagos de un crédito aprobado (sistema francés o alemán) para mostrarle al socio sus cuotas, fechas e intereses antes y después del desembolso.

**CU-W26 Desembolso de Créditos** — Actor: Oficial de crédito / Administrador (cash: operator with an open caja).
> Como oficial de crédito quiero desembolsar un crédito aprobado a la cuenta de ahorro del socio o en efectivo por caja, para crear el crédito con su tabla de amortización definitiva y entregar los fondos.

Acceptance criteria:
1. Preview of the full schedule for an APROBADO request with a chosen disbursement date: per installment number, due date, opening balance, principal, interest, installment and closing balance, plus totals.
2. French = fixed installment; German = fixed principal. Monthly interest on the outstanding balance; the last installment absorbs rounding so the principal sums exactly the amount.
3. Disbursement only for APROBADO requests: creates the credit with a correlative number, persists the schedule, moves the request to DESEMBOLSADO and records the money movement (credit to the socio's savings account, or cash out of the operator's open caja).
4. Cash disbursement checks caja cash availability per currency and the UIF threshold (reuse CU-W14 service); account disbursement requires an ACTIVE account of the same socio and currency.
5. Portfolio list and credit detail with its schedule; printable disbursement voucher and schedule. Audited.

## Current state (2026-09-26)
- `credito(id, monto_aprobado, saldo_pendiente, estado DEFAULT 'VIGENTE', solicitud_credito_id UNIQUE)`, `tabla_amortizacion(id, numero_cuota, fecha_vencimiento, monto_capital, monto_interes, monto_cuota_total, estado_pago DEFAULT 'PENDIENTE', credito_id)`, `pago_cuota`, `morosidad` exist. Seed: credit 1 (request 1, socio 1, VIGENTE, 25000/20000) with 2 installments, 1 payment, 1 morosidad `AL_DIA`. Must keep working (new columns nullable / defaulted).
- Reusable: `_cuota_estimada` in `app/api/v1/endpoints/creditos.py` (move schedule math into a service, e.g. `app/services/amortizacion.py`, and make `_cuota_estimada` use it — its results must not change: CU-W21/W23 tests keep passing); caja per-currency theoretical cash (`_resumen_sesion_arqueo` / helpers in `caja.py`, `_sesion_abierta`); UIF `exigir_declaracion_si_corresponde` / `registrar_declaracion` in `app/services/uif.py`.

## Decisions (orchestrator)
- Monthly rate i = TNA / 12 / 100; interest_k = round(saldo_{k-1} × i, 2, HALF_UP).
  - FRANCES: cuota = round(P·i/(1−(1+i)^−n), 2) (P/n if i = 0); capital_k = cuota − interest_k; last installment: capital = remaining balance, cuota = capital + interest.
  - ALEMAN: capital = round(P/n, 2); last installment capital = remaining balance; cuota_k = capital_k + interest_k.
- Due dates: `fecha_primer_vencimiento` = disbursement date + 1 month by default (optional override, must be 15–45 days after disbursement); following installments monthly on the same day, clamped to the month's last day.
- Credit number per cooperative via `secuencia_documento` tipo `'CREDITO'` → `CRE-000001`, with `UNIQUE (cooperativa_id, numero_credito)` (lesson from CU-W21).
- Credit snapshot: moneda, tasa, plazo, tipo_amortizacion, producto, socio, cooperativa, fecha_desembolso, modalidad, cuenta_desembolso, usuario.
- Movement: CUENTA → `transaccion` tipo `DESEMBOLSO_CREDITO` canal `WEB` credited to the account (saldo += monto). EFECTIVO → `transaccion` tipo `RETIRO` canal `VENTANILLA` with `control_caja_id` (so arqueo/cierre count it as cash out) and a link to the credit; requires the caller's open caja session, enough cash in that currency (400 "Efectivo insuficiente en caja"), and UIF when the threshold rule applies (428).
- Roles: preview/list/detail = cooperative staff; disburse CUENTA = OFICIAL_CREDITO or ADMINISTRADOR; disburse EFECTIVO = any operations role with an open caja (CAJERO, OFICIAL_CREDITO, ADMINISTRADOR).
- Bitácora `CREDITOS`: `DESEMBOLSAR_CREDITO`.

## Migration `migrations/017_sprint7_desembolso_credito.sql` (idempotent; do not modify 002–016)
- `credito` add: `numero_credito VARCHAR(20)`, `cooperativa_id BIGINT REFERENCES cooperativa(id)`, `socio_id INT REFERENCES socio(id)`, `producto_credito_id INT REFERENCES producto_credito(id)`, `moneda_id INT REFERENCES moneda(id)`, `tasa_interes NUMERIC(5,2)`, `plazo_meses INT`, `tipo_amortizacion VARCHAR(10)`, `fecha_desembolso DATE`, `modalidad_desembolso VARCHAR(10)` (`CUENTA`|`EFECTIVO`), `cuenta_desembolso_id INT REFERENCES cuenta_ahorro(id)`, `transaccion_desembolso_id INT REFERENCES transaccion(id)`, `usuario_id BIGINT REFERENCES usuario(id)`, `fecha_creacion TIMESTAMPTZ DEFAULT now()`; `UNIQUE (cooperativa_id, numero_credito)`. Backfill the seed credit from its request (cooperativa, socio, tasa, plazo, numero `CRE-000001` for its cooperative when it has one) and advance the sequence.
- `tabla_amortizacion` add: `saldo_inicial NUMERIC(14,2)`, `saldo_final NUMERIC(14,2)`, `monto_pagado NUMERIC(14,2) NOT NULL DEFAULT 0` (used by W27).
- `transaccion` add `credito_id INT REFERENCES credito(id)`.

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos`. Errors `{"detail": "..."}`.

`CuotaOut` = `{numero, fecha_vencimiento, saldo_inicial, capital, interes, cuota, saldo_final, estado_pago}` (preview rows use `estado_pago: "PENDIENTE"`).
`PlanPagosOut` = `{tipo_amortizacion, monto, tasa_interes, plazo_meses, moneda, fecha_desembolso, fecha_primer_vencimiento, cuotas: [CuotaOut], total_capital, total_interes, total_pagar}`.
`CreditoOut` = `{id, numero_credito, estado, socio: {id, nombre_completo, ci}, producto: {id, codigo, nombre} | null, moneda | null, monto_aprobado, saldo_pendiente, tasa_interes, plazo_meses, tipo_amortizacion, fecha_desembolso, modalidad_desembolso, cuenta_desembolso: {id, numero} | null, solicitud: {id, numero_solicitud}, proxima_cuota: CuotaOut | null, cuotas_pagadas, cuotas_totales}`.
`CreditoDetalleOut` = `CreditoOut` + `{cronograma: [CuotaOut], transaccion_desembolso_id, usuario: {id, nombre} | null}`.

1. `GET /creditos/solicitudes/{id}/plan-pagos?fecha_desembolso=YYYY-MM-DD&fecha_primer_vencimiento=` (defaults: today, +1 month) → `PlanPagosOut`. 409 if the request is not APROBADO or has no product; 400 invalid first due date.
2. `POST /creditos/solicitudes/{id}/desembolso` body `{modalidad: "CUENTA"|"EFECTIVO", cuenta_ahorro_id?, fecha_primer_vencimiento?, declaracion_uif?}` → `201 CreditoDetalleOut` (disbursement date = today). 409 not APROBADO / already disbursed; 400 invalid account, no open caja, insufficient cash, invalid date; 428 UIF.
3. `GET /creditos/creditos?estado=&socio_ci=` → `[CreditoOut]` of the cooperative (includes the seed credit, tolerating missing snapshot fields).
4. `GET /creditos/creditos/{id}` → `CreditoDetalleOut`; 404 outside cooperative.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/desembolso-amortizacion`):
- [x] B1 Migration 017 applied twice; models/schemas
- [x] B2 Amortization service FRANCES/ALEMAN with exact hand-computed tests (incl. last-installment adjustment, zero rate, month-end dates); `_cuota_estimada` uses it without changing results (RED→GREEN)
- [x] B3 Plan-pagos preview endpoint (RED→GREEN)
- [x] B4 Disbursement CUENTA and EFECTIVO (caja cash, UIF 428), credit + schedule persisted, request DESEMBOLSADO, audit (RED→GREEN)
- [x] B5 Portfolio list/detail incl. seed credit (RED→GREEN)

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/desembolso-amortizacion`):
- [x] F1 `creditosApi.js`: add `getPlanPagos`, `desembolsarSolicitud`, `listarCreditos`, `getCredito` (add only)
- [x] F2 In request detail for APROBADO: "Plan de pagos" preview (date pickers, table, totals, print) and "Desembolsar" form (CUENTA with the socio's accounts from existing APIs, or EFECTIVO; UIF modal on 428 reusing `src/shared/components/uif/`), printable disbursement voucher
- [x] F3 New page "Cartera de créditos" (`src/modules/oficial/pages/CarteraPage.jsx`) for `/oficial/cartera` and `/admin/cartera` + nav items in OficialLayout/AdminLayout: list with filters, detail with schedule (paid/pending badges), print schedule

Orchestrator — Claude:
- [x] V1 Review, hand-computed schedule checks, E2E through the Vite proxy (both modalities), commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_desembolso_credito.py` (+ `tests/test_amortizacion.py` for the service). Baseline 157 passed / 5 pre-existing failures. No leaked rows even if interrupted.
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings.

## Progress
- 2026-09-26: branches `feat/desembolso-amortizacion` created from `feat/scoring-crediticio`; decisions and contract defined; delegated.
- 2026-09-26: delegated-direct implementation selected after read-only mapping; strict TDD enabled by session instructions with `.venv/bin/python -m pytest`; baseline is 157 passed / 5 known failures. Preview uses the request's stored rate and linked product's amortization type because W21 snapshots rate but has no request-level amortization-type snapshot; the credit persists the exact disbursement-time type.
- 2026-09-26: B5 tenant conflict resolved from observed seed data: legacy credit 1, request 1, and socio 1 all have NULL `cooperativa_id`, while migration 013 establishes the unique demo cooperative `Cooperativa de Prueba SI2`. Migration 017 will attribute only seed credit 1 to that exact natural-key cooperative when exactly one match exists; otherwise it stays unscoped and excluded, never exposed to arbitrary tenants. This reconciles required seed inclusion with tenant isolation.
- 2026-09-26: B1 RED: four migration/model/schema tests failed before implementation (missing migration file, ORM model and response schemas, and DB columns); GREEN after migration 017, `Credito`/`TablaAmortizacion` models and response schemas (`4 passed`). Migration applied twice successfully to local coopDB. Readback showed seed credit 1 backfilled socio/rate/term but has no cooperative, so its cooperative-specific number remains NULL; session 406 stayed `ABIERTA`.
- 2026-09-26: B2 RED: six hand-computed amortization/date cases failed because `app.services.amortizacion` did not exist; GREEN after Decimal/HALF_UP French/German schedule generation and delegation from `_cuota_estimada` (`7 passed`). The W21/W23 regression set including request and scoring tests also passed (`73 passed`).
- 2026-09-26: B3 RED: eight preview route/schedule/state/date cases returned 404 before the route was implemented (`8 failed, 1 passed, 4 deselected`); GREEN after tenant-scoped plan preview and validation (`9 passed, 4 deselected`). Preview uses the stored request rate and linked product amortization type; omitted dates default to today and a clamped +1 calendar month.
- 2026-09-26: B4 RED: disbursement route/credit/schedule/cash/UIF tests failed before implementation (5 failed); added coverage for same-day prior cash disbursements linked through `transaccion.credito_id`. GREEN after CUENTA/EFECTIVO transaction persistence, cash-session checks via the Caja helper, request transition, and CREDITOS audit (`10 passed, 9 deselected`). UIF aggregation now checks socio ownership through either savings-account or linked-credit transaction, using EXISTS clauses to avoid double-counting. Test fixtures clean their own FKs and never modify live session 406.
- 2026-09-26: B5 seed attribution test RED (`1 failed, 22 deselected`) before updating migration 017. The migration now attributes only `credito.id=1` / `solicitud_credito_id=1` to `Cooperativa de Prueba SI2` when exactly one exact-name match exists; otherwise it remains unscoped. Applied corrected migration twice successfully; readback is `(credito=1, cooperativa=1, CRE-000001, siguiente=2)` and session 406 remains `ABIERTA`. Canonical tenant list includes seed credit 1 while unrelated tenant list excludes it; B5 tests GREEN (`4 passed`). Full disbursement module `23 passed`; test-pattern counts unchanged before/after: 10 cooperatives, 40 users, 1 account, 4 boxes, confirming no new test fixture leaks.
- 2026-09-27: Focused regressions (`tests/test_amortizacion.py`, `tests/test_desembolso_credito.py`, W21/W23, Caja, transfer, arqueo, UIF, DPF) passed: `149 passed`. Final `.venv/bin/python -m pytest -q`: `187 passed, 5 failed` with the same five known baseline failures (one multi-tenant role test and four `test_savings.py` request-shape tests); no skips. Test-pattern counts stayed 10 cooperatives, 40 users, 1 account, 4 boxes before/after both focused and full runs; control-caja session 406 remained `ABIERTA`. `git diff --check` passed.
- 2026-09-27: Parent spot-check reran `.venv/bin/python -m pytest -q`: `5 failed, 187 passed, 2 warnings in 145.35s (0:02:25)`; the same five baseline tests failed.
- 2026-09-27: Due-date correction RED reproduced the exact default case (`fecha_desembolso=2026-01-31`, no override): expected `2026-02-28, 2026-03-31, 2026-04-30, 2026-05-31`, actual before fix drifted to the 28th after February (`1 failed, 9 deselected`). GREEN after preserving disbursement day as default monthly anchor; override on day 30 also remains anchored after February (`3 passed, 7 deselected`). Full regression `.venv/bin/python -m pytest -q`: `5 failed, 190 passed, 2 warnings in 143.20s (0:02:23)`, same known baseline failures. No `Desembolso %` test coops/users/boxes remained; caja session 406 is still `ABIERTA`; `git diff --check` passed.
- 2026-09-27: Bounded CU-W24 correction, strict TDD: the explicit Jan-31 first-due test and Jan-30 override test were already green before source changes; the exact no-override case (`fecha_desembolso=2026-01-31`) was RED (`1 failed, 9 deselected`), producing Feb-28 then incorrectly Mar/Apr/May-28 instead of Mar-31/Apr-30/May-31. Updated only `app/services/amortizacion.py` so the default due-date sequence retains the original disbursement day as its anchor while clamping each target month; the override path continues to anchor on the override day. All three regressions GREEN (`3 passed, 7 deselected`).
- 2026-09-27: Due-date correction verification: focused amortization/W21/W23 and Caja regression command passed (`76 passed`); preview-specific regression passed (`9 passed, 14 deselected`); broader focused regression command passed (`152 passed, 2 warnings in 112.42s`). Final `.venv/bin/python -m pytest -q`: `5 failed, 190 passed, 2 warnings in 138.01s`; the same five known baseline failures remain (the one cooperative-role tenant test and four `tests/test_savings.py` request-shape tests); no skips. Fixture-pattern DB counts were 0 cooperatives/users/accounts/boxes both before and after testing; no cleanup was warranted. `control_caja.id=406` remained `ABIERTA` (1 row), and `git diff --check` passed.
- 2026-09-27: Parent final spot-check `.venv/bin/python -m pytest -q`: `5 failed, 190 passed, 2 warnings in 143.20s (0:02:23)`; same five baseline failures. Post-run read-only query confirms zero `Desembolso %` coops/boxes and `desembolso-%@test.invalid` users; control_caja 406 remains `ABIERTA`.
- 2026-09-26 (orchestrator review): scope respected on both sides (frontend creditosApi additions only, cartera routes + nav items, credit components; package/lock intact). Leftover data from Codex's interrupted development runs (10 "Desembolso" cooperatives with credits, cajas, transactions) removed by the orchestrator; the final suite leaks nothing (counts identical before/after).
- E2E through the Vite proxy: FRANCES 10000/12m/18% plan → installment 1 = 766.80 principal + 150.00 interest = 916.80, last 916.81 absorbing rounding, principal sums 10000.00, interest 1001.61 (hand-verified); invalid first due date (5 days) 400; CUENTA without account 400; CUENTA OK → CRE-000002, 12 installments, account +10000, request DESEMBOLSADO, second disbursement 409; portfolio lists new + seed credit; EFECTIVO without open caja 400, insufficient cash 400, OK after deposit → caja theoretical cash 2500 → 500. E2E rows removed, balances and correlatives restored.
- Bug found in E2E and fixed by Codex (one scoped correction, RED→GREEN): due dates drifted after a month-end clamp (31/01 → 28/02, 28/03…). Now anchored to the first due date's day: 28/02, 31/03, 30/04, 31/05; override 30/01 → 30/01, 28/02, 30/03. German first installment 12000/60m/8.5% = 200.00 + 85.00 = 285.00 (hand-verified). Final suite: 190 passed / 5 pre-existing failures (hash identical).
