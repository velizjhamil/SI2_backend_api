from fastapi.testclient import TestClient
from main import app
from app.db.session import SessionLocal
from app.models.models import Usuario, Socio
from app.core.security import create_access_token
from sqlalchemy import select

def test_socio_endpoints():
    client = TestClient(app)
    db = SessionLocal()
    try:
        # Find user 6 (Socio 1) and user 12 (Socio 3)
        for user_id in [6, 12, 15]:
            user = db.get(Usuario, user_id)
            if not user:
                continue
            socio = db.execute(select(Socio).where(Socio.usuario_id == user.id)).scalar_one_or_none()
            if not socio:
                continue
            token, _ = create_access_token(str(user.id), "SOCIO", socio.cooperativa_id or 1)
            headers = {"Authorization": f"Bearer {token}"}
            print(f"\n--- Testing HTTP for User {user.id} ({user.correo}), Socio {socio.id} ---")
            
            # 1. GET /api/v1/socio/creditos
            resp = client.get("/api/v1/socio/creditos", headers=headers)
            print(f"GET /api/v1/socio/creditos -> HTTP {resp.status_code}")
            assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.text}"
            data = resp.json()
            print(f"  Received {len(data)} credits")
            for c in data:
                print(f"  Credit id={c['id']}, numero={c['numero_credito']}, proxima_cuota={c.get('proxima_cuota')}")
                # 2. GET /api/v1/socio/creditos/{id}/cronograma
                crono_resp = client.get(f"/api/v1/socio/creditos/{c['id']}/cronograma", headers=headers)
                print(f"  GET /api/v1/socio/creditos/{c['id']}/cronograma -> HTTP {crono_resp.status_code}")
                assert crono_resp.status_code == 200, f"Expected 200 but got {crono_resp.status_code}: {crono_resp.text}"
                crono_data = crono_resp.json()
                print(f"    Cuotas: {len(crono_data.get('cuotas', []))}")
                for q in crono_data.get('cuotas', [])[:3]:
                    print(f"      Cuota #{q['numero']} ({q['estado']}): total={q['monto_total']}, mora={q['mora']}, vence={q['fecha_vencimiento']}")
        print("\nAll HTTP tests PASSED with 200 OK!")
    finally:
        db.close()

if __name__ == "__main__":
    test_socio_endpoints()
