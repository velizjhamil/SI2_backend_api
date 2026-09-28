# Feature: reportes-gerenciales (CU-W33 Generación de Reportes Financieros y Gerenciales)

## Objective
Management reports for the cooperative's administration (ADMINISTRADOR; CONTADOR and OFICIAL_CREDITO read) that
aggregate what already exists: credit portfolio (CU-W20..W28), savings and DPF (CU-W10..W19) and accounting
(CU-W29..W32). No new business data; read-only aggregation, date/currency filters, CSV export, simple charts.

## Definitions (documented in every response as `definicion`)
- Cartera bruta: Σ `credito.saldo_pendiente` of credits VIGENTE (and any other non-closed state the code uses) at the
  cut-off. Cartera en mora: the subset whose `morosidad.estado = 'EN_MORA'` — the same criterion CU-W28
  `/creditos/monitoreo/resumen` uses (reuse it; do not define a second one). Índice de mora = en mora / bruta × 100.
- Accounting indicators come from the CU-W32 statement logic (reuse `accounting_sql` / estados_financieros helpers),
  consolidated in BOB with the registered exchange rate; if the rate is missing return the indicator as `null` with
  `motivo` instead of failing the whole report:
  - Liquidez = Disponibilidades (110) / (Obligaciones con el público a la vista 211 + por cuentas de ahorro 212) × 100.
  - Cobertura de previsiones = |Previsión para incobrabilidad de cartera (139)| / cartera en mora × 100 (null if en mora = 0).
  - ROA = resultado de la gestión anualizado / activo total × 100; ROE = resultado anualizado / patrimonio × 100;
    anualizado = resultado YTD × 365 / días transcurridos del año.
- These are standard management ratios; label them "indicadores de gestión" (not regulatory ASFI figures — those are W34).

## API (prefix `/api/v1/reportes`, roles ADMINISTRADOR, CONTADOR, OFICIAL_CREDITO; tenant-scoped; money strings)
- `GET /cartera?fecha_corte&moneda_id` → `{totales{cartera_bruta, cartera_en_mora, indice_mora, creditos},
  por_estado[], por_producto[], por_destino[], por_rango_mora[{rango:"0","1-30","31-90","91-180",">180", saldo,
  creditos}], colocaciones_mes[{mes, monto, creditos}] (last 12 months of disbursements)}`.
- `GET /captaciones?fecha_corte&moneda_id` → `{ahorros_por_tipo[{tipo, cuentas, saldo}], dpf_por_plazo[{rango
  (MCEF ranges 30/31-60/61-90/91-180/181-360/361-720/721-1080/>1080 días), certificados, capital}],
  dpf_vencimientos_proximos (next 30 days: count and capital), totales}`.
- `GET /indicadores?fecha_corte` → `{indicadores:[{clave, nombre, valor|null, unidad:"%", motivo?, definicion}],
  tipo_cambio}`.
- `GET /resumen-ejecutivo?fecha_corte&moneda_id` → the headline figures of the three above (for a dashboard).
- `/cartera/export`, `/captaciones/export`, `/indicadores/export` → CSV (UTF-8 BOM, filename with date).
- Validation: `fecha_corte` not in the future (422); unknown moneda (422). Default fecha_corte = today, moneda_id = 1.

## Tasks
- [x] B1 (Codex): cartera report (+ reuse of the W28 mora criterion) + tests.
- [x] B2 (Codex): captaciones report + tests.
- [x] B3 (Codex): indicadores + resumen ejecutivo + CSV exports + tests (known fixtures with hand-computed ratios;
      null indicators with motivo when rate or denominators are missing).
- [x] F1 (Antigravity): "Reportes" page for ADMINISTRADOR (replace the admin ReportesPage stub) and the same component
      for CONTADOR (replace the contador ReportesPage stub): tabs Resumen / Cartera / Captaciones / Indicadores,
      filters, KPI cards, tables, charts WITHOUT new dependencies (inline SVG/CSS bars and a donut), CSV, print.
- [x] V1 (Orchestrator): suite, DB intact (incl. bitacora), E2E, build/lint/no-undef/test, commits, RDD per slice.

## Checks
- Backend strict TDD `.venv/bin/python -m pytest -q` (baseline 353 passed / 5 pre-existing failures / 1 skipped;
  +1 after the W32 fix). Own-cooperative fixtures; report bitacora counts, never delete rows.
- Frontend `pnpm build`, `pnpm lint` (8 pre-existing), `npx oxlint -D no-undef` on changed files, `pnpm test`
  (node:test; add unit tests for any new pure helper, e.g. chart scaling / percentage formatting, in `tests/`).
- Every apply/refresh button forces a refetch even with unchanged filters.
- Review lesson: keep each commit < ~800 lines with its own tests; corrections must stay inside already-changed files.

## Route / trigger evidence
- Backend delegated direct → Codex (w2:p1), branch `feat/reportes-gerenciales` from `feat/estados-financieros`.
- Frontend delegated direct → Antigravity (w2:p5), branch `feat/reportes-gerenciales` from `feat/estados-financieros`.

## Progress
- 2026-09-28: data and dependency survey (no chart library → no new dependency); contract written; branches created.
- 2026-09-28: W28 implementation check found `/creditos/monitoreo/resumen` does not read `morosidad.estado`; it classifies each VIGENTE credit by calling `resumir_morosidad` over unpaid schedule rows and product grace days. W33 will reuse that service at `fecha_corte` so historical reports use the same criterion at the requested cutoff; no alternate arrears rule will be added.
- 2026-09-28: Implemented B1–B3 as read-only `/api/v1/reportes` endpoints in `app/api/v1/endpoints/reportes.py`, registered in `app/api/v1/router.py`, with disposable tenant fixtures in `tests/test_reportes_api.py`. B1 uses W28 `resumir_morosidad` at the requested cutoff and SQL aggregation; B2 uses SQL grouping/aggregation for savings and DPF, term bands, and 30-day maturities; B3 reuses W32 statement helpers, returns null plus `motivo` for missing FX/zero denominators, and exposes executive summary and CSV exports. The W32 consolidated helper requires rates for all non-base currencies even when they have no postings; W33 retries in BOB only when there is no foreign-currency ledger activity, while foreign activity without a rate remains null.
- 2026-09-28: TDD evidence: B1 initial test RED was HTTP 404 before route implementation, GREEN `1 passed`; B2 initial API RED was HTTP 404 (an earlier fixture setup attempt failed on missing `cuenta_ahorro.fecha_registro` and was corrected before the valid RED), GREEN `1 passed`; B3 initial API RED was HTTP 404, then its hand-calculated fixture exposed missing consolidated statement levels and no-activity FX behavior, GREEN exact ratios liquidity `133.33`, coverage `10.00`, ROA `14.97`, ROE `89.79`. Focused report test file: `6 passed` (2026-09-28).
- 2026-09-28: Full suite `.venv/bin/python -m pytest -q`: `359 passed, 5 failed, 1 skipped in 166.42s`. Failures: `tests/test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`; `tests/test_savings.py::test_registrar_socio_y_abrir_cuenta`, `test_emitir_y_listar_certificado_aportacion`, `test_deposito_retiro_y_saldo_insuficiente`, and `test_tenant_no_puede_abrir_cuenta_para_socio_ajeno` (existing assertion/request-schema drift; no W33 test failed). Bitacora count before full suite: `7620`; after: `7683` (+63, no rows deleted). W33 fixtures clean up their own business rows and never issue any DELETE against `bitacora`; full-suite audit growth is from existing tests exercising audited mutations outside the new read-only report API.
- 2026-09-28: Parent review edge corrections: balance FX fallback now checks all cumulative postings, while income FX fallback independently checks YTD postings; arrears coverage converts each overdue currency to BOB with the latest registered rate at cutoff and nulls only coverage with `motivo` if an overdue foreign currency lacks a rate; zero gross returns null `indice_mora` with `motivo_indice_mora`; captaciones and executive summary now include `definicion`; savings cutoff binding is end-of-day inclusive. Local `information_schema` and model both report `cuenta_ahorro.fecha_registro` as DATE (despite the review note describing it as timestamp); the datetime end-of-day bound remains compatible with timestamp deployments, while the local cutoff-day assertion passed.
- 2026-09-28: Correction TDD RED/GREEN: zero-gross test RED observed `indice_mora='0.00'`; definition test RED observed missing `definicion`; pre-YTD FX test RED observed BOB fallback liquidity `133.33`; foreign portfolio coverage test RED observed unconverted `10.00` vs expected `1.27`. Focused `tests/test_reportes_api.py`: `12 passed`. Final full suite `.venv/bin/python -m pytest -q`: `365 passed, 5 failed, 1 skipped in 168.11s`; same five existing failures listed above, no W33 failures. Bitacora immediately before/after: `7683` → `7746` (+63, no rows deleted). No migration or commit.
- Parent spot-check after final suite: `.venv/bin/python -m pytest -q tests/test_reportes_api.py --tb=short` → `12 passed`; cleanup left bitacora at `7746` and cooperative count at `1`.
- 2026-09-28: Bounded legacy-credit correction authorized for `app/api/v1/endpoints/reportes.py` and `tests/test_reportes_api.py`: W33 effective disbursement date uses `COALESCE(credito.fecha_desembolso, solicitud.fecha_resolucion_comite::date, solicitud.fecha_solicitud::date, credito.fecha_creacion::date)`, and effective currency uses `COALESCE(credito.moneda_id, solicitud.moneda_id)`; do not guess a currency if both are null. Apply consistently to portfolio aggregates, delinquency classification, monthly placements, indicator arrears conversion, executive summary, and delegated CSV exports. Captaciones stays unchanged because it does not depend on credits. Compare today’s W33 result to W28 `/creditos/monitoreo/resumen` only for the isolated fixture; W28 has no cutoff parameter and must not be modified. Keep all fixture data in its disposable cooperative; preserve shared cooperative 1 and all bitacora rows. This scope and fallback are recorded before source/test changes.
- 2026-09-28: Legacy-credit fallback verification: added an isolated-cooperative VIGENTE credit with NULL `credito.moneda_id` and `credito.fecha_desembolso`, but populated request currency/date. RED before source edits: W33 gross was `100.00` instead of expected `200.00`; W28 and W33 both returned HTTP 200. After applying the request fallback in `reportes.py`, focused test passed and matched W28 BOB portfolio/mora values exactly (`200.00` / `200.00`); W33 also included the `100.00` placement in the request month. `tests/test_reportes_api.py`: `13 passed`.
- 2026-09-28: Final `.venv/bin/python -m pytest -q`: `366 passed, 5 failed, 1 skipped in 165.94s`. The same five known failures remain: `tests/test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` (expected 403, got 200) and four `tests/test_savings.py` request-schema/assertion failures (opening savings missing `monto_apertura`, certificate missing `numero_titulos`/`valor_unitario`, resulting withdrawal flow KeyError, and foreign-socio request expected 404 but failed schema validation with 422). No W33 failures. Bitacora before/after this final suite: `7624` → `7687` (+63; no audit rows deleted or modified); cooperative count remained `1`. Do not touch cooperative 1. No migration, dependency, git, or review operation.
- 2026-09-28 (orchestrator verification): suite `365/366 passed, 5 failed, 1 skipped` (same 5 pre-existing); DB identical except the legacy bitacora leak (suite rows removed by the orchestrator each time; real E2E rows kept). E2E: cajero 403, future cut-off 422; indicators without USD rate return null + motivo (report does not fail); with a temporary 6.86 test rate (deleted afterwards): liquidez 97.98 %, ROA 0.26 %, ROE 134.69 % (tiny demo equity), cobertura null (no arrears in BOB). Captaciones: VISTA 5 / PROGRAMADO 3 accounts, DPF by MCEF term bands; CSV with BOM.
- E2E found the seeded legacy credit CRE-000001 (20000.00 VIGENTE) missing from W33 totals: it has no currency in credito, solicitud or product (seed data from bd.sql). Codex added the request-date/currency fallback (tested) and, since the currency is unknowable, an `advertencias` entry CREDITOS_SIN_MONEDA (1 credit, 20000.00) in cartera, resumen ejecutivo and CSV instead of silently omitting it. W28 shows it as moneda "N/A" at 100 % arrears. Assigning its currency is a pending data decision for the user.
- Frontend cf7b039 (Antigravity): Reportes page shared by ADMINISTRADOR and CONTADOR, SVG/CSS charts without new dependencies, 10 node:test tests; build OK, lint 8 pre-existing, no-undef clean. Follow-up: render `advertencias`.
