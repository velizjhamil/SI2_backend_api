# Feature: mobile-socio (CU-M3, CU-M5, CU-M6, CU-M13)

## Objective
Socio self-service for the Flutter app: account statements (M3), mobile payment of credit installments (M5),
credit and DPF payment schedules (M6), and pre-approved re-credit offers (M13).

## Problem / Why
Staff endpoints already implement the business rules (CU-W27 payments, CU-W23p2 offers, CU-W18 DPF schedule),
but they are protected by staff roles. The socio can only list their own accounts and certificates.

## Scope
- Backend (Codex, branch `feat/mobile-socio` from `feat/intereses-dpf`): new socio-scoped endpoints under
  `/api/v1/socio/...` using `get_current_socio` (app/api/v1/deps.py). Reuse existing services; no duplicated
  business rules. No new migration unless strictly needed (ask the orchestrator first).
- Mobile (Copilot, SI2_mobile_app): services + screens consuming the endpoints below.

## Constraints
- Ownership is validated in the WHERE clause: resources of another socio or another cooperative → 404
  (never 403, never leaking existence).
- Money serialized as strings with 2 decimals, Decimal ROUND_HALF_UP.
- Tests must use their own cooperative/socio fixtures and leave the real DB unchanged.
- Strict TDD: RED → GREEN → REFACTOR. Runner: `.venv/bin/python -m pytest -q`.
- Mobile: must NOT modify AndroidManifest.xml, android/build.gradle.kts, ios/*, flutter_export_environment.sh,
  lib/services/mock_auth_service.dart (user changes under review). No new pub dependencies. No git write commands.
  Read `AuthService.baseUrl` and the stored token the same way `cuentas_service.dart` does.

## API contract (all require a socio JWT; non-socio → 403 "Operación disponible solo para socios")

### M3 — Extracto de cuenta
`GET /socio/cuentas/{cuenta_id}/extracto?desde=YYYY-MM-DD&hasta=YYYY-MM-DD&limit=50&offset=0`
- Defaults: `hasta` = today, `desde` = hasta − 30 days. `desde > hasta` → 422. Range > 366 days → 422.
- Response:
```json
{"cuenta": {"id": 1, "numero_cuenta": "...", "moneda": "BOB", "saldo_disponible": "0.00"},
 "desde": "...", "hasta": "...",
 "saldo_inicial": "0.00", "total_creditos": "0.00", "total_debitos": "0.00", "saldo_final": "0.00",
 "total": 12,
 "movimientos": [{"transaccion_id": 1, "fecha": "ISO datetime", "tipo": "DEPOSITO", "descripcion": "...",
                  "canal": "VENTANILLA", "monto": "10.00", "sentido": "CREDITO", "saldo_resultante": "10.00"}]}
```
- Movements ordered by date desc; `saldo_resultante` is the running balance after that movement;
  `saldo_final - saldo_inicial == total_creditos - total_debitos` over the full range (not only the page).

### M5 — Pago móvil de cuotas
- `GET /socio/creditos` → own credits `[{"id","numero_credito","producto","moneda","monto_desembolsado","saldo_pendiente","estado","proxima_cuota": {"numero","fecha_vencimiento","monto_total"} | null}]` (VIGENTE and EN_MORA first).
- `GET /socio/creditos/{credito_id}/deuda` → same shape as staff `GET /creditos/creditos/{id}/deuda`.
- `POST /socio/creditos/{credito_id}/pagos` body `{"cuenta_ahorro_id": 1}` → 201, same shape as staff `PagoOut`.
  Reuses the CU-W27 payment service with modalidad CUENTA and canal `MOVIL`; the account must belong to the
  socio (else 404) and have the credit's currency and enough balance (else 422 with the existing messages).
  Pays the next due installment (including mora), exactly like the staff flow.
- `GET /socio/creditos/{credito_id}/pagos` → own payment history.

### M6 — Cronogramas
- `GET /socio/creditos/{credito_id}/cronograma` → `{"credito": {...}, "cuotas": [{"numero","fecha_vencimiento","capital","interes","seguro","monto_total","saldo_capital","estado","fecha_pago"}]}`.
- `GET /socio/dpf` → own DPFs `[{"id","numero_certificado","moneda","monto","tasa_interes","plazo_dias","fecha_apertura","fecha_vencimiento","estado","frecuencia_pago"}]`.
- `GET /socio/dpf/{dpf_id}` → DPF detail plus `cronograma` `[{"numero","fecha_pago","interes_bruto","retencion_rciva","interes_neto","estado","fecha_pago_real"}]`.

### M13 — Ofertas de re-crédito
- `GET /socio/recreditos` → own VIGENTE (non-expired) offers with the same shape as staff `OfertaOut`.
- `POST /socio/recreditos/{oferta_id}/aceptar` body `{"plazo_meses": 12}` (optional, defaults to offer) → 201,
  reuses the CU-W23p2 accept service (creates the solicitud); `usuario` recorded is the socio's user.
- `POST /socio/recreditos/{oferta_id}/descartar` → reuses the CU-W23p2 discard service.
- Offers of another socio → 404; expired/non-VIGENTE → 422 with the existing messages.

## Tasks
- [x] T1 (Codex, delegated): M3 extracto endpoint + tests.
- [x] T2 (Codex, delegated): M5 credits/debt/pay/history + tests (canal MOVIL, ownership, currency, balance).
- [x] T3 (Codex, delegated): M6 schedules (credit + DPF) + tests.
- [x] T4 (Codex, delegated): M13 offers list/accept/discard + tests.
- [x] T5 (Copilot, delegated): Flutter services + screens: account statement, my credits with pay flow,
      schedules (credit/DPF), re-credit offers; navigation entries; widget/service tests with mocked HTTP.
- [x] T6 (Orchestrator): pytest full suite, DB-intact check, E2E over HTTP with socio@test.com, flutter analyze/test, commits.

## Acceptance criteria
- All endpoints enforce socio ownership (404 for foreign resources) and 403 for staff users.
- A socio can pay an installment from the app and the staff view shows the payment with canal MOVIL.
- Full backend suite green; DB unchanged after the suite; `flutter analyze` and `flutter test` green.

## Route / trigger evidence
- Backend: delegated direct (writer trigger: 2+ non-trivial files) → Codex (herdr w2:p1).
- Mobile: delegated direct (writer trigger) → Copilot (herdr w2:p6), per user instruction.
- TDD: strict (session config), runner backend `.venv/bin/python -m pytest -q`, mobile `/home/yimy/flutter/bin/flutter test`.

## Progress
- 2026-09-27: branch `feat/mobile-socio` created; contract written.
- [x] T1 (Codex, delegated): M3 extracto endpoint + tests.
  - Route: delegated direct; writer applied isolated M3 change in `socio.py` and router wiring.
  - TDD evidence: `tests/test_mobile_socio.py` first failed with `404 Not Found` for missing endpoint; implementation then passed both focused tests (`2 passed`).
  - Scope: statement defaults, date-range rejection, socio-owned account query, full-range totals, running movement balances, descending page output, money string formatting.
- T2 completed: own credit list, debt, payment, and payment history. Payment uses shared CU-W27 operation with a server-set mobile context for socio WHERE ownership and MOVIL channel; mobile account ownership failures return 404, currency/active/balance failures 422. Staff WEB and role behavior retained.
- T3 completed: credit schedule, DPF list/detail with socio_id ownership. Credit and DPF records use disposable fixture rows; DPF detail reuses `_certificado_out`.
- T4 completed: own VIGENTE/non-expired offer list, accept with optional term, and discard. Shared accept/discard core functions are called by existing staff and mobile routes; socio's user ID is recorded for generated requests.
- TDD: initial missing-route REDs for M3/M5 list/debt/payment/M6 DPF list/M13 offer list+mutations; focused functional RED for populated DPF detail; implementations GREEN. Final scoped command `.venv/bin/python -m pytest -q tests/test_mobile_socio.py tests/test_cobro_cuotas.py tests/test_recredito_ia.py tests/test_dpf.py`: **62 passed, 2 warnings**. `git diff --check` clean. Disposable test tenant audit: 0 cooperatives with the unique `Mobile %` fixture prefix remain after cleanup.
- 2026-09-27 correction: Added a regression for an account with an unbacked opening balance of 500.00 plus a 100.00 deposit today. RED observed (`saldo_inicial` was `0.00`, expected `500.00`); `extracto_cuenta` now anchors the range close to current `saldo_disponible` minus post-range net movements, then derives the opening balance from range net. GREEN observed. `.venv/bin/python -m pytest -q tests/test_mobile_socio.py`: **18 passed, 2 warnings**; full `.venv/bin/python -m pytest -q`: **288 passed, 5 failed, 2 warnings** (same baseline failures: 4 outdated `tests/test_savings.py` payload expectations and 1 `tests/test_multi_tenant.py` authorization expectation). Post-run DB cleanup spot-check: **1 cooperative, 0 `@test.invalid` users, 0 `Mobile %` cooperatives**.
- 2026-09-27: No migration/dependency changes. `TablaAmortizacion` schema has no insurance column; credit schedule returns `seguro: "0.00"` because there is no persisted insurance value to serialize.

- 2026-09-27 (orchestrator verification): E2E over HTTP with socio@test.com / socio2@test.com: staff on /socio → 403; foreign credit/account/DPF → 404; socio paid installment 1 of CRE-000002 from own account (tx PAGO_CUOTA canal MOVIL, staff history shows it); schedule shows #1 PAGADA; extract range validation 422.
- Bug caught by E2E: extract rebuilt balances from zero, but balances not backed by transactions exist (seed/legacy flows) → saldo_final 989.37 vs real 3489.37. Codex fixed by anchoring on current balance (RED test with unbacked opening balance). Re-verified live: saldo_final == saldo_disponible (3489.37).
- Mobile bugs caught in review: Copilot's socio_service sent a literal masked `Authorization: ******` header (every call would 401; tests did not check headers) → fixed with RED test asserting `Bearer <token>`. Re-credit accept now asks for confirmation and both accept/discard reload the list.
- Final checks: backend full suite `5 failed, 288 passed` (same 5 pre-existing failures: test_multi_tenant tenant-list + 4 stale test_savings payloads); DB snapshot identical before/after suite; `git diff --check` clean. Mobile: `flutter test` 43 passed; `flutter analyze` 0 errors/warnings (57 pre-existing infos). User's uncommitted mobile files (manifest, gradle, ios, mock_auth_service baseUrl) not committed.
- E2E leaves demo data: solicitud 5429 / credit CRE-000002 (id 2607) for socio 3 with installment 1 paid.
