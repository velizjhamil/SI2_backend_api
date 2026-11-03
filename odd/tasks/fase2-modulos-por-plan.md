# Fase 2: módulos por plan de suscripción

Objetivo: que el plan de cada cooperativa decida qué módulos puede usar, con la regla aplicada en el backend.
Rama: `main`, sin commit hasta que el usuario lo pida. Estrategia de entrega: `single-pr`. Runner: `.venv/bin/python scripts/run_tests.py`.

## Specs

- S1. Matriz aprobada por el usuario ("la tabla me parecio bien"):

  | Módulo | `BASICO` | `CRECIMIENTO_PRO` | `RED_ENTERPRISE` |
  |---|---|---|---|
  | Socios y KYC, ahorros, créditos, caja y contabilidad | ✅ | ✅ | ✅ |
  | Reportes gerenciales | ✅ | ✅ | ✅ |
  | Hasta 500 socios | ✅ | Ilimitados | Ilimitados |
  | Banca móvil (app del socio) y comprobantes digitales | ❌ | ✅ | ✅ |
  | Scoring crediticio | ❌ | ✅ | ✅ |
  | Informes ASFI | ❌ | ✅ | ✅ |
  | Respaldos y bitácora | ❌ | ✅ | ✅ |
  | Chatbot con IA y dictado por voz | ❌ | ✅ | ✅ |
  | Notificaciones a socios | ❌ | ✅ | ✅ |
  | Recomendación de recrédito con IA | ❌ | ❌ | ✅ |

- S2. El backend define una lista de módulos por código de plan y una dependencia reutilizable `require_feature("<módulo>")`, usada en cada ruta de un módulo no incluido en todos los planes. Sin el módulo responde 403.
- S3. Un plan con código desconocido solo tiene los módulos de la primera fila. Assumption: fallar cerrado.
- S4. `SUPERADMIN`, un usuario sin cooperativa y una cooperativa sin suscripción no se bloquean, igual que en la fase 1. Assumption: mantener el criterio de la fase 1.
- S5. `BASICO` admite hasta 500 socios y rechaza con 409 al crear uno más. Assumption: cuentan los socios `ACTIVO`.
- S6. El catálogo que muestra la web deja de ofrecer en `BASICO` los informes ASFI. Las promesas de `RED_ENTERPRISE` no implementadas quedan sin cambios hasta que el usuario decida.
- S8. En `BASICO`, "scoring crediticio" significa solo las funciones avanzadas: `POST /creditos/precalificacion` y `GET /creditos/evaluaciones/{id}/explicacion` quedan restringidas. `POST` y `GET` de `/creditos/solicitudes/{id}/evaluacion(es)` y `POST .../resolucion` quedan disponibles en todos los planes, para que el crédito se pueda aprobar.
- S7. Fuera de alcance: ocultar opciones en web y mobile (fase 3), el plan por defecto y las migraciones de cooperativas existentes.

## Tasks

- T1 | S1-S6, S8 | delegated, Codex por Herdr, pedido por el usuario | done, verificado | commit pendiente

## Log

- L1 (2026-11-03): "mejor si vamos con la otra fase 2"
- L2 (2026-11-03): "ok, commitea la fase 1, y la tabla me parecio bien"
- L3 (2026-11-03): Fase 1 commiteada en main de backend, web y mobile, sin push. Siguiente paso: T1.
- L4 (2026-11-03): T1 escrita por Codex, sin commit. Revisión: 50 tests propios pasan, pero la suite completa agrega 10 fallos en tests/test_chatbot_transcripcion.py porque el router exige get_current_user y el test solo reemplaza get_chat_user. Además, S1 se contradice en BASICO: resolver una solicitud exige una EvaluacionCrediticia y esa ruta quedó restringida como scoring, así que no se puede aprobar ningún crédito. T1 abierta hasta que el usuario decida.
- L5 (2026-11-03): El usuario eligió la opción A, separar el scoring interno: "A". Se agrega S8 y T1 sigue abierta.
- L6 (2026-11-03): Revisión independiente de Antigravity: S1-S4, S6 y S8 aprobados, con flujo de crédito BASICO completo sin 403 y rutas mapeadas. Bloqueante en S5: PUT /socios/{id} reactiva socios sobre el tope de 500, y el conteo no bloquea filas. Su segundo hallazgo, la llamada directa a get_current_socio en chatbot.py, no bloquea porque esa ruta ya exige chatbot_ia, módulo que BASICO no tiene. Corrección enviada a Codex; navegador y dispositivo no comprobados.
- L7 (2026-11-03): S5 corregido. Suite completa: 133 failed, 659 passed, 1 error, sin fallos nuevos causados por esta fase; test_chat_admin_responde_mora_con_datos_reales falla de forma intermitente porque llama al proveedor LLM real. Pendiente: commit y prueba en navegador y dispositivo.
