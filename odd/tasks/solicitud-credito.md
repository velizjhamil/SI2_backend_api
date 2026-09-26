# Feature: Credit request registration (CU-W21)

Second prerequisite of CU-W23. Plan: W20 ✅ → **W21** → W23 part 1 → W24 → W26 → W27 → W23 part 2. Builds on `odd/tasks/productos-crediticios.md`.

## User story (fictional, drafted by the orchestrator; user approved using drafts)
**CU-W21: Registro y Recepción de Solicitud de Crédito (Web)** — Actor: Oficial de crédito (also Administrador).
> Como oficial de crédito quiero registrar la solicitud de crédito de un socio con el producto, monto, plazo, destino y su evaluación socioeconómica, para que quede lista para la evaluación crediticia y el comité.

Acceptance criteria:
1. Find an ACTIVE socio of the cooperative by CI or name and pick an ACTIVE credit product.
2. Amount and term must fall within the product ranges; the interest rate and currency are copied from the product and cannot be edited.
3. Registers the purpose (destino) and the socioeconomic evaluation: monthly income, expenses, other debt installments, economic activity, income source, job seniority and ASFI rating; payment capacity is computed automatically.
4. Shows an informative estimated installment and the installment-to-income ratio against the product maximum before saving (the decision belongs to CU-W23).
5. Assigns a unique correlative number and leaves the request PENDIENTE; it can be edited while PENDIENTE/OBSERVADA and voided (ANULADA) with a reason. A socio cannot have two requests in progress at once. Every action goes to the audit log.

## Current state (2026-09-26)
- `solicitud_credito(id, monto, plazo_meses, tasa_interes, calificacion_asfi, tiene_deudas, estado DEFAULT 'PENDIENTE', socio_id, usuario_id, evaluacion_campo_id, producto_credito_id)` — `producto_credito_id` added by migration 014.
- `evaluacion_campo(id, ingreso_mensual, egreso_mensual, capacidad_pago, fotografias_respaldo, coordenadas, fecha, resumen_cualitativo_ia, usuario_id)` — no link to the socio.
- Seed rows: 3 requests (states APROBADO, PENDIENTE, PENDIENTE) without product. They must keep working (list/detail tolerate `producto = null`).
- No credit request endpoints exist. `/api/v1/creditos/productos` exists (CU-W20) in `app/api/v1/endpoints/creditos.py`.

## Decisions (orchestrator)
- States (VARCHAR): `PENDIENTE` (initial), `OBSERVADA`, `EN_EVALUACION`, `APROBADO`, `RECHAZADO`, `DESEMBOLSADO`, `ANULADA`. W21 only creates PENDIENTE, edits PENDIENTE/OBSERVADA and voids (→ ANULADA) PENDIENTE/OBSERVADA. In progress = PENDIENTE, OBSERVADA, EN_EVALUACION.
- Roles: write (create/edit/void) = OFICIAL_CREDITO or ADMINISTRADOR with a cooperative; read = cooperative staff (ADMINISTRADOR, CAJERO, OFICIAL_CREDITO, CONTADOR). Users without a cooperative → 403 (same helpers style as CU-W20).
- Tenant = the socio's cooperative must equal the caller's.
- Rate and currency are snapshots from the product at creation (`tasa_interes` and internal `moneda_id` columns); later product edits do not change the request. Editing the product/amount/term of a request re-copies the current product rate; changing the selected product also snapshots its currency. Legacy productless requests keep `moneda_id = NULL` and serialize `moneda = null`.
- `numero_solicitud` correlative per cooperative via `secuencia_documento` tipo `'SOLICITUD_CREDITO'` → `SOL-000001`.
- Evaluation: extend `evaluacion_campo`; `capacidad_pago = ingreso_mensual − egreso_mensual − cuota_deudas_mensual` (server-computed, may be negative). `tiene_deudas` on the request = `cuota_deudas_mensual > 0`.
- Estimated installment (monthly, i = TNA/12/100, Decimal ROUND_HALF_UP to 2 decimals):
  - FRANCES: `P·i / (1 − (1+i)^−n)` (if i = 0: P/n).
  - ALEMAN: first installment `P/n + P·i` (the highest).
  - `relacion_cuota_ingreso` = cuota_estimada / ingreso_mensual × 100 (2 decimals); `supera_relacion_maxima` = relacion > product `relacion_cuota_ingreso_max`. Informative only; never blocks creation.
- Purposes (`destino`): `CAPITAL_TRABAJO`, `ACTIVO_FIJO`, `CONSUMO`, `VIVIENDA`, `EDUCACION`, `SALUD`, `REFINANCIAMIENTO`, `OTRO` (OTRO requires `destino_detalle`).
- ASFI rating: `A`, `B`, `C`, `D`, `E`, `F`.
- Bitácora modulo `CREDITOS`: `REGISTRAR_SOLICITUD`, `ACTUALIZAR_SOLICITUD`, `ANULAR_SOLICITUD`.

## Migration `migrations/015_sprint6_solicitud_credito.sql` (idempotent; do not modify 002–014)
- `solicitud_credito` add: `numero_solicitud VARCHAR(20)` with `UNIQUE (cooperativa_id, numero_solicitud)` (per-cooperative correlative; decided 2026-09-26 after Codex flagged the global UNIQUE contradiction), internal `moneda_id INT NULL REFERENCES moneda(id)` (currency snapshot), `destino VARCHAR(20)`, `destino_detalle TEXT`, `observaciones TEXT`, `motivo_anulacion TEXT`, `fecha_solicitud TIMESTAMPTZ DEFAULT now()`, `fecha_actualizacion TIMESTAMPTZ DEFAULT now()`, `cooperativa_id BIGINT REFERENCES cooperativa(id)`. Backfill `cooperativa_id` from the socio, `moneda_id` from the linked product when present, and `numero_solicitud` for existing rows (correlative per cooperative, then advance `secuencia_documento`). Legacy productless requests retain a null currency snapshot.
- `evaluacion_campo` add: `socio_id INT REFERENCES socio(id)`, `cuota_deudas_mensual NUMERIC(12,2) NOT NULL DEFAULT 0`, `actividad_economica VARCHAR(150)`, `fuente_ingresos VARCHAR(20)` (`DEPENDIENTE`, `INDEPENDIENTE`, `MIXTO`), `antiguedad_laboral_meses INT`, `calificacion_asfi VARCHAR(1)`, `observaciones TEXT`. Backfill `socio_id` from the request that references each evaluation.
- Partial unique index: one in-progress request per socio: `ON solicitud_credito (socio_id) WHERE estado IN ('PENDIENTE','OBSERVADA','EN_EVALUACION')` — if existing data violates it, do not create the index blindly: report to the orchestrator instead (enforce in code regardless).

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos`. Errors `{"detail": "..."}` (Spanish). Amounts/rates 2-decimal strings. `moneda` = MonedaOut. Persons/products are objects.

`SocioResumen` = `{id, nombre_completo, ci, estado}`; `ProductoResumen` = `{id, codigo, nombre, tipo_amortizacion, relacion_cuota_ingreso_max}`;
`EvaluacionOut` = `{id, ingreso_mensual, egreso_mensual, cuota_deudas_mensual, capacidad_pago, actividad_economica, fuente_ingresos, antiguedad_laboral_meses, calificacion_asfi, coordenadas, observaciones, fecha}`;
`SolicitudOut` = `{id, numero_solicitud, fecha_solicitud, fecha_actualizacion, estado, socio: SocioResumen, producto: ProductoResumen | null, moneda: MonedaOut | null, monto, plazo_meses, tasa_interes, destino, destino_detalle, observaciones, motivo_anulacion, tiene_deudas, cuota_estimada | null, relacion_cuota_ingreso | null, supera_relacion_maxima | null, evaluacion: EvaluacionOut | null, oficial: {id, nombre}}`.
`cuota_estimada`/`relacion_*` are computed on read (null when there is no product or no evaluation income).

1. `GET /creditos/socios/buscar?q=` (q ≥ 3 chars; matches CI prefix or name/surname, case-insensitive; ACTIVE socios of the cooperative; max 20) → `[SocioResumen]`.
2. `POST /creditos/solicitudes/simulacion` body `{producto_id, monto, plazo_meses, ingreso_mensual?, egreso_mensual?, cuota_deudas_mensual?}` → `{producto: ProductoResumen, moneda, monto, plazo_meses, tasa_interes, cuota_estimada, total_intereses_estimado, capacidad_pago | null, relacion_cuota_ingreso | null, relacion_maxima, supera_relacion_maxima | null, dentro_de_rangos: bool, errores: [string]}` — never 400 for out-of-range values: it reports them in `errores` (404 only for a product outside the cooperative). `total_intereses_estimado` = Σ interest of the full schedule.
3. `POST /creditos/solicitudes` body `{socio_id, producto_id, monto, plazo_meses, destino, destino_detalle?, observaciones?, evaluacion: {ingreso_mensual, egreso_mensual, cuota_deudas_mensual, actividad_economica, fuente_ingresos, antiguedad_laboral_meses, calificacion_asfi, coordenadas?, observaciones?}}` → `201 SolicitudOut` (PENDIENTE). 400: product INACTIVO, amount/term out of range, invalid destino/fuente/calificación, OTRO without detail, negative incomes; 404: socio/product outside cooperative; 409: socio inactive or already has a request in progress (`"El socio ya tiene una solicitud en curso"`).
4. `GET /creditos/solicitudes?estado=&socio_ci=&desde=&hasta=` → `[SolicitudOut]` of the cooperative, newest first.
5. `GET /creditos/solicitudes/{id}` → `SolicitudOut`; 404 outside cooperative.
6. `PUT /creditos/solicitudes/{id}` partial body (same fields as POST except `socio_id`; `evaluacion` partial) → `200 SolicitudOut`; 409 if state is not PENDIENTE/OBSERVADA; same validations.
7. `PATCH /creditos/solicitudes/{id}/anulacion` body `{motivo}` (≥ 5 chars) → `200 SolicitudOut` (ANULADA); 409 if not PENDIENTE/OBSERVADA.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/solicitud-credito`):
- [x] B1 Migration 015 applied twice to local coopDB; verified backfills, nullable currency snapshot, cooperative-scoped number uniqueness, and models/schemas.
- [x] B2 Socio search + simulation incl. FRANCES/ALEMAN math (RED→GREEN; P=1200, n=12, TNA 18% => FRANCES 110.02, ALEMAN first 118.00).
- [x] B3 Create request validations, per-coop correlative, one-in-progress rule, audit (RED→GREEN); focused create tests passed.
- [x] B4 List/detail (including legacy productless rows), PUT, and annulment (RED→GREEN); currency snapshot regression test passed.

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/solicitud-credito`):
- [x] F1 `src/core/api/creditosApi.js`: add solicitud functions (add only; keep product functions unchanged)
- [x] F2 New `src/modules/oficial/pages/SolicitudesPage.jsx`: list with state filter + "Nueva solicitud"; route `/oficial/solicitudes` in `AppRouter.jsx` and nav item "Solicitudes" in `OficialLayout.jsx`
- [x] F3 Request form (new components under `src/modules/oficial/components/solicitudes/`): socio search, active product picker showing its ranges, amount/term, purpose, evaluation fields, live simulation panel (installment, ratio vs max, errores) using endpoint 2
- [x] F4 Detail view with evaluation data, edit when PENDIENTE/OBSERVADA, void with reason; printable request summary (window.print())

Orchestrator — Claude:
- [x] V1 Review, checks, E2E through the Vite proxy, commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_solicitud_credito.py`. Baseline 91 passed / 5 pre-existing failures (`test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`, 4 in `test_savings.py`).
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings.

## Progress
- 2026-09-26: branches `feat/solicitud-credito` created from `feat/productos-crediticios` in both repos; scope, decisions and contract defined; delegated.
- 2026-09-26: delegated direct implementation route selected after mapping the existing credit, auth, audit, schema, migration, and test surfaces; strict TDD with `.venv/bin/python -m pytest`.
- 2026-09-26: preserve the already-approved currency snapshot invariant with an internal nullable `solicitud_credito.moneda_id`; keep the public `SolicitudOut` contract unchanged and legacy productless requests currency-null.
- 2026-09-26: verification: 50 focused request/product/DPF/caja/transfer tests passed; full suite 105 passed, 5 known baseline failures (one multi-tenant authorization and four savings tests), 2 warnings.
- 2026-09-26 (orchestrator review): scope respected on both sides (frontend creditosApi additions only, route + nav item, SolicitudesPage + components; package/lock intact). Backend `pytest -q`: 105 passed / 5 failures identical to baseline (hash). Accepted deviation: internal `moneda_id` snapshot column on the request. Frontend build OK, oxlint 8 pre-existing / 0 errors; nullable producto/moneda/evaluacion guarded.
- Found and removed leftover data from two interrupted earlier test runs (4 "Loan Test" cooperatives with socios, users, products, requests); verified the final suite leaks nothing (row counts identical before/after running both credit test files).
- E2E through the Vite proxy (oficial.credito@test.com): search q<3 → 400, by CI → socio; FRANCES 10000/12m/18% → 916.80 (total interest 1001.61, verified by hand), ratio 22.92 vs 40; ALEMAN 12000/60m/8.5% → 285.00 (hand-verified); out-of-range simulation → 200 with `errores`; create out of range 400, OTRO without detail 400, cajero 403, OK 201 SOL-000003 (rate 18.00, tiene_deudas true, capacidad 2300.00); second in-progress 409; PUT 12000/18m → 765.67 (hand-verified); list includes legacy rows with producto null; void short reason 400, void OK → ANULADA; PUT on ANULADA 409. E2E rows removed, correlative restored.
- Follow-up (not in scope): DPF `numero_certificado` has a global UNIQUE index while its correlative is per cooperative (same latent issue Codex flagged here).
