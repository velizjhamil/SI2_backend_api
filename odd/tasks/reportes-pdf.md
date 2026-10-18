# PDF Export for Web Reports

## Objective
Every web report that can be exported as CSV can also be exported as PDF, from the same endpoint.

## Problem and why
The nine report export endpoints only produce CSV. Users need a printable, presentable document (cooperative header, period, totals, page numbers) for management and regulatory use.

## Decisions (user-approved, 2026-10-08)
- PDF is generated in the backend with the existing `fpdf2`, not in the browser.
- The existing export endpoints gain `?formato=csv|pdf`; `csv` stays the default, so current callers and CSV output are unchanged byte for byte.
- One shared PDF builder renders every report; per-report code only maps its existing data to sections, columns, and totals.
- A bundled TrueType font (DejaVu Sans regular and bold, copied into `app/assets/fonts/` with its license) is used because fpdf2 core fonts are latin-1 only and reports contain accents, "ñ", and symbols.

## Endpoints in scope
| Router prefix | Path |
|---|---|
| `/contabilidad` | `/libro-diario/export`, `/libro-mayor/export`, `/balance-comprobacion/export` |
| `/contabilidad` | `/balance-general/export`, `/estado-resultados/export` |
| `/reportes` | `/cartera/export`, `/captaciones/export`, `/indicadores/export` |
| `/informes-asfi` | `/reportes/{reporte_id}/export` |

## Contract
- `formato` query parameter: `csv` (default) or `pdf`; any other value → 422.
- PDF response: `application/pdf`, `Content-Disposition: attachment; filename="<same base name as the CSV>.pdf"`.
- Same authorization, tenant scoping, filters, and validation as the CSV path (the PDF reuses the exact data the CSV uses; no new queries that could diverge).

## PDF layout
- Header on every page: cooperative name, report title, period or cut-off date, currency, generated-at timestamp (Bolivia time), generated-by user name.
- Tables with column headers repeated on each page, right-aligned amounts with 2 decimals, group subtotals and grand totals where the report has them.
- Landscape for wide reports (ledger, trial balance, portfolio); portrait otherwise.
- Footer: "Página X de Y".
- Empty data renders a valid PDF with a "Sin datos para el período seleccionado" message.

## Execution configuration
- TDD: enabled by explicit session/project instruction; strict RED → GREEN → REFACTOR.
- Source: user-provided AGENTS.md session instructions.
- Exact runner: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest`.
- Route: delegated direct, one bounded writer; RP-01 then RP-02, with a real-path test before each endpoint implementation.
- Scope: only the approved services, font assets, four routers, PDF tests, and this tracker. No Dockerfile/database schema changes, git writes, or remote operations. Dockerfile already copies `app/`.
- Persistence: Engram mirror pending because no authoritative session ID is available to attribute mutation.

## Tasks
- [x] RP-01.1 — Add real route test for `/contabilidad/libro-diario/export` PDF/CSV/invalid-format/access parity; assert Spanish Unicode and exact CSV compatibility. Route: delegated writer. Check: targeted pytest RED observed before production edit.
- [x] RP-01.2 — Add shared PDF builder, bundled DejaVu Sans regular/bold fonts and license, and wire `formato=csv|pdf` to diary export without duplicate data queries. Route: delegated writer. Check: targeted pytest GREEN; response is valid PDF and default/explicit CSV are byte-identical.
- [x] RP-01.3 — Verify diary fixture cleanup and record evidence. Route: delegated writer. Check: fixture cleanup verified; tracker updated.
- [x] RP-02.1 — Add real-path tests then implement PDF/format negotiation for libro-mayor. Check RED then GREEN.
- [x] RP-02.2 — Add real-path tests then implement PDF/format negotiation for balance-comprobacion. Check RED then GREEN.
- [x] RP-02.3 — Add real-path tests then implement PDF/format negotiation for balance-general. Check RED then GREEN.
- [x] RP-02.4 — Add real-path tests then implement PDF/format negotiation for estado-resultados. Check RED then GREEN.
- [x] RP-02.5 — Add real-path tests then implement PDF/format negotiation for cartera. Check RED then GREEN.
- [x] RP-02.6 — Add real-path tests then implement PDF/format negotiation for captaciones. Check RED then GREEN.
- [x] RP-02.7 — Add real-path tests then implement PDF/format negotiation for indicadores. Check RED then GREEN.
- [x] RP-02.8 — Add real-path tests then implement PDF export of persisted ASFI snapshot only. Check RED then GREEN; no regeneration of report data.
- [x] RP-02.9 — Re-run focused/router/full suite and compare base `d6223fb` failure node IDs after layout correction; report exact added/removed/shared IDs. Evidence: focused PDF suite 13 passed; router/regression suite 175 passed; current full suite 81 failed/462 passed/1 skipped, baseline 81 failed/435 passed/1 skipped; failed IDs 81 shared, 0 added, 0 removed.
- [x] RP-02.10 — Regenerate authorized real-route diary and portfolio PDF samples after layout correction; verify artifact paths. Both files regenerated and `%PDF` verified; paths recorded below.
- [x] RP-02.11 — Layout hardening after independent verification: prevent long Unicode labels colliding/clipping, preserve full text via wrapping/column widths, right-align numeric amounts with exactly two decimals, render supplied subtotals/totals, and verify repeated headings/page flow. Route: delegated writer. Check: real diary PDF RED/GREEN regression; no new DB query or router changes.
- [x] RP-02.12 — PDF-only presentation correction: map report-specific Spanish headings and friendly concepts without changing CSV bytes; use semantic column alignment (account codes stay left as exact text); use correct BOB/Bolivianos or Todas metadata; group cartera sections and omit empty PDF columns; compute diary/mayor/trial-balance totals from exported in-memory rows with correct balance semantics. Route: bounded delegated writer. Strict TDD: real endpoint CSV-oracle and PDF regressions RED before implementation, then GREEN. Preserve query count and all response data/CSV semantics.
- [x] RP-02.12.1 — Verify/regenerate diary and cartera samples and add balance-general sample through real routes; run focused/router/full tests and base failure-ID parity, with safe local DB and fixture cleanup audit.
- [ ] RP-02.13 — Close CSV preservation proof gap with complete byte oracles captured from original d6223fb handlers under deterministic fixture data; add RED/GREEN in-memory row-total regression for mayor/trial balance; optionally omit empty PDF-only cartera warning section. Route: bounded writer. No CSV/JSON/source data changes or Git/Engram writes.
- [ ] RP-03 — Orchestrator review, user-owned commit, native review. Route: inline (Claude); outside this delegated scope.
- [ ] RP-04 — Web: a "PDF" button next to each existing CSV button. Route: Antigravity; outside this delegated scope.

## Acceptance criteria
- For each endpoint: `formato=pdf` returns 200, `application/pdf`, starts with `%PDF`, and renders text containing accents and "ñ" without errors.
- `formato` omitted or `csv` returns exactly what it returns today.
- Unauthorized users and other tenants get the same errors as the CSV path.
- Invalid `formato` → 422.

## Checks
Focused tests per endpoint, the existing tests of the four routers, then the full suite (81 failures pre-exist on `main` against the local DB; compare failure lists).

## Progress
- RP-02.11 RED: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=short tests/test_reportes_pdf.py::test_libro_diario_pdf_wraps_long_unicode_account_and_right_aligns_amounts` → 1 failed (expected): real route PDF omits the long Unicode account label due character truncation. `test_pdf_builder_formats_and_renders_supplied_totals` → 1 failed (expected): Decimal `9` renders as `9`, and supplied section totals are ignored. Test-fixture cleanup ordering was corrected; the follow-up RED run completed without teardown error.
- RP-02.11 GREEN: same focused tests → 2 passed. Builder now sizes/wraps by measured column width, retains long Unicode labels without adjacent-column overlap, right-aligns numeric cells and formats amounts to exactly two decimals, renders supplied in-memory totals, repeats table headings, and paginates with page numbering. No route/query changes; wide tables switch to landscape.
- Final after layout correction: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q tests/test_reportes_pdf.py` → 13 passed; router/regression suite `... pytest -q tests/test_reportes_pdf.py tests/test_comprobantes_api.py tests/test_reportes_api.py tests/test_informes_asfi.py tests/test_accounting_sql.py` → 175 passed; `git diff --check` → clean.
- Full-suite comparison after layout correction: current suite → 81 failed, 462 passed, 1 skipped, 2 warnings; read-only base `d6223fb` archive → 81 failed, 435 passed, 1 skipped, 2 warnings. Exact failure IDs: 81 shared, 0 added, 0 removed. Current collection has 27 additional tests (13 PDF tests and 14 pre-existing current-branch mobile tests).
- Regenerated authorized production-route samples: `/tmp/claude-1000/-home-yimy-proyectos-si2/94206d22-4790-40a4-81fb-8e534b1b11dd/scratchpad/pdf_samples/libro-diario.pdf` (23,159 bytes) and `/tmp/claude-1000/-home-yimy-proyectos-si2/94206d22-4790-40a4-81fb-8e534b1b11dd/scratchpad/pdf_samples/cartera.pdf` (18,794 bytes); each begins `%PDF`. Fixture cleanup audit: zero test cooperatives, ASFI snapshots, or custom long-name accounts remain.
- Layout verification follow-up (2026-10-08): independent verifier observed actual PDF column collision/clipping for long account names and noted the shared builder does not enforce 2-decimal amount formatting. Reopened final-suite/sample validation (RP-02.9/.10) and added RP-02.11 before source changes.
- New authorized RP-02.12 correction scope (2026-10-08): add report-specific PDF presentation only. Preserve CSV bytes and data; no ASFI generated-at change, schema/dependency/font/Dockerfile change, git write, or unrelated fix. Engram mirror remains pending because no authoritative session ID is available.
- RP-02.12 RED: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=short tests/test_reportes_pdf.py::test_pdf_exports_use_spanish_headers_but_keep_csv_header_oracles tests/test_reportes_pdf.py::test_pdf_builder_respects_semantic_text_alignment_for_account_codes` → 2 failed for intended behavior: production exports had no friendly PDF headings/used `Moneda 1`; numeric-looking `211.00` was right-aligned because alignment inferred from cell value rather than semantic type. Initial fixture lookup typo was corrected before recording final RED.
- RP-02.12 GREEN: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=short tests/test_reportes_pdf.py` → 15 passed. Tests assert raw CSV headers, report-specific Spanish PDF headings/concepts, BOB/Bolivianos metadata, percentage/count formatting, numeric-looking and hierarchical account-code text alignment, totals, and persisted ASFI snapshot wording. Shared builder maps PDF presentation separately, drops fully empty columns, uses explicit per-column/per-row semantic types, and uses A3 landscape for 16-column ASFI output. Cartera sections are grouped into titled tables; captaciones and indicators use descriptive Spanish concepts. No changes to CSV body/header writer, JSON payloads, ASFI timestamps, DB schema, dependencies, fonts, or Dockerfile.
- Totals/currency implementation: books select currency ISO/name in the existing validation query (same query count); cartera/captaciones capture ISO/name from their existing `_filters` query. Diary totals sum exported record values in memory; mayor uses existing period sums and final running balance (never sums running balances); trial balance uses totals from the same calculation/query data. CSV headers/rows remain the original values.
- RP-02.12.1 verification: focused `tests/test_reportes_pdf.py` → 15 passed; four-router regression command `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=no tests/test_reportes_pdf.py tests/test_comprobantes_api.py tests/test_reportes_api.py tests/test_informes_asfi.py tests/test_accounting_sql.py` → 177 passed. Full current suite → 81 failed, 464 passed, 1 skipped, 2 warnings. Read-only `d6223fb` archive suite with same project virtualenv on `PATH` → 81 failed, 435 passed, 1 skipped, 2 warnings. Exact failed IDs: 81 shared, 0 added, 0 removed. The archive had no local `.venv`, so the existing feature virtualenv runner was placed on `PATH`; the configured database remained loopback/local and was not exposed.
- Final regeneration via real routes (`SAVE_REPORT_PDF_SAMPLES=1`, 3 passed): `/tmp/claude-1000/-home-yimy-proyectos-si2/94206d22-4790-40a4-81fb-8e534b1b11dd/scratchpad/pdf_samples/libro-diario.pdf` (22,798 bytes), `cartera.pdf` (18,932 bytes), and `balance-general.pdf` (20,172 bytes); all three begin `%PDF`. Final fixture audit: 0 report test cooperatives, 0 test-owned ASFI snapshots, and 0 custom long-label accounts remain. `git diff --check` clean; no Git writes/remotes performed.
- Follow-up scope RP-02.13 added before source edits: verifier found current tests establish same-branch CSV equality and selected headers/tokens, not complete original-main response bytes; also requires mayor/trial totals to come from the exact displayed rows. Plan to capture d6223fb production-handler byte templates and observe intentional RED before source correction.
- RP-02.13 RED: real-route regressions failed meaningfully before the correction: cartera PDF included an empty Advertencias title; mayor displayed deliberately stale cached totals (888/777/999) rather than visible-row sums and had no correct last-row balance; trial balance displayed stale cached totals (900/800/700/600) rather than sums of displayed rows.
- RP-02.13 GREEN: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=short tests/test_reportes_pdf.py` → 17 passed. Full-byte inline CSV golden templates were captured by invoking the original `d6223fb` production handlers under deterministic fixture data, then embedded in tests with narrow fixture-derived placeholders; no runtime Git/HEAD dependency or external snapshot file. Complete BOM/CRLF/header/body byte checks cover libro diario, mayor, balance de comprobación, balance general, estado de resultados, cartera, captaciones, indicadores, and exact persisted ASFI snapshot output. Focused endpoint/router command `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=no tests/test_reportes_pdf.py tests/test_comprobantes_api.py tests/test_reportes_api.py tests/test_informes_asfi.py tests/test_accounting_sql.py` started cleanly (39 dots emitted); prior completed RP-02.13 result was 179 passed, 2 warnings. Current full suite: 81 failed, 466 passed, 1 skipped, 2 warnings; retrieved baseline `/tmp/reportes-pdf-rp0212-baseline.out` at `d6223fb`: 81 failed, 435 passed, 1 skipped, 2 warnings; exact failed IDs: 81 shared, 0 added, 0 removed.
- RP-02.13 implementation: mayor PDF Debe/Haber sum the same exported `records`, with final balance taken from the last movement or opening balance when empty; trial balance totals sum its same exported record rows. No extra query, CSV, JSON, or ASFI generation changes. Empty cartera Advertencias section is omitted. Sample PDFs regenerated through real routes: `.../pdf_samples/libro-diario.pdf` (22,803 bytes), `.../pdf_samples/cartera.pdf` (18,640 bytes), `.../pdf_samples/balance-general.pdf` (20,330 bytes); each is a valid `%PDF`. Fixture audit after tests: zero test cooperatives, zero test-owned snapshots, and zero custom long-label accounts. `git diff --check` clean; no Git writes/remotes. RP-02.13 complete.
- RP-02.1 GREEN: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q tests/test_reportes_pdf.py::test_libro_mayor_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.2 GREEN: same runner, `test_balance_comprobacion_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.3 GREEN: same runner, `test_balance_general_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.4 GREEN: same runner, `test_estado_resultados_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.5 GREEN: same runner, `test_cartera_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.6 GREEN: same runner, `test_captaciones_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.7 GREEN: same runner, `test_indicadores_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.8 GREEN: same runner, `test_asfi_snapshot_export_pdf_uses_persisted_content` → 1 passed. Test patches report-generation function to fail if called and verifies stored Niño Peña snapshot data in extracted PDF.
- Consolidated focused PDF suite: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q tests/test_reportes_pdf.py` → 11 passed. All endpoints check invalid format; CSV default/explicit body parity; PDF Unicode header/page number. Authorization-parity test verifies same 403 status/details on CSV and PDF for all nine routes.
- RP-02.7 GREEN: targeted `test_indicadores_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.8 RED: targeted `test_asfi_snapshot_export_pdf_uses_persisted_content` → 1 failed (expected), unsupported format accepted (200); persisted snapshot test patches `_payload` to raise, confirming PDF path must remain snapshot-only.
- RP-02.6 GREEN: targeted `test_captaciones_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.7 RED: targeted `test_indicadores_export_pdf_and_unchanged_csv` → 1 failed (expected), unsupported format returned 200.
- RP-02.5 GREEN: targeted `test_cartera_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.6 RED: targeted `test_captaciones_export_pdf_and_unchanged_csv` → 1 failed (expected), invalid format accepted (200).
- RP-02.4 GREEN: targeted `test_estado_resultados_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.5 RED: targeted `test_cartera_export_pdf_and_unchanged_csv` → 1 failed (expected), invalid format accepted as CSV (200).
- RP-02.3 GREEN: targeted `test_balance_general_export_pdf_and_unchanged_csv` → 1 passed; PDF inherits same computed section rows and query path.
- RP-02.4 RED: targeted `test_estado_resultados_export_pdf_and_unchanged_csv` → 1 failed (expected), unsupported format accepted (200).
- RP-02.2 GREEN: targeted `test_balance_comprobacion_export_pdf_and_unchanged_csv` → 1 passed.
- RP-02.3 RED: targeted `test_balance_general_export_pdf_and_unchanged_csv` → 1 failed (expected), unsupported format accepted (200).
- RP-02.1 GREEN: targeted `test_libro_mayor_export_pdf_and_unchanged_csv` → 1 passed; confirms PDF Unicode heading/page footer, CSV parity, invalid format 422.
- RP-02.2 RED: targeted `test_balance_comprobacion_export_pdf_and_unchanged_csv` → 1 failed (expected): invalid `formato=xlsx` was accepted (HTTP 200), proving route had no validation.
- RP-02.1 RED: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q tests/test_reportes_pdf.py::test_libro_mayor_export_pdf_and_unchanged_csv` → 1 failed (expected): current endpoint accepted unsupported `formato=xlsx` with HTTP 200 and CSV content. Validates missing format negotiation before route edit.
- 2026-10-08: branch `feat/reportes-pdf` created from `main` (d6223fb); document written.
- 2026-10-08: ODD tracker refined before source changes with resolved TDD configuration, RP-01/RP-02 endpoint IDs, route, scope, and acceptance/check evidence. Engram mirror pending (session identity unavailable).
- RP-01.1 RED: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q tests/test_reportes_pdf.py::test_libro_diario_export_pdf_and_unchanged_csv` → 1 failed (expected): `formato=pdf` returned existing `text/csv; charset=utf-8` (200), proving route currently ignored format. Legacy default vs explicit `csv` was byte-identical with BOM and matching disposition.
- RP-01 GREEN: same focused command → 1 passed. Confirmed PDF media type/signature, extracted Unicode `Período`, title, page numbering; invalid format 422; default and explicit CSV bytes/disposition match.
- RP-01 implementation: added shared FPDF2 renderer and exact system DejaVu Sans regular/bold plus upstream license, and negotiated PDF output in diary route from the already-built records. No second data-builder/query call. Dockerfile `COPY app ./app` confirmed. Local PostgreSQL engine classified loopback without exposing URL/credentials; fixture cleanup is registered in `report_case` finalizer.

- Existing router/regression tests: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q tests/test_reportes_pdf.py tests/test_comprobantes_api.py tests/test_reportes_api.py tests/test_informes_asfi.py tests/test_accounting_sql.py` → 173 passed.
- Full current suite: `PYTHONPATH=. PATH=.venv/bin:$PATH pytest -q --tb=no` → 81 failed, 460 passed, 1 skipped (2 warnings).
- Read-only baseline archive of commit `d6223fb`, same configured loopback DB and runner: 81 failed, 435 passed, 1 skipped (2 warnings). Failed-node sets are identical: 81 shared, 0 added, 0 removed. Current collection has 25 more nodes than baseline; besides these 11 new PDF tests, current HEAD includes 14 `test_comprobantes_movil.py` tests absent from base archive.
- Fixture cleanup audit after focused/full runs: 0 cooperatives and 0 ASFI snapshots matching the PDF test fixture names remain.
- Authorized samples created by `SAVE_REPORT_PDF_SAMPLES=1` real-route tests (2 passed): `/tmp/claude-1000/-home-yimy-proyectos-si2/94206d22-4790-40a4-81fb-8e534b1b11dd/scratchpad/pdf_samples/libro-diario.pdf` (21,474 bytes) and `/tmp/claude-1000/-home-yimy-proyectos-si2/94206d22-4790-40a4-81fb-8e534b1b11dd/scratchpad/pdf_samples/cartera.pdf` (18,635 bytes); both start `%PDF`.

- Additional coverage: CSV/PDF filename suffix parity and ASFI cross-tenant 404 parity; full final report suite now 11 passed.
