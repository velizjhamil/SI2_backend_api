# SI2_backend_api — CoopSmart API

Backend transaccional para la gestión de cooperativas (multi-tenant), construido con **FastAPI**, **SQLAlchemy 2.0** y **PostgreSQL**. Incluye autenticación con JWT, control de acceso por roles (RBAC), aislamiento por tenant y bitácora de auditoría.

## 🧱 Stack tecnológico

- **Python 3.11+**
- **FastAPI** — framework web ASGI
- **SQLAlchemy 2.0** — ORM (estilo `Mapped` / `mapped_column`)
- **PostgreSQL 16** — base de datos (vía Docker)
- **pydantic-settings** — configuración por variables de entorno
- **python-jose** — emisión/validación de JWT
- **bcrypt** — hash de contraseñas
- **psycopg2** — driver de PostgreSQL
- **Docker Compose** — orquestación de la base de datos
- **pytest** — pruebas (ver `requirements-dev.txt`)


## 📁 Estructura del proyecto

```
SI2_backend_api/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── deps.py            # Dependencias: auth, roles (RBAC)
│   │       ├── router.py          # Ruteo de endpoints v1
│   │       └── endpoints/
│   │           ├── auth.py        # login, logout, me
│   │           ├── cooperativas.py
│   │           ├── admin.py
│   │           ├── socios_kyc.py
│   │           ├── socios.py
│   │           └── creditos.py
│   ├── core/
│   │   ├── config.py             # Settings (env vars)
│   │   ├── security.py           # JWT + bcrypt
│   │   ├── tenancy.py            # Lógica multi-tenant
│   │   └── bitacora.py           # Auditoría
│   ├── db/
│   │   ├── base.py               # Base declarativa de SQLAlchemy
│   │   ├── session.py            # Engine + SessionLocal + get_db
│   │   ├── seed.py
│   │   └── seed_users.py         # Seed de roles + usuarios de prueba
│   ├── models/
│   │   └── models.py             # Modelos ORM (Cooperativa, Usuario, Rol, ...)
│   └── schemas/
│       └── schemas.py            # Esquemas Pydantic
├── migrations/                   # Scripts SQL de migraciones
│   ├── 002_sprint0_multi_tenant.sql
│   └── 003_bitacora_audit_trail.sql
├── tests/
│   └── test_multi_tenant.py
├── bd.sql                        # Script de inicialización de la BD
├── compose.yml                   # PostgreSQL en Docker
├── main.py                       # Punto de entrada de FastAPI
├── requirements.txt
└── requirements-dev.txt
```

## ✅ Prerrequisitos

- **Python 3.11+**
- **Docker** y **Docker Compose**
- **git** (opcional)

## 🚀 Instalación y puesta en marcha

### 1. Clonar el repositorio

```bash
git clone https://github.com/velizjhamil/SI2_backend_api.git
cd SI2_backend_api
```

### 2. Levantar la base de datos con Docker

El archivo `compose.yml` levanta un PostgreSQL 16 en el **puerto 5433** del host:

```bash
docker compose up -d
```

Esto crea la base de datos `cooperativa_db` y carga automáticamente el script `bd.sql` como esquema inicial. El contenedor se llama `coopDB`.

> Verifica que esté corriendo:
> ```bash
> docker compose ps
> ```

### 3. Crear y activar el entorno virtual

```bash
python3 -m venv .venv
source .venv/bin/activate
```

En Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 4. Instalar dependencias

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Para entorno de desarrollo (incluye `pytest`):

```bash
pip install -r requirements-dev.txt
```

### 5. Configurar las variables de entorno

Crea un archivo `.env` en la raíz del proyecto con la cadena de conexión a la BD de Docker:

```env
DATABASE_URL=postgresql://yimysito:tarqui231B@localhost:5433/cooperativa_db
SECRET_KEY=super_secreta_llave_para_jwt_cooperativa_si2_uagrm
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480
```

> **Importante:** el `config.py` trae valores por defecto que apuntan a `postgres:postgres@localhost:5432`, pero el `compose.yml` expone PostgreSQL en el **5433** con usuario `yimysito`. El `.env` anterior corrige esa diferencia.

#### Asistente IA y reportes PDF (opcional)

El chatbot funciona sin LLM (responde con plantillas deterministas). Para que
redacte las respuestas con IA agrega al `.env`:

```env
LLM_PROVIDER=gemini
LLM_API_KEY=<tu_api_key>
LLM_MODEL=gemini-3.5-flash
```

En el frontend también se puede dictar una consulta con el botón del micrófono
y activar la lectura en voz alta desde el encabezado del chat. El audio se envía
a `POST /api/v1/chatbot/transcribir`, que lo transcribe con Groq, y el texto llega
al chatbot como un mensaje normal, de modo que también sirve para pedir reportes.
Para activarlo agrega al `.env`:

```env
GROQ_API_KEY=<tu_api_key>
# Opcional; este es el valor por defecto
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
```

Sin `GROQ_API_KEY` el endpoint responde 503. El audio admite hasta 10 MB y sale
hacia Groq, un proveedor externo.

- La descarga de reportes **no** depende del LLM: los enlaces se construyen de
  forma determinista y respetan el rol del usuario.
- `gemini-2.5-flash` ya no está disponible para cuentas nuevas; usa
  `gemini-3.5-flash`. El cliente corta a los 15 s y, si el proveedor falla, el
  backend responde con la plantilla.
- En Render configura estas variables en **Environment** del servicio; el
  `.env` está ignorado por git y excluido de la imagen Docker.

### 6. Ejecutar migraciones (si aplica)

El esquema base ya se crea con `bd.sql` al iniciar el contenedor. Las migraciones adicionales se aplican contra la BD:

```bash
# Migración 003 — bitácora/auditoría (vía script Python)
python run_migration_003.py

# O aplicar los SQL manualmente, por ejemplo:
# psql -h localhost -p 5433 -U yimysito -d cooperativa_db -f migrations/002_sprint0_multi_tenant.sql
# psql -h localhost -p 5433 -U yimysito -d cooperativa_db -f migrations/003_bitacora_audit_trail.sql
```

### 7. Sembrar usuarios de prueba (roles + cooperativa + usuarios)

```bash
python -m app.db.seed_users
```

Esto crea (de forma idempotente) los **6 roles** del sistema, una **cooperativa de prueba** y **6 usuarios** con la contraseña `Password123`.

### 8. Levantar el servidor

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

La API quedará disponible en:

- **API:** http://localhost:8000
- **Docs interactivas (Swagger UI):** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **Health check:** http://localhost:8000/health

## 👥 Usuarios de prueba

Todos comparten la contraseña `Password123`:

| Correo                  | Rol             | Nombre             |
|-------------------------|-----------------|--------------------|
| superadmin@test.com     | SUPERADMIN      | Super Admin SaaS   |
| admin@test.com          | ADMINISTRADOR   | Ana Cooperativa    |
| cajero@test.com         | CAJERO          | Carlos Ventanilla  |
| oficial.credito@test.com| OFICIAL_CREDITO | Orlando Campo      |
| contador@test.com       | CONTADOR        | Catalina Contable |
| socio@test.com          | SOCIO           | Sofia Socia        |

## 🔐 Autenticación

1. Inicia sesión con `POST /api/v1/auth/login`:

```json
{
  "correo": "admin@test.com",
  "contrasena": "Password123"
}
```

2. Recibirás un `access_token` (JWT, válido por 8 horas).

3. Usa el token en las peticiones protegidas con la cabecera:

```
Authorization: Bearer <access_token>
```

### Control de acceso por suscripción

Una suscripción está vigente cuando su estado es `ACTIVA` y no tiene fecha de fin, o cuando `fecha_fin` es hoy o una fecha futura. Si deja de estar vigente, el login devuelve `403` después de validar credenciales y estado del usuario; las sesiones ya abiertas reciben `401` en su siguiente petición protegida. Ambos casos usan el mensaje: `La suscripción de tu cooperativa no está vigente. Contacta al administrador de la plataforma.`

`SUPERADMIN`, usuarios sin cooperativa y cooperativas sin una fila de suscripción conservan el acceso. Al crear usuarios de personal, reactivar personal o cambiar a un rol de personal/estado activo, la API respeta `limite_usuarios` del plan: cuenta solo usuarios `ACTIVO` cuyo rol no sea `SOCIO`, y rechaza con `409` al alcanzar el límite. Los socios no consumen cupos; un plan con límite `NULL` no restringe altas.

### Módulos habilitados por plan

La API verifica las funciones restringidas en el backend usando la suscripción activa de cada cooperativa. `BASICO` conserva socios/KYC, ahorros, créditos, caja, contabilidad y reportes gerenciales; admite hasta 500 socios activos. `CRECIMIENTO_PRO` agrega banca móvil del socio y comprobantes digitales, scoring crediticio, informes ASFI, respaldos/bitácora, chatbot y dictado por voz, y notificaciones. `RED_ENTERPRISE` agrega además recomendaciones de recrédito con IA. El scoring restringido es el avanzado: precalificación y explicación de evaluación. La evaluación interna de solicitudes, su historial y la resolución quedan disponibles en todos los planes para mantener el flujo de aprobación crediticia. Un código de plan desconocido solo habilita los módulos de la primera fila de la matriz (socios/KYC, ahorros, créditos, caja y contabilidad), sin reportes gerenciales ni funciones adicionales.

Las rutas restringidas responden `403` cuando la función no está incluida. `SUPERADMIN`, usuarios sin cooperativa y cooperativas sin suscripción conservan las excepciones de la fase 1. El tope de socios aplica solo al crear un socio `ACTIVO` en `BASICO` y responde `409` al superar el límite. Las operaciones de crédito usadas por el personal —incluida la ficha de campo— permanecen disponibles en todos los planes; no se limita el router completo de créditos. Las operaciones globales de respaldos y la verificación pública de comprobantes no se asocian a un plan de cooperativa.

## 📌 Endpoints principales

Prefijo global: `/api/v1`

| Método | Ruta                        | Descripción                          | Acceso         |
|--------|-----------------------------|--------------------------------------|----------------|
| POST   | `/auth/login`               | Inicia sesión y devuelve un JWT      | Público        |
| POST   | `/auth/logout`              | Cierra la sesión                     | Autenticado    |
| GET    | `/auth/me`                  | Datos del usuario actual             | Autenticado    |
|        | `/cooperativas/...`         | Gestión de cooperativas (tenants)    | SUPERADMIN     |
|        | `/admin/...`                | Administración                       | SUPERADMIN/ADMINISTRADOR |
|        | `/socios/...`               | Socios y KYC                         | Autenticado    |

Los roles `SUPERADMIN` y `ADMINISTRADOR` se validan mediante dependencias (`require_superadmin`, `require_admin`) en `app/api/v1/deps.py`.

## 🧪 Pruebas

```bash
pytest -v
```

Las pruebas se encuentran en `tests/`.

## 🛠️ Comandos útiles

```bash
# Detener la base de datos de Docker
docker compose down

# Detener y borrar el volumen (reinicia la BD desde cero)
docker compose down -v

# Ver logs del contenedor de BD
docker compose logs -f db

# Acceder a la consola de PostgreSQL
docker exec -it coopDB psql -U yimysito -d cooperativa_db

# Re-ejecutar el seed de usuarios
python -m app.db.seed_users
```

## 🗺️ Diagrama de la base de datos

El diagrama entidad-relación se genera desde los modelos SQLAlchemy con un
script Node sin dependencias:

```bash
node scripts/generar_diagrama_bd.mjs
```

Produce dos archivos en `docs/`:

- `diagrama_bd.mmd`: diagrama Mermaid (para GitHub, Notion, draw.io, etc.).
- `diagrama_bd.html`: visor autónomo con zoom, paneo y descarga en SVG
  (Mermaid se carga desde un CDN, así que requiere Internet).

Opciones: `--models <ruta>` para indicar otro archivo de modelos y
`--out <carpeta>` para cambiar el directorio de salida.

## 🔧 CORS

El servidor permite peticiones desde el frontend de desarrollo en:

- `http://localhost:5173` y `http://127.0.0.1:5173` (Vite dev)
- `http://localhost:4173` y `http://127.0.0.1:4173` (Vite preview)

Configurado en `main.py`.

## 📄 Licencia

Proyecto académico — SI2 / UAGRM.

## Database backups

Backups are full PostgreSQL custom-format dumps, stored in a private Supabase Storage bucket. SUPERADMIN users can start and inspect manual backups through `/api/v1/backups`; GitHub Actions triggers the daily automatic backup. The backend host's filesystem is temporary and is never the backup destination.

### Render and Supabase setup

1. Switch the Render web service to **Docker** deployment so the image installs `pg_dump`.
2. Create a **private** Supabase Storage bucket (default name: `backups`).
3. Configure Render environment variables:
   - `BACKUP_DATABASE_URL`: direct or session-mode PostgreSQL connection URL; when omitted, `DATABASE_URL` is used. Prefer a direct connection for large dumps.
   - `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`: project URL and server-only service-role key. Never expose the key in a client or logs.
   - Optional: `BACKUP_BUCKET` (`backups`), `BACKUP_RETENTION_AUTOMATIC` (`7`), `BACKUP_STALE_MINUTES` (`60`), `BACKUP_SIGNED_URL_SECONDS` (`300`), and `PG_MAJOR` (`17`, set as a Docker build argument when changing the installed client major).
   - `BACKUP_CRON_TOKEN`: a long random secret shared only with the workflow below.
4. Apply `migrations/030_backups.sql` through the project's normal migration process before deploying the backup endpoints.
5. Add GitHub Actions repository secrets `BACKUP_API_URL` (the backend origin, without the API path) and `BACKUP_CRON_TOKEN` (the same value configured in Render). Run **Scheduled database backup** manually once and confirm the run succeeds.

The workflow runs daily at 06:00 UTC (02:00 Bolivia time). It retries transient failures to allow a sleeping Render instance to cold-start. A scheduled trigger returns `202` when accepted; inspect the SUPERADMIN backup list for the final state.

### Manual restore

Download the completed dump from the SUPERADMIN signed-download endpoint. Restore it out of the application using PostgreSQL client tools, after verifying the target database and making a separate copy of any data that must be retained:

```sh
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$RESTORE_DATABASE_URL" si2_backup.dump
```

Restoration is deliberately an operator-run procedure, not an application endpoint. Treat the downloaded dump and database URL as sensitive.
