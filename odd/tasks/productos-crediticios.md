# Feature: Credit product configuration (CU-W20)

First prerequisite of CU-W23 (credit scoring), agreed with the user on 2026-09-26. Plan: W20 → W21 → W23 part 1 → W24 → W26 → W27 → W23 part 2.

## User story (fictional, drafted by the orchestrator; user approved using drafts)
**CU-W20: Configuración de Productos Crediticios (Web)** — Actor: Administrador de la cooperativa.
> Como administrador de la cooperativa quiero configurar los productos crediticios (montos, plazos, tasas y reglas de amortización y mora) para que las solicitudes, la evaluación crediticia y las tablas de amortización usen parámetros oficiales y consistentes.

Acceptance criteria:
1. Create a product with code, name, currency, amount range, term range (months), annual nominal interest rate, amortization type (French fixed installment / German fixed principal), late-payment rules, maximum installment-to-income ratio and whether a guarantee is required.
2. Validates coherent ranges (min ≤ max, positive values, rates between 0 and 100) and a unique code per cooperative.
3. Edit a product; changes never alter existing requests or loans (they keep their own terms).
4. Activate / deactivate instead of deleting; only ACTIVE products can be used for new requests.
5. Loan officers and cashiers can consult the catalog of active products; only administrators can modify it. Every change is recorded in the audit log.

## Decisions (orchestrator)
- Tenant: products belong to the caller's `cooperativa_id`. Users without a cooperative (e.g. SUPERADMIN) get 403 "Operación no disponible para este usuario" (same pattern as `caja.py::_validar_operador`).
- Read: `require_operaciones` + CONTADOR (all staff of the cooperative). Write: `require_admin` (ADMINISTRADOR with a cooperative).
- Installment frequency is monthly only in v1 (no field). Interest is nominal annual (TNA, %).
- Late payment: `dias_gracia_mora` (days before an installment is considered late) and `tasa_mora_anual` (penalty TNA over overdue principal). Used later by W27/W28.
- `relacion_cuota_ingreso_max` (%): maximum installment / net income ratio; used later by W23 scoring. Default 40.00.
- No delete endpoint. Code is immutable after creation.
- Migration seeds 3 demo products per existing cooperative (idempotent by `(cooperativa_id, codigo)`):
  - `CONS-BOB` Crédito de Consumo — BOB, 1,000–50,000, 6–48 months, TNA 18.00, FRANCES, grace 3 days, mora 3.00, ratio 40, no guarantee.
  - `MICRO-BOB` Microcrédito Productivo — BOB, 2,000–100,000, 6–60 months, TNA 15.00, FRANCES, grace 5, mora 3.00, ratio 45, guarantee required.
  - `VIV-USD` Crédito de Vivienda — USD, 10,000–150,000, 60–240 months, TNA 8.50, ALEMAN, grace 5, mora 2.00, ratio 35, guarantee required.
- `solicitud_credito.producto_credito_id` nullable FK added now (W21 will use it; existing rows stay NULL).
- Bitácora modulo `CREDITOS`, acciones `CREAR_PRODUCTO`, `ACTUALIZAR_PRODUCTO`, `ACTIVAR_PRODUCTO`, `DESACTIVAR_PRODUCTO`.

## Migration `migrations/014_sprint6_productos_crediticios.sql` (idempotent; do not modify 002–013)
- `producto_credito(id SERIAL PK, cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id), codigo VARCHAR(20) NOT NULL, nombre VARCHAR(100) NOT NULL, descripcion TEXT NULL, moneda_id INT NOT NULL REFERENCES moneda(id), monto_min NUMERIC(14,2) NOT NULL, monto_max NUMERIC(14,2) NOT NULL, plazo_min_meses INT NOT NULL, plazo_max_meses INT NOT NULL, tasa_interes_anual NUMERIC(5,2) NOT NULL, tipo_amortizacion VARCHAR(10) NOT NULL CHECK (tipo_amortizacion IN ('FRANCES','ALEMAN')), dias_gracia_mora INT NOT NULL DEFAULT 0, tasa_mora_anual NUMERIC(5,2) NOT NULL DEFAULT 0, relacion_cuota_ingreso_max NUMERIC(5,2) NOT NULL DEFAULT 40.00, requiere_garantia BOOLEAN NOT NULL DEFAULT false, estado VARCHAR(10) NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','INACTIVO')), fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(), fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (cooperativa_id, codigo), CHECK (monto_min > 0 AND monto_min <= monto_max), CHECK (plazo_min_meses > 0 AND plazo_min_meses <= plazo_max_meses), CHECK (tasa_interes_anual >= 0 AND tasa_interes_anual <= 100), CHECK (tasa_mora_anual >= 0 AND tasa_mora_anual <= 100), CHECK (relacion_cuota_ingreso_max > 0 AND relacion_cuota_ingreso_max <= 100), CHECK (dias_gracia_mora >= 0))`
- `solicitud_credito` add `producto_credito_id INT NULL REFERENCES producto_credito(id)`.
- Seed the 3 products above for every cooperativa.

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos/productos`. Errors `{"detail": "..."}` (Spanish). Amounts/rates as 2-decimal strings. `moneda` is MonedaOut `{id, codigo_iso, nombre, simbolo}`.

`ProductoCreditoOut` = `{id, codigo, nombre, descripcion, moneda: MonedaOut, monto_min, monto_max, plazo_min_meses, plazo_max_meses, tasa_interes_anual, tipo_amortizacion, dias_gracia_mora, tasa_mora_anual, relacion_cuota_ingreso_max, requiere_garantia, estado, fecha_creacion, fecha_actualizacion}`

1. `GET /api/v1/creditos/productos?estado=ACTIVO|INACTIVO` → `[ProductoCreditoOut]` of the caller's cooperative, ordered by `codigo`. No filter = all.
2. `GET /api/v1/creditos/productos/{id}` → `ProductoCreditoOut`; 404 if not in the caller's cooperative.
3. `POST /api/v1/creditos/productos` (admin) body = all writable fields: `{codigo, nombre, descripcion?, moneda_id, monto_min, monto_max, plazo_min_meses, plazo_max_meses, tasa_interes_anual, tipo_amortizacion, dias_gracia_mora?, tasa_mora_anual?, relacion_cuota_ingreso_max?, requiere_garantia?}` → `201 ProductoCreditoOut` (estado ACTIVO). `codigo` normalized to uppercase, `^[A-Z0-9-]{2,20}$`. 409 duplicate code in the cooperative; 400 incoherent ranges/rates or unknown currency.
4. `PUT /api/v1/creditos/productos/{id}` (admin) body = same fields as POST **except `codigo`** (all optional; partial update) → `200 ProductoCreditoOut`; same 400 validations on the resulting values; 404 outside cooperative.
5. `PATCH /api/v1/creditos/productos/{id}/estado` (admin) body `{estado: "ACTIVO"|"INACTIVO"}` → `200 ProductoCreditoOut`.
- 403 for non-admin writes and for users without a cooperative.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/productos-crediticios`):
- [x] B1 Migration 014 applied twice to local coopDB; model `ProductoCredito` + schemas — migration applied twice successfully; model/schema test RED (`AttributeError` for missing model), then GREEN (1 passed).
- [x] B2 GET list/detail with tenant isolation and role checks (RED→GREEN) — route registration RED, then focused product suite GREEN (3 passed against local coopDB).
- [x] B3 POST with validations (ranges, rates, code format, duplicate 409, currency) (RED→GREEN) — POST RED with 405 Method Not Allowed before implementation; focused create/validation/audit test GREEN (1 passed).
- [x] B4 PUT partial update (code immutable, re-validation) + PATCH estado + bitácora (RED→GREEN) — PUT RED with 405 after successful create; update/state/audit test GREEN (1 passed).

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/productos-crediticios`):
- [x] F1 `src/core/api/creditosApi.js` (productos functions)
- [x] F2 `src/modules/admin/pages/CreditosPage.jsx` (today a 5-line placeholder): product catalog table with state filter, create/edit modal with client-side validation mirroring the contract, activate/deactivate with confirmation
- [x] F3 `src/modules/oficial/pages/CreditosPage.jsx` (placeholder): read-only catalog of ACTIVE products (cards or table) with conditions

Orchestrator — Claude:
- [x] V1 Review, checks, E2E through the Vite proxy, commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_productos_credito.py`. Baseline: 86 passed / 5 pre-existing failures (`test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]`, 4 in `test_savings.py`).
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings (baseline on main 2026-09-26).

## Progress
- 2026-09-26: branches created in both repos from main; scope, decisions and contract defined.
- 2026-09-26: delegated direct implementation route selected after mapping; the work spans the migration, model, schemas, router/endpoint, tests, and this task document. Strict TDD runner: `.venv/bin/python -m pytest -q`.
- 2026-09-26: B1–B4 completed with observed RED→GREEN tests. Migration 014 applied twice to local coopDB (`INSERT 0 3`, then `INSERT 0 0`); 3 products and the nullable request FK verified. Final full suite: `5 failed, 91 passed, 2 warnings in 49.76s`; the 5 failures are the documented pre-existing multi-tenant and savings failures. Existing open cash control 406 remained unchanged.
- Next: orchestrator review; the user reviews and commits.
- 2026-09-26 (orchestrator review): scope respected (backend: creditos.py, router, models, schemas, migration 014, tests; frontend: creditosApi.js, admin/oficial CreditosPage, admin/components/creditos; package/lock intact). Backend `pytest -q`: 91 passed / 5 failures identical to baseline (hash). 5 new test functions with 19 status assertions. Frontend build OK, oxlint 8 pre-existing / 0 errors; all UI fields exist in the contract; PUT payload excludes `codigo`.
- E2E through the Vite proxy: admin lists 3 seeded products; create lower-case code → 201 `TEST-E2E` with defaults; duplicate 409; min>max 400; invalid code 400; rate>100 400; PUT partial 200; PUT making plazo_min>plazo_max 400; PATCH INACTIVO → excluded from ?estado=ACTIVO; oficial reads catalog 200; cajero reads detail 200; oficial create 403; superadmin without cooperative 403. E2E product and its audit rows removed.
- Engram mirror: Codex's writes failed with unknown_session; orchestrator mirror saved under project si2_backend_api.
