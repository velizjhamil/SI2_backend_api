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

## 🔧 CORS

El servidor permite peticiones desde el frontend de desarrollo en:

- `http://localhost:5173` y `http://127.0.0.1:5173` (Vite dev)
- `http://localhost:4173` y `http://127.0.0.1:4173` (Vite preview)

Configurado en `main.py`.

## 📄 Licencia

Proyecto académico — SI2 / UAGRM.
