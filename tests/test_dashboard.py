"""Role dashboards: staff get a structured cooperative summary; socios get their own financial summary."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    from main import app
except Exception:
    app = None

pytestmark = pytest.mark.skipif(app is None, reason="PostgreSQL unavailable")
TODAY = date.today()


@pytest.fixture
def case():
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        roles = {r.nombre: r for r in db.query(models.Rol).all()}
        coop = models.Cooperativa(nombre=f"Dash {suffix}", estado="ACTIVO")
        other = models.Cooperativa(nombre=f"OtraDash {suffix}", estado="ACTIVO")
        db.add_all([coop, other]); db.flush()

        def user(label, rol, coop_id):
            row = models.Usuario(correo=f"dash-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                                 rol_id=roles[rol].id, cooperativa_id=coop_id, nombre=f"{label.title()} {suffix}",
                                 estado="ACTIVO")
            db.add(row); db.flush()
            return row

        admin = user("admin", "ADMINISTRADOR", coop.id)
        cajero = user("cajero", "CAJERO", coop.id)
        other_admin = user("otro", "ADMINISTRADOR", other.id)
        socio_user = user("socio", "SOCIO", coop.id)
        socio = models.Socio(ci=f"D{suffix}", nombre="Ana", apellido="Dash", cooperativa_id=coop.id, estado="ACTIVO",
                             usuario_id=socio_user.id, fecha_registro=TODAY)
        inactive = models.Socio(ci=f"DI{suffix}", nombre="Inactivo", apellido="Dash", cooperativa_id=coop.id,
                                estado="INACTIVO", fecha_registro=TODAY - timedelta(days=400))
        db.add_all([socio, inactive]); db.flush()
        db.add(models.CuentaAhorro(numero=f"D{suffix}", tipo_producto="VISTA", saldo_disponible=Decimal("250.00"),
                                   saldo_bloqueado=0, estado="ACTIVA", fecha_registro=TODAY, socio_id=socio.id,
                                   moneda_id=1))
        product = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"D{suffix}", nombre="Consumo", moneda_id=1,
              monto_min=1, monto_max=100000, plazo_min_meses=1, plazo_max_meses=60, tasa_interes_anual=0,
              tipo_amortizacion="FRANCES", dias_gracia_mora=0, estado="ACTIVO", tipo_credito_asfi="CONSUMO")
        db.add(product); db.flush()
        request = models.SolicitudCredito(monto=1000, plazo_meses=2, tasa_interes=0, socio_id=socio.id,
              usuario_id=admin.id, producto_credito_id=product.id, numero_solicitud=f"D{suffix}", destino="CONSUMO",
              moneda_id=1, cooperativa_id=coop.id, estado="DESEMBOLSADO")
        db.add(request); db.flush()
        credit = models.Credito(monto_aprobado=1000, saldo_pendiente=500, estado="VIGENTE",
              solicitud_credito_id=request.id, numero_credito=f"D{suffix}", cooperativa_id=coop.id, socio_id=socio.id,
              producto_credito_id=product.id, moneda_id=1, fecha_desembolso=TODAY, tipo_amortizacion="FRANCES",
              plazo_meses=2, tasa_interes=0, usuario_id=admin.id)
        db.add(credit); db.flush()
        paid = models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=TODAY - timedelta(days=3), monto_capital=500,
              monto_interes=0, monto_seguro=Decimal("4.00"), monto_cuota_total=Decimal("504.00"), estado_pago="PAGADA",
              credito_id=credit.id, saldo_inicial=1000, saldo_final=500, monto_pagado=Decimal("504.00"))
        due = models.TablaAmortizacion(numero_cuota=2, fecha_vencimiento=TODAY + timedelta(days=27), monto_capital=500,
              monto_interes=0, monto_seguro=Decimal("2.00"), monto_cuota_total=Decimal("502.00"),
              estado_pago="PENDIENTE", credito_id=credit.id, saldo_inicial=500, saldo_final=0, monto_pagado=0)
        db.add_all([paid, due]); db.flush()
        db.add(models.PagoCuota(tabla_amortizacion_id=paid.id, credito_id=credit.id, cooperativa_id=coop.id,
                                monto_capital=500, monto_interes_pagado=0, monto_seguro=Decimal("4.00"), monto_mora=0,
                                fecha=datetime.combine(TODAY, datetime.min.time()), numero_recibo=f"D{suffix}",
                                modalidad="EFECTIVO", dias_atraso=0, usuario_id=admin.id))
        db.add(models.PrediccionDeMorosidad(credito_id=credit.id, probabilidad_mora=Decimal("0.7100"),
                                            nivel_riesgo="ALTO", version_modelo="test"))
        db.add(models.AlertaCredito(cooperativa_id=coop.id, credito_id=credit.id, tipo="POR_VENCER",
                                    severidad="MEDIA", mensaje="Cuota por vencer", estado="ACTIVA"))
        db.add_all([models.EncuestaSatisfaccion(cooperativa_id=coop.id, socio_id=socio.id, servicio="CREDITO",
                                                calificacion=value, recomendaria=nps, canal="WEB")
                    for value, nps in ((5, 10), (3, 5))])
        db.add(models.Bitacora(usuario_id=admin.id, cooperativa_id=coop.id, modulo="CREDITO", accion="DESEMBOLSAR",
                               descripcion="Desembolso de prueba"))
        db.commit()
        data = {"coop_id": coop.id, "other_id": other.id, "credit_id": credit.id,
                "tokens": {name: create_access_token(str(u.id), rol, u.cooperativa_id)[0]
                           for name, u, rol in (("admin", admin, "ADMINISTRADOR"), ("cajero", cajero, "CAJERO"),
                                                ("other", other_admin, "ADMINISTRADOR"),
                                                ("socio", socio_user, "SOCIO"))}}
    yield data
    with SessionLocal() as db:
        for sql in (
            "DELETE FROM alerta_credito WHERE cooperativa_id = :c",
            "DELETE FROM prediccion_de_morosidad WHERE credito_id IN (SELECT id FROM credito WHERE cooperativa_id = :c)",
            "DELETE FROM pago_cuota WHERE cooperativa_id = :c",
            "DELETE FROM tabla_amortizacion WHERE credito_id IN (SELECT id FROM credito WHERE cooperativa_id = :c)",
            "DELETE FROM credito WHERE cooperativa_id = :c",
            "DELETE FROM solicitud_credito WHERE cooperativa_id = :c",
            "DELETE FROM producto_credito WHERE cooperativa_id = :c",
            "DELETE FROM encuesta_satisfaccion WHERE cooperativa_id = :c",
            "DELETE FROM cuenta_ahorro WHERE socio_id IN (SELECT id FROM socio WHERE cooperativa_id = :c)",
            "DELETE FROM socio WHERE cooperativa_id = :c",
            "DELETE FROM bitacora WHERE cooperativa_id = :c",
            "DELETE FROM usuario WHERE cooperativa_id = :c",
            "DELETE FROM cooperativa WHERE id = :c",
        ):
            for coop_id in (data["coop_id"], data["other_id"]):
                db.execute(text(sql), {"c": coop_id})
        db.commit()


def _get(case, who, path):
    with TestClient(app) as client:
        return client.get(path, headers={"Authorization": f"Bearer {case['tokens'][who]}"})


def test_staff_summary_is_structured_with_real_cooperative_figures(case):
    response = _get(case, "admin", "/api/v1/dashboard/resumen")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["socios"] == {"total": 2, "activos": 1, "nuevos_mes": 1}
    assert body["captaciones"]["cuentas_activas"] == 1
    assert body["captaciones"]["saldo_ahorro"] == "250.00"
    assert body["cartera"]["creditos_vigentes"] == 1
    assert body["cartera"]["cartera_bruta"] == "500.00"
    assert body["operaciones_mes"] == {"desembolsos": 1, "monto_desembolsado": "1000.00",
                                       "pagos": 1, "monto_cobrado": "504.00"}
    assert body["solicitudes_por_estado"] == {"DESEMBOLSADO": 1}
    assert body["riesgo_ml"] == {"BAJO": 0, "MEDIO": 0, "ALTO": 1, "SIN_PREDICCION": 0}
    assert body["alertas_activas"] == 1
    assert body["satisfaccion"]["total"] == 2 and body["satisfaccion"]["promedio"] == 4.0
    assert body["satisfaccion"]["nps"] == 0.0
    assert len(body["colocaciones_12m"]) == 12 and body["colocaciones_12m"][-1]["creditos"] == 1
    assert len(body["cobranza_12m"]) == 12 and body["cobranza_12m"][-1]["monto"] == "504.00"
    assert body["actividad_reciente"][0]["accion"] == "DESEMBOLSAR"
    assert body["actividad_reciente"][0]["usuario"].startswith("Admin")


def test_every_staff_role_gets_the_summary_and_tenants_are_isolated(case):
    assert _get(case, "cajero", "/api/v1/dashboard/resumen").status_code == 200
    other = _get(case, "other", "/api/v1/dashboard/resumen").json()
    assert other["socios"]["total"] == 0 and other["cartera"]["creditos_vigentes"] == 0
    assert other["actividad_reciente"] == [] and other["satisfaccion"]["promedio"] is None


def test_socio_dashboard_summarises_own_savings_credits_and_payments(case):
    response = _get(case, "socio", "/api/v1/dashboard/socio")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ahorros"] == {"cuentas": 1, "saldo_total": "250.00"}
    assert body["creditos"][0]["tipo_credito"] == "CONSUMO"
    assert body["creditos"][0]["saldo_pendiente"] == "500.00"
    assert body["creditos"][0]["proxima_cuota"]["monto_total"] == "502.00"
    assert body["pagado"] == {"capital": "500.00", "interes": "0.00", "seguro": "4.00", "mora": "0.00",
                              "total": "504.00"}
    assert body["calificaciones_enviadas"] == 2


def test_dashboards_reject_the_wrong_audience(case):
    assert _get(case, "socio", "/api/v1/dashboard/resumen").status_code == 403
    assert _get(case, "admin", "/api/v1/dashboard/socio").status_code == 403
