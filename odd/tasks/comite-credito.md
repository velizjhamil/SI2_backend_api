# Feature: Credit committee approval (CU-W25)

Route agreed with the user on 2026-09-27: **W25** → W28 → W22 → W18, same strategy (Codex backend, Antigravity frontend, orchestrator contract/review/E2E/commits), continuing without pauses.

## User story (fictional, drafted by the orchestrator)
**CU-W25 Aprobación de Créditos en Comité** — Actors: members of the credit committee (Administrador, Oficial de crédito, Contador of the cooperative).
> Como miembro del comité de créditos quiero revisar las solicitudes derivadas al comité, con su dictamen y puntaje, y votar su aprobación, rechazo u observación, para que la decisión sea colegiada, trazable y quede registrada en un acta.

Acceptance criteria:
1. Requests reach the committee automatically after the CU-W23 evaluation when the amount exceeds the product's direct-approval limit, or when the dictamen is REVISION_MANUAL.
2. The committee queue shows each request with socio, product, amount, term, score, dictamen, knock-outs, model risk (informative) and the explanation.
3. Each eligible member casts one vote (APROBAR / RECHAZAR / OBSERVAR) with a mandatory comment; the loan officer who registered the request cannot vote on it.
4. With a quorum of 3 votes the request is resolved by simple majority: APROBAR → APROBADO; RECHAZAR → RECHAZADO; otherwise (OBSERVAR majority or no majority) → OBSERVADA, which returns it to the officer for correction and re-evaluation.
5. A printable committee record (acta) lists the request data, dictamen, every vote with member, date and comment, and the result. Audited.

## Current state (2026-09-27)
- CU-W23 (`evaluar_solicitud_credito`, `resolver_solicitud_crediticia` in `app/api/v1/endpoints/creditos.py`): dictamen APROBADO → request APROBADO; RECHAZADO → RECHAZADO; REVISION_MANUAL → EN_EVALUACION, resolved by a single ADMINISTRADOR (`POST /creditos/solicitudes/{id}/resolucion`).
- OBSERVADA is already an editable/evaluable state from CU-W21/W23.

## Decisions (orchestrator)
- New request state **`EN_COMITE`**. Routing after evaluation (replaces the W23 REVISION_MANUAL routing):
  - dictamen RECHAZADO → RECHAZADO (unchanged);
  - dictamen APROBADO and `monto ≤ producto.monto_aprobacion_directa` → APROBADO (delegated approval, unchanged for small amounts);
  - dictamen APROBADO above the limit, or REVISION_MANUAL → **EN_COMITE**.
- `producto_credito.monto_aprobacion_directa NUMERIC(14,2) NOT NULL DEFAULT 0` (0 = every approved request goes to committee). Migration sets demo values: `CONS-BOB` 20000, `MICRO-BOB` 30000, `VIV-USD` 0. Editable through the CU-W20 product endpoints/UI (additive field).
- **Breaking change (documented):** new REVISION_MANUAL requests no longer go to EN_EVALUACION; the single-admin `resolucion` endpoint stays only for legacy requests already in EN_EVALUACION (unchanged behavior for them) and returns 409 "La solicitud se resuelve en comité" for EN_COMITE. Update only the W23 tests affected by this routing change.
- Eligible voters: users of the request's cooperative with role ADMINISTRADOR, OFICIAL_CREDITO or CONTADOR, excluding the request's registering officer (`solicitud_credito.usuario_id`). One vote per member per committee round; a vote cannot be changed.
- Quorum 3 (constant). Resolution happens on the vote that completes the quorum, by simple majority of those 3 votes (2 of 3). No majority (1/1/1) → OBSERVADA.
- A request that returns to EN_COMITE after OBSERVADA + re-evaluation starts a **new round** (`ronda` increments); previous rounds stay in the acta history.
- Bitácora `CREDITOS`: `DERIVAR_COMITE`, `VOTAR_COMITE`, `RESOLVER_COMITE`.

## Migration `migrations/020_sprint9_comite_credito.sql` (idempotent; do not modify 002–019)
- `producto_credito` add `monto_aprobacion_directa NUMERIC(14,2) NOT NULL DEFAULT 0` + demo values above.
- `voto_comite(id BIGSERIAL PK, solicitud_credito_id INT NOT NULL REFERENCES solicitud_credito(id), cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id), ronda INT NOT NULL, usuario_id BIGINT NOT NULL REFERENCES usuario(id), voto VARCHAR(10) NOT NULL CHECK (voto IN ('APROBAR','RECHAZAR','OBSERVAR')), comentario TEXT NOT NULL, fecha TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (solicitud_credito_id, ronda, usuario_id))`.
- `solicitud_credito` add `ronda_comite INT NOT NULL DEFAULT 0`, `resultado_comite VARCHAR(12) NULL`, `fecha_resolucion_comite TIMESTAMPTZ NULL`.

## API contract (backend and frontend MUST follow exactly)
Base `/api/v1/creditos`. Errors `{"detail": "..."}`.

`VotoOut` = `{id, ronda, usuario: {id, nombre, rol}, voto, comentario, fecha}`.
`ComiteItemOut` = `{solicitud: SolicitudOut, evaluacion: EvaluacionCrediticiaOut | null, ronda, votos: [VotoOut], votos_requeridos: 3, puede_votar: bool, motivo_no_puede_votar: string | null}`.
`ActaOut` = `{solicitud: SolicitudOut, rondas: [{ronda, votos: [VotoOut], resultado | null, fecha_resolucion | null}], evaluacion: EvaluacionCrediticiaOut | null, cooperativa: {id, nombre}}`.

1. `GET /creditos/comite` → `[ComiteItemOut]` requests EN_COMITE of the caller's cooperative, oldest first (cooperative staff; `puede_votar` computed for the caller).
2. `POST /creditos/comite/{solicitud_id}/votos` body `{voto, comentario}` (comentario ≥ 10 chars) → `201 ComiteItemOut` after the vote (if this vote completes the quorum the request is already resolved and the item shows the final state). 403 not an eligible role or registering officer; 409 not EN_COMITE / already voted this round; 400 invalid vote/comment.
3. `GET /creditos/solicitudes/{id}/acta-comite` → `ActaOut`; 404 outside cooperative; 409 if the request never reached committee.
4. Additive: `ProductoCreditoOut` + `monto_aprobacion_directa` (and writable in POST/PUT of CU-W20); `SolicitudOut` + `ronda_comite`, `resultado_comite`.
5. Changed: CU-W23 evaluation routing as described; `POST /creditos/solicitudes/{id}/resolucion` only for legacy EN_EVALUACION.

## Tasks
Backend — Codex (`SI2_backend_api`, branch `feat/comite-credito`):
- [x] B1 Migration 020 applied twice; models/schemas; product field in W20 endpoints
- [x] B2 Evaluation routing (direct limit, REVISION_MANUAL → EN_COMITE) + legacy resolution guard; update only affected W23 tests (RED→GREEN; see TDD chronology note)
- [x] B3 Committee queue, voting rules (eligibility, one vote, conflict of interest, quorum, majority, OBSERVADA, rounds) (RED→GREEN; see TDD chronology note)
- [x] B4 Acta endpoint + audit (RED→GREEN; see TDD chronology note)

Frontend — Antigravity (`SI2_frontend_web`, branch `feat/comite-credito`):
- [x] F1 `creditosApi.js`: add `listarComite`, `votarComite`, `getActaComite` (add only)
- [x] F2 New `ComitePage.jsx` for `/admin/comite`, `/oficial/comite` and `/contador/comite` + nav item "Comité" in the three layouts: queue cards with score/dictamen/risk/explanation, vote form (disabled with reason when `puede_votar` is false), vote progress (x/3)
- [x] F3 Printable acta (window.print()) from the queue and from the request detail when `ronda_comite > 0`
- [x] F4 Product form (admin CreditosPage) gains `monto_aprobacion_directa`; request detail shows EN_COMITE / result badges

Orchestrator — Claude:
- [x] V1 Review, E2E (small approved → APROBADO directly; above limit → committee → votes → APROBADO; 1/1/1 → OBSERVADA → re-evaluate → round 2; registering officer 403; acta), commits

## Checks
- Backend TDD strict: `.venv/bin/python -m pytest -q`; new tests `tests/test_comite_credito.py`. Baseline 228 passed / 5 pre-existing failures. No leftover test cooperatives (verify before finishing).
- Frontend: `pnpm build` OK; oxlint 0 errors, 8 pre-existing warnings. The 5 deleted files under `stitch_nexacoop_frontend_redesign/` are the user's uncommitted change: do not touch or stage them.

## Progress
- 2026-09-27: branches `feat/comite-credito` created from `main`; decisions and contract defined; delegated.
- 2026-09-27: Backend route is delegated direct for B1–B4 after read-only mapping across migration, ORM, schemas, product CRUD, W23 evaluation/resolution, audit and tests (4+ file mapping and multi-file writer triggers). Strict TDD is explicitly required by the user; runner `.venv/bin/python -m pytest`, base reported as 228 passed / 5 pre-existing failures. Forecast ~900–1,300 authored changed lines; delivery strategy `ask-on-risk`. No work-unit commits or Git writes will be attempted under the user's forbidden-Git rule; the orchestrator will review and commit.
- Mapping decision before implementation: the existing one-open-request partial unique index and runtime duplicate-request guards omit `EN_COMITE`; migration 020 and guards must include it to prevent a second active request while a request awaits committee. Committee queue remains readable by cooperative staff; voting uses the three enumerated roles with the registering officer excluded. Acta rounds will be ascending and votes chronological; a quorum-completing vote response remains a `ComiteItemOut` with the resolved request and `puede_votar=false`.
- 2026-09-27: Implemented migration 020, committee ORM/schema/API, direct-approval threshold, committee routing, legacy resolution guard, lock-serialized voting, tenant-paired foreign keys, acta history, and the three audit actions. Existing W23 fixture sets `monto_aprobacion_directa=2000.00` so its APROBADO sample remains a direct approval. The only existing W23 tests changed were `test_manual_evaluation_can_be_re_evaluated_and_history_is_newest_first` (new requests enter EN_COMITE; a committee observation returns them to OBSERVADA before re-evaluation) and the legacy manual-resolution tests `test_admin_resolves_manual_review_with_justification_and_audit` / `test_resolution_rejects_wrong_role_tenant_short_reason_and_non_manual_state` (explicitly restore EN_EVALUACION to model pre-migration legacy rows).
- TDD chronology note: Before implementation, B1 contract tests recorded 3 RED failures, then GREEN. The direct-limit helper (B2), committee routes (B3), quorum reducer (B3), and acta route registration (B4) each had RED/GREEN evidence. The PostgreSQL-down phase skipped the DB integration tests, so the first executable end-to-end coverage came only after implementation; no chronological RED is claimed for those initial behaviors. After PostgreSQL was started, the committee integration exposed a final-vote response failure: committee code populated `EvaluacionCrediticia.resolucion` without its paired `resolucion_usuario`/justification/date fields, and the serializer dereferenced a null user. The corrective test run was RED then GREEN after stopping this unrelated legacy-evaluation mutation; committee result remains on the request and is audited. Also corrected the test fixture to set required `EvaluacionCampo.fecha` after its first live DB run surfaced that fixture error.
- Verification: Migration 020 applied twice successfully. Threshold seed values read back as CONS-BOB 20000.00, MICRO-BOB 30000.00, VIV-USD 0.00. Focused `.venv/bin/python -m pytest tests/test_comite_credito.py tests/test_scoring_crediticio.py tests/test_productos_credito.py -q`: 66 passed. Full `.venv/bin/python -m pytest -q`: 237 passed, 5 failed; failures are exactly the known baseline (`tests/test_multi_tenant.py::test_roles_de_cooperativa_sin_acceso_a_tenants[credenciales0]` and four `tests/test_savings.py` cases: `test_registrar_socio_y_abrir_cuenta`, `test_emitir_y_listar_certificado_aportacion`, `test_deposito_retiro_y_saldo_insuficiente`, `test_tenant_no_puede_abrir_cuenta_para_socio_ajeno`). `git diff --check` and `compileall` passed. DB hygiene query after tests found only `Cooperativa de Prueba SI2`, zero committee test requests/votes, and `control_caja.id=406` still exists; no mutation was made to that row. No product/spec contradiction was found; kept the written routing and legacy-resolution policy unchanged.
- 2026-09-27 verifier follow-up: Independent read-only verification found `EN_COMITE` absent from the GET `/solicitudes?estado=` allowlist and migration replay reseeding demo thresholds over admin edits. Added focused tests first: list filter observed RED (400 invalid state); migration regression test simulated a fresh column inside a rolled-back DB transaction, confirmed initial demo seeds, then observed RED when the replay reset a custom CONS-BOB threshold to 20000.00. Fixed by allowing EN_COMITE in `ESTADOS_SOLICITUD` and moving threshold seeding into the `monto_aprobacion_directa`-column-not-yet-present branch. Re-runs GREEN: both focused RED tests pass; full committee/scoring/product command now reports 68 passed. Migration test performs fresh-column scenario in a transaction and rolls back; committed coopDB thresholds remain 20000.00 / 30000.00 / 0.00.
- 2026-09-27 final verification after verifier fixes: `.venv/bin/python -m pytest -q`: 239 passed, 5 failed, same known baseline failures listed above; no new failures. `git diff --check` and `compileall` passed. Post-suite hygiene: only Cooperativa de Prueba SI2 remains; zero Committee Test requests and votes; control_caja id 406 still exists; demo thresholds retain their expected values. No extra W23 tests were changed for the verifier follow-up.
- No Git writes, commits, native review/RDD, network, requirements edits, or access to `control_caja.id=406` were performed. `skill_resolution: fallback-path`.
- Parent final spot-check after the filter/migration replay fixes: `.venv/bin/python -m pytest -q` → `5 failed, 239 passed, 2 warnings in 115.94s (0:01:55)`; the same five documented baseline failures. Reapplied the final migration 020 twice to local coopDB (`migration020_pass_1=ok`, `migration020_pass_2=ok`), with zero committee votes afterward. Final DB query: only `1:Cooperativa de Prueba SI2`, zero test cooperatives and Committee Test requests; `control_caja.id=406` remains `ABIERTA`. `git diff --check` passed.
- 2026-09-27 (orchestrator review): scope respected on both sides (W23 test file only gained tests; frontend creditosApi additions only, 3 routes + 3 nav items, committee page/components, product field; user's stitch deletions untouched; package/lock intact). Backend `pytest -q`: 239 passed / 5 failures identical to baseline (hash); no leaks. TDD caveat reported by Codex: coopDB was stopped at the start, so the first integration RED runs were skipped — documented, not claimed. Frontend build OK, oxlint 8 pre-existing / 0 errors.
- E2E through the Vite proxy (tokens minted with the backend's create_access_token for 6 real users of cooperative 1): 10000 ≤ 20000 → APROBADO directly (round 0); 25000 → score 830 APROBADO → EN_COMITE round 1; registering officer can_vote false + vote 403; cashier 403; short comment 400; legacy resolucion on EN_COMITE 409; votes APROBAR/RECHAZAR/APROBAR → APROBADO (resultado_comite APROBADO); repeated vote 409; vote after resolution 409; APROBAR/RECHAZAR/OBSERVAR → OBSERVADA; re-evaluation → EN_COMITE round 2; acta lists r1 OBSERVADA (3 votes) + r2 in progress; acta of a request never in committee 409. E2E rows removed, correlative restored.
