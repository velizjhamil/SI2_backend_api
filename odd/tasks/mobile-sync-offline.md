# Feature: mobile-sync-offline (CU-M11 Sincronización de Datos Offline/Online)
Objective: the loan officer keeps working in the field without connectivity (CU-M8 evaluation, CU-M9 GPS/photos,
CU-M10 prequalification); work is queued on the device and synchronized when the network returns, without duplicates
and with the server as the source of truth. Branch `feat/mobile-sync-offline` in both repos, stacked on
`feat/mobile-precalificacion`. Runners: backend `.venv/bin/python -m pytest -q` (`docker start coopDB`); mobile
`/home/yimy/flutter/bin/flutter test`.

## Specs
- S1 (L1): "CU-M11: Sincronización de Datos Offline/Online" for the mobile version.
- S2 Current state (verified 2026-10-26): everything is online-only. Evaluation save is
  `PUT /creditos/solicitudes/{id}` {evaluacion} (overwrite; 409 "La solicitud ya no se puede modificar" when the
  request left an editable state). Photo upload `POST .../evaluacion/fotos` is NOT idempotent (random uuid key), so a
  retry after a lost response duplicates the photo. Prequalification `POST /creditos/precalificacion` persists nothing
  (bitácora only). Mobile already depends on `path_provider`; no local DB or connectivity package.
- S3 Backend — idempotent photo upload: optional header `X-Client-Id` (UUID v4). When present the object key is
  `{cooperativa_id}/{solicitud_id}/{client_id}.{ext}`; if that key is already in the evaluation's photo list, return
  200 with the current list, without uploading again and without a new bitácora entry. Invalid UUID → 422. Without the
  header, behavior is unchanged (random key, 201). All existing checks (role, tenant, in-progress, cap of 5, type,
  size) still run first, except that the idempotent replay of an already-stored key never hits the cap.
- S4 Mobile — offline queue (no new dependencies; JSON files under the app documents directory via `path_provider`):
  - Read cache: the officer's worklist, each ficha-campo opened, active socios and products are cached when fetched;
    when a request fails for network reasons (SocketException/TimeoutException/ClientException) the cached copy is
    shown with an "Sin conexión — datos guardados el <fecha>" banner.
  - Write queue (FIFO, persisted, survives app restart): evaluation save, photo upload (bytes stored as a file in the
    app directory, each with a client UUID sent as `X-Client-Id`), photo delete, and prequalification requests.
    On network failure the action is queued and the UI shows it as "Pendiente de sincronizar".
  - Sync: automatic attempt when the Campo module opens/resumes and after any successful request, plus a manual
    "Sincronizar ahora" button with a badge of pending items. Items are sent in order; a photo for a request whose
    evaluation save is still pending waits for it.
  - Results: 2xx → removed from the queue (prequalification results stored and shown as completed); 409/422/404/403
    → moved to a "Con conflicto" list with the server `detail` (server wins; the user can discard it); network failure
    → stays queued; 401 → stop and ask to sign in again. Never drop an item silently.
  - Queue items are scoped to the signed-in user; logout keeps them but another user never sees or sends them.
  - Tests with injectable clock/storage/HTTP: queue persistence, ordering, replay idempotency (X-Client-Id), conflict
    handling, offline banner. Fixtures in `test/fixtures/m11/`.

## Tasks
| ID | Specs | Route | Status | Commit |
|---|---|---|---|---|
| B1 | S2–S3 | Codex (wB:p2) | done | see git log feat/mobile-sync-offline |
| M1 | S4 | Antigravity (wB:p3), in parallel (contract fixed in S3) | in progress | — |
| V1 | S3 | Orchestrator: focused + full suite vs baseline, E2E + fixtures | done | same commit |
| V2 | S1–S4 | Orchestrator: flutter test/analyze, verifier, assess/review | pending | — |

## Log
- L1 (2026-10-26, user): "si, vamos con ese" — in reply to "¿Seguimos con el CU-M11 (sincronización offline/online)?"
- 2026-10-26: design chosen by the orchestrator (no new dependencies; server wins; X-Client-Id idempotency);
  branches created; B1 and M1 dispatched in parallel.
- 2026-10-26 B1/V1: tests/test_solicitud_credito.py 39 passed; full suite failure set unchanged (82 failed incl.
  data-dependent payment flow, 513 passed). E2E real DB (faked storage, solicitud 3 restored): first upload with
  X-Client-Id 201, replay 200 with the same list, 1 storage upload, bitácora +1; invalid id 422; evaluation PUT on
  APROBADA solicitud 66 -> 409 "La solicitud ya no se puede modificar". Fixtures -> mobile test/fixtures/m11.
  Risk: none (optional header; no-header behavior pinned by existing tests; replay serialized by the row lock).
