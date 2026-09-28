# Feature: estados-financieros (CU-W32 Emisión de Balance General y Estado de Resultados)

## Objective
Financial statements in Bolivianos built from the posted vouchers (CU-W30) on the ASFI MCEF chart (CU-W29), reusing
the balance logic of the books (CU-W31): Balance General (Estado de Situación Patrimonial) at a cut-off date and
Estado de Resultados for a period.

## Framework (verified vs. assumed)
- Verified: cooperatives present statements in national currency (Bolivianos); foreign-currency assets and liabilities
  are valued at the official exchange rate in force at the closing date (e.g. Cooperativa San Antonio R.L., statements
  at 31-12-2022: 6.86 Bs per USD) — https://www.cacsa.com.bo/sites/default/files/2023-03/Estados%20Financieros%2031-12-2022.pdf
- Verified: MCEF groups exist in the seeded catalog (110…190, 210…280, 310…350, 410…490, 510…590).
- Assumed (Título V layout not verified, scanned PDF): the Estado de Resultados subtotals follow the MCEF group
  order below. Document this in the response as `formato: "MCEF grupos (Título II)"`.
- The current official rate is NOT hardcoded: the cooperative registers it per date.

## Design
### Migration `026_sprint11_tipo_cambio.sql` (idempotent)
- `tipo_cambio(id, cooperativa_id FK, moneda_id FK, fecha DATE, valor NUMERIC(12,5) CHECK > 0, fuente VARCHAR(30)
  DEFAULT 'BCB', usuario_id FK, fecha_registro TIMESTAMP DEFAULT now(), UNIQUE(cooperativa_id, moneda_id, fecha))`.
  Not for the base currency (BOB, `moneda.es_base`/id 1 → 422 on insert).
- API (roles CONTADOR, ADMINISTRADOR; tenant-scoped): `GET /contabilidad/tipos-cambio?moneda_id&desde&hasta`,
  `POST /contabilidad/tipos-cambio {moneda_id, fecha, valor, fuente?}` (future date 422; duplicate date 409 —
  use `PUT /contabilidad/tipos-cambio/{id} {valor, fuente?}` to correct), all writes `registrar_accion`.
- Rate lookup for a statement date: latest `fecha <= corte` for that currency; missing → 422
  "Registre el tipo de cambio de USD vigente al {fecha}".

### Balance General — `GET /contabilidad/balance-general?fecha_corte&nivel=3&moneda=CONSOLIDADO|BOB|USD`
- Balances cumulative up to `fecha_corte` (inclusive), by nature, same rules as CU-W31 (voided + reversal net out).
- `moneda=CONSOLIDADO` (default): BOB balances + USD balances × rate at `fecha_corte`, rounded HALF_UP to 2 decimals
  per account line, then summed (state it: `criterio_conversion`). `BOB`/`USD`: that currency only, unconverted.
- Sections: ACTIVO (class 100), PASIVO (200), PATRIMONIO (300). Rows grouped by grupo (nivel 2) with cuentas
  (nivel 3; `nivel=2` hides them). Regularizing accounts (e.g. 139 previsiones) shown as negative within their
  section. Accounts with zero balance omitted.
- `resultado_gestion`: Σ ingresos (500) − Σ gastos (400) from 1 January of the `fecha_corte` year up to `fecha_corte`,
  shown as an extra PATRIMONIO line "Resultado de la gestión" (it is not yet closed into 350 — no closing entries
  in scope).
- Totals: `total_activo`, `total_pasivo`, `total_patrimonio` (incl. resultado), `cuadra` = activo == pasivo +
  patrimonio. Response includes `tipo_cambio {moneda, fecha, valor}` used.

### Estado de Resultados — `GET /contabilidad/estado-resultados?desde&hasta&moneda=CONSOLIDADO|BOB|USD`
- Period movements (not cumulative); `desde`/`hasta` within the same year (else 422). Conversion as above using the
  rate at `hasta` (simplification; document it).
- Lines in this order, each with its component groups and amount, then the running subtotal:
  1. Ingresos financieros (510) − Gastos financieros (410) = **Resultado financiero bruto**
  2. + Otros ingresos operativos (540) − Otros gastos operativos (440) = **Resultado de operación bruto**
  3. + Recuperaciones de activos financieros (530) − Cargos por incobrabilidad y desvalorización (430)
     = **Resultado de operación después de incobrables**
  4. − Gastos de administración (450) = **Resultado de operación neto**
  5. + Abonos por diferencia de cambio (520) − Cargos por diferencia de cambio (420)
     = **Resultado después de ajuste por diferencia de cambio y mantenimiento de valor**
  6. + Ingresos extraordinarios (570) − Gastos extraordinarios (470) = **Resultado neto del ejercicio antes de
     ajustes de gestiones anteriores**
  7. + Ingresos de gestiones anteriores (580) − Gastos de gestiones anteriores (480)
     = **Resultado antes de impuestos y ajuste contable por efecto de la inflación**
  8. + Abonos por ajuste por inflación (590) − Cargos por ajuste por inflación (490)
     = **Resultado antes de impuestos**
  9. − Impuesto sobre las utilidades de las empresas (460) = **Resultado neto de la gestión**
- Each group row lists its nivel-3 accounts with amounts (expandable in the UI).
- Invariant tested: Resultado neto de la gestión (period = 1 Jan..corte) == `resultado_gestion` of the Balance General.

### CSV export
- `/balance-general/export`, `/estado-resultados/export` (same params), UTF-8 BOM, filename with date/range.

## Tasks
- [x] B1 (Codex): migration 026 + tipo_cambio model/schemas/API + tests.
- [x] B2 (Codex): Balance General (consolidated/per currency, regularizing accounts, resultado de gestión, cuadra) + tests.
- [x] B3 (Codex): Estado de Resultados (subtotals, invariant with Balance General) + CSV exports + tests.
- [ ] F1 (Antigravity): contador pages "Tipos de cambio" (list/register/correct), "Balance General" (cut-off date,
      currency mode, level, sections with totals, cuadra badge, rate used), "Estado de Resultados" (range, currency
      mode, subtotal ladder, expandable groups), CSV and print layout.
- [x] V1 (Orchestrator): suite, DB intact (incl. bitacora), E2E, build/lint/no-undef, commits, RDD.

## Acceptance criteria
- With a registered USD rate, the consolidated Balance General over the demo data `cuadra = true`; without a rate →
  clear 422. Estado de Resultados net result equals the Balance General `resultado_gestion` for the same year-to-date.

## Checks
- Backend strict TDD `.venv/bin/python -m pytest -q` (baseline 347 passed / 5 pre-existing failures / 1 skipped);
  own-cooperative fixtures; report bitacora counts before/after, never delete rows.
- Frontend `pnpm build`, `pnpm lint` (8 pre-existing), `npx oxlint -D no-undef` on changed files (browser globals only).
  Every "refresh/apply" button must force a refetch even when filters are unchanged (W31 bug).

## Route / trigger evidence
- Backend delegated direct → Codex (w2:p1), branch `feat/estados-financieros` from `feat/libros-contables`.
- Frontend delegated direct → Antigravity (w2:p5), branch `feat/estados-financieros` from `feat/libros-contables`.

## Progress
- 2026-09-28: exchange-rate valuation rule verified (source above); contract written; branches created.
- 2026-09-28: B1–B3 implemented on `feat/estados-financieros`: migration `026_sprint11_tipo_cambio.sql`, tenant-scoped exchange-rate API with audit, Balance General/Estado de Resultados endpoints and CSV exports. Consolidated amounts combine analytic leaves at level 3 before HALF_UP rounding per displayed account line.
- TDD evidence: before implementation, `.venv/bin/python -m pytest -q tests/test_comprobantes_api.py -k 'exchange_rate or balance_general_and_income_statement' --tb=short` → 3 failed because the requested endpoints returned 404. After implementation and follow-up red/green rounding test, `.venv/bin/python -m pytest -q tests/test_comprobantes_api.py --tb=short` → 36 passed.
- Migration evidence: `026_sprint11_tipo_cambio.sql` applied twice to local `coopDB` (`localhost:5433/cooperativa_db`); both executions printed `ok`.
- Initial full suite after B1–B3: `.venv/bin/python -m pytest -q --tb=short` → 352 passed, 5 failed, 1 skipped. The five failures remain the existing `test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` and four `tests/test_savings.py` failures (same baseline failure set: 347 passed, 5 failed, 1 skipped).
- Bitacora: 7615 rows before initial tests; 7678 before the final full suite; 7741 immediately after. Test-owned coop fixtures and their records were cleaned; no shared bitacora rows were deleted. No disposable `Voucher test` cooperatives remain.
- Parent spot-check: `.venv/bin/python -m pytest -q tests/test_comprobantes_api.py -k test_balance_general_and_income_statement_reconcile_with_usd_conversion` → 1 passed, 35 deselected; bitacora remained 7741, cooperative count 1, and `tipo_cambio` rows 0 after fixture cleanup.
- Follow-up 2026-09-28: extracted `nature_balance_delta_sql` into `app/api/v1/endpoints/accounting_sql.py` and reused it in W31 libro mayor, W31 balance de comprobación, and W32 statement aggregation. Added `tests/test_accounting_sql.py`; TDD RED was `ModuleNotFoundError` before the shared module existed, GREEN was included in the 37-pass focused run.
- Follow-up verification: `.venv/bin/python -m pytest -q tests/test_accounting_sql.py tests/test_comprobantes_api.py --tb=short` → 37 passed; `.venv/bin/python -m pytest -q --tb=short` → 353 passed, 5 failed, 1 skipped (same five baseline failures). Existing per-level-3 HALF_UP conversion/rounding behavior was not changed.
- Follow-up DB integrity: bitacora 7741 immediately before the final full suite and 7804 immediately after; no shared bitacora rows deleted. Cooperative count stayed 1 and disposable `Voucher test` cooperatives/rates remained 0 after fixtures.
- Migration 026 was unchanged in this follow-up; prior successful double application remains valid.
- Parent post-refactor spot-check: `.venv/bin/python -m pytest -q tests/test_accounting_sql.py tests/test_comprobantes_api.py -k 'nature or balance_general_and_income_statement_reconcile_with_usd_conversion' --tb=short` → 2 passed, 35 deselected; bitacora stayed 7804, cooperative count 1, and `tipo_cambio` count 0.
- 2026-09-28 (orchestrator verification): migration 026 replayed (exit 0). Full suite `353 passed, 5 failed, 1 skipped` (same 5 pre-existing). DB identical except the known legacy bitacora leak: 252 suite rows (4 runs × 63) removed by the orchestrator, 3 real E2E logins kept.
- E2E over HTTP (contador@test.com) with a temporary test rate (USD 6.86 at 2026-09-01, source "PRUEBA E2E", deleted afterwards — the cooperative must register the real current BCB rate): without rate → 422 asking to register it; BOB rate → 422; future date → 422; duplicate date → 409; cajero → 403. Balance General consolidated at 2026-09-28: activo 151195.45 = BOB 150509.45 + USD 100.00×6.86; pasivo 150900.50 = 150213.54 + 100.14×6.86; patrimonio 294.95 = resultado_gestion; `cuadra = true` (also per currency BOB and USD). Estado de Resultados 2026-01-01..2026-09-28: resultado neto 294.95 = Balance General resultado_gestion; range across years → 422.
- Data note (not a statement bug): cartera vigente shows -992.63 because the demo data has a legacy payment of 2000.00 capital on 131.05 without a matching disbursement voucher (older disbursements were never posted).
- Frontend: first version assumed response shapes different from the backend (`secciones.activo`, `resultado_neto`, numeric `subtotal`); correction sent to Antigravity with captured real samples.
