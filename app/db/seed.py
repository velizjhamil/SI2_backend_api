"""Seed de usuarios de prueba con contraseñas reales (hash bcrypt).

Uso:
    python -m app.db.seed

Crea o actualiza los roles y usuarios de prueba necesarios para el login.
Es idempotente: puede ejecutarse varias veces sin duplicar datos.
"""

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.models import Rol, Usuario

# (correo, contraseña, rol, nombre)
USUARIOS_PRUEBA = [
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


def upsert_usuario(db, correo: str, contrasena: str, rol: Rol, nombre: str) -> Usuario:
    usuario = db.execute(select(Usuario).where(Usuario.correo == correo)).scalar_one_or_none()
    if usuario is None:
        usuario = Usuario(
            correo=correo,
            contrasena=hash_password(contrasena),
            rol=rol,
            nombre=nombre,
            estado="ACTIVO",
        )
        db.add(usuario)
    else:
        # Actualizar el hash y el rol en caso de que hayan cambiado (ej. hashes falsos del bd.sql)
        usuario.contrasena = hash_password(contrasena)
        usuario.rol = rol
        usuario.nombre = nombre
        usuario.estado = "ACTIVO"
    return usuario


def main() -> None:
    roles = {
        "ADMINISTRADOR": "Acceso total al sistema",
        "ASESOR_CREDITO": "Gestión y evaluación de créditos",
        "SOCIO": "Acceso limitado a su propia información",
    }

    db = SessionLocal()
    try:
        for correo, contrasena, rol_nombre, nombre in USUARIOS_PRUEBA:
            rol = get_or_create_rol(db, rol_nombre, roles[rol_nombre])
            upsert_usuario(db, correo, contrasena, rol, nombre)
            print(f"OK  {correo} -> rol {rol_nombre}")

        db.commit()
        print("\nSeed completado correctamente.")
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