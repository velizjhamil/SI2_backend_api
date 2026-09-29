# Feature: mobile-evaluacion-campo (CU-M8 Levantamiento Socioeconómico en Campo)

## Objective
A loan officer (OFICIAL_CREDITO) uses the mobile app in the field to complete the socioeconomic evaluation of pending
credit requests (typically the ones socios submit from the app, CU-M7), and then runs the existing scoring (CU-W23)
to see the dictamen. Photos/GPS (CU-M9) and offline sync (CU-M11) are later features; this one works online.

## Current state (verified)
- `evaluacion_campo` table: ingreso_mensual, egreso_mensual, capacidad_pago, cuota_deudas_mensual,
  actividad_economica, fuente_ingresos (DEPENDIENTE|INDEPENDIENTE|MIXTO), antiguedad_laboral_meses,
  calificacion_asfi (A–F), coordenadas, fotografias_respaldo, observaciones, fecha, usuario_id, socio_id.
- Staff W21: `GET /creditos/solicitudes` (filters estado, socio_ci, desde, hasta), `GET /creditos/solicitudes/{id}`,
  `PUT /creditos/solicitudes/{id}` with `evaluacion` (completes the evaluation of a MOVIL request — covered by the
  M7 tests), W23 `POST /creditos/solicitudes/{id}/evaluacion` (scoring; 422 "Falta la evaluación de campo del
  oficial" when missing).
- Mobile: role OFICIAL_CREDITO already allowed to sign in (CU-M1), but the app only has socio screens.

## Backend (Codex) — small
- `GET /creditos/solicitudes` new optional filters `requiere_evaluacion: bool` and `canal_origen: VENTANILLA|MOVIL`
  (validated; 422 on unknown) — the officer's field worklist.
- `GET /creditos/solicitudes/{id}/ficha-campo` (OFICIAL_CREDITO, ADMINISTRADOR; tenant-scoped, 404 foreign) →
  `{solicitud:{id, numero_solicitud, producto, monto, plazo_meses, destino, destino_detalle, estado, canal_origen,
  requiere_evaluacion}, socio:{id, nombre_completo, ci, telefono, direccion, actividad (if stored)},
  datos_declarados (from M7, may be null), evaluacion (current one or null), catalogos:{fuentes_ingresos,
  calificaciones_asfi}}` — only fields that exist in the DB; never invent values.
- Tests for both (RED first); reuse existing serializers.

## Mobile (Antigravity)
- After sign-in, route by role: SOCIO → existing screens; OFICIAL_CREDITO → new "Campo" module (bottom nav or home):
  worklist (requests with requiere_evaluacion=true, filter by canal; pull-to-refresh), request detail (ficha-campo:
  socio contact, request, the socio's declared data shown as reference only), evaluation form (all fields above;
  money as strings; selects from `catalogos`; validation: amounts ≥ 0, ingreso > 0, antigüedad ≥ 0, observaciones
  optional), submit with `PUT /creditos/solicitudes/{id}` {evaluacion}, then a "Calcular scoring" button calling W23
  and showing score, dictamen and factors (from the real response).
- Coordinates and photos are NOT captured here (CU-M9); leave the field untouched.
- Tests with the CAPTURED real payloads that the orchestrator vendors into `test/fixtures/m8/` (relative paths —
  never absolute paths outside the repo).

## Tasks
- [x] B1 (Codex): list filters + ficha-campo + tests.
- [ ] M1 (Antigravity): role routing + Campo module + tests (after payload capture).
- [ ] V1 (Orchestrator): suite, DB intact, E2E + payload capture, flutter test/analyze, commits.

## Checks
- Backend strict TDD (baseline 488 passed / 5 pre-existing / 1 skipped); own-cooperative fixtures, cleanup deletes
  bitacora before users/cooperatives; report bitacora counts, never delete rows.
- Mobile `flutter test` (baseline 68) and `flutter analyze` (no new errors/warnings); no new dependencies; do not
  touch android/ or ios/.

## Route / trigger evidence
- Backend → Codex (w2:p1), branch `feat/mobile-evaluacion-campo` from `feat/mobile-solicitud-credito`.
- Mobile → Antigravity (w2:p5), branch `feat/mobile-evaluacion-campo` from `feat/mobile-solicitud-credito`.

## Progress
- 2026-09-29: CU-M8 correction completed (direct delegated writer; one regression test, existing test fixtures). Added a regression for an own-coop mobile request created then canceled: the canceled request is excluded from `requiere_evaluacion=true`, and returns `requiere_evaluacion=false` from cancellation/M7 list, W21 staff list, and ficha-campo. RED observed before implementation (`canceled.json()['requiere_evaluacion']` was `true`). Shared Python and SQL predicates now use exactly `evaluacion_campo_id IS NULL AND estado IN ESTADOS_SOLICITUD_EN_CURSO`. Focused M8/W21 tests: 2 passed. Full `.venv/bin/python -m pytest -q`: 490 passed, 5 failed, 1 skipped; the five failures are the pre-existing multi-tenant role expectation and four savings API tests requiring fields absent from their payloads. Counts at correction start: coop=1, usuario=20, bitacora=7641; after the correction full-suite run: coop=1, usuario=20, bitacora=7787. No existing real rows were deleted; disposable fixture cleanup handled its own audit rows.
- Parent spot-check of the exact combined `requiere_evaluacion=true&canal_origen=MOVIL` regression passed (1 passed); counts remained coop=1, usuario=20, bitacora=7787 before/after, and `git diff --check` passed.
- 2026-09-30: backend surface verified (evaluation table and W21/W23 endpoints); contract written.
- 2026-09-29: B1 completed. Strict TDD RED observed after temporarily disabling the filter parameter (worklist included the complete request); restored implementation and fixed fixture evaluation ownership / decimal expectation. GREEN: focused M8 test 1 passed; `tests/test_solicitud_credito.py` 15 passed. Full `.venv/bin/python -m pytest -q`: 489 passed, 5 failed, 1 skipped; failures were existing multi-tenant access and savings API expectations (missing required fields). Counts before: coop=1, user=20, bitacora=7641; after: coop=1, user=20, bitacora=7704. Bitacora delta +63 from full-suite activity; no rows deleted.
- Parent spot-check: the focused M8 test passed (1 passed); counts remained coop=1, user=20, bitacora=7704 before/after. `git diff --check` passed.
- 2026-09-30 (orchestrator verification): E2E socio → SOL-000005 (MOVIL) → oficial worklist, ficha-campo (socio telefono/direccion null in demo data; catalogos present), invalid calificación 400, evaluation saved (requiere_evaluacion false), scoring 910 APROBADO (reglas-v2); socio cannot open ficha (403); unknown canal filter 422. Defect found: the worklist listed SOL-000004 (ANULADA) — Codex now defines requiere_evaluacion = no evaluation AND in-progress state (reusing the existing in-progress constant) for the list filter, SolicitudOut/ficha-campo and /socio/solicitudes; verified live (worklist excludes closed requests). Affected tests 44 passed. Real payloads captured into SI2_mobile_app/test/fixtures/m8/ (no tokens). Known bitacora leak cleaned.
