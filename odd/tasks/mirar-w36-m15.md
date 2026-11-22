# mirar.md gaps CU-W36..CU-M15

Objective: close the acceptance-criteria gaps found when auditing `mirar.md` for CU-W36, W37, W38, W39, W40 and M15 (W37 and M15 already pass).
Branch: `feat/mirar-w36-m15` in SI2_backend_api and SI2_frontend_web. Delivery: ask-on-risk. Runner: `scripts/run_tests.py` (backend, needs `compose.test.yml` test-db), `npm run build` + `npm run lint` (web).

## Specs

- S1 (CU-W36): "Muestra el plan de pagos con el desglose por cuota de capital, interés y seguro de desgravamen, además de los totales del crédito." Gap: `/socio/creditos/{id}/cronograma` only returns `total_seguro`; add total capital, interest and total to pay (excluding REPROGRAMADA installments), and show a totals row in the web plan table.
- S2 (CU-W38): "Permite filtrar por rango de fechas y por canal (Web o Móvil)." Gap: no `canal` filter in `GET /feedback` and `/feedback/estadisticas`, nor in the web staff page.
- S3 (CU-W39): "Restringe el registro y la resolución a los roles Administrador y Oficial de Crédito. El Asesor de Crédito y el Contador solo pueden consultar." Gap: Asesor and Contador have no web screen to consult reprogramaciones; Cajero sees the Reprogramaciones tab that the backend rejects with 403.
- S4 (CU-W40): "Controla el plazo de respuesta de 5 días hábiles y alerta los reclamos vencidos." Gap: business days skip weekends only; skip Bolivian national public holidays too.
- S5 (CU-W40): "Permite atender y responder el reclamo. El socio puede dar su conformidad o escalarlo a la ASFI si está disconforme o si venció el plazo." Gap: the web socio has no claims page; add one listing own claims with conformity / escalation actions (backend `/socio/reclamos` already exists). Assumption: registering a claim from the socio web is out of scope (criteria only require counter registration; DB constraint allows MOVIL/VENTANILLA).

## Tasks

- [x] T1 S1, S2, S4 backend — route: inline (codex not running in Herdr); RED 8 failed → GREEN; suites reclamos/feedback/mobile_socio/reprogramacion/dashboard 77 passed
- [ ] T2 S1, S2, S3, S5 web — route: delegated to agy (team split, memory `si2-delegate-to-codex-and-agy`); in progress

## Log

- L1 (2026-11-22, user): "quiero que revises el nuevo archivo llamado "mirar.md", esta hecha la implementacion de esa forma o no" → audit done in conversation.
- L2 (2026-11-22, user): "vamos con los de W36 para adelante"
- L3: Bolivian national holidays (assumption, departmental holidays excluded): Jan 1, Jan 22, Carnival Monday and Tuesday (Easter -48/-47), Good Friday (Easter -2), May 1, Corpus Christi (Easter +60), Jun 21, Aug 6, Nov 2, Dec 25.
- L4: T1 contracts: `/socio/creditos/{id}/cronograma` adds `total_capital`, `total_interes`, `total_pagar` (plus existing `total_seguro`), all excluding REPROGRAMADA; `/feedback` and `/feedback/estadisticas` accept `canal=WEB|MOVIL` (422 otherwise); `sumar_dias_habiles` skips national holidays (only used by reclamos deadline and extensions).
