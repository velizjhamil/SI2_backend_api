# Observaciones del docente (presentación)

Objective: close the professor's presentation observations: feedback CRM, report filters, statement with installment/amortization/insurance/credit type, 1-year ML data, complete dashboard.
Branch: `feat/observaciones-docente` in SI2_backend_api, SI2_frontend_web, SI2_mobile_app. Delivery: ask-on-risk. Runner: `scripts/run_tests.py` (backend, needs `compose.test.yml` test-db), `npm run build` (web), `flutter test` (mobile).

## Specs

- S1 (dashboard): "El dashboard tiene que estar completo, totalmente explícito y estructurado."
- S2 (reportes): "Toda la parte de reportes tiene que estar completo, reporte con filtros." Today `reportes.py` only filters by `fecha_corte` and `moneda_id`; add filters for product, credit officer (asesor), state/category and date range, in the API and the web UI.
- S3 (extracto): "En nuestro caso, se debe ver su extracto de pago y cuota, amortización, también que se vea el seguro." The socio sees, for each credit, a payment statement with installment, capital (amortization), interest and insurance, filterable by date range and downloadable as PDF, in web and mobile.
- S4 (tipo de crédito): "Identificar que tipo de credito es." Socio credit views (list, detail, statement) show the credit type (`ProductoCredito.tipo_credito_asfi`) and amortization type.
- S5 (CRM feedback): "Aumentar crum, que pueda calificar el servicio  y pueda sacar datos estadísticos de la respuesta del cliente, que muestre el feedback."
  - S5.1 A socio rates a service: `servicio` in CREDITO, AHORRO, CAJA, ATENCION, APP_MOVIL, OTRO; `calificacion` 1-5 (required); `recomendaria` 0-10 (optional, NPS); `comentario` up to 1000 chars (optional); `canal` WEB or MOVIL. Scoped to the socio's cooperative.
  - S5.2 The socio lists their own ratings.
  - S5.3 Staff (ADMINISTRADOR, OFICIAL_CREDITO, ASESOR_CREDITO, CAJERO, CONTADOR) list the cooperative's feedback filtered by servicio, calificacion, desde, hasta; never another cooperative's.
  - S5.4 Staff get statistics for the same filters: total, average rating, distribution 1-5, NPS (promoters 9-10, detractors 0-6, score = %promoters - %detractors over answers with `recomendaria`), per-service count/average, monthly trend (month, total, average), latest comments.
  - S5.5 Available in every subscription plan (assumption: CRM is core, not a premium module); socio access follows the existing socio self-service dependency.
  - S5.6 Migration `036_encuesta_satisfaccion.sql`, idempotent.
  - S5.7 Web: socio rating form; staff feedback page with filters, KPIs and charts. Mobile: socio rating screen.
- S6 (ML): "La parte de machine learning poblar con datos de al menos 1 año." A Supabase-runnable `.sql` seed with at least 12 months of credits, installments and payments (on-time and late) so the ML/mora views have a year of history.

## Tasks

- [x] T1 S5.1-S5.6 backend feedback CRM — route: inline (codex not running in Herdr); `tests/test_feedback.py` RED then 12 passed
- [x] T2 S5.7 web + mobile feedback UI — route: delegated to agy (team split); web e81d05a, mobile 347b75a; agy: lint 0 errors, build OK, web tests 50 pass, flutter analyze clean, 227 mobile tests pass (1 live-API test needs a server); parent spot check: build OK, analyze clean
- [x] T3 S2 backend report filters — route: inline; `tests/test_reportes_filtros.py` RED then 12 passed; report suites 126 passed, 1 pre-existing failure (`test_mora_export_pdf_and_unchanged_csv`, fails on base too)
- [x] T4 S3-S4 backend credit statement + credit type — route: inline; 4 new tests in `tests/test_mobile_socio.py` RED then pass (30 passed); 2 `test_cobro_cuotas` debt-route failures pre-exist on base
- [ ] T5 S2-S4 web + mobile UI — route: delegated to agy
- [x] T6 S6 one-year SQL seed — route: inline; `scripts/seed_historial_1_anio.sql` dry-run on test DB in a rolled-back transaction: 36 socios, 36 credits over 15 months (31 VIGENTE, 5 CANCELADO), 192 payments (48 late), 14 overdue unpaid, 90 feedback rows over 13 months, balances consistent
- [ ] T7 S1 dashboard — backend inline DONE (62870e7, `tests/test_dashboard.py` RED then 4 passed: `/api/v1/dashboard/resumen` staff, `/api/v1/dashboard/socio`); web+mobile UI delegated to agy together with T5

## Log

- L1 (2026-11-20, user, pasted presentation observations): "El dashboard tiene que estar completo, totalmente explícito y estructurado. Toda la parte de reportes tiene que estar completo, reporte con filtros. En nuestro caso, se debe ver su extracto de pago y cuota, amortización, también que se vea el seguro. Identificar que tipo de credito es. Aumentar crum, que pueda calificar el servicio  y pueda sacar datos estadísticos de la respuesta del cliente, que muestre el feedback. La parte de machine learning poblar con datos de al menos 1 año." Documentation observations are a separate feature (needs sprint grades from the user).
- L2 (2026-11-20, user): "ok, vamos con eso" — accepted order: professor observations first, then banking features (deferral, prepayment, claims, reprogramming).
- L3: `feat/seguro-desgravamen` fast-forwarded into local `main` (not pushed). Baseline: 134 backend tests fail on a fresh test DB with `NoResultFound` (missing reference seed), also failing on pre-merge `origin/main`.
- L4: T1-T4 backend contracts: `/api/v1/feedback` (POST, /mias, list, /estadisticas); `/api/v1/reportes/cartera|cartera/export|mora/export` accept `producto_id, tipo_credito, oficial_id, desde, estado_mora` and echo `filtros`; `/api/v1/reportes/filtros` lists options; `/api/v1/socio/creditos` and `/cronograma` add `tipo_credito`, `tipo_amortizacion`, `producto`; new `/api/v1/socio/creditos/{id}/extracto` and `/extracto/export?formato=pdf|csv|html`.
- L5: Finding for S1: role dashboards for socio/cajero/oficial/contador/asesor only showed user info (`shared/components/DashboardContent.jsx`), and `AdminDashboard.jsx` had 4 counters plus hardcoded fake activity ('Comité de Crédito #48', 'Auditoría Tenant R.L.').
