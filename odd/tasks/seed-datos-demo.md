# Feature: Demo data seed

## Objective
One SQL seed in `migrations/` that fills a database (bd.sql baseline + migrations 002–012 applied) with realistic demo data for every implemented module, safely and repeatably.

## Constraints (user)
- File lives in `migrations/`; must not break anything; reuse existing users; new users use the bcrypt hash from `bd.sql`: `$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2`.

## Decisions (orchestrator)
- File: `migrations/013_seed_datos_demo.sql`, one `BEGIN … COMMIT`.
- **Idempotent**: running it twice leaves the same data (guard every insert with `WHERE NOT EXISTS` on a natural key such as correo, ci, numero, numero_certificado, nombre of caja; or `ON CONFLICT` only on real unique constraints). Resolve ids by natural key (`SELECT id FROM usuario WHERE correo = …`), never hardcode ids.
- **Additive only**: no DELETE, no TRUNCATE, no DDL. The single allowed UPDATE family: assign `cooperativa_id` = the demo cooperative to non-SUPERADMIN users whose `cooperativa_id IS NULL` (`admin@gmail.com`, `acredito@cooperativa.com`, `rcontador@cooperativa.com`, `juan.perez@email.com`), because without it they get 403 in every tenant module; and bump `secuencia_documento` counters when the seed consumes correlatives.
- Target cooperative: `Cooperativa de Prueba SI2` (resolve by name; create it only if missing).
- **Never leave a caja session ABIERTA** and never touch existing `control_caja`/`caja` rows (a user may have a live session). Historical sessions must be CERRADA with coherent arqueo + cierre.
- **Coherent money**: every balance must equal the sum of its seeded movements (account `saldo_disponible` set consistently with the seeded `transaccion` rows; caja theoretical cash = apertura + deposits − withdrawals; arqueo/cierre figures consistent with that). DPF interest must follow the implemented rules (base 360, RC-IVA 13% exempt for BOB ≥ 30 days, `tasa_dpf` band of the cooperative).
- Dates relative to `CURRENT_DATE` so the data stays meaningful (e.g. DPFs maturing in 3 days, one already matured, one far).
- Correlatives (`CUENTA_AHORRO`, `CERTIFICADO_APORTACION`, `DPF`) taken from and advanced in `secuencia_documento`, same format as the app.
- Data in Spanish, Bolivian context (names, CI with extension, cities).

## Content
- 2 extra cajas in the cooperative (e.g. "Ventanilla 3 - Plan 3000", "Ventanilla 4 - Equipetrol").
- ~8 new socios (+ SOCIO users for 2 of them with the bd.sql hash), ACTIVO; 1 INACTIVO.
- Savings accounts VISTA/PROGRAMADO in BOB and USD; certificates of contribution.
- 2–3 closed historical caja sessions (past days) by existing cashiers with deposits, withdrawals (retirante TITULAR/APODERADO), window transfers, one arqueo CUADRADO, one FALTANTE signed by `admin@test.com`, and their cierre sheets.
- UIF declarations: one ≥ 70,000 BOB deposit, one fraccionada.
- DPFs: VIGENTE (far), VIGENTE maturing ≤ 7 days, VIGENTE already matured, LIQUIDADO, RENOVADO→new VIGENTE, CANCELADO; BOB and USD; with `dpf_cronograma` (one MENSUAL).
- A few `solicitud_credito` rows (PENDIENTE) with `evaluacion_campo`, useful for the future CU-W23 (no credit logic implemented).
- Bitácora entries only if natural (optional).

## Tasks
- [x] S1 Write `migrations/013_seed_datos_demo.sql`
- [x] S2 Apply to a throwaway DB built from `bd.sql` + migrations 002–012 twice (idempotent) — create/drop it inside the `coopDB` container (e.g. `seed_check`), never drop `cooperativa_db`
- [x] S3 Apply to local `cooperativa_db` twice
- [x] S4 Consistency queries: each seeded account balance = Σ movements; no `control_caja` left ABIERTA by the seed; secuencias ≥ max used
- [x] S5 Full `pytest -q` with the seed loaded in cooperativa_db: 86 passed / 5 failures, identical set to baseline (orchestrator rerun; the earlier `91 skipped` run was transient).

## Progress
- 2026-09-25: delegated to Codex.
- 2026-09-25 (orchestrator review): no DELETE/TRUNCATE/DDL; only UPDATEs are the agreed tenant assignment and correlative bumps; new users use the bd.sql hash. Full sequence bd.sql + 002–012 (with 008) → 013 twice: identical counts (socio 12, cuenta 13, cert 5, caja 3, control 3, tx 28, arqueo 2, cierre 2, uif 3, dpf 8, crono 9, liq 4, sol 3, usuario 16). Every seeded account balance equals APERTURA + deposits/transfers-in − withdrawals/transfers-out. Closing sheets consistent (theoretical = apertura + deposits − withdrawals; FALTANTE −100 signed). No session left ABIERTA by the seed; the user's live session 406 untouched.
- Known dependency: 013 requires 008 (creates the demo cooperative before 012 seeds DPF bands). Without 008 it aborts atomically with "No hay banda DPF…". Accepted by the user: all migrations 002–013 will be run in order on Supabase for a university demo. Documented in the file header.
