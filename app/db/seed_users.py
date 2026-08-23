"""Seed de los 6 roles base, cooperativa de prueba y usuarios de prueba.

Uso:
    python -m app.db.seed_users

Crea (o actualiza de forma idempotente) los 6 roles del sistema Multi-Tenant,
una cooperativa de prueba y un usuario por rol, todos con la contraseña 'Password123'
hasheada con bcrypt para ser válida en el login.
"""

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.models import Cooperativa, Rol, Usuario

# ─────────────────────────────────────────────
# 6 Roles base del sistema
# ─────────────────────────────────────────────
ROLES = [
    ("SUPERADMIN",         "Super Administrador SaaS: gestiona todas las cooperativas (tenants) de la plataforma"),
    ("ADMINISTRADOR",      "Administrador de la Cooperativa: acceso total dentro de su tenant"),
    ("CAJERO",             "Cajero / Ventanilla: gestión de operaciones de caja y atención al socio"),
    ("OFICIAL_CREDITO",    "Oficial de Crédito / Campo: evaluación y seguimiento de créditos en campo"),
    ("CONTADOR",           "Contador / Cumplimiento: contabilidad, reportes y control de cumplimiento"),
    ("SOCIO",              "Socio / Cliente: acceso limitado a su propia información dentro de la cooperativa"),
]

# ─────────────────────────────────────────────
# Cooperativa de prueba
# ─────────────────────────────────────────────
NOMBRE_COOPERATIVA = "Cooperativa de Prueba SI2"

# ─────────────────────────────────────────────
# Usuarios de prueba  (correo, rol, nombre_completo)
# Todos usan la misma contraseña: Password123
# ─────────────────────────────────────────────
PASSWORD = "Password123"

USUARIOS = [
    ("superadmin@test.com",       "SUPERADMIN",      "Super Admin SaaS"),
    ("admin@test.com",            "ADMINISTRADOR",   "Ana Cooperativa"),
    ("cajero@test.com",           "CAJERO",          "Carlos Ventanilla"),
    ("oficial.credito@test.com",  "OFICIAL_CREDITO", "Orlando Campo"),
    ("contador@test.com",         "CONTADOR",        "Catalina Contable"),
    ("socio@test.com",            "SOCIO",           "Sofia Socia"),
]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def get_or_create_rol(db, nombre: str, descripcion: str) -> Rol:
    rol = db.execute(select(Rol).where(Rol.nombre == nombre)).scalar_one_or_none()
    if rol is None:
        rol = Rol(nombre=nombre, descripcion=descripcion)
        db.add(rol)
        db.flush()
        print(f"  [NUEVO ROL]  {nombre}")
    else:
        print(f"  [ROL OK]     {nombre}")
    return rol


def get_or_create_cooperativa(db) -> Cooperativa:
    coop = db.execute(
        select(Cooperativa).where(Cooperativa.nombre == NOMBRE_COOPERATIVA)
    ).scalar_one_or_none()
    if coop is None:
        coop = Cooperativa(
            nombre=NOMBRE_COOPERATIVA,
            razon_social="Cooperativa de Prueba SI2 Ltda.",
            nit="9988776655",
            correo="contacto@cooptest.bo",
            telefono="+591 3 9876543",
            direccion="Av. Testing #001, Santa Cruz",
            estado="ACTIVO",
        )
        db.add(coop)
        db.flush()
        print(f"\n  [NUEVA COOPERATIVA]  {NOMBRE_COOPERATIVA} (id={coop.id})")
    else:
        print(f"\n  [COOPERATIVA OK]     {NOMBRE_COOPERATIVA} (id={coop.id})")
    return coop


def upsert_usuario(
    db,
    correo: str,
    rol: Rol,
    nombre: str,
    cooperativa_id: int | None,
) -> Usuario:
    usuario = db.execute(
        select(Usuario).where(Usuario.correo == correo)
    ).scalar_one_or_none()
    hashed = hash_password(PASSWORD)
    if usuario is None:
        usuario = Usuario(
            correo=correo,
            contrasena=hashed,
            rol=rol,
            cooperativa_id=cooperativa_id,
            nombre=nombre,
            estado="ACTIVO",
        )
        db.add(usuario)
        print(f"  [NUEVO USUARIO]  {correo:35s}  rol={rol.nombre}")
    else:
        usuario.contrasena = hashed
        usuario.rol = rol
        usuario.cooperativa_id = cooperativa_id
        usuario.nombre = nombre
        usuario.estado = "ACTIVO"
        print(f"  [ACTUALIZADO]    {correo:35s}  rol={rol.nombre}")
    return usuario


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    db = SessionLocal()
    try:
        print("=" * 60)
        print("  SEED: 6 roles + cooperativa de prueba + 6 usuarios")
        print("=" * 60)

        # 1. Roles
        print("\n[1/3] Creando / verificando roles...")
        roles_map: dict[str, Rol] = {}
        for nombre, descripcion in ROLES:
            roles_map[nombre] = get_or_create_rol(db, nombre, descripcion)

        # 2. Cooperativa
        print("\n[2/3] Creando / verificando cooperativa de prueba...")
        cooperativa = get_or_create_cooperativa(db)

        # 3. Usuarios
        print("\n[3/3] Creando / actualizando usuarios de prueba...")
        for correo, rol_nombre, nombre in USUARIOS:
            # El SUPERADMIN no pertenece a ningun tenant
            coop_id = None if rol_nombre == "SUPERADMIN" else cooperativa.id
            upsert_usuario(db, correo, roles_map[rol_nombre], nombre, coop_id)

        db.commit()

        # Tabla resumen
        print("\n" + "=" * 60)
        print("  SEED completado correctamente")
        print("=" * 60)
        print(f"\n  Cooperativa demo : {cooperativa.nombre}")
        print(f"  Contrasena comun : {PASSWORD}\n")
        print(f"  {'CORREO':35s}  {'ROL':20s}  {'NOMBRE'}")
        print(f"  {'-'*35}  {'-'*20}  {'-'*25}")
        for correo, rol_nombre, nombre in USUARIOS:
            print(f"  {correo:35s}  {rol_nombre:20s}  {nombre}")
        print()

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
