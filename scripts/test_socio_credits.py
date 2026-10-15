import sys
import traceback
from app.db.session import SessionLocal
from app.models.models import Socio, Usuario, Credito
from app.api.v1.endpoints.socio import listar_creditos_socio, cronograma_credito_socio
from sqlalchemy import select

def main():
    db = SessionLocal()
    try:
        socios = db.execute(select(Socio)).scalars().all()
        print(f"Total socios in db: {len(socios)}")
        for s in socios:
            db.rollback()
            try:
                credits = listar_creditos_socio(socio=s, db=db)
                print(f"Socio ID={s.id} (usuario_id={s.usuario_id}): {len(credits)} credits found")
                for c in credits:
                    crono = cronograma_credito_socio(credito_id=c['id'], socio=s, db=db)
                    print(f"   Credit id={c['id']}, num={c['numero_credito']}, cronograma={len(crono['cuotas'])} cuotas")
            except Exception:
                print(f"  -> ERROR for socio {s.id}:")
                traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()
