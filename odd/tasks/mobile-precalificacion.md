# Feature: mobile-precalificacion (CU-M10 Precalificación y Scoring Instantáneo en Campo)
Objective: in the field, before any formal request exists, the loan officer enters a socio's basic data and gets an
instant, explained pre-score from the SAME rule engine as CU-W23, with nothing persisted except the audit entry.
Branch `feat/mobile-precalificacion` in both repos, stacked on `feat/mobile-fotos-gps`. Runners: backend
`.venv/bin/python -m pytest -q` (DB: `docker start coopDB`); mobile `/home/yimy/flutter/bin/flutter test`.

## Specs
- S1 (L1): "CU-M10: Precalificación y Scoring Instantáneo en Campo" for the mobile version.
- S2 Current state (verified 2026-10-26): `app/services/scoring.py::score_application(solicitud, *, savings_accounts,
  has_credit_history, has_current_arrears)` reads a request-like object (monto, plazo_meses, tasa_interes, moneda_id,
  producto, socio, evaluacion, garantias). W23 `POST /creditos/solicitudes/{id}/evaluacion` (creditos.py ~940-1050)
  builds the history flags with one SQL query and loads ACTIVE savings, then also calls `predecir_mora`, persists
  `EvaluacionCrediticia` and changes the request state. `POST /creditos/solicitudes/simulacion` already validates
  product active/monto/plazo/non-negative amounts. Mobile M8/M9 already shows a W23 scoring result in the Campo module.
- S3 Backend `POST /creditos/precalificacion`, roles OFICIAL_CREDITO and ADMINISTRADOR, tenant-scoped:
  - Body: socio_id, producto_id, monto, plazo_meses, ingreso_mensual, egreso_mensual, cuota_deudas_mensual,
    antiguedad_laboral_meses, calificacion_asfi (optional, A–F), fuente_ingresos (optional, existing catalog),
    moneda_id (optional, defaults to the product/cooperative default used by W21).
  - Socio of another cooperative or unknown → 404; product validations reuse the simulation rules (same messages);
    out-of-range monto/plazo → 400 with the existing simulation messages.
  - Builds a transient request + evaluation (never added to the session) and calls `score_application` and
    `predecir_mora` exactly as W23 does. Extract the history/savings lookup from W23 into one shared helper used by
    both (the precalification has no request id to exclude). W23 behavior and output must not change.
  - Response: {precalificacion: true, score, dictamen, factores, knockouts, explicacion, cuota_estimada,
    relacion_cuota_ingreso, probabilidad_mora, nivel_riesgo, version_modelo, socio:{id, nombre_completo, ci},
    producto:{id, nombre}} — same factor/knockout shapes as W23.
  - Persists nothing (no solicitud, evaluacion_campo, evaluacion_crediticia); one bitácora entry `PRECALIFICAR`.
  - If the product requires a guarantee, the existing GARANTIA_INSUFICIENTE knockout applies (no guarantees yet).
- S4 Mobile (OFICIAL_CREDITO Campo module): a "Precalificar" entry; form with socio lookup (reuse an existing staff
  socio search endpoint, e.g. by CI), product selector (existing staff product list), monto, plazo and the evaluation
  fields; client validation like M8; result screen with score 0–1000, dictamen badge, knockouts first, factors with
  points/max/reason, cuota and probabilidad de mora; label it clearly as a pre-qualification (not a request). No offline
  here (CU-M11). Tests use captured payloads in `test/fixtures/m10/`.

## Tasks
| ID | Specs | Route | Status | Commit |
|---|---|---|---|---|
| B1 | S2–S3 | Codex (wB:p2) | done (+1 correction) | see git log feat/mobile-precalificacion |
| V1 | S3 | Orchestrator: focused + full suite vs baseline, E2E + payload capture | done: verifier PASS S2–S3 | same commit |
| M1 | S4 | Antigravity (wB:p3); fixtures in SI2_mobile_app/test/fixtures/m10 | done | mobile c1420cc |
| V2 | S1–S4 | Orchestrator: flutter test/analyze, assess/review | done | backend 50c5132 |

## Log
- L1 (2026-10-26, user): "ok" — in reply to "¿Seguimos con el CU-M10 (precalificación y scoring en campo)?"
- 2026-10-26: contract written from scoring.py/W23/simulation; branches created by the orchestrator; B1 dispatched.
- 2026-10-26 (orchestrator handoff to Codex): B1 per S2-S3, strict TDD, W23 parity test, no-persistence counts.
- 2026-10-26: B1 implemented and verified: 3 new precalification tests observed RED (404), then focused tests passed (36 solicitud, 56 scoring). Full suite: 82 failed, 507 passed, 1 skipped; none failed in either requested focused file. The single stated-baseline-count delta cannot be attributed to a new failure by name without the baseline's exact failure list; `scripts/test_payment_flow.py::test_socio_endpoints` is the reported data-dependent failure. `git diff --check` passed; no commit/review.
- 2026-10-26 V1: full suite failure set identical to the CU-M9 branch (82 failed incl. data-dependent payment flow;
  507 passed). E2E real DB (socio 3, coop 1): producto 2 -> 910 APROBADO (BAJO); ASFI E -> RECHAZADO; producto 1
  (requiere garantía) -> REVISION_MANUAL; monto 999999 -> 400; socio 999999 -> 404; ASFI Z -> 400. Row counts
  solicitud_credito/evaluacion_campo/evaluacion_crediticia unchanged; bitácora +3. Risk: item 3 (W23 history lookup
  extracted into a shared helper) -> independent verifier for W23 parity.
- 2026-10-26 Independent verifier: PASS S2–S3, no blockers. W23 differential (baseline export vs branch, 4 fresh
  scenarios incl. own-request exclusion) identical except solicitud_id; precalification flushes only Bitacora; roles
  and tenant 404 confirmed. Advisories fixed by Codex: product-active check before plazo/monto (simulation order);
  moneda_id different from the product currency -> 400; test for inactive socio 409. Parent spot check: 95 passed.
- 2026-10-26 Backend 50c5132: assess escalated (item 3); user granted native review (risk, reliability) -> approved +
  acknowledged. Follow-ups: hardcoded destino passed to validate_base_credit_request, more input-validation tests.
- 2026-10-26 M1: flutter test 120 passed (baseline 107), analyze clean; mobile c1420cc (assess medium, under_budget).
  Follow-up: GET /socios/ defaults to limite=50, so the socio picker misses socios beyond 50 (needs paging/search).
- 2026-10-26 Whole-branch review vs main (CU-M9 + CU-M10, reliability lens; user granted) -> approved + acknowledged.
  Follow-ups: coordinates re-validation of legacy stored values on round-trip, EvaluacionOut.fotografias empty outside
  ficha-campo, photo tenant/role coverage in committed tests.
