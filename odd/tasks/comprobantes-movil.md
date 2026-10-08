# Mobile Transaction Receipts (verifiable)

## Objective
Every successful money operation a member makes from the mobile app issues a tamper-evident receipt ("comprobante de transacción") that the member can view, share as a PDF, and that anyone can verify through a QR code.

## Problem and why
Mobile transfers and loan installment payments finish with no durable proof: the transfer screen just closes, and the payment shows a transient snackbar. For traceability, each operation needs a unique, immutable, verifiable receipt.

## Decisions (user-approved, 2026-10-08)
- Scope: mobile channel only. Teller (web) operations are out of scope.
- Operations: own-account transfer (`POST /ahorros/transferencias` when the caller is a SOCIO) and loan installment payment (`POST /socio/creditos/{id}/pagos`).
- Security model: unique per-cooperative sequential number, server-side HMAC-SHA256 signature over the canonical receipt fields, a public QR verification page with masked data, immutability, and audit.
- This is not an invoice (no tax authority integration) and is unrelated to the accounting vouchers in `app/api/v1/endpoints/comprobantes.py`.

## Data
Migration `migrations/031_comprobantes_movil.sql` (029 is used by the CU-W23 seed branch, 030 by backups):
- `comprobante_transaccion`: `id`, `cooperativa_id`, `numero` (unique per cooperative, e.g. `TRF-2026-000123`, `PAG-2026-000045`), `tipo` (`TRANSFERENCIA`|`PAGO_CUOTA`), `canal` (`MOVIL`), `socio_id`, `usuario_id`, `monto`, `moneda`, `cuenta_origen_id`, `cuenta_destino_id` (nullable), `credito_id` and `numero_cuota` (nullable), references to the underlying movement/payment rows, `glosa` (nullable), `emitido_en`, `codigo_verificacion` (unique, short human-readable code derived from the signature), `firma` (full HMAC hex).
- `comprobante_secuencia(cooperativa_id, tipo, anio, ultimo)`: gap-free counter incremented under a row lock (`INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`) inside the same transaction as the operation.
- Receipts are immutable: no update or delete paths; a DB trigger or a constraint-level guard is optional, but the API must expose none.

## Behavior
- The receipt is created in the same DB transaction as the transfer or payment. Operation fails → no receipt and no consumed number; receipt creation fails → the whole operation rolls back.
- Signature: `HMAC-SHA256(COMPROBANTE_SIGNING_KEY, canonical string of numero|tipo|cooperativa_id|socio_id|monto|moneda|cuenta_origen_id|cuenta_destino_id|credito_id|numero_cuota|emitido_en ISO UTC)`. `codigo_verificacion` = first 80 bits of the signature in Crockford base32, grouped `XXXX-XXXX-XXXX-XXXX`.
- Verification recomputes the HMAC from the stored fields; a mismatch reports the receipt as invalid (detects DB tampering).
- Each issuance writes a bitacora entry.
- Teller/admin callers of the transfer endpoint do not get a receipt (mobile scope only); the existing response must stay backward compatible.

## API contract
| Method | Path | Auth | Result |
|---|---|---|---|
| POST | `/ahorros/transferencias` (existing) | SOCIO JWT | existing body plus `comprobante` (receipt object) |
| POST | `/socio/creditos/{id}/pagos` (existing) | SOCIO JWT | existing body plus `comprobante` |
| GET | `/socio/comprobantes?tipo=&desde=&hasta=` | SOCIO JWT | own receipts, newest first |
| GET | `/socio/comprobantes/{id}` | SOCIO JWT | own receipt; 404 for others' receipts (no existence leak) |
| GET | `/socio/comprobantes/{id}/pdf` | SOCIO JWT | PDF with the receipt data, verification code, and QR |
| GET | `/verificacion/comprobantes/{codigo}` | public | minimal server-rendered HTML page: VALID/INVALID, number, type, date, amount, currency, cooperative name, masked accounts (`****4521`); `?formato=json` returns the same as JSON; unknown code → "not found" without detail |

Receipt object: `id, numero, tipo, canal, monto, moneda, cuenta_origen (masked), cuenta_destino (masked or null), credito_id, numero_cuota, glosa, emitido_en, codigo_verificacion, url_verificacion`.
History linkage: the existing extract items (`GET /socio/cuentas/{id}/extracto`) and payment items (`GET /socio/creditos/{id}/pagos`) gain a nullable `comprobante_id`.

## Configuration
`COMPROBANTE_SIGNING_KEY` (required in production; a dev default is acceptable only with a logged warning), `PUBLIC_API_URL` (public API base used to build `url_verificacion` and the QR; include `/api/v1`, for example `https://<render-service>.onrender.com/api/v1`).

## Libraries
PDF with the existing `fpdf2`; QR with `segno` (pure Python, writes PNG without Pillow).

## Tasks
- [ ] CM-01 — Migration, models, sequence, signing/verification service, issuance inside both operations (TDD). Route: delegated (Codex). **Implementation present; strict TDD evidence incomplete, so not accepted/checked.**
- [ ] CM-02 — Member receipt endpoints, PDF with QR, public verification page, `comprobante_id` in extract and payment lists (TDD). Route: delegated (Codex). **Implementation present; strict TDD evidence incomplete, so not accepted/checked.**
- [ ] CM-03 — Orchestrator review, full suite, commit, native review. Route: inline (Claude).
- [ ] CM-04 — Capture real payloads as mobile fixtures. Route: inline (Claude).
- [ ] CM-05 — Mobile: receipt screen after transfer and payment, share/download PDF, open receipt from extract and credit payments (Copilot).
- [ ] CM-06 — Optional: "Mis comprobantes" list in Services (Copilot).
- [x] CM-07 — Bounded correction: pin/install real `segno`, test the authenticated PDF route and owner isolation, localize the public verification page, document `PUBLIC_API_URL`, and move the credit callback below constants (strict TDD). Route: delegated (Codex).

## Acceptance criteria
- A successful mobile transfer or payment always returns a receipt; a failed one never consumes a number.
- Numbers are unique and gap-free per cooperative, type, and year under concurrent operations.
- Changing any signed field in the DB makes verification report INVALID.
- A member cannot read another member's receipt; the public page never shows full account numbers or personal data.
- Teller transfers keep their current behavior and response.

## Checks
`pytest tests/test_comprobantes_movil.py -q`, the existing transfer and payment tests, then the full suite; compare exact failed node IDs with the main baseline (81 failures observed on the local DB) before attributing their cause.

## Progress
- 2026-10-08: branch `feat/comprobantes-movil` created from `main`; document written.
- 2026-10-08: CM-01 implementation added: transactional cooperative/type/year counter, HMAC-SHA256 receipt and Crockford verification code, migration/model/config, receipt issuance in SOCIO transfers and mobile credit payments, and a trusted pre-commit callback for the shared payment handler. The callback rolls back on receipt errors; staff calls omit it and keep their previous response fields.
- 2026-10-08: CM-02 implementation added: member list/detail/PDF routes, QR generation, public HTML/JSON verification, masked public account numbers, and receipt IDs in mobile payment history and account extracts. `segno` is pinned in requirements; PDF tests exercise the authenticated production route with real QR generation and make no network calls.
- 2026-10-08: TDD evidence is partial. Valid RED/GREEN was observed for missing receipt models, missing signing configuration, missing rollback on callback failure, and the short-account masking edge case. The callback test reached production payment logic, failed because rollback was absent, and passed after the fix. Several service/route tests were added after their implementations and do not have valid pre-implementation RED evidence; therefore CM-01 and CM-02 remain unchecked despite the code and current GREEN checks.

## Verification evidence
- `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_comprobantes_movil.py -q` — initial run **12 passed**; after moving the local DB rollback failure-injection test into this explicitly allowed focal module, the moved test passed (**1 passed**); final focal verification passed (**13 passed**, 2 warnings), and combined focal/mobile/payment verification passed (**53 passed**, 2 warnings). Covers HMAC tamper detection, 404 ownership guard, masked public JSON/HTML, real QR PDF through the authenticated HTTP route, actual SOCIO transfer receipt, admin no-receipt response, numbering under 16 concurrent DB sessions, and payment callback rollback.
- RED/GREEN record: the initial model contract test failed on missing `ComprobanteTransaccion`; the config contract test failed with missing `COMPROBANTE_SIGNING_KEY`; callback rollback RED failed at `session.rolled_back is True`; short-account RED returned `****4521` instead of `****`. After their respective fixes, the focused suite passed with 12 tests.
- `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_mobile_socio.py tests/test_cobro_cuotas.py -q` — **40 passed** after moving the new rollback test into the focal module. Mobile payment receipt and history link passed; the injected receipt-rendering failure rolled back payment, movement, receipt, and counter in the local DB.
- `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_transferencias.py -q` — **10 failed** before transfer cases could authenticate: all failed at login with `401 {"detail":"Correo o contraseña incorrectos"}` for the pre-existing `admin@test.com` fixture. A new isolated SOCIO transfer + ADMINISTRADOR compatibility test passed in the focal file.
- `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=no` — final feature run: **81 failed, 448 passed, 1 skipped**. A read-only `git archive` snapshot of base `d6223fb211f419625c420e63356cf809908fde72`, run against the verified loopback DB, produced **81 failed, 435 passed, 1 skipped**. The exact failed-node sets were compared: **81 shared, 0 added, 0 removed**. This confirms no new failing nodes on this local DB; matching the baseline does not by itself prove each failure's cause. Failures cluster in local DB-dependent caja/arqueo/auth/DPF/savings/transfer/UIF/multi-tenant suites.
- `PYTHONPATH=. PATH=.venv/bin:$PATH python -m compileall -q app/api/v1/endpoints/ahorros.py app/api/v1/endpoints/creditos.py app/api/v1/endpoints/socio.py app/api/v1/endpoints/verificacion_comprobantes.py app/services/comprobantes_movil.py app/models/models.py app/core/config.py app/api/v1/router.py` — **passed**.
- `app.openapi()` route generation — **passed**; mobile receipt and public verification routes are registered.
- Database test target was classified as loopback/local before DB-backed tests. No manual migration was applied and no remote service was accessed.
- Production needs a non-default `COMPROBANTE_SIGNING_KEY`; import logs a warning when the development default is active. `segno==1.6.6` is installed in the repository virtualenv and pinned; PDF route test uses the real QR module.

- Rollback-test scope correction: the new actual transaction failure-injection test is now in `tests/test_comprobantes_movil.py`; `tests/test_mobile_socio.py` retains only the successful mobile payment/receipt/history assertions. Temporary payload-print instrumentation used to capture concrete examples was removed.

- CM-07 correction evidence: checklist item recorded before source edits. Installed `segno` 1.6.6 into `.venv` and pinned `segno==1.6.6`; moved `_no_mobile_receipt_callback` below all module constants without changing behavior; public verification now has Spanish `lang`, labels, accented status values, and unknown-code HTML/JSON handling.
- CM-07 TDD: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_comprobantes_movil.py::test_public_verification_html_and_unknown_code_are_safe tests/test_comprobantes_movil.py::test_receipt_pdf_uses_real_qr_on_authenticated_route_and_hides_foreign_receipt -q` — RED observed: **1 failed, 1 passed** because the current page rendered `lang='en'`; the PDF production-route test already passed using real `segno`. After implementation, `PYTHONPATH=. PATH=.venv/bin:$PATH pytest tests/test_comprobantes_movil.py -q` — **13 passed, 2 warnings**. `git diff --check` — passed. Full suite intentionally not run per instruction.
