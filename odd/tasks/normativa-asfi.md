# Normativa boliviana: tipos de crédito, VIS, TEAC, reclamos y reprogramación

Objective: cover the Bolivian (ASFI / Ley 393) features the reviewers asked for: credit types, social housing (VIS), payment breakdown with TEA/TEAC, customer claims with ASFI escalation, and credit rescheduling.
Branch: `feat/normativa-asfi` in SI2_backend_api, SI2_frontend_web, SI2_mobile_app. Delivery: ask-on-risk. Runner: `pytest` (backend), `npm run build` + `npm run lint` (web), `flutter analyze` + `flutter test` (mobile).
Migrations are `.sql` files in `migrations/` (run in Supabase) and are mirrored in `app/db/session.py` `init_db()` for local startup.

## Specs

- S1 Credit types (from L1 "tipos de creditos"). `tipo_credito_asfi` accepts `EMPRESARIAL`, `PYME`, `MICROCREDITO`, `MICROCREDITO_AGROPECUARIO`, `CONSUMO`, `VIVIENDA_HIPOTECARIA`, `VIVIENDA_SIN_GARANTIA`, `VIVIENDA_SOCIAL` (model check constraint, create/update schemas, migration `037_tipos_credito_asfi.sql`). ASFI portfolio rules in `app/services/informes_asfi.py`:
  - `VIVIENDA_SOCIAL` uses the VIVIENDA arrears schedule, the `VIVIENDA_HIPOTECARIA` specific-reserve column and the VIVIENDA cyclical reserve.
  - Assumption: `EMPRESARIAL` and `PYME` (classified by days in arrears) use the MICROCREDITO arrears schedule, the MICRO specific-reserve columns (productive/non-productive in MN, MICRO in ME) and the MICRO cyclical reserve.
- S2 Social housing (from L1 "algun procedimiento con viviendas sociales"; values from DS 1842, to be confirmed by the user):
  - A product with `tipo_credito_asfi = VIVIENDA_SOCIAL` cannot have `tasa_interes_anual` above 6.50: create/update answer 400 `"La tasa de un producto de vivienda de interés social no puede superar 6.50% anual"`.
  - A credit request (staff `POST/PUT /creditos/solicitudes`, mobile `POST /socio/solicitudes`) for a VIS product requires `vivienda_social: {valor_vivienda: Decimal > 0 (Bs), tipo_inmueble: "CASA" | "DEPARTAMENTO", unica_vivienda: bool}`; missing → 400 `"La solicitud de vivienda de interés social requiere los datos de la vivienda"`. Requests for other products ignore it.
  - `unica_vivienda` must be true → else 400 `"La vivienda de interés social debe ser la única vivienda del socio"`.
  - `valor_ufv = valor_vivienda / UFV_BOB` (setting `UFV_BOB`, assumption default `2.60`). Limits: CASA ≤ 460000 UFV, DEPARTAMENTO ≤ 400000 UFV → else 400 `"El valor de la vivienda supera el límite de vivienda de interés social"`.
  - Maximum rate: ≤ 255000 UFV → 5.50; ≤ 380000 UFV → 6.00; otherwise 6.50. Request rate above the maximum → 400 `"La tasa del producto supera la tasa máxima de vivienda de interés social (X% anual)"`.
  - Stored in `solicitud_credito.datos_declarados["vivienda_social"]` with `valor_vivienda`, `tipo_inmueble`, `unica_vivienda`, `valor_ufv`, `tasa_maxima` (no migration). Returned in the request detail.
- S3 Payment breakdown (from L1 "cuanto paga en seguro, capital e interes"): already returns `capital`, `interes`, `seguro` per installment. `generar_plan_pagos` adds `tea` = ((1 + tasa/1200)^12 − 1)·100 and `teac` = annualized internal rate of return of `−monto` followed by every installment total (seguro included), both percentages with 2 decimals. Every schedule/simulation response exposes them (`PlanPagosOut` and simulations). Web and mobile show capital, interés, seguro columns plus TEA and TEAC.
- S4 Claims (from L1 "apelaciones del cliente"; Ley 393 Punto de Reclamo, 5 business days, second instance ASFI Defensoría del Consumidor Financiero). New table `reclamo` (migration `038_reclamos.sql`):
  - Fields: `id`, `cooperativa_id`, `socio_id`, `credito_id?`, `numero_reclamo` (`PR-YYYY-NNNNNN`, sequential per cooperative and year), `tipo` (`CREDITO`, `AHORRO`, `CAJA`, `COBRO_INDEBIDO`, `ATENCION`, `OTRO`), `descripcion` (1–2000), `canal` (`MOVIL`, `VENTANILLA`), `estado` (`REGISTRADO`, `EN_ATENCION`, `RESPONDIDO`, `CERRADO`, `ESCALADO_ASFI`), `fecha_registro`, `fecha_limite_respuesta` (registration date + 5 business days, Mon–Fri; holidays not considered: assumption), `usuario_atencion_id?`, `respuesta?`, `procedente?`, `fecha_respuesta?`, `conforme?`, `motivo_disconformidad?`, `fecha_cierre?`, `fecha_escalamiento?`.
  - Output adds `vencido` (not answered and today > limit), `socio` (full name), `socio_id`.
  - Socio (mobile, `get_current_socio`): `POST /socio/reclamos {tipo, descripcion, credito_id?}` → 201, canal MOVIL, credit must belong to the socio (else 404); `GET /socio/reclamos`; `POST /socio/reclamos/{id}/conformidad {conforme: bool, motivo?: str}`: conforme=true needs RESPONDIDO → CERRADO; conforme=false needs motivo and (RESPONDIDO or vencido) → ESCALADO_ASFI; otherwise 409.
  - Staff (`ADMINISTRADOR`, `OFICIAL_CREDITO`, `ASESOR_CREDITO`, `CAJERO`, `CONTADOR`): `GET /reclamos?estado=&vencido=`, `GET /reclamos/{id}`, `POST /reclamos {socio_id, tipo, descripcion, credito_id?}` (canal VENTANILLA), `POST /reclamos/{id}/atender` (REGISTRADO → EN_ATENCION, sets usuario_atencion_id), `POST /reclamos/{id}/responder {respuesta, procedente: bool}` (REGISTRADO|EN_ATENCION → RESPONDIDO). Wrong state → 409. Tenant isolation on every query. Every mutation is written to the bitácora.
- S5 Rescheduling (from L1 "hay reprogramacion de creditos o no"). New table `reprogramacion_credito` and column `credito.numero_reprogramaciones INT NOT NULL DEFAULT 0` (migration `039_reprogramacion_credito.sql`):
  - Fields: `id`, `cooperativa_id`, `credito_id`, `estado` (`SOLICITADA`, `APROBADA`, `RECHAZADA`), `canal` (`MOVIL`, `VENTANILLA`), `motivo`, `nuevo_plazo_meses`, `saldo_capital`, `interes_diferido`, `mora_condonada`, `plazo_anterior`, `cuotas_reprogramadas`, `motivo_rechazo?`, `usuario_solicitud_id?`, `usuario_resolucion_id?`, `fecha_solicitud`, `fecha_resolucion?`.
  - Request: staff `POST /creditos/creditos/{credito_id}/reprogramaciones {motivo, nuevo_plazo_meses}` (OFICIAL_CREDITO, ADMINISTRADOR) or socio `POST /socio/creditos/{credito_id}/reprogramaciones` (own credit). Credit must be VIGENTE with `saldo_pendiente > 0`, no other SOLICITADA (409), `1 ≤ nuevo_plazo_meses ≤ product.plazo_max_meses` (400).
  - Preview: `POST /creditos/creditos/{credito_id}/reprogramaciones/simulacion {nuevo_plazo_meses}` returns the new plan (same shape as `PlanPagosOut`) plus `saldo_capital`, `interes_diferido`, `mora_condonada`.
  - List: `GET /creditos/reprogramaciones?estado=`, `GET /creditos/creditos/{credito_id}/reprogramaciones`, socio `GET /socio/creditos/{credito_id}/reprogramaciones`.
  - Approve `POST /creditos/reprogramaciones/{id}/aprobar` (ADMINISTRADOR, OFICIAL_CREDITO): every unpaid installment becomes `estado_pago = "REPROGRAMADA"`; the new schedule starts from `saldo_pendiente` with the credit rate, amortization type and product insurance rate, disbursement date = today, numbering continues after the last installment; `interes_diferido` = interest of overdue unpaid installments, added to the first new installment interest and total (never capitalized: ASFI forbids capitalizing interest); `mora_condonada` = accrued late fee at approval, recorded only; `credito.plazo_meses` = number of non-rescheduled installments; `numero_reprogramaciones += 1`. Reject `POST /creditos/reprogramaciones/{id}/rechazar {motivo}`. Non-SOLICITADA → 409.
  - `REPROGRAMADA` installments are closed everywhere: payment selection, debt, arrears, alerts, notifications, socio schedule summaries.
  - Credit detail and ASFI portfolio report expose `numero_reprogramaciones` / `reprogramado`.
- S6 Web (agy): product form offers the new types; VIS fields in the request form when the product is VIS; TEA/TEAC beside every schedule; Claims page (list, filters, register, attend, respond); Rescheduling section in the credit detail (preview, request, approve/reject, history) and a pending-rescheduling list; `REPROGRAMADA` installments shown as such.
- S7 Mobile (agy): VIS fields in the request form; TEA/TEAC in the schedule; Claims screen (create, list, accept/escalate); request rescheduling from the credit detail and see its history; `REPROGRAMADA` installments shown as such.

## Tasks

- [x] T1 S1-S3 backend types + VIS + TEA/TEAC — route: inline (parent); commit ccce9c2; RED 28 → GREEN 33
- [x] T2 S4 backend claims — route: inline (parent); commit 820bd1f; 12 tests GREEN
- [ ] T3 S5 backend rescheduling — route: inline (parent); Risk item 1 (changes stored installment state); RED (collection error) → 7 GREEN; full-suite comparison running
- [ ] T4 S6 web — route: delegated to agy (team split, memory `si2-delegate-to-codex-and-agy`), from the contracts above; in progress
- [ ] T5 S7 mobile — route: delegated to agy, after T4

## Log

- L1 (2026-11-21, user): "evaluemos lo siguiente, me dijeron que vea en el contexto boliviano: * hay reprogramacion de creditos o no * apelaciones del cliente * algun procedimiento con viviendas sociales * me dijeron que muestre cuanto paga en seguro, capital e interes * tipos de creditos" … "si, tengo que implementar todas" … "quiero que sepas que como ayudante tienes a agy, via herdrcli, entonces arranquemos y haz lo que debas"
- L2: Payments close a whole installment (`creditos.py` cobrar_cuota sets PAGADA and lowers `saldo_pendiente` by its capital), so `saldo_pendiente` is the unpaid capital and unpaid installments have no payments; rescheduling marks them REPROGRAMADA instead of deleting (alertas reference installment ids).
- L3: Codex is not running in Herdr this session; backend is written inline by the parent, web/mobile go to agy.
- L4: Baseline on main (isolated test DB): `test_cobro_cuotas` debt-route 2 tests and `test_desembolso_credito` seed/attribution 2 tests already fail on main (environment data); not caused by this feature.
- L5: T1 updated two existing tests whose expectation the request changes: `EMPRESARIAL` was asserted invalid (now `LEASING`), and the plan preview key set now includes `tea`, `teac`.
- L6: T2 tests were written before the endpoints, but the model was added before the first run, so RED was not observed separately for T2.
- L7: `REPROGRAMADA` is closed through `CUOTAS_CERRADAS` (`app/services/cobro_cuotas.py`) in payments, debt, arrears, alerts, notifications, socio views; chatbot arrears queries exclude it too (kept their existing `!= "PAGADO"` semantics otherwise). Money in the new rescheduling endpoints is serialized as two-decimal strings.
