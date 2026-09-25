# Feature: Retiros en ventanilla (CU-W12) y cierre de turno (CU-W16)

Source: user message 2026-09-24. Builds on `odd/tasks/caja-ventanilla.md` and `odd/tasks/arqueo-caja.md`.

## Acceptance criteria
CU-W12 Retiros:
1. Account balance covers the withdrawal AND the minimum permanence balance.
2. Identity check of the titular socio or an authorized proxy (apoderado).
3. The caja has enough physical cash to pay out.
4. Printable withdrawal voucher with a signature line for the socio.
5. Account balance and caja cash are updated in real time.

CU-W16 Cierre de turno:
1. A satisfactory arqueo must have been completed first.
2. New transactions in that caja are blocked immediately.
3. Caja state goes from ABIERTA to CERRADA; the session's records become immutable.
4. Consolidated daily closing sheet: total deposits, withdrawals, and amount transferred to the vault.
5. Success confirmation, or alerts when there are pending operations.

## Decisions (orchestrator)
### CU-W12
- `POST /api/v1/caja/retiros`, requires the caller's open session (same as deposits).
- Minimum permanence balance = existing `MONTO_MINIMO_APERTURA` by `tipo_producto` (VISTA 10.00, PROGRAMADO 50.00) in `app/api/v1/endpoints/ahorros.py`; import it, do not duplicate. Rule: `saldo_disponible − monto ≥ minimo`.
- Identity: body carries `retirante: {tipo: "TITULAR"|"APODERADO", nombre, ci}`. TITULAR → `ci` must equal the account socio's `ci` (else 400). APODERADO → nombre + ci required and recorded (no proxy registry exists; out of scope).
- Physical cash available = the per-currency theoretical balance already computed for the arqueo `resumen` (opening + deposits − withdrawals of this session, in the account's currency). Extract/reuse that helper; do not recompute differently. `monto > efectivo` → 400.
- Persistence: `transaccion` tipo `RETIRO`, canal `VENTANILLA`, `control_caja_id`, plus new columns `retirante_tipo`, `retirante_nombre`, `retirante_ci`. Account `saldo_disponible −= monto`; `control_caja.saldo_sistema −= monto` (mirrors deposits). Bitácora `CAJA`/`RETIRO`.
- The arqueo `resumen` must now reflect withdrawals (it already sums RETIRO rows; add a test).

### CU-W16
- Single closing path: the formal close is `POST /api/v1/caja/cierres`. The arqueo's `cerrar_caja` option is retired: `POST /caja/arqueos` with `cerrar_caja: true` → 400 `"El cierre de caja se realiza desde el cierre de turno"`. Frontend removes that checkbox.
- Satisfactory arqueo = the **latest** arqueo of the open session, every currency `CUADRADO` or the arqueo has `supervisor_id` (authorized difference), and **no ventanilla transaction after the arqueo's `fecha`**.
- Closing: `control_caja.estado='CERRADA'`, `fecha_cierre=now()`, `monto_cierre` = counted BOB; `caja.estado='CERRADA'`. After that, deposits/withdrawals/transfers/arqueos on that session are rejected (already true because they require an open session — add tests).
- Vault transfer (traspaso a bóveda) = the arqueo's counted total per currency (all remaining cash goes to the vault). No vault inventory table (out of scope).
- Closing sheet persisted in `cierre_caja` + `cierre_caja_moneda`.
- Bitácora `CAJA`/`CIERRE`.

## Migration `migrations/011_sprint4_retiros_cierre.sql` (idempotent)
- `transaccion`: `retirante_tipo VARCHAR(10) NULL CHECK (retirante_tipo IN ('TITULAR','APODERADO'))`, `retirante_nombre VARCHAR(150) NULL`, `retirante_ci VARCHAR(20) NULL`.
- `cierre_caja(id BIGSERIAL PK, control_caja_id INT NOT NULL UNIQUE REFERENCES control_caja(id), arqueo_id BIGINT NOT NULL REFERENCES arqueo_caja(id), usuario_id BIGINT NOT NULL REFERENCES usuario(id), fecha TIMESTAMPTZ NOT NULL DEFAULT now(), observacion TEXT NULL)`
- `cierre_caja_moneda(id BIGSERIAL PK, cierre_id BIGINT NOT NULL REFERENCES cierre_caja(id) ON DELETE CASCADE, moneda_id INT NOT NULL REFERENCES moneda(id), monto_apertura NUMERIC(12,2) NOT NULL, total_depositos NUMERIC(12,2) NOT NULL, cantidad_depositos INT NOT NULL, total_retiros NUMERIC(12,2) NOT NULL, cantidad_retiros INT NOT NULL, cantidad_transferencias INT NOT NULL, saldo_teorico NUMERIC(12,2) NOT NULL, total_contado NUMERIC(12,2) NOT NULL, diferencia NUMERIC(12,2) NOT NULL, traspaso_boveda NUMERIC(12,2) NOT NULL)`
- Do NOT modify migrations 002–010.

## API contract (backend and frontend MUST follow exactly)
Errors `{"detail": "..."}`; amounts as 2-decimal strings; `moneda` is MonedaOut; `titular`/`cajero`/`supervisor` are objects.

1. `POST /api/v1/caja/retiros` body `{cuenta_id, monto, retirante: {tipo, nombre, ci}}` → `201 {numero_operacion, fecha_hora, cuenta_numero, titular: {socio_id, nombre_completo, ci}, monto, moneda, saldo_actualizado, saldo_minimo, retirante: {tipo, nombre, ci}, caja_nombre, efectivo_caja_restante}`.
   - 400: no open session; monto ≤ 0; account not ACTIVE; `saldo − monto < minimo` ("Saldo insuficiente: debe mantener un saldo mínimo de X"); titular CI mismatch; missing apoderado data; not enough cash in caja ("Efectivo insuficiente en caja"). 404: account outside cooperative.
2. `GET /api/v1/caja/cierres/verificacion` → `200 {puede_cerrar: bool, alertas: [string], arqueo: {id, fecha, requiere_supervisor, supervisor: {id, nombre}|null, monedas: [{moneda, resultado, diferencia}]} | null}`; 404 if no open session. Alerts (Spanish): no arqueo yet; latest arqueo has an unauthorized difference; there are transactions after the latest arqueo.
3. `POST /api/v1/caja/cierres` body `{observacion?}` → `201` planilla (shape of 4). `409 {"detail": <first alert>}` when `puede_cerrar` is false; 400 without open session.
4. `GET /api/v1/caja/cierres/{id}` → `200 {id, fecha, caja_nombre, cajero: {id, nombre}, fecha_apertura, fecha_cierre, arqueo_id, observacion, monedas: [{moneda, monto_apertura, total_depositos, cantidad_depositos, total_retiros, cantidad_retiros, cantidad_transferencias, saldo_teorico, total_contado, diferencia, traspaso_boveda}]}`; 404 outside cooperative.
5. Changed: `POST /api/v1/caja/arqueos` with `cerrar_caja: true` → 400 (see decisions). `cerrar_caja: false`/omitted unchanged.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/caja-ventanilla`):
- [x] B1 Migration 011 + apply twice to local coopDB; models + schemas (schema/model tests observed RED then 2 passed).
- [x] B2 CU-W12 retiros (RED→GREEN): min balance, titular CI, apoderado, cash per currency, balances, resumen reflects withdrawal (focused RED→GREEN observed; 7 tests passed, plus subsequent USD-specific check).
- [x] B3 Retire `cerrar_caja` in arqueo (RED→GREEN); only affected existing test changed: `tests/test_arqueo_caja.py::test_cerrar_caja_registra_arqueo_y_cierra_sesion` → `test_cerrar_caja_desde_arqueo_se_rechaza_sin_persistir`, because closing is now exclusive to `/cierres`.
- [x] B4 CU-W16 verificacion + cierres (RED→GREEN): no arqueo, unauthorized diff, tx after arqueo → 409; success closes and blocks deposits/retiros/transfers/arqueos (4 focused tests passed; extra authorized-difference/no-session checks passed).
- [x] B5 GET cierres/{id} (RED→GREEN) incl. tenant isolation (1 focused test passed).

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/frontend-redesign`):
- [x] F1 `cajaApi.js`: add `procesarRetiro`, `verificarCierre`, `cerrarTurno`, `getCierre` (add only)
- [x] F2 `RetiroTab.jsx` (new tab "Retiro" in `CajaPage.jsx`): account search (reuse `buscarCuentas`), amount, retirante TITULAR/APODERADO with CI, show min balance/cash errors, printable voucher `ComprobanteRetiroModal.jsx` with signature line
- [x] F3 `ArqueoTab.jsx`: remove the `cerrar_caja` checkbox; after a successful arqueo, offer a button to go to "Cierre"
- [x] F4 `CierreTab.jsx` (new tab "Cierre"): show verificación alerts, confirm, `PlanillaCierreModal.jsx` printable sheet; after closing the page returns to the opening state

Orchestrator — Claude:
- [x] V1 Review, checks, E2E through the Vite proxy, commits

## Checks
- Backend: `.venv/bin/python -m pytest -q` — baseline 55 passed / 5 pre-existing failures (`test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`, 4 in `test_savings.py`). New tests: `tests/test_retiros_cierre_caja.py`.
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings.

## Progress
- 2026-09-24: scope, decisions and contract defined; delegated.
- 2026-09-24: B1 complete. Migration/schema tests were observed RED before migration 011 and model/schema definitions; migration applied twice to local coopDB and tests passed (2).
- 2026-09-24: B2 complete. Withdrawal tests were observed RED before route implementation and passed afterward; balance checks reuse `MONTO_MINIMO_APERTURA` and the extracted per-currency arqueo summary helper.
- 2026-09-24: B3 complete. The new close-from-arqueo rejection test was observed RED before implementation and GREEN afterward; only the previously closing arqueo test was updated.
- 2026-09-24: B4 complete. Verification/closing tests were observed RED before route implementation and GREEN afterward; closure summary and post-close endpoint blocking passed.
- 2026-09-24: B5 complete. Detail/isolation test was observed RED before GET route implementation and GREEN afterward.
- 2026-09-24: Final focused regression (`test_retiros_cierre_caja.py`, `test_arqueo_caja.py`, `test_caja.py`, `test_transferencias.py`) passed: 54 tests. Full `.venv/bin/python -m pytest -q`: 71 passed, 5 failed; the same 5 known baseline failures remain in the tenant-admin access test and four savings request-payload tests.
- 2026-09-24 (orchestrator review): scope respected (frontend: `cajaApi.js` additions only, `CajaPage.jsx`, `ArqueoTab.jsx` close option removed, new `RetiroTab`/`ComprobanteRetiroModal`/`CierreTab`/`PlanillaCierreModal`; package/lock intact). Only existing test changed: arqueo close test → now asserts 400. Backend `pytest -q`: 71 passed / 5 pre-existing failures. Frontend build OK (bundle > 500 kB warning from Vite, not an error), oxlint 8 pre-existing warnings / 0 errors. UI fields and payloads match schemas; no object rendered directly.
- E2E through the Vite proxy: no session (verificación 404, retiro 400) → open 500 → titular CI mismatch 400 → apoderado without data 400 → minimum balance 400 → cash > caja 400 → USD without USD cash 400 → titular 100 OK / apoderado 50 OK → deposit 200 → resumen BOB 550 (dep 200, ret 150) → close without arqueo 409 → arqueo cerrar_caja=true 400 → arqueo 550 CUADRADO → deposit after arqueo → close 409 (posterior tx) → new arqueo 560 → verificación OK → CIERRE 201 (BOB apertura 500, dep 210 (2), ret 150 (2), vault 560) → GET planilla 200 → deposit/withdraw after close 400 → caja CERRADA. Test data removed, balances restored.
