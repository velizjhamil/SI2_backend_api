from app.db.session import engine
from sqlalchemy import text

with engine.connect() as conn:
    users = conn.execute(text("SELECT u.id, u.correo, u.rol_id, s.id as socio_id, s.nombre, s.apellido FROM usuario u LEFT JOIN socio s ON s.usuario_id = u.id ORDER BY u.id")).fetchall()
    print("Users and socios:")
    for u in users:
        print(f"User {u[0]}: {u[1]} (rol_id={u[2]}) -> Socio {u[3]}: {u[4]} {u[5]}")
