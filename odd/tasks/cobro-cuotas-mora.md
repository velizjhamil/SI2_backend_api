# Feature: Installment collection and arrears (CU-W27)

Plan: W20 ✅ → W21 ✅ → W23 p1 ✅ → W24+W26 ✅ → **W27** → W23 part 2. Autonomous run authorized by the user.

## User story (fictional, drafted by the orchestrator)
**CU-W27 Registro y Cobro de Cuotas de Crédito** — Actors: Cajero (cash at the window), Oficial de crédito / Administrador (debit from savings).
> Como cajero u oficial quiero cobrar las cuotas de un crédito, en efectivo por caja o con débito de la cuenta de ahorro del socio, calculando automáticamente la mora de las cuotas vencidas, para mantener la cartera al día y el historial de pagos que usa la evaluación crediticia.

Acceptance criteria:
1. Shows the amount due for the next unpaid installment: principal, interest, days late and late-payment penalty.
2. Collects installments strictly in order (oldest unpaid first); each payment settles exactly one full installment (principal + interest + penalty). Paying future installments early is allowed one by one. Partial payments are out of scope.
3. Penalty only after the product's grace days: `mora = round(capital_cuota × tasa_mora_anual / 100 / 360 × dias_atraso, 2)` where `dias_atraso = fecha_pago − fecha_vencimiento` (days, counted from the due date, only when > `dias_gracia_mora`; else 0).
4. Updates the installment (PAGADA), the credit balance (saldo_pendiente −= capital) and closes the credit (CANCELADO) when all installments are paid; stores the payment with a correlative receipt number and prints the receipt.
5. Arrears tracking: each credit keeps one `morosidad` row (estado AL_DIA or EN_MORA, max days late of unpaid overdue installments, accumulated penalty to date); it is refreshed on every payment and by an explicit "update arrears" action; a portfolio-at-risk view lists credits in arrears. Audited.

## Decisions (orchestrator)
- Cash (`EFECTIVO`): requires the caller's open caja session; `transaccion` tipo `PAGO_CUOTA`, canal `VENTANILLA`, `control_caja_id`, `credito_id`. **Extend the caja theoretical-cash helper so cash-in = DEPOSITO + PAGO_CUOTA** (reported inside the existing `total_depositos` fields, contracts unchanged); arqueo/cierre stay consistent. UIF (428) applies to cash payments with the existing threshold/structuring service.
- Debit (`CUENTA`): account of the same socio and currency, ACTIVE, must keep its minimum permanence balance (`MONTO_MINIMO_APERTURA`); `transaccion` tipo `PAGO_CUOTA`, canal `WEB`, `cuenta_ahorro_id`, `credito_id`; account saldo −= total.
- Roles: EFECTIVO = operations roles with an open caja (CAJERO, OFICIAL_CREDITO, ADMINISTRADOR); CUENTA = OFICIAL_CREDITO or ADMINISTRADOR; reads = cooperative staff; arrears refresh = OFICIAL_CREDITO or ADMINISTRADOR.
- Receipt number per cooperative via `secuencia_documento` tipo `'PAGO_CUOTA'` → `REC-000001`, `UNIQUE (cooperativa_id, numero_recibo)`.
- `morosidad`: exactly one row per credit (create when missing). EN_MORA when any unpaid installment is past due beyond grace days; `dias_de_retaso` = max days late among those; `monto_penalizado` = Σ penalties to date of unpaid overdue installments. Seed row `AL_DIA` for credit 1 must keep working.
- A credit can be paid only while VIGENTE. The seed credit (legacy, possibly missing snapshot fields such as product/grace) uses grace 0 and mora rate 0 when the product is unknown.
- Bitácora `CREDITOS`: `COBRAR_CUOTA`, `ACTUALIZAR_MORA`.

## Migration `migrations/018_sprint8_cobro_cuotas.sql` (idempotent; do not modify 002–017)
- `pago_cuota` add: `credito_id INT REFERENCES credito(id)`, `cooperativa_id BIGINT REFERENCES cooperativa(id)`, `numero_recibo VARCHAR(20)`, `modalidad VARCHAR(10)` (`EFECTIVO`|`CUENTA`), `monto_total NUMERIC(14,2)`, `dias_atraso INT NOT NULL DEFAULT 0`, `transaccion_id INT REFERENCES transaccion(id)`, `usuario_id BIGINT REFERENCES usuario(id)`; `UNIQUE (cooperativa_id, numero_recibo)`. Backfill `credito_id` for the seed payment from its installment.
- `tabla_amortizacion` add: `fecha_pago TIMESTAMPTZ NULL`; `estado_pago` values used: `PENDIENTE`, `PAGADA` (keep existing seed values readable).
- `morosidad` add: `fecha_actualizacion TIMESTAMPTZ DEFAULT now()`; `UNIQUE (credito_id)` only if existing data allows it (otherwise report and enforce in code).
- `credito.estado` values used: `VIGENTE`, `CANCELADO`.

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos`. Errors `{"detail": "..."}`.

`DeudaCuotaOut` = `{credito_id, numero_credito, cuota: CuotaOut, dias_atraso, dias_gracia, en_mora: bool, mora, total_a_pagar, moneda}` (`CuotaOut` from CU-W24).
`PagoOut` = `{id, numero_recibo, fecha, modalidad, credito: {id, numero_credito}, socio: {id, nombre_completo, ci}, numero_cuota, capital, interes, mora, total, dias_atraso, moneda, saldo_pendiente_credito, credito_estado, cuenta: {id, numero} | null, caja_nombre | null, usuario: {id, nombre}, declaracion_uif_id | null}`.
`MoraCreditoOut` = `{credito_id, numero_credito, socio: {id, nombre_completo, ci}, estado_mora, dias_de_retaso, monto_penalizado, cuotas_vencidas, monto_vencido, saldo_pendiente, moneda, fecha_actualizacion}`.

1. `GET /creditos/creditos/{id}/deuda?fecha=YYYY-MM-DD` (default today) → `DeudaCuotaOut` for the next unpaid installment; 409 if the credit is not VIGENTE or has nothing pending.
2. `POST /creditos/creditos/{id}/pagos` body `{modalidad: "EFECTIVO"|"CUENTA", cuenta_ahorro_id?, declaracion_uif?}` → `201 PagoOut` (pays the next unpaid installment computed as of today). 400 invalid account / minimum balance / no open caja; 409 credit not VIGENTE; 428 UIF; 403 by role/modality.
3. `GET /creditos/creditos/{id}/pagos` → `[PagoOut]` oldest first (includes the seed payment, tolerating missing fields).
4. `GET /creditos/pagos/{id}` → `PagoOut` (receipt reprint); 404 outside cooperative.
5. `POST /creditos/mora/actualizar` → `{actualizados, en_mora, al_dia, fecha}` refreshes `morosidad` for every VIGENTE credit of the cooperative.
6. `GET /creditos/mora?estado=EN_MORA|AL_DIA` → `[MoraCreditoOut]` (portfolio at risk), ordered by days late desc.
7. Additive to CU-W24/W26 `CreditoOut`: `estado_mora: "AL_DIA"|"EN_MORA"|null`, `dias_de_retaso: int|null`.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/cobro-cuotas-mora`):
- [x] B1 Migration 018 applied twice; models/schemas
- [x] B2 Penalty + debt calculation (grace boundary, zero rate, legacy credit) with exact hand-computed tests (RED→GREEN)
- [x] B3 Payments EFECTIVO/CUENTA in order, receipt, balances, credit CANCELADO at the end, caja theoretical cash includes PAGO_CUOTA, UIF (RED→GREEN)
- [x] B4 Arrears refresh + portfolio at risk + `estado_mora` in CreditoOut + audit (RED→GREEN)

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/cobro-cuotas-mora`):
- [x] F1 `creditosApi.js`: add `getDeudaCredito`, `pagarCuota`, `listarPagos`, `getPago`, `actualizarMora`, `listarMora` (add only)
- [x] F2 In the credit detail of `CarteraPage`: "Cobrar cuota" panel (debt breakdown with days late and penalty, modality EFECTIVO/CUENTA, UIF modal on 428 reusing `src/shared/components/uif/`), payment history, printable receipt; `estado_mora` badge in the list
- [x] F3 Cashier access: route `/cajero/cartera` reusing `CarteraPage` + nav item "Créditos" in `CajeroLayout.jsx` (cashier sees only the EFECTIVO option)
- [x] F4 "Mora" tab/view in `CarteraPage` (portfolio at risk with filters) with "Actualizar mora" button for oficial/admin

Orchestrator — Claude:
- [x] V1 Review, hand-computed penalty checks, E2E (on-time, late beyond grace, cash + account, full payoff), commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_cobro_cuotas.py`. Baseline 190 passed / 5 pre-existing failures. **Tests must never leave rows behind, even when a run is interrupted (use fixtures with finally-cleanup); before finishing, verify there are no leftover test cooperatives and clean them if any.**
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings.

## Progress
- 2026-09-26: branches `feat/cobro-cuotas-mora` created from `feat/desembolso-amortizacion`; decisions and contract defined; delegated.
- 2026-09-27: delegated-direct implementation selected after read-only mapping; strict TDD enabled by session instructions with `.venv/bin/python -m pytest`; baseline is 190 passed / 5 known failures. Preflight on local coopDB verified only `Cooperativa de Prueba SI2` remains and caja session 406 is `ABIERTA`; both must be preserved. Cash PAGO_CUOTA must be included independently in Caja theoretical cash and UIF same-day aggregation.
- 2026-09-27: B1 RED: migration 018 file absent, `PagoCuota` model absent, and migration-added columns absent (`3 failed, 1 warning`). GREEN after adding idempotent migration `018_sprint8_cobro_cuotas.sql`, `PagoCuota`/`Morosidad` ORM models, and CU-W27 schemas (`3 passed, 1 warning`). Applied migration 018 twice successfully to local coopDB; seed readback is payment 1 → credit 1 → cooperative 1; no duplicate `morosidad.credito_id` groups. Cooperatives remain count 1 and `control_caja.id=406` remains `ABIERTA`.
- 2026-09-27: B2 RED: both penalty-helper tests failed because `app.services.cobro_cuotas` did not exist; the two seed debt-route tests returned 404 (`4 failed, 3 deselected`). GREEN after adding Decimal/HALF_UP mora calculation and tenant-scoped GET debt endpoint; boundary/rounding, zero-rate/future-due, and legacy credit tests pass (`4 passed, 3 deselected`). The hand-calculated rounding expectation was checked: `5.00 × 36 / 100 / 360 × 11 = 0.055`, which rounds HALF_UP to `0.06`; no behavior deviation.
- 2026-09-27: B3 RED: payment route absent (`3 failed, 8 deselected`), and minimum-balance check returned 404 instead of 400. After implementing account/cash payment routes, account lifecycle tests passed; cash total initially failed because Caja excluded PAGO_CUOTA. A separate same-day UIF regression observed RED: two individually sub-threshold cash installments were accepted because UIF aggregation excluded prior PAGO_CUOTA. GREEN after including PAGO_CUOTA in Caja theoretical deposit totals and UIF same-day aggregation. `.venv/bin/python -m pytest tests/test_cobro_cuotas.py -q`: `12 passed, 2 warnings`.
- 2026-09-27: B4 RED: both arrears refresh tests returned 404 before route implementation (`2 failed, 12 deselected`). GREEN after adding tenant-scoped refresh/portfolio endpoints, audit, and additive credit serializer fields; the first implementation attempt exposed conflicting SQLAlchemy loader strategies, resolved by keeping the credit relation `joinedload` and loading its schedule with `selectinload`. Boundary and overdue portfolio checks pass. `.venv/bin/python -m pytest tests/test_cobro_cuotas.py -q`: `14 passed, 2 warnings`.
- 2026-09-27: Focused cash/credit regressions `.venv/bin/python -m pytest tests/test_cobro_cuotas.py tests/test_caja.py tests/test_transferencias.py tests/test_arqueo_caja.py tests/test_retiros_cierre_caja.py tests/test_dpf.py tests/test_uif.py tests/test_desembolso_credito.py -q`: `106 passed, 2 warnings`. Final `.venv/bin/python -m pytest -q`: `204 passed, 5 failed, 2 warnings`; all five failures match the known baseline: `test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` and four `tests/test_savings.py` failures caused by existing contract/request-field mismatch. Post-run local DB check: exactly one cooperative remains, zero `Loan Test %` and `Desembolso %` cooperatives, and control_caja 406 remains ABIERTA.
- Parent spot-check: final `.venv/bin/python -m pytest -q` output was `5 failed, 204 passed, 2 warnings in 94.65s`; it reproduced the same five baseline failures. Post-run query confirmed exactly one cooperative (`Cooperativa de Prueba SI2`), zero `Loan Test %`/`Desembolso %` cooperatives, and `control_caja.id=406` still `ABIERTA`.
- 2026-09-26 (orchestrator review): scope respected on both sides (frontend creditosApi additions only, CarteraPage + credit components, /cajero/cartera + nav item; cashier restricted to EFECTIVO in UI; package/lock intact). Backend `pytest -q`: 204 passed / 5 failures identical to baseline (hash); no leftover test cooperatives before or after the suite. Frontend build OK, oxlint 8 pre-existing / 0 errors.
- E2E through the Vite proxy on a real credit (request → evaluation → CUENTA disbursement, CONS-BOB 2000/12m, installment 183.36, grace 3, mora 3%): installment 1 not due → 0 penalty, paid by CUENTA → REC-000001 (153.36 + 30.00), account 4500.00 → 4316.64; cashier CUENTA 403; installment 2 moved 20 days overdue → penalty 0.26 = 155.66 × 3% / 360 × 20 (hand-verified), arrears refresh → EN_MORA 20 days; EFECTIVO without caja 400; EFECTIVO with caja → REC-000002 total 183.62, caja theoretical cash 100.00 → 283.62; arrears back to AL_DIA; remaining installments paid → 12 receipts, principal Σ 2000.00, credit CANCELADO, further payment 409; receipt reprint OK. The refresh also flagged the seed credit CRE-000001 as EN_MORA (200 days, penalty 0 because legacy product unknown) — correct behavior; the E2E restored its AL_DIA row. E2E rows removed, balances/correlatives restored.
