# Feature: mobile-auth (CU-M1 Iniciar sesión / Autenticación biométrica, CU-M2 Cerrar sesión)

## Objective
Make mobile sign-in and sign-out correct and safe: password login, opt-in biometric unlock that survives app
restarts, session validation/expiry handling, and a real logout.

## Current problems (verified in code, SI2_mobile_app `lib/services/mock_auth_service.dart`, `lib/screens/*`)
- The JWT lives only in memory (`AuthService.tokenJWT`). After an app restart it is null, but
  `authenticateWithBiometrics()` still returns success without a token → the user enters with no session and every
  API call fails.
- Logout (profile screen) only navigates to `/login`: it does not clear the token nor call the backend.
- A 401 (token expired, 8 h lifetime) is shown as an error per screen; the user is never sent back to login.
- Any role can log in, although the app screens call socio-only endpoints (`/socio/*`) → staff users see 403s.

## Backend (no changes needed)
- `POST /api/v1/auth/login {correo, contrasena}` → `access_token` (8 h).
- `GET /api/v1/auth/me` → current user (role name included) — use it to validate a stored token and the role.
- `POST /api/v1/auth/logout` → logs LOGOUT in bitácora.

## Design (mobile)
- Dependency approved by the user (2026-09-29): `flutter_secure_storage` (Keystore/Keychain). No other new deps.
- `SecureTokenStore` abstraction (interface + secure-storage implementation + in-memory fake for tests) storing:
  token, expiry (from JWT `exp` or login time + `expires_in` if provided), correo, `biometria_habilitada`.
- Login with password → `/auth/me`; allowed roles: SOCIO (the app's current features) and OFICIAL_CREDITO
  (future field module M8–M11); any other role → message "Esta aplicación es para socios y oficiales de crédito" and
  no session. Token kept in memory; persisted ONLY if the user has biometrics enabled.
- Opt-in: after a successful password login on a device with biometrics, offer "¿Activar ingreso con huella/Face ID?";
  also a switch in Perfil. Enabling stores the current token; disabling deletes it.
- Biometric login (button visible only when enabled and a stored token exists): native prompt → load stored token →
  if expired locally or `/auth/me` returns 401 → delete stored token, disable biometrics, ask for password with a
  clear message. Never return success without a valid token.
- Central session handling: a single `SessionManager` (or AuthService static API) with `onUnauthorized()` used by the
  existing services (cuentas, transferencias, socio) when they get 401 → clear session and navigate to login via a
  global `navigatorKey` with the message "Tu sesión expiró. Ingresá nuevamente." Keep services' other error handling.
- Logout (CU-M2): confirmation dialog → best-effort `POST /auth/logout` (short timeout; failure does not block) →
  clear memory token and the stored token (biometric enrollment is kept as a preference but it needs a fresh
  password login to store a new token) → `pushNamedAndRemoveUntil('/login')` so Back cannot return.
- Do not log tokens; do not store the password.

## Tasks
- [x] T1 (Copilot): SecureTokenStore + AuthService refactor (login, me/role check, biometric unlock with validation,
      logout) + unit tests with fake store and MockClient.
- [x] T2 (Copilot): global session handling for 401 in existing services + tests.
- [x] T3 (Copilot): Login screen (biometric button state, opt-in prompt, role message), Perfil (biometric switch,
      logout with confirmation) + widget tests.
- [ ] V1 (Orchestrator): flutter analyze/test, backend smoke against /auth/*, commit, RDD.
      Partial: tests/analyze/smoke and commits done; RDD of the correction 26e5c0d is NOT closed
      (stopped terminally with captured_artifacts_unverifiable — see Progress).

## Checks
- `/home/yimy/flutter/bin/flutter test` (baseline 43 passed) and `flutter analyze` (no new errors/warnings).
- Strict TDD (RED observed before GREEN). No git writes by the agent. Do not touch the user's uncommitted files:
  android/app/src/main/AndroidManifest.xml, android/build.gradle.kts, ios/*.
- Platform setup required by flutter_secure_storage (e.g. Android minSdk) must be reported, not silently changed in
  the user's gradle file — ask the orchestrator first.

## Route / trigger evidence
- Mobile delegated direct → Copilot (herdr w2:p6), branch `feat/mobile-auth` from `feat/mobile-socio` (pending
  baseUrl fix committed first as 70c41e0).

## Progress
- 2026-09-29: problems verified in code; dependency approved by the user; contract written.
- 2026-09-29 (Copilot + orchestrator): flutter_secure_storage added (no platform config needed; minSdk unchanged). SecureTokenStore + SessionManager; biometric unlock validates the stored token with /auth/me and never succeeds without a valid token; 401 in cuentas/socio/transferencias services routes to login; logout with confirmation, best-effort POST /auth/logout, clears memory/stored token, pushNamedAndRemoveUntil.
- Critical bug caught by the orchestrator against the real backend: /auth/me returns `rol` as an object ({id, nombre, ...}); the first version compared `rol.toString()` with "SOCIO", so NO user could sign in (tests passed because mocks used a string). Fixed (`rol.nombre`) and all /auth/me mocks now use the captured real payload (contract test added). Backend smoke: login 200, /auth/me 200, logout 200.
- Final: `flutter test` 49 passed; `flutter analyze` 0 errors/warnings. Mobile commits: 70c41e0 (baseUrl --dart-define fix, pending from the earlier review) and the M1/M2 commit.
- 2026-09-29 (RDD): review of 70c41e0+6e0704b required a correction with 7 findings (disabling biometrics killed the live session; transient network errors wiped the stored biometric token (x2); switch shown ON when enabling failed; constructor silently replaced the global session store; storage exceptions could strand login or block logout). Fix 26e5c0d: failure kinds (unauthorized vs network), enableBiometrics returns a result, SessionManager.configure, guarded storage calls, logout always clears memory and navigates. flutter test 54 passed, analyze 0 errors/warnings. Validation then stopped terminally with `captured_artifacts_unverifiable` (the working tree held the user's uncommitted android/ios changes — consistent with the earlier hypothesis); not retried.
- 2026-09-29: user ran out of Copilot credits; mobile work moves to Antigravity from now on.
