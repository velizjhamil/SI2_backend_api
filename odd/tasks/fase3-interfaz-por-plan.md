# Fase 3: interfaz según el plan

Objetivo: que web y mobile oculten lo que el plan de la cooperativa no incluye y expliquen el bloqueo con un mensaje claro.
Rama: `main`, sin commit hasta que el usuario lo pida. Estrategia de entrega: `single-pr` por repo. Runners: `scripts/run_tests.py`, `npm test`, `flutter test`.

## Specs

- S1. La tabla aprobada está en `odd/tasks/fase2-modulos-por-plan.md`, S1 y S8. Los módulos y sus reglas no cambian.
- S2. Backend: `GET /api/v1/suscripciones/mis-modulos`, para cualquier usuario autenticado, responde `{"plan": {"codigo": str, "nombre": str} | null, "modulos": [str], "sin_restriccion": bool}`. Usa los mismos datos y excepciones que `require_feature`: `SUPERADMIN`, usuario sin cooperativa o cooperativa sin suscripción devuelven `sin_restriccion: true`. Los nombres son los de `MODULOS_POR_PLAN`: `banca_movil`, `scoring_crediticio`, `informes_asfi`, `respaldos_bitacora`, `chatbot_ia`, `notificaciones_socios`, `recredito_ia`, `reportes_gerenciales`.
- S3. Web: oculta del menú y de la interfaz lo que no incluye el plan. Entrar por URL directa a una ruta restringida muestra un aviso con este texto, sin llamar a la API del módulo: "Tu plan {nombre} no incluye {módulo}." El aviso de ruta no recibe un 403 porque no llama a la API; cada pantalla conserva su manejo actual de errores.
- S4. Web: restringe informes ASFI, bitácora, chatbot y dictado, comando de voz de reportes, notificaciones, recrédito con IA, precalificación y explicación del modelo. La evaluación y la resolución de créditos siguen visibles en todos los planes.
- S5. Mobile: un socio cuyo plan no incluye `banca_movil` ve, en lugar de su app, una pantalla con el aviso y un botón para cerrar sesión. Assumption: el usuario todavía no definió esta pantalla.
- S6. Mobile: el oficial de campo sin `scoring_crediticio` no ve Precalificar; lista, ficha, GPS, fotos, guardar evaluación y calcular scoring quedan igual. El chatbot y las notificaciones se ocultan según su módulo.
- S7. Si `mis-modulos` falla, las apps no ocultan nada y el backend sigue bloqueando. Assumption: un error de red no debe dejar sin menú a nadie.
- S9. Web: las rutas `/socio/*` exigen `banca_movil` y, sin ese módulo, muestran el aviso de S3 en lugar de las pantallas del socio. Assumption: igual que S5 en mobile.
- S8. Fuera de alcance: cambiar la tabla, el plan por defecto y las promesas de `RED_ENTERPRISE`.

## Tasks

- T1 | S2 | delegated, Codex por Herdr, pedido por el usuario | done, sin commit
- T2 | S3, S4, S7, S9 | delegated, Antigravity por Herdr, pedido por el usuario | done, verificado, sin commit
- T3 | S5, S6, S7 | delegated, Antigravity por Herdr, pedido por el usuario | done, verificado, sin commit

## Log

- L1 (2026-11-03): "entonces, commitea esto y has push a la main, y despues prosigamos con la fase 3"
- L2 (2026-11-03): Fases 1 y 2 subidas a main: backend a997d74, web a795545, mobile 2d1882d. Siguiente paso: T1 y T2.
- L3 (2026-11-03): T1 y T2 entregadas. Revisión de T2: la web no tiene pantalla de precalificación, así que S4 no aplica ahí. El portal web del socio en BASICO quedaría con todas sus pantallas en 403; se agrega S9. Tras un cambio de plan, la web muestra los módulos nuevos al volver a iniciar sesión. Siguiente paso: S9 y T3.
- L4 (2026-11-04): El usuario retomó la fase 3: "entonces primerao trae esos 2 commits, y despues hagamos lo que debemos". Mobile actualizado a 7686bad, con avisos de notificaciones en segundo plano. Siguiente paso: S9 y T3.
- L5 (2026-11-04): S9 y T3 entregadas. Revisión independiente de Codex: la web puede reutilizar módulos del usuario anterior si el token vence, mobile deja módulos en memoria tras 401 y logout de campo, el monitor de movimientos puede consultar cuentas con un socio bloqueado, y la espera de mis-modulos en web llega a 15 s. Se ajusta S3 porque el guard no recibe un 403. Corrección enviada a Antigravity.
- L6 (2026-11-04): Corrección entregada. Observado: web 50 pass, oxlint 0 errores y build correcto; mobile 210 pass y analyze limpio. Backend en la corrida anterior: 133 failed, 666 passed, 1 error. Pendiente: commit y prueba en navegador y dispositivo.
