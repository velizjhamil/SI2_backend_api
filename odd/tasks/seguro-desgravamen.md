# Seguro de desgravamen en el plan de pagos

Objective: charge and show a real credit-life insurance (seguro de desgravamen) per installment, alongside capital and interest.
Branch: `feat/seguro-desgravamen` in SI2_backend_api, SI2_frontend_web, SI2_mobile_app. Delivery: ask-on-risk. Runner: `pytest` (backend), `npm run build` (web), `flutter test` (mobile).

## Specs

- S1 (from L1): "Demostrar cuanto paga en seguro, capital e interes". Today `app/api/v1/endpoints/socio.py` hardcodes `"seguro": "0.00"`; the value must be real.
- S2 (assumption, Bolivian practice): each credit product has `tasa_seguro_desgravamen` = monthly percentage applied to the installment's opening capital balance (`saldo_inicial`). Default `0`, so existing products and existing schedules keep their current amounts.
- S3: `generar_plan_pagos` adds `seguro` per installment, rounded to cents with ROUND_HALF_UP; `cuota = capital + interes + seguro`; capital and interest stay exactly as today; the result adds `total_seguro` and `total_pagar` includes it.
- S4: schedules persisted at disbursement store `monto_seguro` per row and include it in `monto_cuota_total`; payments record `monto_seguro` in `pago_cuota` (the charged total already uses `monto_cuota_total`).
- S5: every API that returns a schedule or simulation exposes `seguro` per installment and `total_seguro`; the credit product create/update/read API accepts and returns `tasa_seguro_desgravamen` (0 to 5).
- S6: migration `035_seguro_desgravamen.sql`, idempotent, adds the three columns with default 0.
- S7: web shows the field in the product form and a "Seguro" column wherever a payment schedule or simulation is shown; mobile shows the real seguro per installment.

## Tasks

- [x] T1 S2-S6 backend — route: delegated to codex (team split, memory `si2-delegate-to-codex-and-agy`); evidence: no new test failures vs main baseline
- [ ] T2 S7 web — route: delegated to agy
- [ ] T3 S7 mobile — route: delegated to agy

## Log

- L1 (2026-11-12, user): "Demostrar cuanto paga en seguro, capital e interes" … "ok, vamos con esa suena sencilla"
- L2: Payment flow (`creditos.py` ~2540) charges `monto_cuota_total + mora`, so including seguro in the installment total charges it automatically. No double-entry posting exists for payments.
- Risk: item 1 (migration adds persisted columns), item 3 (schedule API shapes).
- L3 (2026-11-12, user): "lo estas haciendo con .py, recuerda que necesito un .sql, para poder hacer correr en supabase" → delivered as `migrations/035_seguro_desgravamen.sql`; `app/db/session.py` mirrors it for local startup.
- L4: Review fixes: reverted unrequested demo schedule rewrite (only adds seguro now); added columns to `init_db`; test fake gained `monto_seguro`. Suite vs main baseline: 0 new failures (132 pre-existing environment failures).
