# Feature: informes-asfi (CU-W34 Generación de Informes Normativos ASFI)

## Objective
Generate the regulatory reports that can be built faithfully from the data we have, following the CURRENT ASFI
norms (verified by the orchestrator against the primary PDF), keep a record of each generated report, and state
explicitly which regulatory reports are out of scope and why.

## Verified sources (local copies in /home/yimy/proyectos/si2/investigacion/)
- RNSF Libro 3°, Título II, Capítulo IV "Reglamento para la Evaluación y Calificación de la Cartera de Créditos"
  — https://servdmzw.asfi.gob.bo/circular/Textos/L03T02.pdf (`L03T02.pdf` / `L03T02.txt`).
  Sección 2 current up to Modificación 24 (Circular ASFI/877/25, 06/2025); Sección 3 (previsiones) up to
  Modificación 28 (Circular ASFI/954/26, 06/2026).
- Ley N° 393 de Servicios Financieros, Arts. 415–416 (CAP ≥ 10 %, capital regulatorio) — researcher read the text.
- DO NOT use `asfi_T05C01A01.txt` (2012 version, outdated: e.g. agricultural microcredit thresholds changed).

## Verified rules to implement
### Calificación por días de mora (Sección 2)
| Categoría | Vivienda (Art. 7) | Microcrédito (Art. 8.1) | Microcrédito agropecuario (Art. 8.2) |
|---|---|---|---|
| A | al día o ≤ 30 | al día o ≤ 5 | al día o ≤ 40 |
| B | 31–90 | 6–30 | 41–60 |
| C | 91–180 | 31–55 | 61–105 |
| D | 181–270 | 56–75 | 106–155 |
| E | 271–360 | 76–90 | 156–180 |
| F | > 360 | > 90 | > 180 |
- Consumo: Art. 8 regulates "créditos de consumo y microcréditos" together and only publishes the microcrédito
  table → INTERPRETATION (document it in the response `criterio`): consumo uses the Art. 8.1 table.
- Empresarial / PYME with empresarial criteria are rated qualitatively (Art. 5) → out of scope; our products are
  consumo, vivienda and microcrédito only.
- Días de mora = days since the oldest unpaid installment's due date at the cut-off (reuse the CU-W28/W33 arrears
  helper with `dias_gracia_mora=0` for W34 regulatory classification only; never alter W28/W33's operational grace).

### Previsión específica % (Sección 3, Art. 1) — credits after 17/12/2010 (all of ours)
| Cat | MN/MNUFV Micro–PYME productivo | MN no productivo | MN Vivienda (1) | MN Vivienda (2) | MN Consumo | ME Micro–PYME directo | ME Vivienda (1) | ME Vivienda (2) | ME Consumo |
|---|---|---|---|---|---|---|---|---|---|
| A | 0% | 0.25% | 0.25% | 3% | 3% | 2.5% | 2.5% | 7% | 7% |
| B | 2.5% | 5% | 5% | 6.5% | 6.5% | 5% | 5% | 12% | 12% |
| C | 20% | 20% | 20% | 20% | 20% | 20% | 20% | 20% | 20% |
| D | 50% | 50% | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| E | 80% | 80% | 80% | 80% | 80% | 80% | 80% | 80% | 80% |
| F | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
(1) hipotecario de vivienda / de interés social; (2) vivienda sin garantía hipotecaria. (Contingent columns omitted:
we have no contingent credits.) Keep the table as data (a versioned constant with the circular reference), not
scattered literals.
- Garantía hipotecaria en primer grado (Art. 1 num. 3): `Previsión = R × (P − 0.50 × M)`, P = capital,
  M = min(P, valor comercial del inmueble − 15 %). Apply only with a VERIFICADA HIPOTECARIA guarantee (CU-W22).
- Previsión cíclica (Art. 8), additional, only for category A: MN/MNUFV vivienda 1.05 %, consumo 1.45 %,
  microcrédito 1.10 %; ME/MNMV vivienda 1.80 %, consumo 2.60 %, microcrédito 1.90 %. Report it separately (the
  constitution schedule of Art. 9 is out of scope — report the required amount only).

## Design
### Migration `027_sprint11_informes_asfi.sql` (idempotent)
- `producto_credito.tipo_credito_asfi VARCHAR(30) CHECK IN ('MICROCREDITO','MICROCREDITO_AGROPECUARIO','CONSUMO',
  'VIVIENDA_HIPOTECARIA','VIVIENDA_SIN_GARANTIA')` and `sector_productivo BOOLEAN DEFAULT false`; backfill existing
  products by code prefix (CONS-* → CONSUMO; VIV-* → VIVIENDA_HIPOTECARIA; MICRO-* → MICROCREDITO,
  sector_productivo true); expose/edit them in the CU-W20 product API (admin) — validation 422 on unknown values.
- Extend `reporte` (exists in bd.sql, 1 row): nullable `cooperativa_id` FK (backfill only when the owner has a
  cooperative; NULL means a platform/legacy report and remains tenant-invisible),
  `periodo DATE` (cut-off), `estado VARCHAR(12) DEFAULT 'GENERADO'`, `contenido JSONB` (full snapshot),
  `resumen JSONB`, `hash_contenido VARCHAR(64)` (sha256 of the canonical JSON). Keep `tipo`, `formato`, `parametros`,
  `fecha_generacion`, `usuario_id`.

### API (prefix `/api/v1/informes-asfi`; roles CONTADOR, ADMINISTRADOR; tenant-scoped; money strings)
1. `GET /calificacion-cartera?fecha_corte` (preview, not stored) → per credit: `numero_credito, socio{ci,nombre},
   producto, tipo_credito_asfi, sector_productivo, moneda, saldo_capital, dias_mora, categoria, porcentaje,
   base_prevision (P or P−0.5M), prevision_especifica, prevision_ciclica, garantia_hipotecaria_aplicada`;
   `por_categoria` totals; `total_prevision_especifica`, `total_prevision_ciclica`; `prevision_contable` (balance of
   139 by currency, from CU-W32 logic) and `diferencia` (requerida − contable, per currency); `criterio` text with
   the norm references and the consumo interpretation; `advertencias` (credits without currency — reuse W33 —
   or product without tipo_credito_asfi → excluded and listed).
2. `GET /estados-financieros?fecha_corte` (preview) → the monthly package: Balance General + Estado de Resultados
   (1 Jan..corte) from CU-W32 in consolidated BOB, grouped by MCEF code, with the rate used. If the rate is missing
   → 422 with the W32 message.
3. `GET /cartera-deudores?fecha_corte` (preview) → one row per debtor-credit: CI, nombre, número de crédito,
   tipo_credito_asfi, moneda, monto desembolsado, saldo, fecha desembolso, fecha vencimiento final, días mora,
   categoría, previsión. Label: "Base para reporte a la CIC — formato interno; el layout oficial SCIP/CIC no está
   publicado en fuente verificable".
4. `POST /generar` `{tipo: CALIFICACION_CARTERA|ESTADOS_FINANCIEROS|CARTERA_DEUDORES, fecha_corte}` → computes the
   same payload, stores a `reporte` row (contenido, resumen, hash), `registrar_accion`; 201 with id. Future cut-off
   422. Re-generating the same tipo+periodo creates a new version (keep history; mark previous `estado =
   'REEMPLAZADO'`).
5. `GET /reportes?tipo&desde&hasta` (history), `GET /reportes/{id}` (stored snapshot, verifies hash →
   `integridad_ok`), `GET /reportes/{id}/export` → CSV (UTF-8 BOM) of the stored snapshot.
6. `GET /catalogo` → list of regulatory reports with `implementado: bool` and `motivo` for the ones out of scope
   (CAP ratio: risk-weighting factors not verified; encaje legal: base and current rates not verified in primary
   text; límites de concentración: thresholds not verified; CIC official layout not published; UIF: covered by
   CU-W14 declarations). Include the source URL for each.

## Tasks
- [x] B1 (Codex): migration 027 + product classification fields in the product API + tests.
- [x] B2 (Codex): calificación de cartera y previsiones (tables as versioned data, mortgage formula, cíclica,
      comparison with 139) + tests with hand-computed cases per product type/currency/category boundary.
- [ ] B3 (Codex): estados financieros package, cartera-deudores, generation/history/snapshot integrity/export,
      catálogo + tests. Implementation and functional tests are present; strict RED-before-implementation evidence
      is incomplete for some snapshot behaviors, so this checkbox remains open.
- [x] F1 (Antigravity): "Informes ASFI" page (contador/admin): catálogo with implemented/out-of-scope status and
      sources; previews of the three reports with fecha de corte; "Generar" and history with integrity badge and
      CSV; calificación view with category distribution and requerida vs contable; product form gets tipo ASFI.
- [x] V1 (Orchestrator): suite, DB intact (incl. bitacora), migration replay, E2E, build/lint/no-undef/test,
      commits, RDD per slice.

## Checks
- Backend strict TDD `.venv/bin/python -m pytest -q` (baseline 366 passed / 5 pre-existing failures / 1 skipped).
  Own-cooperative fixtures; report bitacora counts, never delete rows. Boundary tests for every category edge
  (e.g. micro 5→A, 6→B, 90→E, 91→F; agro 40→A, 41→B; vivienda 30→A, 31→B, 360→E, 361→F).
- Frontend `pnpm build`, `pnpm lint` (8 pre-existing), `npx oxlint -D no-undef`, `pnpm test`.
- Keep commits < ~800 lines with their own tests; corrections stay inside already-changed files.

## Route / trigger evidence
- Backend delegated direct → Codex (w2:p1), branch `feat/informes-asfi` from `feat/reportes-gerenciales`.
- Frontend delegated direct → Antigravity (w2:p5), branch `feat/informes-asfi` from `feat/reportes-gerenciales`.

## Progress
- 2026-09-28: research (subagent) + orchestrator verification against the current RNSF PDF (the subagent's first
  source was the outdated 2012 annex; agricultural microcredit thresholds and vivienda-sin-garantía percentages
  differ in the current text); contract written; branches created.
- 2026-09-28: confirmed legacy `reporte.id=1` belongs to SUPERADMIN user 5 with no cooperative. Decision: migration 027
  leaves `reporte.cooperativa_id` nullable and preserves this row with NULL (platform/legacy); no arbitrary tenant
  assignment or deletion. All report history/detail/export endpoints must filter by the authenticated tenant, so this
  row returns 404 by id and never appears in a cooperative's history. Add a regression test (RED before implementation).
- 2026-09-28: B1 implemented. Focused TDD: product classification test RED (`2 failed, 3 passed`), then GREEN
  (`5 passed`). Migration 027 applied twice via psycopg2 against local `coopDB`; legacy report id=1 remains
  `(usuario_id=5, cooperativa_id=NULL)`. Added product ASFI classification fields, allowlist validation, API
  serialization/update, tenant FK and snapshot columns. Focused test bitacora count: 7625 → 7630; fixture cleanup
  no longer deletes audit rows and retains users referenced by bitacora. `psql` was unavailable; Python psycopg2
  successfully executed the migration twice.
- 2026-09-28: B2 implemented. Added a versioned ASFI category/previsión schedule, category and currency functions,
  mortgage reserve base (`P - 0.50 * min(P, 0.85 * commercial value)`), cyclic reserve, and W32 rolled account-139
  comparison. `tests/test_informes_asfi.py`: `106 passed`; includes every category boundary, all specific reserve
  rates across types/currencies, mortgage arithmetic, cyclic rates, and account-139 currency extraction.
- 2026-09-28: B3 implemented. Added W32 financial package, internal debtor-CIC base, tenant-scoped generation and
  replacement history, canonical JSON SHA-256 integrity, CSV UTF-8 BOM export, and catalog/out-of-scope reasons.
  TDD RED observed for route registration and legacy id=1 tenant visibility before route registration; final W34
  integration tests include history, replacement, hash tampering, BOM export, future cutoff, and catalog source.
  However, some snapshot behavior tests were added after their implementation, so B3 strict TDD is incomplete and
  remains unchecked pending a test-first completion. Legacy report id=1 remains visible to no cooperative:
  `(usuario_id=5, cooperativa_id=NULL)`.
- 2026-09-28: full suite ran on disposable local clone `cooperativa_db_w34_verify_20260928`, never against source:
  `473 passed, 5 failed, 1 skipped`. Observed failures are one `test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` and four `test_savings.py` cases
  (`test_registrar_socio_y_abrir_cuenta`, `test_emitir_y_listar_certificado_aportacion`,
  `test_deposito_retiro_y_saldo_insuficiente`, `test_tenant_no_puede_abrir_cuenta_para_socio_ajeno`); none
  touches W34, but the prior baseline supplied only aggregate counts so same-name identity is unverified. Clone
  bitacora/cooperativa counts were `7635/4 → 7753/28`; clone dropped after the run. Source counts stayed at
  `bitacora=7635`, `cooperativas=4`; source report id=1 remained `(5,NULL)`.
- 2026-09-28: bounded W34 correction completed test-first. Before RED, source coopDB had `cooperativas=1`
  (id 1 only), `usuarios=20`, `bitacora=7631`, no `@test.invalid` users. Test fixture cleanup was corrected to
  preserve all bitacora rows, and the `/generar` integration test's audit writer is monkeypatched to a no-op.
  RED was observed: new mora assertion failed because internal 3-day grace hid a 6-day overdue microcredit;
  all three typed CSV cases failed because export was JSON-in-one-cell. Implementation now passes `grace=0`
  through the existing W28/W33 helper for both previews and their shared snapshot payload path, with the response
  criterion retaining the regulatory day-count explanation without mentioning grace. Exports are typed rows (per-credit plus category totals for
  calificación, one debtor-credit row, and financial section/code/name/amount rows); BOM and filename are retained.
  Focused RED-to-GREEN: `4 failed` then `4 passed` (`106 deselected`). Required real-coopDB run:
  `.venv/bin/python -m pytest -q tests/test_informes_asfi.py` → `110 passed, 2 warnings`; after cleanup counts
  remain `cooperativas=1`, `usuarios=20`, `bitacora=7631`; only cooperative id 1 (`Cooperativa de Prueba SI2`),
  and no `@test.invalid` users remain. B3 remains unchecked: this closes the new
  correction's test-first gap but does not resolve the historical B3 strict-TDD gap across all snapshot behaviors.
- 2026-09-28: follow-up contract-gap correction also completed test-first. RED: criterion contained “gracia” and
  calificación/debtor CSV omitted required payload columns. GREEN: expanded exact headers and value assertions;
  criterion is grace-free, while the technical zero-grace rule remains in this ODD document. Focused tests:
  `3 failed, 1 passed` then `4 passed`; full module run: `110 passed, 2 warnings`. Audited test source confirms
  no `Bitacora` deletion and generation audit is monkeypatched. coopDB before/after remained
  `(cooperativas=1, usuarios=20, bitacora=7631)`, cooperative `(id=1, Cooperativa de Prueba SI2)`, no
  `@test.invalid` users.
- 2026-09-28 (orchestrator verification): migration 027 replayed (exit 0); legacy reporte id=1 (SUPERADMIN, no cooperative) kept with cooperativa_id NULL by decision and returns 404 to cooperatives. Full suite `477 passed, 5 failed, 1 skipped` (same 5 pre-existing; many new parametrized boundary tests). DB identical except the known legacy bitacora leak (removed). A Codex run had leaked 3 "Product Test" cooperatives (cleanup did not delete bitacora before users/cooperatives); the orchestrator removed them and Codex fixed the cleanup in the touched tests.
- E2E (contador): cajero 403; catálogo 3 implemented / 5 out of scope with reasons and source URLs; CRE-000002 consumo BOB cat A: específica 3 % × 1007.37 = 30.22, cíclica 1.45 % = 14.61, requerida 44.83 vs contable (139) 0.00 → diferencia 44.83; legacy CRE-000001 (202 days overdue, no currency/product type) excluded with advertencia; generation versions (previous → REEMPLAZADO), integrity check true, future cut-off 422. E2E-generated reports removed afterwards.
- Corrections after E2E: regulatory days of arrears no longer apply the product's internal grace days (contract error by the orchestrator; W28/W33 untouched); snapshot CSV export is now tabular per report type (it was one JSON column). Frontend: adapted to the real response shapes (captured samples) and the catalog no longer falls back silently to a hardcoded list (orchestrator edit).
- TDD note (Codex, honest): part of B3's snapshot behaviors had tests written after the implementation, so strict RED→GREEN is not proven for all of B3.
