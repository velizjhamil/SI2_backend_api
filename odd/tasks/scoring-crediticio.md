# Feature: Credit scoring and automated dictamen — CU-W23 part 1

Plan: W20 ✅ → W21 ✅ → **W23 part 1** → W24 → W26 → W27 → W23 part 2 (re-credit offers + default-probability model + explanation layer). Autonomous run authorized by the user on 2026-09-26.

## Scope of part 1
Rule-based, explainable credit scoring (0–1000) with an automated dictamen (APROBADO / RECHAZADO / REVISION_MANUAL) for requests registered in CU-W21, plus a minimal human resolution for the gray zone. The README rule stands: the core risk engine is rule-based; AI (part 2) never alters it.

## User story (fictional, drafted by the orchestrator)
> Como oficial de crédito quiero obtener un puntaje y un dictamen automático y explicado para cada solicitud, para decidir de forma rápida, consistente y auditable; y como administrador quiero resolver los casos que el motor deriva a revisión manual.

Acceptance criteria:
1. Evaluating a request computes a 0–1000 score from explicit, weighted factors and returns each factor's points, maximum, observed value and reason.
2. Knock-out rules can reject or send to manual review regardless of the score, and are listed explicitly.
3. The dictamen updates the request state (APROBADO / RECHAZADO / EN_EVALUACION for manual review) and every evaluation is stored as history (re-evaluation allowed while not finally decided).
4. An administrator resolves manual-review cases (approve/reject) with a mandatory justification.
5. A plain-language explanation of the dictamen is generated deterministically from the factors (no external AI in part 1). Everything is audited.

## Scoring model `reglas-v1` (constants in one backend module, e.g. `app/services/scoring.py`)
Inputs: the request (monto, plazo, tasa snapshot, product), its evaluation (`evaluacion_campo`), the socio (fecha_registro), the socio's ACTIVE savings accounts, and internal credit history (`credito` of previous requests of the same socio, `morosidad`).
Let `cuota` = estimated installment of the request (same function as CU-W21, FRANCES/ALEMAN first installment), `ingreso` = ingreso_mensual, `ratio` = cuota / ingreso × 100, `max` = product `relacion_cuota_ingreso_max`.

| Factor (code) | Max | Points |
|---|---|---|
| `CAPACIDAD_PAGO` ratio vs max | 300 | ratio ≤ 0.5·max → 300; 0.5·max < ratio ≤ max → linear from 300 down to 120 (rounded to int); ratio > max → 0 |
| `CALIFICACION_ASFI` | 200 | A 200, B 150, C 80, D 20, E 0, F 0, missing 0 |
| `ANTIGUEDAD_LABORAL` months | 100 | ≥36 100; ≥24 80; ≥12 60; ≥6 30; else 0 |
| `ENDEUDAMIENTO` cuota_deudas / ingreso | 100 | 0 → 100; ≤10% 80; ≤20% 50; ≤30% 20; else 0 |
| `ANTIGUEDAD_SOCIO` months since socio.fecha_registro | 100 | ≥24 100; ≥12 70; ≥6 40; else 10 |
| `AHORRO` Σ saldo_disponible of ACTIVE accounts in the request currency / monto | 100 | ≥20% 100; ≥10% 70; ≥5% 40; >0 20; 0 → 0 |
| `HISTORIAL_INTERNO` previous credits of the socio | 100 | any `morosidad` with estado EN_MORA → 0; previous credits without arrears → 100; no history → 50 |

Knock-outs (evaluated first, all that apply are listed; dictamen precedence RECHAZADO > REVISION_MANUAL):
- `CUOTA_SUPERA_CAPACIDAD`: cuota > capacidad_pago (ingreso − egreso − cuota_deudas) → RECHAZADO.
- `CALIFICACION_ASFI_CRITICA`: ASFI E or F → RECHAZADO.
- `SIN_EVALUACION`: no evaluation or ingreso ≤ 0 → RECHAZADO (cannot assess).
- `MORA_VIGENTE`: socio has an EN_MORA morosidad → REVISION_MANUAL.
- `RATIO_SUPERA_MAXIMO`: ratio > max → REVISION_MANUAL.
Without knock-outs: score ≥ 700 → APROBADO; 500–699 → REVISION_MANUAL; < 500 → RECHAZADO.

Explanation (`explicacion`, Spanish, deterministic): one sentence with dictamen + score, then the knock-outs, then the two weakest and the two strongest factors with their reasons.

## Decisions (orchestrator)
- Roles: evaluate = OFICIAL_CREDITO or ADMINISTRADOR; resolve = ADMINISTRADOR only; read = cooperative staff. Tenant rules as in CU-W21.
- Evaluable states: PENDIENTE, OBSERVADA, EN_EVALUACION. After evaluation the request goes to APROBADO / RECHAZADO (final for W23) or EN_EVALUACION (manual review). Requests without product (legacy) → 409 "La solicitud no tiene producto crediticio".
- Resolution only when the request is EN_EVALUACION and its latest dictamen is REVISION_MANUAL; decision APROBADO or RECHAZADO; justification ≥ 10 chars; stored on the evaluation row.
- Bitácora modulo `CREDITOS`: `EVALUAR_SOLICITUD`, `RESOLVER_SOLICITUD`.

## Migration `migrations/016_sprint7_scoring_crediticio.sql` (idempotent; do not modify 002–015)
- `evaluacion_crediticia(id BIGSERIAL PK, solicitud_credito_id INT NOT NULL REFERENCES solicitud_credito(id), cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id), version_modelo VARCHAR(20) NOT NULL, score INT NOT NULL CHECK (score BETWEEN 0 AND 1000), dictamen VARCHAR(20) NOT NULL CHECK (dictamen IN ('APROBADO','RECHAZADO','REVISION_MANUAL')), factores JSONB NOT NULL, knockouts JSONB NOT NULL, explicacion TEXT NOT NULL, cuota_estimada NUMERIC(14,2), relacion_cuota_ingreso NUMERIC(7,2), usuario_id BIGINT NOT NULL REFERENCES usuario(id), fecha TIMESTAMPTZ NOT NULL DEFAULT now(), resolucion VARCHAR(20) NULL CHECK (resolucion IN ('APROBADO','RECHAZADO')), resolucion_justificacion TEXT NULL, resolucion_usuario_id BIGINT NULL REFERENCES usuario(id), resolucion_fecha TIMESTAMPTZ NULL)`; index on (solicitud_credito_id, fecha DESC).

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos`. Errors `{"detail": "..."}` (Spanish).

`FactorOut` = `{codigo, descripcion, puntos, maximo, valor, motivo}` (`valor` string, human readable).
`KnockoutOut` = `{codigo, descripcion, efecto: "RECHAZADO"|"REVISION_MANUAL"}`.
`EvaluacionCrediticiaOut` = `{id, solicitud_id, version_modelo, score, dictamen, factores: [FactorOut], knockouts: [KnockoutOut], explicacion, cuota_estimada, relacion_cuota_ingreso, fecha, usuario: {id, nombre}, resolucion: null | {decision, justificacion, fecha, usuario: {id, nombre}}}`.

1. `POST /creditos/solicitudes/{id}/evaluacion` → `201 EvaluacionCrediticiaOut`; updates request state. 409 if state not evaluable or no product; 404 outside cooperative; 403 by role.
2. `GET /creditos/solicitudes/{id}/evaluaciones` → `[EvaluacionCrediticiaOut]` newest first.
3. `POST /creditos/solicitudes/{id}/resolucion` body `{decision: "APROBADO"|"RECHAZADO", justificacion}` → `200 EvaluacionCrediticiaOut` (latest, with resolucion) and request state = decision. 409 if not EN_EVALUACION or latest dictamen is not REVISION_MANUAL; 400 short justification; 403 non-admin.
4. Additive change to CU-W21 `SolicitudOut`: new field `ultima_evaluacion: null | {id, score, dictamen, fecha}`. Nothing else in W21 changes.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/scoring-crediticio`):
- [x] B1 Migration 016 applied twice to local coopDB; history model/schemas, constraints, and index verified; open control_caja 406:ABIERTA unchanged before/after.
- [x] B2 Scoring service: every factor band and every knock-out covered by unit tests with exact points (RED→GREEN; 44 passed).
- [x] B3 Evaluation endpoint + history + state transitions + `ultima_evaluacion` in SolicitudOut (RED→GREEN; role/tenant/product/state and history covered).
- [x] B4 Resolution endpoint with role/state checks + audit (RED→GREEN; administrator approval, justification, state, and audit verified).

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/scoring-crediticio`):
- [x] F1 `creditosApi.js`: add `evaluarSolicitud`, `listarEvaluaciones`, `resolverSolicitud` (add only)
- [x] F2 In the request detail (`src/modules/oficial/components/solicitudes/DetalleSolicitudModal.jsx`): "Evaluar" button for evaluable states; result panel with score gauge 0–1000, dictamen badge, knock-outs, factor table (points/max/value/reason), explanation; evaluation history; list shows `ultima_evaluacion` dictamen badge
- [x] F3 Admin access: route `/admin/solicitudes` reusing `SolicitudesPage` and nav item "Solicitudes" in `AdminLayout.jsx`; "Resolver" action (approve/reject + justification) visible only to ADMINISTRADOR when the request is EN_EVALUACION with REVISION_MANUAL
- [x] F4 Printable evaluation report (window.print())

Orchestrator — Claude:
- [x] V1 Review, checks, hand-computed E2E scores through the Vite proxy, commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_scoring_crediticio.py`. Baseline 105 passed / 5 pre-existing failures. Tests must not leak rows even when interrupted mid-run (use try/finally or fixtures with cleanup).
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings.

## Progress
- 2026-09-26: branches `feat/scoring-crediticio` created from `feat/solicitud-credito`; model, decisions and contract defined; delegated.
- 2026-09-26: delegated-direct implementation selected after read-only mapping; strict TDD enabled by session instructions with `.venv/bin/python -m pytest`; parent baseline is 105 passed / 5 known failures. For deterministic integer interpolation and tied factor ordering, use Decimal `ROUND_HALF_UP` and stable factor-code ordering, respectively.
- 2026-09-26: B1 RED: new model/schema/migration tests failed (model missing, migration file/table absent); GREEN after migration/model/schema implementation, dual apply, and `3 passed`.
- 2026-09-26: B2 RED: 41 scoring cases failed because `app.services.scoring` was absent; GREEN after service implementation and corrected the zero-income fixture to expect every applicable knockout (44 passed). Decimal ROUND_HALF_UP and stable code-order ties used as planned.
- 2026-09-26: B3 RED: four endpoint/route/history tests failed (routes absent/404); GREEN after tenant-scoped evaluation/history handlers and `ultima_evaluacion` serialization (`4 passed`).
- 2026-09-26: B4 RED: resolution route/schema tests failed (route absent/404); GREEN after admin-only resolution endpoint and audit/state update (`3 passed`).
- 2026-09-26: B2 boundary regression: RED for ratios 40.004% and 10.004% because quantizing ratios before thresholds masked values just above limits; GREEN after keeping ratios exact for scoring/knockouts (`11 passed`).
- 2026-09-26: Final focused regression command `.venv/bin/python -m pytest tests/test_scoring_crediticio.py tests/test_solicitud_credito.py tests/test_productos_credito.py tests/test_dpf.py tests/test_caja.py tests/test_transferencias.py -q`: `102 passed, 2 warnings in 48.92s`.
- 2026-09-26: Final full command `.venv/bin/python -m pytest -q`: `157 passed, 5 failed, 2 warnings in 63.33s`. The 5 failures match the documented baseline: `test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` and four stale payload expectations in `tests/test_savings.py` (`test_registrar_socio_y_abrir_cuenta`, `test_emitir_y_listar_certificado_aportacion`, `test_deposito_retiro_y_saldo_insuficiente`, `test_tenant_no_puede_abrir_cuenta_para_socio_ajeno`).
- 2026-09-26: Parent spot-check reran `.venv/bin/python -m pytest -q`: `5 failed, 157 passed, 2 warnings in 63.28s (0:01:03)`; the same 5 baseline tests failed.
- 2026-09-26: `git diff --check` passed; no changes outside the authorized backend/task paths were observed. No contract deviations.
- 2026-09-26 (orchestrator review): scope respected on both sides (frontend creditosApi additions only, /admin/solicitudes route + nav item, solicitudes/scoring components; package/lock intact; role read from user.rol.nombre, matching /auth/me UserOut). Backend `pytest -q`: 157 passed / 5 failures identical to baseline (hash); row counts identical before/after the full suite (no leaks). Frontend build OK, oxlint 8 pre-existing / 0 errors.
- E2E through the Vite proxy with an independent calculator of the reglas-v1 table on real socio data (socio 3: 0 months as member, BOB savings 3350.50, no prior credits): scenario A (10000/12m, income 6000, ASFI A, 36 months) → backend 860 = expected 860 factor by factor, APROBADO, request APROBADO, re-evaluation 409; scenario B (ASFI E) → 660, knock-out CALIFICACION_ASFI_CRITICA → RECHAZADO; scenario C (income 2000, ratio 45.84 > 40) → 560, knock-out RATIO_SUPERA_MAXIMO → REVISION_MANUAL, request EN_EVALUACION; oficial resolve 403; short justification 400; admin resolves APROBADO → request APROBADO; history returned. E2E rows removed and correlative restored.
