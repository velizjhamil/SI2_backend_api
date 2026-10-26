# Feature: mobile-fotos-gps (CU-M9 Captura de Fotos y Georreferenciación GPS)
Objective: during the field evaluation (CU-M8) the loan officer captures the GPS position of the visit and backup
photos from the phone. Online only (offline is CU-M11). Branch `feat/mobile-fotos-gps` (backend and mobile, from main).
Delivery: one commit per side. Runners: backend `.venv/bin/python -m pytest -q`; mobile `/home/yimy/flutter/bin/flutter test`.

## Specs
- S1 (L1): "CU-M9: Captura de Fotos y Georreferenciación GPS" for the mobile version.
- S2 Current state (verified 2026-10-26): `evaluacion_campo.coordenadas` String(100) and `fotografias_respaldo` Text
  exist in the model; `coordenadas` is accepted as a free string in `EvaluacionCreditoCreate/Update` and returned in
  `EvaluacionOut` and ficha-campo; `fotografias_respaldo` is never written or returned. `SupabaseStorage`
  (app/services/backups.py) already uploads and signs objects. Mobile `pubspec.yaml` already declares `camera` and
  `geolocator` (unused); AndroidManifest has no camera/location permissions.
- S3 Coordinates (backend): on evaluation create/update, `coordenadas` must be `"lat,lng"` decimal degrees with
  -90 ≤ lat ≤ 90 and -180 ≤ lng ≤ 180 (null still allowed); otherwise 422. Normalize to max 6 decimals.
- S4 Photos (backend), roles OFICIAL_CREDITO and ADMINISTRADOR, tenant-scoped (404 for foreign requests):
  - `POST /creditos/solicitudes/{id}/evaluacion/fotos` multipart field `archivo`: JPEG or PNG only (check content
    type and magic bytes), ≤ 5 MB, at most 5 photos per evaluation; 422 when the request has no evaluation yet,
    415 wrong type, 413 too large, 409 limit reached. Upload to a Supabase bucket (new setting, default
    `evaluaciones`) under `{cooperativa_id}/{solicitud_id}/{uuid}.{ext}`; store the object paths as a JSON list in
    `fotografias_respaldo`. Returns the photo list.
  - `GET .../evaluacion/fotos` → `[{id (object name), url (signed, short-lived), subida_en?}]`.
  - `DELETE .../evaluacion/fotos/{foto_id}` → removes from storage and list; 404 if unknown.
  - Do not allow photo changes when the request is no longer in progress (reuse the existing in-progress constant).
  - ficha-campo `evaluacion` and `EvaluacionOut` add `fotografias: [{id, url}]` (empty list when none).
  - Storage is injected/mocked in tests (no real network). Bitácora entry on upload/delete following existing pattern.
- S5 Mobile (OFICIAL_CREDITO Campo module from CU-M8):
  - Evaluation form: "Capturar ubicación" button using `geolocator` (request permission; show lat/lng and accuracy;
    clear errors for denied/disabled GPS); sends `coordenadas` as `"lat,lng"`.
  - Photos section: take photo with the camera (or gallery if already a dependency), compress/resize before upload
    (≤ 5 MB), upload via the S4 endpoint, thumbnail grid from signed URLs, delete with confirmation, max 5.
  - Photos only enabled after the evaluation exists (S4 returns 422 otherwise) — show that hint.
  - Android: add CAMERA, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION permissions; iOS: NSCameraUsageDescription,
    NSLocationWhenInUseUsageDescription (user authorized on 2026-10-26: AndroidManifest.xml and Info.plist, permissions only).
  - Tests use captured real payloads vendored into `test/fixtures/m9/` (relative paths); GPS/camera behind an
    injectable interface so widget tests do not hit plugins.

## Tasks
| ID | Specs | Route | Status | Commit |
|---|---|---|---|---|
| B1 | S2–S4 | Codex (wB:p2) | done (4 corrections) | see git log feat/mobile-fotos-gps |
| V1 | S3–S4 | Orchestrator: suite, E2E, payload capture + independent verifier (risk 1/3/4) | done: PASS S2–S4 | same commit |
| M1 | S5 | Antigravity (wB:p3); fixtures in SI2_mobile_app/test/fixtures/m9 | in progress | — |
| V2 | S1–S5 | Orchestrator: flutter test/analyze, review | pending | — |

## Log
- L1 (2026-10-26, user): "mejor, no hagamos nada de momento y enfoquemonos en los CU que te pase antes de este, y si
  arranquemos con el CU-M9, en donde recuerda que tienes a codex y agy"
- 2026-10-26: CU-M7 V1 verification left pending by user decision. Contract written; B1 dispatched to Codex.
- 2026-10-26: Codex's sandbox rejected branch creation; orchestrator created `feat/mobile-fotos-gps` and re-dispatched.
- 2026-10-26 V1 progress: local DB (coopDB) was down; started it. Baseline main full suite 81 failed / 482 passed
  (pre-existing: caja, arqueo, retiros, transferencias, multi_tenant, dpf, uif, savings, intereses_dpf, auth); branch
  has the same failure set + scripts/test_payment_flow.py, which also fails on main now (data-dependent). Review fixes
  sent to Codex: W21 serialization must not call storage; image content type on upload. E2E blocker: persisted
  fotografias_respaldo is legacy comma-separated text (bd.sql:402/1778, migration 013) -> ficha-campo 500; fix sent.
  Risk: item 1 (reads an existing persisted format) and item 4 (row lock) -> independent verifier before commit.
- 2026-10-26 E2E after correction 3 (real DB, faked storage, solicitud/evaluacion 3, restored after): legacy
  ficha-campo 200 (url null), upload 201, gif 415, list 200, delete 200, unknown 404, coords 95,.. 422. Coordinate
  save on solicitud 3 returns pre-existing 400 "Debe seleccionar un producto crediticio" (demo row without product).
  Focused: test_solicitud_credito 33 passed, test_backups 21 passed. Payloads vendored to mobile test/fixtures/m9.
- 2026-10-26 Independent verifier: PASS S2–S4, no blockers (roles 403, foreign 404, 4-thread race kept the cap at
  5, legacy rows untouched on read). Advisories fixed by Codex: signing failure on upload degrades to url null;
  failed upload commit rolls back + best-effort deletes the object; storage delete after DB commit. Final focused
  run (parent spot check): 57 passed. Note for M1: multipart must send image/jpeg or image/png (octet-stream -> 415).
- 2026-10-26 (user): "ok" — authorizes agy to edit AndroidManifest.xml and Info.plist for camera/location permissions only.
