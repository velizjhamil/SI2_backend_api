# Feature: mobile-solicitud-credito (CU-M7 Solicitud Digital de Crédito)

## Objective
Let a socio request a credit from the mobile app: browse active credit products, simulate the installment, submit a
digital request with self-declared data, follow its status, and cancel it while it is still pending. The loan
officer completes the socioeconomic evaluation afterwards (web CU-W21 edit, or CU-M8 in the field) before scoring
(CU-W23), committee (CU-W25) and disbursement (CU-W24) — those flows stay unchanged.

## Current state (verified)
- Staff `POST /api/v1/creditos/solicitudes` (CU-W21) requires `socio_id` and a full `evaluacion`
  (`SolicitudCreditoCreate`); roles OFICIAL_CREDITO/ADMINISTRADOR.
- `solicitud_credito.evaluacion_campo_id` is nullable; `estado` default PENDIENTE; `destino`, `destino_detalle` exist.
- Socio self-service pattern exists: `app/api/v1/endpoints/socio.py` with `get_current_socio` (CU-M3..M13).
- Mobile: `SocioService` + screens (`lib/services/socio_service.dart`, `lib/screens/socio_screens.dart`), secure
  session from CU-M1 (`AuthService.tokenJWT`, `SessionManager.onUnauthorized` on 401).

## Backend design (Codex) — prefix `/api/v1/socio`, `get_current_socio`, tenant + ownership in WHERE (foreign → 404)
- Migration `028_sprint11_solicitud_movil.sql` (idempotent): `solicitud_credito.canal_origen VARCHAR(12) DEFAULT
  'VENTANILLA' CHECK IN ('VENTANILLA','MOVIL')`; `solicitud_credito.datos_declarados JSONB NULL` (socio's
  self-declared income/expenses/activity, never used as the officer's evaluation).
- `GET /productos-credito` → active products of the socio's cooperative: `id, codigo, nombre, moneda, monto_min,
  monto_max, plazo_min_meses, plazo_max_meses, tasa_interes_anual, tipo_amortizacion, requiere_garantia`.
- `GET /creditos/simulacion?producto_id&monto&plazo_meses` → `{cuota_estimada, total_intereses, total_a_pagar,
  cronograma:[{numero, capital, interes, cuota, saldo}]}` reusing the CU-W21/W24 amortization logic (no new formula);
  out-of-range monto/plazo → 422 with the same messages as W21. Informative only (not persisted).
- `POST /solicitudes` `{producto_id, monto, plazo_meses, destino, destino_detalle?, datos_declarados:{
  ingreso_mensual, egreso_mensual, actividad_economica, fuente_ingresos}}` → 201; creates the request for the
  authenticated socio with `canal_origen='MOVIL'`, `estado='PENDIENTE'`, `evaluacion_campo_id=NULL`, number from the
  existing sequence, `usuario_id` = socio's user; reuse W21 validations (product ACTIVO and same cooperative, amount
  and term within limits, destino catalogue, one in-progress request per socio → 409 with the existing message);
  `registrar_accion`. Declared amounts are strings with 2 decimals, > 0 (egreso ≥ 0).
- `GET /solicitudes` → own requests newest first: `id, numero_solicitud, producto, monto, plazo_meses, destino,
  estado, canal_origen, fecha_solicitud, requiere_evaluacion (evaluacion_campo_id is null), motivo (rejection/
  observation text if any)`; `GET /solicitudes/{id}` detail with a status timeline from existing data.
- `POST /solicitudes/{id}/cancelar {motivo}` → only own PENDIENTE requests without evaluation; reuse the W21
  cancellation/annulment path and state; else 409.
- Staff side must tolerate requests without evaluation: W21 list/detail show `canal_origen` and
  `requiere_evaluacion`; W23 scoring on such a request → 422 "Falta la evaluación de campo del oficial" (no crash);
  the existing W21 `PUT` with `evaluacion` completes it. Add tests for these three staff behaviours.

## Mobile design (Antigravity) — SI2_mobile_app
- `SocioService` (or a new `SolicitudService` following the same pattern and 401 handling) for the endpoints above;
  money as strings; errors show backend `detail`.
- Screens: "Solicitar crédito" entry (Services/Dashboard) → product list → simulator (monto/plazo sliders or fields
  within product limits, shows cuota and schedule preview) → form (destino select with the backend catalogue values,
  detalle, declared income/expenses/activity) → confirmation summary → submit; "Mis solicitudes" list with status
  chips and detail timeline; cancel with reason + confirmation for PENDIENTE.
- Tests: service tests with MockClient using CAPTURED real payloads (the orchestrator will capture them from the
  running backend and pass the file paths); widget tests for the form validation and submit flow.

## Tasks
- [x] B1 (Codex): migration 028 + products + simulation + tests.
- [x] B2 (Codex): create/list/detail/cancel socio requests + staff tolerance (W21/W23) + tests.
- [ ] M1 (Antigravity): mobile service + screens + tests (after the orchestrator hands real payloads).
- [ ] V1 (Orchestrator): suite, DB intact (incl. bitacora), migration replay, E2E, payload capture, flutter
      analyze/test, commits, RDD.

## Checks
- Backend strict TDD `.venv/bin/python -m pytest -q` (baseline 477 passed / 5 pre-existing / 1 skipped); own
  cooperative fixtures, cleanup deletes bitacora before users/cooperatives; report bitacora counts, never delete rows.
- Mobile `/home/yimy/flutter/bin/flutter test` (baseline 54) and `flutter analyze` (no new errors/warnings); no new
  dependencies; do not touch android/ or ios/ (user's uncommitted files).

## Route / trigger evidence
- Backend → Codex (w2:p1), branch `feat/mobile-solicitud-credito` from `feat/informes-asfi`.
- Mobile → Antigravity (w2:p5, mobile owner since 2026-09-29), branch `feat/mobile-solicitud-credito` from
  `feat/mobile-auth`.

## Progress
- 2026-09-29: current W21 flow verified (evaluation required for staff, nullable in DB); contract written.
- 2026-09-29: B1/B2 implemented in `SI2_backend_api`; migration 028 applied twice. Initial full backend suite: 482 passed, 5 failed, 1 skipped. Failures are existing tenant-listing/savings test expectations. Test DB cooperative/user counts returned to baseline (1/20); bitacora increased during suite execution and was preserved without cleanup. No commit created per explicit instruction.
- 2026-09-29 correction: B2's initial implementation preceded its tests; the original B2 test chronology was not strict test-first. Added explicit W21 list/detail metadata, W21 PUT evaluation-completion, and W23 missing-evaluation tests. Temporarily removed W21 response metadata and observed the test fail (`requiere_evaluacion` defaulted false); temporarily removed the W23 guard and observed W23 return 201 instead of required 422. Restored production behavior; correction tests pass (3 passed), entire mobile module passes (24 passed). Re-run full backend suite: 484 passed, 5 failed, 1 skipped; the same five tenant-listing/savings test failures remain. Counts after re-run: cooperativa=1, usuario=20, bitacora=7764; the prior count was 7701, and none of the additional audit rows were deleted. Fixture cleanup removes evaluation rows only for fixture-owned mobile request IDs and evaluation-field rows only for fixture-owned socios.
- 2026-09-29 shared-rule correction: B2's original production code duplicated W21 validation, in-progress-request lookup, and cancellation/audit behavior. Added `app/services/credit_request_rules.py` and routed both W21 and mobile create/cancel through those shared helpers; mobile-only cancellation remains restricted to PENDIENTE with no officer evaluation. Recredit in-progress checks also use the shared lookup. Added helper tests before implementation (RED: module import unavailable; GREEN: 4 passed). Focused W21/mobile/shared tests: 12 passed. Full backend suite: 488 passed, 5 failed, 1 skipped; the same tenant-listing/savings failures remain. Counts before/after this suite: coop=1, usuario=20; bitacora=7764→7827, preserved without deletion. No commit created per explicit instruction.
- 2026-09-29 parent spot-check: focused command across `test_credit_request_rules.py`, `test_mobile_socio.py`, and `test_solicitud_credito.py` returned 24 passed, 18 deselected; counts remained coop=1, usuario=20, bitacora=7827 before/after. `git diff --check` passed.
- 2026-09-29 (orchestrator verification): migration 028 replayed (exit 0); full suite `488 passed, 5 failed, 1 skipped` (same 5 pre-existing); DB identical except the known bitacora leak (removed). E2E as socio@test.com: cajero 403; 3 active products; simulation CONS-BOB 1500/6 → cuota 263.29 (consistent with CRE-000002 1200/6 → 210.63), out-of-range 422; invalid destino 400; create 201 SOL-000004 (canal MOVIL, requiere_evaluacion true); second in-progress 409; list/detail with timeline; other socio 404; staff detail shows canal/requiere_evaluacion; staff scoring 422 "Falta la evaluación de campo del oficial"; cancel without reason 400, cancel 200 (ANULADA), cancel again 409. SOL-000004 remains in the DB as an ANULADA demo request. Real payloads captured for the mobile work in /home/yimy/proyectos/si2/.rdd/out/m7_*.json.
