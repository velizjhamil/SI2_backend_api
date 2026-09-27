# Feature: Predictive arrears monitoring and alerts (CU-W28)

Route: W25 ✅ → **W28** → W22 → W18. Autonomous run authorized by the user (2026-09-27).

## User story (fictional, drafted by the orchestrator)
**CU-W28 Monitoreo Predictivo de Mora y Alertas (IA)** — Actors: Oficial de crédito, Administrador.
> Como oficial de crédito quiero recibir alertas tempranas de cuotas por vencer, cuotas vencidas, créditos en mora y créditos con riesgo alto según el modelo predictivo, y registrar la gestión de cobranza de cada alerta, para actuar antes de que la mora crezca.

Acceptance criteria:
1. An on-demand "monitoring run" refreshes arrears (CU-W27) and model predictions (CU-W23 p2) and creates alerts idempotently; alerts whose condition no longer holds are closed automatically.
2. Alert types with severity: `CUOTA_POR_VENCER` (INFO, next unpaid installment due in 0–5 days), `CUOTA_VENCIDA` (ADVERTENCIA, overdue but within grace days), `MORA` (CRITICA, overdue beyond grace), `RIESGO_ALTO` (ADVERTENCIA, model level ALTO — labeled as synthetic/informative).
3. Alert list with filters and a portfolio summary: total portfolio, portfolio in arrears, arrears index %, credits by model risk level, active alerts by severity, top 5 credits by predicted probability.
4. Each alert can be attended by registering a collection action (contact type, result, optional promise-to-pay date) — stored in `historial_gestion_de_cobranza` — or dismissed with a reason.
5. Collection history visible per credit. Audited.

## Decisions (orchestrator)
- One ACTIVE alert per (credito, tipo, cuota) — enforced by a partial unique index; the run is idempotent (running twice creates nothing new).
- Auto-close: an ACTIVE alert whose condition is false in the current run → `RESUELTA` (with `fecha_cierre`); ATENDIDA/DESCARTADA alerts are never reopened for the same (credito, tipo, cuota); a new condition occurrence for another installment creates a new alert.
- `CUOTA_POR_VENCER` window = 5 days (constant). Grace/penalty rules come from CU-W27 services (`app/services/cobro_cuotas.py`); predictions from `app/services/modelo_mora.py` (legacy credits without evaluation are skipped for RIESGO_ALTO, as in W23 p2).
- Only VIGENTE credits are monitored; alerts of credits that became CANCELADO are closed as RESUELTA.
- Arrears index = Σ saldo_pendiente of credits EN_MORA / Σ saldo_pendiente of VIGENTE credits × 100 (per currency; response grouped by currency).
- Roles: run/attend/dismiss = OFICIAL_CREDITO or ADMINISTRADOR; read = cooperative staff.
- No scheduler in v1 (manual run; a cron can call the endpoint later — documented).
- Bitácora `CREDITOS`: `MONITOREO_MORA`, `ATENDER_ALERTA`, `DESCARTAR_ALERTA`.

## Migration `migrations/021_sprint9_alertas_mora.sql` (idempotent; do not modify 002–020)
- `alerta_credito(id BIGSERIAL PK, cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id), credito_id INT NOT NULL REFERENCES credito(id), tabla_amortizacion_id INT NULL REFERENCES tabla_amortizacion(id), tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('CUOTA_POR_VENCER','CUOTA_VENCIDA','MORA','RIESGO_ALTO')), severidad VARCHAR(12) NOT NULL CHECK (severidad IN ('INFO','ADVERTENCIA','CRITICA')), mensaje TEXT NOT NULL, datos JSONB NOT NULL DEFAULT '{}', estado VARCHAR(12) NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA','ATENDIDA','DESCARTADA','RESUELTA')), fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(), fecha_cierre TIMESTAMPTZ NULL, usuario_cierre_id BIGINT NULL REFERENCES usuario(id), comentario_cierre TEXT NULL, gestion_id INT NULL REFERENCES historial_gestion_de_cobranza(id))`; partial unique index on (credito_id, tipo, COALESCE(tabla_amortizacion_id, 0)) WHERE estado = 'ACTIVA'.
- `historial_gestion_de_cobranza` add: `cooperativa_id BIGINT REFERENCES cooperativa(id)`, `usuario_id BIGINT REFERENCES usuario(id)`, `fecha TIMESTAMPTZ DEFAULT now()`, `alerta_id BIGINT NULL` (no FK cycle needed). Keep the seed row readable.

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos`. Errors `{"detail": "..."}`.

`AlertaOut` = `{id, tipo, severidad, estado, mensaje, credito: {id, numero_credito}, socio: {id, nombre_completo, ci}, cuota: {numero, fecha_vencimiento, cuota} | null, datos, fecha_creacion, fecha_cierre | null, usuario_cierre: {id, nombre} | null, comentario_cierre | null, gestion: GestionOut | null}`.
`GestionOut` = `{id, tipo_contacto, resultado_gestion, fecha_compromiso_pago | null, fecha, usuario: {id, nombre} | null, alerta_id | null}`.

1. `POST /creditos/alertas/monitoreo` → `{fecha, creditos_monitoreados, alertas_creadas, alertas_resueltas, activas_por_severidad: {INFO, ADVERTENCIA, CRITICA}}`.
2. `GET /creditos/alertas?estado=&tipo=&severidad=` → `[AlertaOut]` (default estado=ACTIVA), CRITICA first then newest.
3. `POST /creditos/alertas/{id}/atender` body `{tipo_contacto: "LLAMADA"|"VISITA"|"MENSAJE"|"OTRO", resultado_gestion (≥ 10 chars), fecha_compromiso_pago?}` → `AlertaOut` (ATENDIDA with its `gestion`). 409 if not ACTIVA; 400 invalid data (promise date must be ≥ today).
4. `POST /creditos/alertas/{id}/descartar` body `{comentario}` (≥ 5 chars) → `AlertaOut` (DESCARTADA). 409 if not ACTIVA.
5. `GET /creditos/creditos/{id}/gestiones` → `[GestionOut]` newest first (includes the seed row).
6. `GET /creditos/monitoreo/resumen` → `{por_moneda: [{moneda, cartera_total, cartera_en_mora, indice_mora}], creditos_por_riesgo: {BAJO, MEDIO, ALTO, SIN_PREDICCION}, alertas_activas: {INFO, ADVERTENCIA, CRITICA}, top_riesgo: [{credito_id, numero_credito, socio: {id, nombre_completo}, probabilidad_mora, nivel_riesgo, saldo_pendiente}], ultima_ejecucion | null}`.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/alertas-mora`):
- [x] B1 Migration 021 applied twice; models/schemas
- [x] B2 Monitoring run: each alert type + severity, idempotency, auto-close (condition gone / credit CANCELADO)
- [x] B3 Attend (collection action) / dismiss + collection history
- [x] B4 Summary endpoint (arrears index per currency, risk counts, top 5) + audit

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/alertas-mora`):
- [x] F1 `creditosApi.js`: add functions for endpoints 1–6 (add only)
- [x] F2 New `AlertasPage.jsx` for `/oficial/alertas` and `/admin/alertas` + nav item "Alertas" in both layouts: summary cards (per currency index, risk levels labeled "Modelo sintético (informativo)", alerts by severity, top 5), "Ejecutar monitoreo" button, alert list with filters and severity colors
- [x] F3 Attend modal (collection action form) and dismiss modal; collection history in the credit detail (Cartera)

Orchestrator — Claude:
- [x] V1 Review, E2E (due soon / overdue within grace / arrears / high risk; idempotent rerun; auto-resolve after payment; attend + history; summary numbers hand-checked), commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_alertas_mora.py`. Baseline 239 passed / 5 pre-existing failures. **Start coopDB first if it is stopped (`docker start coopDB`) so integration tests actually run and RED is observed.** No leftover test cooperatives.
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings. Do not touch the user's deleted `stitch_nexacoop_frontend_redesign/` files.

## Progress
- 2026-09-27: branches `feat/alertas-mora` created from `feat/comite-credito`; decisions and contract defined; delegated.
- 2026-09-27 resume preflight: the previous turn left no source changes; only this task document is untracked on `feat/alertas-mora`. Local `coopDB` was already running on port 5433 and `pg_isready` accepted connections before TDD. Backend route is delegated direct for B1–B4 after 4+ file mapping; strict TDD is explicitly required by the user, runner `.venv/bin/python -m pytest`, baseline 239 passed / 5 pre-existing failures. Forecast ~900–1,300 authored changed lines, delivery strategy `ask-on-risk`; no Git write/commit will be attempted under the user's rule.
- Mapping decisions: use database state `ACTIVA` (not the prose word ACTIVE), preserve the legacy `historial_gestion_de_cobranza` seed and map API `fecha_compromiso_pago` to SQL `fecha_de_compromiso_de_pago`. Existing W27/W23 refresh endpoints commit independently, so monitoring must reuse their calculation/service helpers rather than call the routes. Interpret `RESUELTA` as eligible for a new ACTIVE alert only if the same condition later recurs; `ATENDIDA`/`DESCARTADA` suppress further alerts for the same credit/type/installment key. Keep all monetary portfolio/index arithmetic Decimal with ROUND_HALF_UP.
- 2026-09-27 backend execution: B1 migration/models/schemas implemented. Test-first evidence: `.venv/bin/python -m pytest tests/test_alertas_mora.py -q` initially RED (`1 failed`: migration 021 absent); after implementation GREEN (`2 passed`), including applying migration 021 twice and verifying legacy history row count remains ≥1. coopDB was rechecked before integration test: container `coopDB Up`, localhost:5433 TCP connect succeeded (`pg_isready` executable unavailable).
- B2: `app/services/alertas_mora.py` classifies installments, reconciles active conditions, suppresses ATENDIDA/DESCARTADA keys, and restricts high-risk candidate creation to eligible W23 predictions. `creditos.py` monitoring reuses W27 arrears refresh and a shared W23 prediction refresh helper; it creates/resolves alerts and audits each run. RED/GREEN evidence: missing classifier (`ModuleNotFoundError`) and reconciler/high-risk candidate functions (`ImportError`) each failed before their implementations; focused service tests then passed. DB-backed fixture observed three installment alert types, exact severity counts, repeated-run idempotence, condition-gone resolution, new installment occurrence, and CANCELADO auto-resolution. High-risk candidate gating is covered at the helper boundary; the integration test uses a test-held existing prediction and deliberately stubs refresh to isolate alert candidate behavior.
- B3: attend/dismiss actions, payload validation, role+tenant restrictions, and collection-history query are implemented. DB-backed focused test observes successful attend/dismiss, second attend returns 409, an out-of-tenant dismissal returns 404, and history includes both a legacy row and new action. Cleanup removes collection rows before credit parents.
- B4: summary groups portfolio/index per currency with Decimal/ROUND_HALF_UP, risk counts, severity counts, sorted top-five risk, and last monitored timestamp. DB-backed assertions verify BOB 900.00 balance, 100.00% arrears index, a 0.9000 top risk, empty summary, audit timestamps, and read/write role scope.
- TDD limitation: the classifier, reconciliation, high-risk gating, prediction-helper, and schema-boundary tests each recorded RED before implementation. The full DB endpoint fixture for B2–B4 was added after endpoint code existed; its later green run is integration evidence, not a genuine pre-implementation RED for every HTTP route.
- Verification: `.venv/bin/python -m pytest tests/test_alertas_mora.py -q` — 9 passed; `.venv/bin/python -m pytest -q` — 248 passed, 5 failed (the same five existing failures: `test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`, `test_registrar_socio_y_abrir_cuenta`, `test_emitir_y_listar_certificado_aportacion`, `test_deposito_retiro_y_saldo_insuficiente`, `test_tenant_no_puede_abrir_cuenta_para_socio_ajeno`); `git diff --check` clean.
- Hygiene: all focused integration fixture artifacts were removed. Final DB query showed only `Cooperativa de Prueba SI2`, zero `alerta_credito` rows. A direct final query of `control_caja` confirmed session ID 406 exists and remains `ABIERTA`; no change to it was made.
- 2026-09-27 contract follow-up: added HTTP regressions before fixes. Invalid attend data (short result and promise date in the past) and invalid dismissal observed RED (`422 != 400`); after correction these return HTTP 400 with the standard string `detail`. Summary regression sets `credito.moneda_id=NULL` while the originating request retains BOB; observed RED (`N/A != BOB`); after correction the currency falls back to `credito.solicitud.moneda`. Final focused suite: `9 passed`; final full suite: `248 passed, 5 failed` (same known failures listed above). `git diff --check` clean. Final hygiene: only `Cooperativa de Prueba SI2`, zero alerts, zero test users; a direct query confirms `control_caja.id=406` is `ABIERTA` and was untouched.
- 2026-09-27 (orchestrator review): scope respected on both sides (frontend creditosApi additions only, 2 routes + 2 nav items, alerts page/components, collection history in credit detail; package/lock and the user's stitch deletions untouched). Backend `pytest -q`: 248 passed / 5 failures identical to baseline (hash); no leaks. Frontend build OK, oxlint 8 pre-existing / 0 errors.
- E2E through the Vite proxy on a real credit (CONS-BOB 2000/12, grace 3): installment due in 3 days → CUOTA_POR_VENCER/INFO; rerun creates 0 (idempotent); overdue 2 days → CUOTA_VENCIDA/ADVERTENCIA (previous resolved); overdue 10 days → MORA/CRITICA (previous resolved); summary BOB index 100.00 = hand check; payment of installment 1 → its alerts RESUELTA; installment 2 in arrears → cashier attend 403, past promise date 400, attend OK → ATENDIDA with collection action, second attend 409, monitoring does not reopen it; credit history lists the action. RIESGO_ALTO not reachable with the demo data (all predictions BAJO) — covered by Codex's unit tests only. E2E rows removed; seed arrears row restored.
- Minor follow-ups (sent with CU-W22): summary amounts are JSON numbers instead of 2-decimal strings; validation `detail` leaks the Pydantic prefix "Value error, ". Legacy seed credit has no currency and appears as "N/A" in the summary (data, not code).
