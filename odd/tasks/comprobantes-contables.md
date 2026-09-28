# Feature: comprobantes-contables (CU-W30 Gestión de Comprobantes y Asientos Contables)

## Objective
Double-entry accounting vouchers on top of the ASFI MCEF chart (CU-W29): manual vouchers written by the contador and
automatic vouchers generated from the operational transactions that already exist (savings, credits, DPF).
Base for W31 (libro diario/mayor) and W32 (estados financieros).

## Bolivian framework (what is verified vs. practice)
- Verified: account codes/names/nature come from the MCEF Título II already seeded (migration 024).
- General accounting practice applied (not an ASFI-specific citation): double entry (sum debe = sum haber),
  consecutively numbered vouchers by type (Ingreso / Egreso / Traspaso), vouchers are never deleted — they are
  voided by a reversal voucher that keeps the audit trail.

## Current state
- `comprobante_contable(id, tipo, glosa, es_automatico, fecha, transaccion_id)` — no cooperative, number, currency,
  state or user. 1 legacy row with 3 `detalle_asiento` rows (now pointing to 111.01, 131.05, 131.05).
- `detalle_asiento(id, debe, haber, comprobante_contable_id, plan_cuenta_id)`.
- `transaccion.tipo` values seen in the DB: APERTURA, DEPOSITO, RETIRO, TRANSFERENCIA_ENTRADA, TRANSFERENCIA_SALIDA,
  DESEMBOLSO_CREDITO, PAGO_CUOTA, APERTURA_DPF. The code may create more (DPF interest payment, DPF liquidation,
  certificates of contribution, cash movements) — Codex must enumerate every `transaccion.tipo` the code writes and
  either map it or report it as unmapped.

## Design
### Migration `025_sprint11_comprobantes.sql` (idempotent)
- `comprobante_contable` new columns: `cooperativa_id` (FK, backfill the legacy row from its transaction's
  cooperative or the only cooperative), `numero VARCHAR(20)`, `gestion INT`, `fecha_contable DATE`,
  `moneda_id` (FK moneda), `estado VARCHAR(10) CHECK IN ('REGISTRADO','ANULADO') DEFAULT 'REGISTRADO'`,
  `usuario_id` (FK usuario, NULL for automatic), `origen VARCHAR(12) CHECK IN ('MANUAL','AUTOMATICO')`,
  `comprobante_reversion_id` (FK self, the reversal that voided it), `revierte_a_id` (FK self),
  `motivo_anulacion TEXT`, `fecha_registro TIMESTAMP DEFAULT now()`. `tipo CHECK IN ('INGRESO','EGRESO','TRASPASO')`
  (normalize the legacy value).
- Unique `(cooperativa_id, tipo, gestion, numero)`; partial unique on `transaccion_id` where origen = 'AUTOMATICO'
  and it is not a reversal (idempotency: one automatic voucher per transaction).
- `detalle_asiento` new columns: `glosa TEXT NULL`, `orden INT`; CHECK `debe >= 0 AND haber >= 0 AND
  (debe = 0) <> (haber = 0)` (exactly one side per line) — fix the legacy rows if they violate it.
- New table `parametro_contable(id, cooperativa_id, clave VARCHAR(40), plan_cuenta_id, UNIQUE(cooperativa_id, clave))`
  holding the account mapping per cooperative. Defaults (seeded lazily per cooperative or by the migration for
  existing cooperatives):
  | clave | default account |
  |---|---|
  | CAJA | 111.01 Billetes y monedas |
  | AHORRO_VISTA | 212.01 Depósitos en caja de ahorros |
  | AHORRO_PROGRAMADO | 212.01 Depósitos en caja de ahorros |
  | CARTERA_VIGENTE | 131.05 Préstamos amortizables vigentes |
  | INTERES_CARTERA | 513.05 Intereses préstamos amortizables |
  | INTERES_PENAL | 515.03 Intereses penales cartera vencida |
  | DPF_30 … DPF_MAS_1080 | 213.01 … 213.08 by term (plazo_dias ranges exactly as the MCEF names) |
  | INTERES_DPF | 411.04 Intereses obligaciones con el público por depósitos a plazo fijo |
  | RETENCION_RCIVA | 242.03 Acreedores fiscales por retenciones a terceros |
  | CERTIFICADOS_APORTACION | 311.02 Certificados de aportaciones (Cooperativas de Ahorro y Crédito) |
  | COMISIONES | 541.99 Comisiones varias |
  If the cooperative has analytic accounts under a mapped subcuenta, the parameter must point to one of them
  (validation on update; generation fails with a clear per-transaction error otherwise).

### Numbering
- `numero` = `{I|E|T}-{gestion}-{000001}` correlative per (cooperativa, tipo, gestion), assigned inside the same
  DB transaction with a row lock (reuse the existing sequence helper `_siguiente_secuencia` pattern if suitable).
- Tipo rule for automatic vouchers: INGRESO if the CAJA account is debited, EGRESO if CAJA is credited, else TRASPASO.

### Posting rules (all amounts in the transaction's currency; one voucher per transaction)
- APERTURA / DEPOSITO in cash: D CAJA / H AHORRO_{tipo cuenta}.
- RETIRO in cash: D AHORRO / H CAJA.
- TRANSFERENCIA (SALIDA + ENTRADA pair): one TRASPASO voucher D AHORRO(origin) / H AHORRO(destination) — generate it
  from the SALIDA and mark the ENTRADA as covered (no duplicate).
- DESEMBOLSO_CREDITO: D CARTERA_VIGENTE / H AHORRO (to account) or H CAJA (cash).
- PAGO_CUOTA: D CAJA or AHORRO (total) / H CARTERA_VIGENTE (capital), H INTERES_CARTERA (interest),
  H INTERES_PENAL (mora), other charges → report if present. Use the payment row components, not recomputation.
- APERTURA_DPF: D CAJA or AHORRO / H DPF_{term}.
- DPF interest payment: D INTERES_DPF (gross) / H AHORRO (net) + H RETENCION_RCIVA (retention).
- DPF liquidation: D DPF_{term} (capital) (+ D INTERES_DPF for interest paid at liquidation) / H AHORRO or CAJA
  (net) + H RETENCION_RCIVA.
- Certificates of contribution: D CAJA or AHORRO / H CERTIFICADOS_APORTACION.
- Any unmapped type → skipped and reported, never guessed.

### API (prefix `/api/v1/contabilidad`, roles CONTADOR, ADMINISTRADOR; tenant-scoped, foreign → 404)
- `GET /comprobantes?desde&hasta&tipo&origen&estado&q&limit&offset` → `{total, items:[{id, numero, tipo, fecha_contable,
  glosa, origen, estado, moneda, total_debe, total_haber, transaccion_id}]}`.
- `GET /comprobantes/{id}` → header + lines `[{orden, cuenta:{id,codigo,nombre}, glosa, debe, haber}]` + totals +
  reversal links.
- `POST /comprobantes` (manual) `{tipo, fecha_contable, glosa, moneda_id, lineas:[{plan_cuenta_id, debe, haber, glosa?}]}`
  → 201. Validations (422 with specific messages): ≥ 2 lines; each line exactly one side > 0; Σdebe = Σhaber > 0
  (Decimal, 2 decimals); accounts must `acepta_movimientos`, be ACTIVA, visible to the tenant, and if the subcuenta has
  analytic accounts in this cooperative the analytic one must be used; `fecha_contable` not in the future; glosa ≥ 5.
- `POST /comprobantes/{id}/anular` `{motivo}` (≥ 5 chars) → creates the reversal voucher (lines swapped, same tipo,
  fecha_contable today, glosa "Anulación de {numero}: {motivo}"), marks the original ANULADO. Only REGISTRADO vouchers;
  automatic ones: 409 "Los comprobantes automáticos se revierten con la operación de origen" (keep them immutable).
- `POST /comprobantes/generar-automaticos` `{desde?, hasta?}` (default: all pending) → `{procesadas, generados,
  omitidas:[{transaccion_id, tipo, motivo}], por_tipo}`. Idempotent: re-running generates nothing new.
- `GET /parametros-contables` / `PUT /parametros-contables/{clave}` `{plan_cuenta_id}` (validated as above, logged).
- All writes use `registrar_accion` (modulo CONTABILIDAD).

## Tasks
- [x] B1 (Codex): migration 025 + models/schemas + parameter defaults.
- [x] B2 (Codex): manual vouchers (create, list, detail, void by reversal) + tests.
- [x] B3 (Codex): automatic generation for every mapped transaction type + idempotency + unmapped report + tests
      (balanced vouchers, correct accounts per type, currency, transfer pair not duplicated).
- [x] B4 (Codex): parametros-contables endpoints + tests.
- [x] F1 (Antigravity): contador pages: Comprobantes (list with filters, detail, manual create with balanced-lines
      editor and account picker limited to movement accounts, void with reason, "Generar automáticos" with result
      summary) and Parámetros contables.
- [x] V1 (Orchestrator): suite, DB intact, migration replay, E2E, build/lint, commits, RDD per slice.

## Acceptance criteria
- Running the automatic generation over the demo data yields only balanced vouchers, one per transaction (transfer
  pairs once), correct numbering per type, and a report of unmapped types; a second run generates 0.
- A manual unbalanced voucher is rejected; a balanced one gets the next `T-2026-xxxxxx` number; voiding creates the
  reversal and keeps both.

## Checks
- Backend strict TDD `.venv/bin/python -m pytest -q` (baseline 310 passed / 5 pre-existing failures); tests use their
  own cooperative; coopDB identical after the suite except for the migration. Keep commits reviewable: split the
  migration, the generation engine and the manual API into separate commits when handing over (the orchestrator
  commits; report a suggested split).
- Frontend `pnpm build`, `pnpm lint` (8 pre-existing warnings).

## Route / trigger evidence
- Backend delegated direct → Codex (w2:p1), branch `feat/comprobantes-contables` from `feat/plan-cuentas`.
- Frontend delegated direct → Antigravity (w2:p5), branch `feat/comprobantes-contables` from `feat/plan-cuentas`.

## Progress
- 2026-09-27: mapping accounts verified in the seeded MCEF catalog; branches created; contract written.
- 2026-09-27: B1 completed — migration `025_sprint11_comprobantes.sql` applied twice successfully to local coopDB; ORM models, parameter schemas, 18 per-cooperative MCEF defaults, and legacy chart-test fixtures updated. RED was observed first on the absent models/schemas; focused checks: 22 passed; full suite: 314 passed, 5 pre-existing failures, 1 skipped. Database had 1 cooperative, 1 legacy voucher, and 3 legacy lines both before and after; post-suite it contains 30 transactions and 18 tenant defaults. Automatic voucher generation was not run. No commit created (orchestrator owns commits).
- 2026-09-27: B2 completed — added tenant-scoped manual voucher list/detail/create/reversal endpoints, half-up cent rounding, account/analytic/date/balance validation, cooperative-row locked numbering, audit actions, and automatic-voucher immutability. RED observed with the route absent (10 focused cases failed); focused checks after implementation: 34 passed across voucher, model, and chart-account tests. Disposable test cooperatives, voucher rows, details, and audit rows were cleaned; post-check database remains at 1 cooperative, 1 legacy voucher, 3 lines, 18 parameter defaults, and 0 B2 audit rows. No full suite yet (deferred to B4); no commit created (orchestrator owns commits).
- 2026-09-27: B3 completed — added tenant-derived, SQL-only transaction selection and automatic source-specific posting with cross-link tenant consistency checks, cooperative locking/numbering, per-transaction unique idempotency, HALF_UP Decimal amounts, balanced details, currency retention, and audit records. Covered teller cash deposits/withdrawals, cash/account credit disbursement, transfers from reciprocal salida only, persisted payment capital/interest/mora/total/modality, cash/account DPF issuance, linked liquidation payout and renewal rollover, and gross/RCIVA/net DPF interest from the linked cronograma. Explicitly reports account-only APERTURA/DEPOSITO/RETIRO, duplicate/invalid components, unsupported types, incoming transfer counterparts, legacy APERTURA_DPF, and liquidation discount/prepaid-interest as omitted rather than inferring. Initial RED observed for the absent route (405); source regression RED probe showed the teller fixture produced 0 instead of expected 2, then GREEN after implementation. Focused checks: 39 passed across voucher API, model, and chart-account tests. All test transactions, vouchers, analytics, users, and cooperative rows were cleaned; DB post-check matches pre-B3 baseline: 1 cooperative, 1 legacy voucher, 3 lines, 18 parameter defaults, 0 generation audit rows, 0 disposable test cooperatives. No full suite yet (deferred to B4); no commit created (orchestrator owns commits).
- 2026-09-27: B4 completed — added tenant-scoped GET/PUT parameter APIs. GET lazily initializes/repairs 18 defaults and logs initialization; a configured default with tenant analytic descendants must use an active tenant analytic account, while a key with no analytic descendants accepts only its MCEF official account. Updates validate role, account visibility, active/postable state, default-account lineage, and audit each success. RED observed (both routes 404 before implementation); focused voucher/model/chart tests: 41 passed. Full suite: 333 passed, 5 failed, 1 skipped; all 5 failures are known baseline failures (one multi-tenant authorization expectation, four savings API tests with missing monto_apertura input/related assertion). `git diff --check` passed. No migration changed or replayed. Post-suite DB matches pre-B4 baseline: 1 cooperative, 1 legacy voucher, 3 lines, 18 parameter defaults, 0 generation audit rows, 0 parameter update/init audit rows, 0 disposable test cooperatives. No commit created (orchestrator owns commits).
- Runtime `transaccion.tipo` inventory and disposition: `APERTURA` — omitted when no counter-account/cash source is persisted; `DEPOSITO` — mapped when linked to cash control, DPF issuance, or DPF liquidation (account-only savings deposit omitted); `RETIRO` — mapped for teller cash withdrawals, cash credit disbursement, and linked account-funded DPF issue/renewal (account-only savings withdrawal omitted); `TRANSFERENCIA_SALIDA` — mapped once from the linked reciprocal pair; `TRANSFERENCIA_ENTRADA` — reported as covered by the linked SALIDA to prevent duplication; `PAGO_CUOTA` — mapped from persisted capital/interest/mora components and modality; `DESEMBOLSO_CREDITO` — mapped for account-funded disbursement (cash disbursement is written as RETIRO); `PAGO_INTERES_DPF` — mapped from cronograma gross/RCIVA/net. Unsupported or inconsistent component sets are omitted with per-row reasons. Legacy `APERTURA_DPF` exists in seed data but has no runtime writer and is omitted. DPF liquidations with prepaid-interest/discount components are omitted because available contract mappings do not support a safe split. `DEBITO` was found in a read query but has no runtime writer. `CERTIFICADO_APORTACION` is not a `transaccion.tipo` and its issuance has no transaction writer. Nothing is assigned an account by inference.
- Final verification evidence: migration 025 replay 1 and replay 2 each committed successfully. Final full suite output: `333 passed, 5 failed, 1 skipped, 2 warnings` (235.73s); failures match the stated pre-existing multi-tenant and four savings tests. Suite-wide legacy tests appended 189 `bitacora` rows on seeded cooperative 1 (not W30 rows); removed those test-generated rows to restore the observed pre-task `bitacora` count of 7,602. W30 fixture data was cleaned; automatic generation was invoked only by isolated test cooperatives, never the real cooperative. No suite rerun after cleanup.
- 2026-09-27 bounded correction: API voucher amounts are emitted as fixed two-decimal JSON strings for list/detail/create/reversal; request-body Pydantic `Value error, ` prefixes are stripped using the established CU-W28 validation pattern. Persisted legacy payment components confirm principal 2,000.00 + interest 302.08 + mora 0.00 = transaction 2,302.08; migration 025 now reclassifies only the matching named legacy voucher's 302.08 interest detail from 131.05 to 513.05, via transaction/payment identity and component amount (no fixed row IDs). Applied updated migration 025 twice successfully; verified resulting account 513.05. RED: three targeted API cases failed before implementation; GREEN: those three passed, focused voucher/model/chart tests 43 passed, and the strengthened list serialization case passed again. Full suite: 335 passed, 5 failed, 1 skipped (237.26s); failures were the same existing multi-tenant and savings cases. `bitacora` was 7,610 before any tests and 7,673 after the full suite (+63); no rows were deleted. Tests use disposable cooperatives; automatic generation was not run on the seeded cooperative. No git write command or gentle-ai review was run.
- 2026-09-28 (orchestrator verification): migration 025 replayed by the orchestrator (exit 0). Full suite `333 passed, 5 failed, 1 skipped` (same 5 pre-existing). DB snapshot identical except `bitacora`: legacy tests (socios/ahorros/login with seeded users) append 63 rows to cooperative 1 per suite run — pre-existing leak, not W30; the orchestrator removed exactly the suite-generated rows (count back to baseline). Follow-up: isolate those legacy tests.
- E2E over HTTP (contador@test.com): cajero 403; 1-line, unbalanced, both-sides, title account (111.00), future date → 422; balanced manual → I-2026-000002; void → reversal I-2026-000003 and second void 409; automatic generation over demo data: 29 processed, 14 generated, omitted with explicit reasons (legacy APERTURA_DPF, account openings without cash source, transfer entry covered by its exit, ambiguous withdrawals); second run 0 generated; automatic void 409; 0 unbalanced vouchers in DB; 0 duplicated automatic transactions; numbering I/E/T-2026 correlative.
- Bugs caught by E2E and fixed by Codex: money serialized as JSON numbers (now "0.00" strings); validation messages carried the "Value error," prefix (now clean); legacy voucher I-2026-000001 had the 302.08 interest on 131.05 — confirmed by the persisted payment components and re-pointed to 513.05 idempotently in migration 025.
- Frontend (Antigravity): Comprobantes page (filters, detail, manual editor with cents arithmetic, void, automatic generation summary) and Parámetros contables page; `pnpm build` OK, lint 8 pre-existing warnings, no new ones.
- 2026-09-28 (RDD): backend slice 1 (f25a1c5) correction — voucher schema validators had no tests; added characterization tests (GREEN on first run, no RED: existing code already correct) as ec99a19; approved. Backend slice 2 (96eeb41) approved (advisory: cross-tenant source guard, list filters, numbering exhaustion, unknown payment modality lack tests). Frontend 6f262ce: BLOCKER found — account picker search called an undefined `fetchCuentas` (typing crashed the picker; build and lint did not catch it); fixed in frontend 6f55c0d. The frontend review then stopped terminally with `captured_artifacts_unverifiable` (not retried per contract) — frontend W30 remains without a closed receipt.
