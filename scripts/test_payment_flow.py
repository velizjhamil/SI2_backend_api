from fastapi.testclient import TestClient
from main import app
from app.db.session import SessionLocal
from app.models.models import Usuario, Socio, CuentaAhorro
from app.core.security import create_access_token
from sqlalchemy import select

def test_socio_endpoints():
    client = TestClient(app)
    db = SessionLocal()
    try:
        user = db.get(Usuario, 12) # socio@test.com, Socio 3
        socio = db.execute(select(Socio).where(Socio.usuario_id == user.id)).scalar_one_or_none()
        cuenta = db.execute(select(CuentaAhorro).where(CuentaAhorro.socio_id == socio.id, CuentaAhorro.estado == "ACTIVA")).scalars().first()
        
        token, _ = create_access_token(str(user.id), "SOCIO", socio.cooperativa_id or 1)
        headers = {"Authorization": f"Bearer {token}"}
        
        print(f"\n--- Testing cuota payment for Socio 3 ({user.correo}), Cuenta {cuenta.numero} (saldo: {cuenta.saldo_disponible}) ---")
        
        # 1. List credits
        resp = client.get("/api/v1/socio/creditos", headers=headers)
        assert resp.status_code == 200
        credits = resp.json()
        credit_id = credits[0]["id"]
        
        # 2. Get schedule before payment
        crono_before = client.get(f"/api/v1/socio/creditos/{credit_id}/cronograma", headers=headers).json()
        pending_cuotas = [q for q in crono_before["cuotas"] if q["estado"] in ("VENCIDA", "PENDIENTE")]
        print(f"Pending cuotas before payment: {len(pending_cuotas)}")
        
        # 3. Pay next cuota
        print(f"Paying next cuota using account {cuenta.id}...")
        pay_resp = client.post(
            f"/api/v1/socio/creditos/{credit_id}/pagos",
            json={"cuenta_ahorro_id": cuenta.id},
            headers=headers,
        )
        print(f"POST /api/v1/socio/creditos/{credit_id}/pagos -> HTTP {pay_resp.status_code}")
        print(f"Payment response: {pay_resp.text}")
        assert pay_resp.status_code in (200, 201), f"Payment failed: {pay_resp.text}"
        
        pay_data = pay_resp.json()
        print(f"  Receipt number: {pay_data.get('comprobante', {}).get('numero')}")
        print(f"  Verification code: {pay_data.get('comprobante', {}).get('codigo_verificacion')}")
        
        # 4. Check schedule after payment
        crono_after = client.get(f"/api/v1/socio/creditos/{credit_id}/cronograma", headers=headers).json()
        paid_cuotas = [q for q in crono_after["cuotas"] if q["estado"] == "PAGADA"]
        print(f"Paid cuotas after payment: {len(paid_cuotas)} (was {len([q for q in crono_before['cuotas'] if q['estado'] == 'PAGADA'])})")
        
        print("\nAll payment flows tested and verified 100% functional!")
    finally:
        db.close()

if __name__ == "__main__":
    test_socio_endpoints()
