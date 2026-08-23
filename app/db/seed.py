"""Seed de roles, cooperativas y usuarios de prueba con contraseñas reales (bcrypt).

Uso:
    python -m app.db.seed

Crea o actualiza los roles, la cooperativa demo y los usuarios de prueba necesarios
para el login. Es idempotente: puede ejecutarse varias veces sin duplicar datos.
"""

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.models import Cooperativa, Rol, Usuario

# Roles del sistema (nivel SaaS + por cooperativa)
ROLES = {
    "SUPERADMIN": "Super Administrador SaaS: gestiona cooperativas (tenants) de toda la plataforma",
    "ADMINISTRADOR": "Acceso total dentro de su cooperativa",
    "ASESOR_CREDITO": "Gestión y evaluación de créditos dentro de su cooperativa",
    "SOCIO": "Acceso limitado a su propia información dentro de su cooperativa",
}

NOMBRE_COOPERATIVA_DEMO = "Cooperativa Demo SI2"

# (correo, contraseña, rol, nombre)
USUARIOS_PRUEBA = [
    ("superadmin@si2.com", "SuperAdmin123!", "SUPERADMIN", "Valeria Rojas"),
    ("admin@cooperativa.com", "Admin123!", "ADMINISTRADOR", "Carlos Mendoza"),
    ("asesor1@cooperativa.com", "Asesor123!", "ASESOR_CREDITO", "Juan Perez"),
    ("socio1@cooperativa.com", "Socio123!", "SOCIO", "Roberto Chavez"),
]


def get_or_create_rol(db, nombre: str, descripcion: str) -> Rol:
    rol = db.execute(select(Rol).where(Rol.nombre == nombre)).scalar_one_or_none()
    if rol is None:
        rol = Rol(nombre=nombre, descripcion=descripcion)
        db.add(rol)
        db.flush()
    return rol


def get_or_create_cooperativa_demo(db) -> Cooperativa:
    cooperativa = db.execute(
        select(Cooperativa).where(Cooperativa.nombre == NOMBRE_COOPERATIVA_DEMO)
    ).scalar_one_or_none()
    if cooperativa is None:
        cooperativa = Cooperativa(
            nombre=NOMBRE_COOPERATIVA_DEMO,
            razon_social="Cooperativa Demo SI2 Ltda.",
            nit="1020304050",
            correo="contacto@coopdemo.bo",
            telefono="+591 3 1234567",
            direccion="Av. Principal #123, Santa Cruz",
            estado="ACTIVO",
        )
        db.add(cooperativa)
        db.flush()
    return cooperativa


def upsert_usuario(
    db,
    correo: str,
    contrasena: str,
    rol: Rol,
    nombre: str,
    cooperativa_id: int | None,
) -> Usuario:
    usuario = db.execute(select(Usuario).where(Usuario.correo == correo)).scalar_one_or_none()
    if usuario is None:
        usuario = Usuario(
            correo=correo,
            contrasena=hash_password(contrasena),
            rol=rol,
            cooperativa_id=cooperativa_id,
            nombre=nombre,
            estado="ACTIVO",
        )
        db.add(usuario)
    else:
        # Actualizar el hash, rol y tenant en caso de que hayan cambiado
        usuario.contrasena = hash_password(contrasena)
        usuario.rol = rol
        usuario.cooperativa_id = cooperativa_id
        usuario.nombre = nombre
        usuario.estado = "ACTIVO"
    return usuario


def main() -> None:
    db = SessionLocal()
    try:
        roles = {
            nombre: get_or_create_rol(db, nombre, descripcion)
            for nombre, descripcion in ROLES.items()
        }

        cooperativa_demo = get_or_create_cooperativa_demo(db)

        for correo, contrasena, rol_nombre, nombre in USUARIOS_PRUEBA:
            # Solo el SUPERADMIN vive fuera de un tenant; el resto se vincula a la demo
            cooperativa_id = (
                None if rol_nombre == "SUPERADMIN" else cooperativa_demo.id
            )
            upsert_usuario(db, correo, contrasena, roles[rol_nombre], nombre, cooperativa_id)
            print(f"OK  {correo} -> rol {rol_nombre}")

        db.commit()
        print("\nSeed completado correctamente.")
        print(f"\nCooperativa demo: {cooperativa_demo.nombre} (id={cooperativa_demo.id})")
        print("\nCredenciales de prueba:")
        for correo, contrasena, rol, _ in USUARIOS_PRUEBA:
            print(f"  {correo} / {contrasena}  ({rol})")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
