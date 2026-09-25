# Feature: Arqueo y conciliación de caja (CU-W15)

Source: user message 2026-09-24 (CU-W15). CU-W13 (transferencias internas) was already delivered in `odd/tasks/caja-ventanilla.md` and meets all its acceptance criteria.

## Objective
Let the cashier count physical cash by denomination, compare it automatically against the theoretical balance derived from the session's transactions, record surplus/shortage, require supervisor sign-off when the difference is significant, optionally close the session, and print the cash-count sheet.

## Acceptance criteria (CU-W15)
1. Template for denomination breakdown (bills and coins).
2. Automatic comparison of counted total vs. theoretical balance derived from transactions.
3. Detects and records surplus (SOBRANTE) or shortage (FALTANTE).
4. Supervisor confirmation required when the difference is significant.
5. Printable cash-count sheet with full detail.

## Current state
- `caja`, `control_caja` (session: `monto_apertura`, `saldo_sistema`, `monto_cierre`, `fecha_cierre`, `estado`), `transaccion` with `control_caja_id`, canal `VENTANILLA` (deposits add cash; transfers do not move cash). Endpoints in `app/api/v1/endpoints/caja.py`.
- Known issue: `POST /caja/depositos` adds every deposit to `control_caja.saldo_sistema` regardless of account currency (BOB + USD mixed). Arqueo MUST NOT rely on `saldo_sistema`.
- Monedas: 1 BOB (Bs.), 2 USD ($).

## Decisions (orchestrator)
- Theoretical balance is computed **per currency from transactions** of the open session:
  - BOB: `monto_apertura` (opening cash is BOB) + Σ deposits − Σ withdrawals (canal VENTANILLA, this `control_caja_id`, moneda BOB).
  - USD: Σ deposits − Σ withdrawals of USD in this session (opening = 0).
  - A currency appears in the sheet if it is BOB or had movements in the session.
- Denominations (fixed constants in backend, returned by API):
  - BOB bills 200, 100, 50, 20, 10; coins 5, 2, 1, 0.50, 0.20, 0.10.
  - USD bills 100, 50, 20, 10, 5, 1.
- Result per currency: `diferencia = contado − teorico`; `CUADRADO` if 0, `SOBRANTE` if > 0, `FALTANTE` if < 0.
- Significant difference: any currency with `|diferencia| > caja.umbral_diferencia_arqueo` (new column, default 50.00).
- Supervisor sign-off ("firma digital"): supervisor enters `correo` + `contrasena` in the same request; backend verifies password hash, role `ADMINISTRADOR` of the same cooperative, ACTIVE, and different from the cashier. Stored as `supervisor_id` + `fecha_autorizacion`.
- `cerrar_caja: true` closes the session: `control_caja.estado='CERRADA'`, `fecha_cierre=now()`, `monto_cierre` = counted BOB total, `caja.estado='CERRADA'`. `false` = partial count, session stays open.
- Arqueo requires an open session of the calling user. Bitácora modulo `CAJA`, accion `ARQUEO` (and `CIERRE` when closing).
- Out of scope: supervisor performing the count of another cashier's session; editing/deleting arqueos; currency conversion.

## Migration `migrations/010_sprint4_arqueo_caja.sql` (idempotent)
- `caja.umbral_diferencia_arqueo NUMERIC(12,2) NOT NULL DEFAULT 50.00`
- `arqueo_caja(id BIGSERIAL PK, control_caja_id INT NOT NULL REFERENCES control_caja(id), usuario_id BIGINT NOT NULL REFERENCES usuario(id), supervisor_id BIGINT NULL REFERENCES usuario(id), fecha TIMESTAMPTZ NOT NULL DEFAULT now(), fecha_autorizacion TIMESTAMPTZ NULL, cierre BOOLEAN NOT NULL DEFAULT false, requiere_supervisor BOOLEAN NOT NULL, observacion TEXT NULL)`
- `arqueo_caja_moneda(id BIGSERIAL PK, arqueo_id BIGINT NOT NULL REFERENCES arqueo_caja(id) ON DELETE CASCADE, moneda_id INT NOT NULL REFERENCES moneda(id), saldo_teorico NUMERIC(12,2) NOT NULL, total_contado NUMERIC(12,2) NOT NULL, diferencia NUMERIC(12,2) NOT NULL, resultado VARCHAR(10) NOT NULL CHECK (resultado IN ('CUADRADO','SOBRANTE','FALTANTE')))`
- `arqueo_caja_detalle(id BIGSERIAL PK, arqueo_moneda_id BIGINT NOT NULL REFERENCES arqueo_caja_moneda(id) ON DELETE CASCADE, tipo VARCHAR(10) NOT NULL CHECK (tipo IN ('BILLETE','MONEDA')), denominacion NUMERIC(12,2) NOT NULL, cantidad INT NOT NULL CHECK (cantidad >= 0), subtotal NUMERIC(12,2) NOT NULL)`

## API contract (backend and frontend MUST follow exactly)
Errors: `{"detail": "<mensaje en español>"}`. Amounts as decimal strings with 2 decimals.

1. `GET /api/v1/caja/arqueos/resumen` → `200 {control_caja_id, caja_nombre, fecha_apertura, umbral_diferencia, monedas: [{moneda: MonedaOut, saldo_teorico, monto_apertura, total_depositos, total_retiros, cantidad_movimientos, denominaciones: [{tipo: "BILLETE"|"MONEDA", valor}]}]}`; `404` if no open session.
2. `POST /api/v1/caja/arqueos` body:
   ```
   {
     "monedas": [{"moneda_id": 1, "detalle": [{"denominacion": "200.00", "cantidad": 3}, ...]}],
     "observacion": "opcional",
     "cerrar_caja": false,
     "supervisor": {"correo": "...", "contrasena": "..."}   // optional
   }
   ```
   - Every currency listed in `resumen` must be present (missing ones count as 0 only if the client sends them with empty `detalle`; otherwise 400). Unknown denomination or negative quantity → 400. No open session → 400.
   - Significant difference without `supervisor` → `409 {"detail": "Diferencia significativa: se requiere autorización del supervisor"}` and nothing is persisted.
   - Invalid supervisor credentials / wrong role / other cooperative / same user → `403` and nothing is persisted.
   - `201` → hoja de arqueo (same shape as 3).
3. `GET /api/v1/caja/arqueos/{id}` → `200 {id, fecha, caja_nombre, cajero: {id, nombre}, supervisor: {id, nombre} | null, fecha_autorizacion, requiere_supervisor, cierre, observacion, monedas: [{moneda: MonedaOut, saldo_teorico, total_contado, diferencia, resultado, detalle: [{tipo, denominacion, cantidad, subtotal}]}]}`; `404` if not in the user's cooperative.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/caja-ventanilla`):
- [x] B1 Migration 010 + apply twice to local coopDB — `migrations/010_sprint4_arqueo_caja.sql` applied successfully twice; second run skipped the existing column and tables.
- [x] B2 Models + schemas — added `ArqueoCaja`, `ArqueoCajaMoneda`, `ArqueoCajaDetalle`, the caja threshold mapping, and request/response schemas; availability test RED→GREEN.
- [x] B3 `resumen` (RED→GREEN): per-currency theoretical balance from transactions, USD deposit does not leak into BOB; observed 2 focused tests passed.
- [x] B4 `POST arqueos` (RED→GREEN): cuadrado / sobrante / faltante; 409 without supervisor; 403 bad supervisor; nothing persisted on 409/403; `cerrar_caja` closes session and caja; observed 10 focused tests passed.
- [x] B5 `GET arqueos/{id}` (RED→GREEN) incl. tenant isolation; focused retrieval/isolation test passed.

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/frontend-redesign`):
- [x] F1 `cajaApi.js`: `getResumenArqueo`, `registrarArqueo`, `getArqueo` (add functions only)
- [x] F2 New tab "Arqueo" in `CajaPage.jsx` (only visible with an open session) → `ArqueoTab.jsx`: per-currency denomination grid, live subtotals/total, theoretical vs counted, live SOBRANTE/FALTANTE/CUADRADO badge
- [x] F3 Supervisor block (correo + contraseña) shown when a difference exceeds `umbral_diferencia` or after a 409; `cerrar_caja` checkbox with confirmation
- [x] F4 `HojaArqueoModal.jsx` printable sheet (`window.print()`); after a closing arqueo, page returns to the opening state

Orchestrator — Claude:
- [x] V1 Review diffs, run checks, E2E through the Vite proxy, commit per work unit

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest` (tests in `tests/test_arqueo_caja.py`). Known pre-existing failures: 5 (`test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`, 4 in `test_savings.py`).
- Frontend: `pnpm build` OK; oxlint 0 errors, only the 8 pre-existing warnings.

## Progress
- 2026-09-24: scope, decisions and contract defined; delegated.
- 2026-09-24: B1 complete. The migration test was observed RED before schema changes and GREEN after applying 010 twice to local coopDB.
- 2026-09-24: B2 complete. Model/schema availability test was observed RED before those definitions and GREEN afterward.
- 2026-09-24: B3 complete. The resumen test was observed RED before the endpoint and GREEN afterward; a USD deposit is counted only in USD, while BOB remains opening balance plus BOB activity.
- 2026-09-24: B4 complete. POST tests were observed RED before implementation and GREEN afterward (10 passed); validation, supervisor checks, persisted result, and optional closure are covered.
- 2026-09-24: B5 complete. Retrieval/tenant-isolation test was observed RED before the route and GREEN afterward; the full arqueo suite passed (15 tests).
- 2026-09-24: Regression checks: `tests/test_caja.py tests/test_transferencias.py` passed (23); full `pytest -q` reported 55 passed and 5 pre-existing failures in one multi-tenant role test and four savings payload tests.
- 2026-09-24 (orchestrator review): scope respected on both sides (frontend: only `cajaApi.js` additions, `CajaPage.jsx`, new `ArqueoTab.jsx`/`HojaArqueoModal.jsx`; package/lock intact). Backend `pytest -q`: 55 passed / 5 pre-existing failures. Frontend build OK, oxlint 8 pre-existing warnings / 0 errors. UI-consumed fields and POST payload match the Pydantic schemas; `cajero`/`supervisor`/`moneda` rendered through their fields.
- E2E through the Vite proxy (:5173 → :8000): open 1000 BOB → deposit 200 BOB + 50 USD → resumen BOB 1200 / USD 50 (no currency leak) → partial count CUADRADO 201 → shortage 100 without supervisor 409 → wrong password 403 → cashier as supervisor 403 → admin supervisor + close 201 (BOB FALTANTE −100, USD CUADRADO) → GET sheet 200 → sesion-actual 404, caja CERRADA. Test data removed, balances restored.
