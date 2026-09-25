# Feature: UIF declaration (CU-W14), DPF issuance (CU-W17), DPF settlement/renewal (CU-W19)

Source: `/home/yimy/proyectos/si2/casosDeUso.md` lines 63–231 (normative context + user stories). Ignore "Responsable".

## Current state (2026-09-24)
- Tables in `bd.sql` without models/endpoints: `deposito_plazo_fijo(id, monto, tasa_interes_anual, plazo_dias, fecha_inicio, fecha_vencimiento, interes_calculado, estado DEFAULT 'VIGENTE', socio_id, moneda_id)`, `declaracion_jurada_uif(id, origen, destino)`, `liquidacion(id, monto_capital_retornado, monto_interes_pagado, tipo_operacion, fecha, deposito_plazo_fijo_id UNIQUE)`, `tipo_de_cambio`. `transaccion` already has `deposito_plazo_fijo_id`, `liquidacion_id`, `declaracion_jurada_uif_id`.
- Seed data: 1 DPF (LIQUIDADO), 1 UIF row, 1 liquidación. New columns must be nullable or have defaults; do not break seed rows.
- Roles in DB: SUPERADMIN, ADMINISTRADOR, CAJERO, OFICIAL_CREDITO, CONTADOR, SOCIO. There is **no ASESOR role** (frontend `/asesor` is a legacy alias).
- Caja endpoints (`app/api/v1/endpoints/caja.py`): deposits/withdrawals at the window, per-currency theoretical cash (used by arqueo, retiros, cierre).

## Decisions (orchestrator)
### Roles
- "Asesor financiero" → staff with `require_operaciones` (ADMINISTRADOR, CAJERO, OFICIAL_CREDITO). Web UI for DPF mounted for **oficial** and **cajero**.
- "Oficial de cumplimiento" → **CONTADOR** (role description: "Contador / Cumplimiento") + ADMINISTRADOR for listing/auditing UIF declarations.

### CU-W14 UIF
- Thresholds: **BOB ≥ 70,000.00** or **USD ≥ 10,000.00** (≥, the normative rule; stricter than "superiores a"). Constants in backend, exposed by API.
- Applies to cash deposits (`POST /caja/depositos`), cash withdrawals (`POST /caja/retiros`) and DPF issuance (`POST /dpf`).
- Structuring (operaciones fraccionadas): the declaration is also required when the socio's **same-day** sum of window cash operations (deposits + withdrawals, same currency) plus the current amount reaches the threshold.
- When required and missing → **HTTP 428** `{"detail": "Se requiere declaración jurada UIF para esta operación"}`; nothing is persisted. 428 is reserved for this case so the frontend can react reliably.
- The declaration is sent inline in the operation body as `declaracion_uif` and stored in `declaracion_jurada_uif` in the same DB transaction; linked via `transaccion.declaracion_jurada_uif_id` (and `deposito_plazo_fijo.declaracion_jurada_uif_id` for DPF).
- "Firma": `declara_bajo_juramento: true` is mandatory (electronic acceptance) + printable document for handwritten signature.

### CU-W17 DPF issuance
- Rate table `tasa_dpf` per cooperativa + moneda + day range (see migration); seeded for every existing cooperativa. Editing the rate table from the UI is out of scope (GET only).
- Minimum term 30 days; the term must fall in a rate band, else 400.
- Interest (base 360): `interes_bruto = capital × TNA × dias / 36000`, rounded to 2 decimals (ROUND_HALF_UP).
- RC-IVA 13%: **exempt** when moneda BOB and plazo ≥ 30 days (all socios are natural persons); **withheld** otherwise (USD). `interes_neto = bruto − rciva`.
- Interest payment modality: `VENCIMIENTO` (single payment at maturity) or `MENSUAL` (every 30 days from `fecha_inicio`; last installment covers remaining days). The schedule is persisted (`dpf_cronograma`). Paying monthly installments automatically is out of scope (installments stay `PENDIENTE` and are settled at liquidation).
- Funding (`origen_fondos`): `CUENTA` (debit a savings account of the same socio and currency; the account keeps its minimum permanence balance `MONTO_MINIMO_APERTURA`) or `EFECTIVO` (requires the caller's open caja session; the cash-in transaction is linked to `control_caja` and **must be counted as cash in the caja theoretical balance** used by arqueo/retiros/cierre).
- `cuenta_abono_id` (savings account of the same socio and currency) is mandatory: where interest/capital will be paid.
- Certificate number: correlative per cooperativa using `secuencia_documento` tipo `'DPF'` → `numero_certificado` like `DPF-000001`.
- "Certificado digital firmado": `codigo_verificacion` = first 16 hex chars of SHA-256 over `numero_certificado|socio_ci|monto|moneda|fecha_inicio|fecha_vencimiento|tna`.
- Bitácora modulo `DPF`, accion `EMISION`.

### CU-W19 Settlement / renewal
- States: `VIGENTE` → `LIQUIDADO` (paid at/after maturity), `CANCELADO` (early redemption), `RENOVADO` (replaced by a new DPF).
- Settlement always credits `cuenta_abono_id` (no cash payout in v1 → no UIF needed).
- At/after maturity: pays capital + full net interest (minus installments already `PAGADO`, none in v1).
- Early redemption: allowed only if `cooperativa.dpf_permite_cancelacion_anticipada` (default true); interest recalculated for the elapsed days at `cooperativa.dpf_tasa_penalizacion` TNA (default 1.00) with the same RC-IVA rule; capital returned in full.
- Renewal (only at/after maturity; 400 before): new DPF with capital = old capital (`capitalizar=false`, net interest credited to `cuenta_abono_id`) or old capital + net interest (`capitalizar=true`); rate from `tasa_dpf` **on the renewal date**; new term/modality optional (default = previous). New DPF gets a new `numero_certificado` and `dpf_origen_id` → old one.
- Each settlement creates one `liquidacion` row + `transaccion` rows (credits to the account) linked by `liquidacion_id`.
- Bitácora `DPF`, accion `LIQUIDACION` / `CANCELACION` / `RENOVACION`.

## Migration `migrations/012_sprint5_dpf_uif.sql` (idempotent; do not modify 002–011)
- `declaracion_jurada_uif` add: `tipo_operacion VARCHAR(30)`, `monto NUMERIC(14,2)`, `moneda_id INT REFERENCES moneda(id)`, `socio_id INT REFERENCES socio(id)`, `realizado_por VARCHAR(10) CHECK IN ('TITULAR','TERCERO')`, `tercero_nombre VARCHAR(150)`, `tercero_ci VARCHAR(20)`, `tercero_parentesco VARCHAR(50)`, `actividad_economica VARCHAR(150)`, `origen_detalle TEXT`, `destino_detalle TEXT`, `declara_bajo_juramento BOOLEAN NOT NULL DEFAULT false`, `fraccionada BOOLEAN NOT NULL DEFAULT false`, `usuario_id BIGINT REFERENCES usuario(id)`, `cooperativa_id BIGINT REFERENCES cooperativa(id)`, `fecha TIMESTAMPTZ NOT NULL DEFAULT now()` (existing `origen`/`destino` keep the selected option codes).
- `deposito_plazo_fijo` add: `numero_certificado VARCHAR(30) UNIQUE`, `modalidad_pago_interes VARCHAR(12) DEFAULT 'VENCIMIENTO'`, `interes_bruto NUMERIC(12,2)`, `retencion_rciva NUMERIC(12,2)`, `interes_neto NUMERIC(12,2)`, `origen_fondos VARCHAR(10)`, `cuenta_origen_id INT REFERENCES cuenta_ahorro(id)`, `cuenta_abono_id INT REFERENCES cuenta_ahorro(id)`, `codigo_verificacion VARCHAR(16)`, `dpf_origen_id INT REFERENCES deposito_plazo_fijo(id)`, `declaracion_jurada_uif_id INT REFERENCES declaracion_jurada_uif(id)`, `usuario_id BIGINT REFERENCES usuario(id)`, `cooperativa_id BIGINT REFERENCES cooperativa(id)`, `fecha_emision TIMESTAMPTZ DEFAULT now()`.
- `tasa_dpf(id SERIAL PK, cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id), moneda_id INT NOT NULL REFERENCES moneda(id), plazo_min_dias INT NOT NULL, plazo_max_dias INT NULL, tna NUMERIC(5,2) NOT NULL, UNIQUE (cooperativa_id, moneda_id, plazo_min_dias))`. Seed for each cooperativa — BOB: 30–59 2.00, 60–89 2.50, 90–179 3.00, 180–359 4.00, 360–719 5.50, 720–1079 6.00, ≥1080 6.50; USD: 30–179 0.50, 180–359 1.00, 360–719 1.50, ≥720 2.00.
- `dpf_cronograma(id BIGSERIAL PK, deposito_plazo_fijo_id INT NOT NULL REFERENCES deposito_plazo_fijo(id) ON DELETE CASCADE, numero INT NOT NULL, fecha_pago DATE NOT NULL, dias INT NOT NULL, interes_bruto NUMERIC(12,2) NOT NULL, retencion_rciva NUMERIC(12,2) NOT NULL, interes_neto NUMERIC(12,2) NOT NULL, estado VARCHAR(10) NOT NULL DEFAULT 'PENDIENTE')`.
- `cooperativa` add: `dpf_permite_cancelacion_anticipada BOOLEAN NOT NULL DEFAULT true`, `dpf_tasa_penalizacion NUMERIC(5,2) NOT NULL DEFAULT 1.00`.
- `liquidacion` add: `tipo VARCHAR(20)` (`LIQUIDACION|CANCELACION|RENOVACION`), `retencion_rciva NUMERIC(12,2)`, `usuario_id BIGINT REFERENCES usuario(id)`, `cuenta_abono_id INT REFERENCES cuenta_ahorro(id)`, `dpf_renovado_id INT REFERENCES deposito_plazo_fijo(id)`.

## API contract (backend and frontend MUST follow exactly)
Errors `{"detail": "..."}`; amounts 2-decimal strings; `moneda` = MonedaOut; persons are objects.

`DeclaracionUifIn` = `{origen: <code>, origen_detalle?, destino: <code>, destino_detalle?, actividad_economica, realizado_por: "TITULAR"|"TERCERO", tercero_nombre?, tercero_ci?, tercero_parentesco?, declara_bajo_juramento: true}` (tercero_* required when TERCERO).

### UIF
1. `GET /api/v1/uif/configuracion` → `{umbral_bob, umbral_usd, origenes: [{codigo, descripcion}], destinos: [{codigo, descripcion}]}`. Origins: AHORROS_PROPIOS, VENTA_INMUEBLE, VENTA_VEHICULO, COBRO_SERVICIOS, ACTIVIDAD_COMERCIAL, SUELDO, HERENCIA, PRESTAMO, OTRO. Destinations: CONSTITUCION_DPF, AHORRO, PAGO_PROVEEDORES, COMPRA_BIENES, INVERSION, GASTOS_PERSONALES, PAGO_DEUDAS, OTRO. `OTRO` requires the `_detalle` field.
2. Changed: `POST /caja/depositos`, `POST /caja/retiros` accept optional `declaracion_uif: DeclaracionUifIn`; 428 when required and missing; 400 on invalid declaration. Responses add `declaracion_uif_id` (int | null). Everything else unchanged.
3. `GET /api/v1/uif/declaraciones?desde=&hasta=&socio_ci=` (ADMINISTRADOR, CONTADOR; own cooperativa) → `[{id, fecha, tipo_operacion, monto, moneda, socio: {id, nombre_completo, ci}, realizado_por, fraccionada, usuario: {id, nombre}}]`.
4. `GET /api/v1/uif/declaraciones/{id}` (ADMINISTRADOR, CONTADOR, and operations roles of the same cooperativa) → full record `{id, fecha, tipo_operacion, monto, moneda, socio, origen: {codigo, descripcion}, origen_detalle, destino: {codigo, descripcion}, destino_detalle, actividad_economica, realizado_por, tercero_nombre, tercero_ci, tercero_parentesco, declara_bajo_juramento, fraccionada, usuario, transaccion_id | null, dpf_id | null}`.

### DPF (operations roles; own cooperativa)
5. `GET /api/v1/dpf/tarifario` → `[{moneda, plazo_min_dias, plazo_max_dias | null, tna}]`.
6. `POST /api/v1/dpf/simulacion` body `{monto, moneda_id, plazo_dias, modalidad_pago_interes}` → `{monto, moneda, plazo_dias, tna, fecha_inicio, fecha_vencimiento, interes_bruto, exento_rciva, retencion_rciva, interes_neto, total_a_recibir, cronograma: [{numero, fecha_pago, dias, interes_bruto, retencion_rciva, interes_neto}]}`. 400: plazo < 30, no band, monto ≤ 0.
7. `POST /api/v1/dpf` body `{socio_id, monto, moneda_id, plazo_dias, modalidad_pago_interes, origen_fondos: "CUENTA"|"EFECTIVO", cuenta_origen_id?, cuenta_abono_id, declaracion_uif?}` → `201` certificate (shape 9). 400: socio inactive, account not of the socio / other currency / inactive, minimum balance, no open session for EFECTIVO; 428 UIF.
8. `GET /api/v1/dpf?estado=&vence_en_dias=&socio_ci=` → `[{id, numero_certificado, socio: {id, nombre_completo, ci}, monto, moneda, tna, plazo_dias, fecha_inicio, fecha_vencimiento, interes_neto, estado, dias_para_vencer}]`. `vence_en_dias=N` → VIGENTE with maturity ≤ today+N (includes already matured, negative `dias_para_vencer`).
9. `GET /api/v1/dpf/{id}` → `{id, numero_certificado, codigo_verificacion, socio, monto, moneda, tna, plazo_dias, modalidad_pago_interes, fecha_emision, fecha_inicio, fecha_vencimiento, interes_bruto, exento_rciva, retencion_rciva, interes_neto, origen_fondos, cuenta_origen: {id, numero} | null, cuenta_abono: {id, numero}, estado, cronograma: [...+estado], declaracion_uif_id | null, dpf_origen_id | null, usuario: {id, nombre}}`.
10. `GET /api/v1/dpf/{id}/liquidacion/preview?tipo=` → `{tipo, anticipada: bool, dias_transcurridos, tasa_aplicada, interes_bruto, retencion_rciva, interes_neto, capital, total_a_abonar, cuenta_abono: {id, numero}, permitido: bool, motivo | null}`.
11. `POST /api/v1/dpf/{id}/liquidacion` body `{tipo: "LIQUIDACION"|"CANCELACION"|"RENOVACION", capitalizar?: bool, plazo_dias?, modalidad_pago_interes?}` → `201 {liquidacion_id, tipo, dpf: {id, numero_certificado, estado}, capital, interes_bruto, retencion_rciva, interes_neto, total_abonado, cuenta_abono: {id, numero}, nuevo_dpf: <shape 9> | null}`.
   - `LIQUIDACION` only at/after maturity; `CANCELACION` only before maturity and if allowed; `RENOVACION` only at/after maturity. 400 otherwise; 400 if not VIGENTE.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/dpf-uif`):
- [x] B1 Migration 012 (+ seed tarifario) applied twice; models + schemas — `docker exec ... < migrations/012_sprint5_dpf_uif.sql` twice succeeded; `pytest tests/test_uif.py tests/test_dpf.py -q`: 2 passed (RED observed before model/schema/migration implementation).
- [x] B2 UIF config + validation service (thresholds, same-day structuring, 428) wired into `POST /caja/depositos` and `POST /caja/retiros` — combined focused regression passed: 69 passed. RED observed for missing route/428 behavior (3 failures); GREEN for threshold no-write, declaration linkage, structuring, invalid declaration 400, and withdrawal 428/no-write. Supplemental withdrawal-specific test was added after implementation and has no separate RED capture.
- [x] B3 UIF list/detail with role and tenant checks — combined focused regression passed: 69 passed; observed RED 404 on absent route. CONTADOR list works, CAJERO list is 403, operations detail works, and cross-tenant detail is 404.
- [x] B4 DPF tarifario + simulación: base 360, RC-IVA exemption, monthly schedule — focused DPF tests pass; RED observed 404 before routes. Monthly rounding residual goes to the final installment and tested schedule sum reconciles to gross interest.
- [x] B5 DPF emisión: CUENTA and EFECTIVO funding, UIF, correlative number, verification code, schedule; EFECTIVO counted in caja theoretical cash — combined focused regression passed: 69 passed; RED observed 404 on missing issuance route. Cash-in uses the existing `DEPOSITO`/`VENTANILLA` type already summed by `_resumen_sesion_arqueo`; test confirms opening 200 + DPF cash 500 = 700 BOB. Supplemental high-amount DPF UIF linkage test was added after implementation, without a separate RED capture.
- [x] B6 DPF list/detail + vence_en_dias — combined focused regression passed: 69 passed; RED observed 405 before GET collection route. Test covers state, CI and expiry cutoff plus detail schedule.
- [x] B7 Liquidación preview + LIQUIDACION / CANCELACION (penalty) / RENOVACION (capitalizar true/false) — combined focused regression passed: 69 passed; RED observed 404 before preview route. Tests cover maturity settlement, early-cancel penalty + RC-IVA, both renewal capitalization modes, origin linkage, and account movements.

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/frontend-redesign`):
- [x] F1 `src/core/api/uifApi.js`, `src/core/api/dpfApi.js`
- [x] F2 Shared `DeclaracionUifModal.jsx` (form + printable declaration) in `src/shared/components/uif/`; integrate into `DepositoTab.jsx` and `RetiroTab.jsx`: open proactively when amount ≥ threshold, and on 428; resend the operation with `declaracion_uif`
- [x] F3 Shared `PlazoFijoPage.jsx` in `src/shared/components/dpf/`: tabs Simular/Emitir (with UIF when needed) and Cartera (filters vigentes / próximos a vencer / vencidos), certificate modal printable, liquidation modal (preview → confirm) for LIQUIDACION/CANCELACION/RENOVACION
- [x] F4 Routes `/oficial/plazo-fijo` and `/cajero/plazo-fijo` + nav item "Plazo Fijo" in `OficialLayout.jsx` and `CajeroLayout.jsx`
- [x] F5 `src/modules/contador/pages/CumplimientoPage.jsx`: list of UIF declarations with date/CI filters and printable detail

Orchestrator — Claude:
- [x] V1 Review, checks, E2E through the Vite proxy, commits

## Checks
- Backend: `.venv/bin/python -m pytest -q` — baseline 71 passed / 5 pre-existing failures (`test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`, 4 in `test_savings.py`). New tests: `tests/test_uif.py`, `tests/test_dpf.py`.
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings.

## Progress
- 2026-09-24: scope, decisions and contract defined; delegated.
- 2026-09-24: B1 migration 012, ORM models, and request schemas added; migration applied twice successfully to local coopDB. B1 model/schema test passed after observed RED.
- 2026-09-24: B2 UIF threshold config, validation, same-day cash aggregation and caja deposit/retiro wiring added; focused tests passed, including 428 without persistence and declaration linkage.
- 2026-09-24: B3 UIF declaration list/detail endpoints added with role and cooperative scoping; focused tests passed.
- 2026-09-24: B4–B7 DPF tariff/simulation, account/cash issuance, portfolio/detail, preview/liquidation/cancellation/renewal implemented; cash funding reuses the existing ventanilla `DEPOSITO` transaction classification consumed by shared Caja summary. Focused UIF/DPF+Caja+transfer tests: 69 passed.
- 2026-09-24: Final `.venv/bin/python -m pytest -q`: 86 passed, 5 failed. The five failures match the documented baseline: one `test_multi_tenant.py` admin tenant-list authorization case and four `tests/test_savings.py` requests missing current required fields.
- 2026-09-25 (orchestrator review): scope respected on both sides (frontend untouched: package/lock, client.js, cajaApi.js; router/layouts only gained the plazo-fijo route + nav item). Backend `pytest -q`: 86 passed / 5 failures, identical set to baseline (compared by hash). Frontend build OK, oxlint 8 pre-existing warnings / 0 errors. All UI-consumed fields exist in the contract. TDD caveat reported by Codex: two complementary tests (retiro UIF 428, DPF–UIF link) were added after implementation without an independent RED.
- E2E through the Vite proxy (cajero@test.com on caja 541; user's live session on caja 1 left untouched): deposit 70000 → 428 / no oath 400 / third party without data 400 / with UIF 201; structuring 40000+40000 same day → 2nd 428 → with UIF 201 (fraccionada); simulation BOB 10000·360d → 550.00 exempt, USD → 150.00 − 19.50 RC-IVA = 130.50, monthly 95d → 30/30/30/5-day schedule; issuance CUENTA min-balance 400, other socio's account 400, 70000 without UIF 428, 5000 OK (DPF-000110 + verification code); EFECTIVO 3000 → 428 by same-day structuring (correct) → with UIF 201 and caja theoretical cash +3000; early cancel before maturity pays 0 interest at penalty rate; LIQUIDACION/RENOVACION before maturity 400; CANCELACION after maturity 400; RENOVACION capitalizar → new DPF 3005.00 at the renewal-day rate; LIQUIDACION of matured renewal → 3005.00 + 5.01; contador lists 3 declarations (2 fraccionadas) and reads detail; cajero listing → 403. Only E2E rows removed afterwards; balances restored to snapshot.
- Known limitation: DPF cash funding is recorded as `DEPOSITO`/`VENTANILLA` without `cuenta_ahorro_id`, so the closing sheet (CU-W16) counts it among deposits. Correct for cash totals; label could be refined later.
