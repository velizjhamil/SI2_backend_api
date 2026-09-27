"""Contract tests for CU-W28 arrears alerts."""
from pathlib import Path
from sqlalchemy import inspect
from app.db.session import engine

from app.models import models
from app.schemas import schemas


def test_alerts_migration_and_contract_models_are_declared():
    migration = Path(__file__).parents[1] / "migrations" / "021_sprint9_alertas_mora.sql"
    assert migration.exists()
    sql = migration.read_text(encoding="utf-8").lower()
    assert "create table if not exists alerta_credito" in sql
    assert "historial_gestion_de_cobranza" in sql
    assert "create unique index if not exists" in sql
    assert models.AlertaCredito.__tablename__ == "alerta_credito"
    assert models.GestionCobranza.__tablename__ == "historial_gestion_de_cobranza"
    assert "fecha_de_compromiso_de_pago" in models.GestionCobranza.__table__.columns
    assert schemas.AlertaOut and schemas.GestionOut


def test_alerts_migration_applies_twice_and_preserves_legacy_history():
    migration = Path(__file__).parents[1] / "migrations" / "021_sprint9_alertas_mora.sql"
    sql = migration.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql(sql)
    with engine.begin() as connection:
        connection.exec_driver_sql(sql)
    inspector = inspect(engine)
    assert {"cooperativa_id", "usuario_id", "fecha", "alerta_id"} <= {
        column["name"] for column in inspector.get_columns("historial_gestion_de_cobranza")
    }
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM historial_gestion_de_cobranza"
        ).scalar_one() >= 1


def test_alert_candidate_builder_classifies_due_overdue_grace_and_mora_boundaries():
    from datetime import date, timedelta
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services.alertas_mora import construir_candidatos_alerta

    today = date(2026, 9, 27)
    cuotas = [
        SimpleNamespace(id=i, numero_cuota=i, fecha_vencimiento=today + timedelta(days=offset),
                        monto_cuota_total=Decimal("120.00"), monto_capital=Decimal("100.00"),
                        estado_pago="PENDIENTE")
        for i, offset in enumerate((5, 0, -5, -6), 1)
    ]
    found = construir_candidatos_alerta(cuotas, today, dias_gracia_mora=5, en_mora=True)
    assert [(a["tipo"], a["tabla_amortizacion_id"]) for a in found] == [
        ("CUOTA_POR_VENCER", 1), ("CUOTA_POR_VENCER", 2),
        ("CUOTA_VENCIDA", 3), ("MORA", 4),
    ]
    assert [a["severidad"] for a in found] == ["INFO", "INFO", "ADVERTENCIA", "CRITICA"]


def test_high_risk_candidate_is_only_admitted_for_prediction_eligible_credits():
    from app.services.alertas_mora import candidato_riesgo_alto
    assert candidato_riesgo_alto("ALTO", eligible=True, probability="0.8100")["tipo"] == "RIESGO_ALTO"
    assert candidato_riesgo_alto("ALTO", eligible=False, probability="0.8100") is None
    assert candidato_riesgo_alto("BAJO", eligible=True, probability="0.0500") is None


def test_alert_reconciliation_is_idempotent_and_resolves_only_lost_conditions():
    from types import SimpleNamespace
    from app.services.alertas_mora import reconciliar_alertas

    active = SimpleNamespace(id=1, tipo="MORA", tabla_amortizacion_id=7, estado="ACTIVA")
    attended = SimpleNamespace(id=2, tipo="RIESGO_ALTO", tabla_amortizacion_id=None, estado="ATENDIDA")
    kept, resolved, created = reconciliar_alertas(
        [active, attended], [{"tipo": "MORA", "tabla_amortizacion_id": 7},
                             {"tipo": "RIESGO_ALTO", "tabla_amortizacion_id": None}]
    )
    assert kept == [active] and resolved == [] and created == []
    kept, resolved, created = reconciliar_alertas([active, attended], [])
    assert kept == [] and resolved == [active] and created == []
    _, _, created = reconciliar_alertas([attended], [{"tipo": "RIESGO_ALTO", "tabla_amortizacion_id": None}])
    assert created == []


def test_collection_and_dismissal_payloads_enforce_contract_boundaries():
    from datetime import date, timedelta
    import pytest
    from pydantic import ValidationError
    from app.schemas.schemas import GestionCreateIn, DescartarAlertaIn

    with pytest.raises(ValidationError):
        GestionCreateIn(tipo_contacto="LLAMADA", resultado_gestion="too short")
    with pytest.raises(ValidationError):
        GestionCreateIn(tipo_contacto="OTRO", resultado_gestion="Suficientemente largo", fecha_compromiso_pago=date.today()-timedelta(days=1))
    with pytest.raises(ValidationError):
        DescartarAlertaIn(comentario="no")


def test_prediction_refresh_helper_is_shared_and_handles_empty_eligible_set():
    from app.api.v1.endpoints.creditos import _refrescar_predicciones_mora
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        assert _refrescar_predicciones_mora(db, []) == {"actualizados": 0, "bajo": 0, "medio": 0, "alto": 0}


def test_empty_cooperative_monitoring_is_idempotent_audited_and_summarized():
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from app.core.security import create_access_token, hash_password
    from app.db.session import SessionLocal
    from app.models import models
    from main import app

    suffix = uuid4().hex[:10]
    ids = {"user": None, "coop": None}
    try:
        with SessionLocal() as db:
            coop = models.Cooperativa(nombre=f"Alert Test {suffix}", estado="ACTIVO")
            db.add(coop); db.flush(); ids["coop"] = coop.id
            role = db.query(models.Rol).filter_by(nombre="ADMINISTRADOR").one()
            user = models.Usuario(correo=f"alert-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                rol_id=role.id, cooperativa_id=coop.id, nombre="Alert Test", estado="ACTIVO")
            db.add(user); db.flush(); ids["user"] = user.id; db.commit()
            token, _ = create_access_token(str(user.id), "ADMINISTRADOR", coop.id)
        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(app) as client:
            first = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            second = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert first.status_code == 200, first.text
            assert second.status_code == 200, second.text
            assert first.json()["creditos_monitoreados"] == 0
            assert first.json()["alertas_creadas"] == second.json()["alertas_creadas"] == 0
            summary = client.get("/api/v1/creditos/monitoreo/resumen", headers=headers)
            assert summary.status_code == 200, summary.text
            assert summary.json()["por_moneda"] == []
            assert summary.json()["creditos_por_riesgo"] == {"BAJO": 0, "MEDIO": 0, "ALTO": 0, "SIN_PREDICCION": 0}
            assert summary.json()["ultima_ejecucion"] is not None
        with SessionLocal() as db:
            assert db.query(models.Bitacora).filter_by(usuario_id=ids["user"], accion="MONITOREO_MORA").count() == 2
    finally:
        with SessionLocal() as db:
            if ids["user"]:
                db.query(models.Bitacora).filter_by(usuario_id=ids["user"]).delete(synchronize_session=False)
                db.query(models.Usuario).filter_by(id=ids["user"]).delete(synchronize_session=False)
            if ids["coop"]:
                db.query(models.Cooperativa).filter_by(id=ids["coop"]).delete(synchronize_session=False)
            db.commit()


def test_monitor_creates_due_soon_once_and_resolves_after_payment_with_cleanup(monkeypatch):
    from datetime import date, timedelta
    from decimal import Decimal
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from app.core.security import create_access_token, hash_password
    from app.db.session import SessionLocal
    from app.models import models
    from main import app

    suffix = uuid4().hex[:10]
    ids = {key: None for key in ("coop", "user", "foreign_coop", "foreign_user", "reader", "socio", "product", "evaluation", "request", "credit")}
    ids["installments"] = []
    try:
        with SessionLocal() as db:
            coop = models.Cooperativa(nombre=f"Alert Due {suffix}", estado="ACTIVO")
            db.add(coop); db.flush(); ids["coop"] = coop.id
            role = db.query(models.Rol).filter_by(nombre="ADMINISTRADOR").one()
            user = models.Usuario(correo=f"due-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                rol_id=role.id, cooperativa_id=coop.id, nombre="Alert Due", estado="ACTIVO")
            db.add(user); db.flush(); ids["user"] = user.id
            foreign = models.Cooperativa(nombre=f"Alert Foreign {suffix}", estado="ACTIVO")
            db.add(foreign); db.flush(); ids["foreign_coop"] = foreign.id
            foreign_user = models.Usuario(correo=f"foreign-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                rol_id=role.id, cooperativa_id=foreign.id, nombre="Foreign User", estado="ACTIVO")
            db.add(foreign_user); db.flush(); ids["foreign_user"] = foreign_user.id
            reader_role = db.query(models.Rol).filter_by(nombre="CONTADOR").one()
            reader = models.Usuario(correo=f"reader-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                rol_id=reader_role.id, cooperativa_id=coop.id, nombre="Alert Reader", estado="ACTIVO")
            db.add(reader); db.flush(); ids["reader"] = reader.id
            currency = db.query(models.Moneda).filter_by(codigo_iso="BOB").one()
            product = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"AD-{suffix}", nombre="Alert test",
                moneda_id=currency.id, monto_min=Decimal("100"), monto_max=Decimal("2000"),
                plazo_min_meses=6, plazo_max_meses=12, tasa_interes_anual=Decimal("12"),
                tipo_amortizacion="FRANCES", dias_gracia_mora=5, tasa_mora_anual=Decimal("0"),
                relacion_cuota_ingreso_max=Decimal("80"), requiere_garantia=False, estado="ACTIVO")
            db.add(product); db.flush(); ids["product"] = product.id
            socio = models.Socio(cooperativa_id=coop.id, ci=f"AD-{suffix}", nombre="Test", apellido="Member",
                estado="ACTIVO", fecha_registro=date(2020, 1, 1))
            db.add(socio); db.flush(); ids["socio"] = socio.id
            evaluation = models.EvaluacionCampo(ingreso_mensual=Decimal("5000"), egreso_mensual=Decimal("1000"),
                capacidad_pago=Decimal("4000"), cuota_deudas_mensual=Decimal("0"), actividad_economica="Comercio",
                fuente_ingresos="INDEPENDIENTE", antiguedad_laboral_meses=36, calificacion_asfi="A",
                fecha=date.today(), usuario_id=user.id, socio_id=socio.id)
            db.add(evaluation); db.flush(); ids["evaluation"] = evaluation.id
            request_row = models.SolicitudCredito(monto=Decimal("1000"), plazo_meses=9, tasa_interes=Decimal("12"),
                estado="DESEMBOLSADO", socio_id=socio.id, usuario_id=user.id, evaluacion_campo_id=evaluation.id, producto_credito_id=product.id,
                moneda_id=currency.id, cooperativa_id=coop.id, numero_solicitud=f"AD-{suffix}", destino="OTRO")
            request_row.evaluacion = evaluation
            db.add(request_row); db.flush(); ids["request"] = request_row.id
            credit = models.Credito(monto_aprobado=Decimal("1000"), saldo_pendiente=Decimal("900"), estado="VIGENTE",
                solicitud_credito_id=request_row.id, numero_credito=f"AD-{suffix}", cooperativa_id=coop.id,
                socio_id=socio.id, producto_credito_id=product.id, moneda_id=currency.id,
                tasa_interes=Decimal("12"), plazo_meses=9, tipo_amortizacion="FRANCES", usuario_id=user.id)
            credit.moneda_id = None  # Summary must use the originating request's currency as fallback.
            db.add(credit); db.flush(); ids["credit"] = credit.id
            db.add(models.PrediccionDeMorosidad(credito_id=credit.id, probabilidad_mora=Decimal("0.9000"),
                nivel_riesgo="ALTO", version_modelo="mora-test"))
            db.add(models.GestionCobranza(credito_id=credit.id, tipo_contacto="OTRO",
                resultado_gestion="Legacy collection seed", cooperativa_id=None, usuario_id=None))
            for number, due_date in enumerate((date.today()+timedelta(days=3),
                    date.today()-timedelta(days=3), date.today()-timedelta(days=6),
                    date.today()+timedelta(days=10)), 1):
                installment = models.TablaAmortizacion(numero_cuota=number, fecha_vencimiento=due_date,
                    monto_capital=Decimal("100"), monto_interes=Decimal("10"), monto_cuota_total=Decimal("110"),
                    estado_pago="PENDIENTE", credito_id=credit.id, monto_pagado=Decimal("0"))
                db.add(installment); db.flush(); ids["installments"].append(installment.id)
            db.commit()
            token, _ = create_access_token(str(user.id), "ADMINISTRADOR", coop.id)
            foreign_token, _ = create_access_token(str(foreign_user.id), "ADMINISTRADOR", foreign.id)
            reader_token, _ = create_access_token(str(reader.id), "CONTADOR", coop.id)
            assert request_row.evaluacion.id == ids["evaluation"]
        from app.api.v1.endpoints import creditos as creditos_endpoint
        monkeypatch.setattr(creditos_endpoint, "_refrescar_predicciones_mora", lambda db, credits: {
            "actualizados": len(credits), "bajo": 0, "medio": 0, "alto": len(credits)})
        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(app) as client:
            first = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert first.status_code == 200, first.text
            second = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert second.status_code == 200, second.text
            assert first.json()["alertas_creadas"] == 4
            assert second.json()["alertas_creadas"] == 0
            alerts = client.get("/api/v1/creditos/alertas", headers=headers)
            assert alerts.status_code == 200 and len(alerts.json()) == 4
            assert {alert["tipo"] for alert in alerts.json()} == {"CUOTA_POR_VENCER", "CUOTA_VENCIDA", "MORA", "RIESGO_ALTO"}
            active_by_type = {alert["tipo"]: alert["id"] for alert in alerts.json()}
            assert client.post(f"/api/v1/creditos/alertas/{active_by_type['CUOTA_POR_VENCER']}/atender",
                headers=headers, json={"tipo_contacto": "LLAMADA", "resultado_gestion": "short"}).status_code == 400
            assert client.post(f"/api/v1/creditos/alertas/{active_by_type['CUOTA_POR_VENCER']}/atender",
                headers=headers, json={"tipo_contacto": "LLAMADA", "resultado_gestion": "Contacto realizado", "fecha_compromiso_pago": str(date.today()-timedelta(days=1))}).status_code == 400
            assert client.post(f"/api/v1/creditos/alertas/{active_by_type['CUOTA_VENCIDA']}/descartar",
                headers=headers, json={"comentario": "no"}).status_code == 400
            summary = client.get("/api/v1/creditos/monitoreo/resumen", headers=headers)
            assert summary.status_code == 200, summary.text
            summary_data = summary.json()
            assert summary_data["por_moneda"][0]["moneda"] == "BOB"
            assert Decimal(str(summary_data["por_moneda"][0]["cartera_total"])) == Decimal("900.00")
            assert Decimal(str(summary_data["por_moneda"][0]["indice_mora"])) == Decimal("100.00")
            assert summary_data["creditos_por_riesgo"] == {"BAJO": 0, "MEDIO": 0, "ALTO": 1, "SIN_PREDICCION": 0}
            assert summary_data["alertas_activas"] == {"INFO": 1, "ADVERTENCIA": 2, "CRITICA": 1}
            assert len(summary_data["top_riesgo"]) == 1
            assert summary_data["top_riesgo"][0]["credito_id"] == ids["credit"]
            assert Decimal(str(summary_data["top_riesgo"][0]["probabilidad_mora"])) == Decimal("0.9000")
            foreign_headers = {"Authorization": f"Bearer {foreign_token}"}
            reader_headers = {"Authorization": f"Bearer {reader_token}"}
            assert client.get("/api/v1/creditos/alertas", headers=foreign_headers).json() == []
            assert client.get("/api/v1/creditos/alertas", headers=reader_headers).status_code == 200
            assert client.post("/api/v1/creditos/alertas/monitoreo", headers=reader_headers).status_code == 403
            assert client.post(f"/api/v1/creditos/alertas/{active_by_type['MORA']}/descartar",
                headers=foreign_headers, json={"comentario": "Cross tenant attempt"}).status_code == 404
            attended = client.post(f"/api/v1/creditos/alertas/{active_by_type['CUOTA_POR_VENCER']}/atender",
                headers=headers, json={"tipo_contacto": "LLAMADA", "resultado_gestion": "Contacto realizado con el socio", "fecha_compromiso_pago": str(date.today())})
            assert attended.status_code == 200, attended.text
            assert attended.json()["estado"] == "ATENDIDA"
            assert attended.json()["gestion"]["fecha_compromiso_pago"] == str(date.today())
            assert client.post(f"/api/v1/creditos/alertas/{active_by_type['CUOTA_POR_VENCER']}/atender",
                headers=headers, json={"tipo_contacto": "LLAMADA", "resultado_gestion": "Another attempt"}).status_code == 409
            dismissed = client.post(f"/api/v1/creditos/alertas/{active_by_type['CUOTA_VENCIDA']}/descartar",
                headers=headers, json={"comentario": "Sin contacto posible"})
            assert dismissed.status_code == 200, dismissed.text
            assert dismissed.json()["estado"] == "DESCARTADA"
            history = client.get(f"/api/v1/creditos/creditos/{ids['credit']}/gestiones", headers=headers)
            assert history.status_code == 200 and len(history.json()) == 2
            assert history.json()[0]["alerta_id"] == active_by_type["CUOTA_POR_VENCER"]
            assert any(item["resultado_gestion"] == "Legacy collection seed" for item in history.json())
            rerun = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert rerun.status_code == 200 and rerun.json()["alertas_creadas"] == 0
            with SessionLocal() as db:
                for installment_id in ids["installments"]:
                    db.get(models.TablaAmortizacion, installment_id).estado_pago = "PAGADA"
                prediction = db.query(models.PrediccionDeMorosidad).filter_by(credito_id=ids["credit"]).one()
                prediction.nivel_riesgo = "BAJO"; prediction.probabilidad_mora = Decimal("0.0500")
                db.commit()
            third = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert third.status_code == 200 and third.json()["alertas_resueltas"] == 2
            assert client.get("/api/v1/creditos/alertas", headers=headers).json() == []
            assert len(client.get("/api/v1/creditos/alertas?estado=ATENDIDA", headers=headers).json()) == 1
            assert len(client.get("/api/v1/creditos/alertas?estado=DESCARTADA", headers=headers).json()) == 1
            with SessionLocal() as db:
                db.get(models.Credito, ids["credit"]).estado = "VIGENTE"
                pending = db.get(models.TablaAmortizacion, ids["installments"][3])
                pending.estado_pago = "PENDIENTE"
                pending.fecha_vencimiento = date.today() + timedelta(days=3)
                db.commit()
            recurrence = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert recurrence.status_code == 200 and recurrence.json()["alertas_creadas"] == 1
            with SessionLocal() as db:
                db.get(models.Credito, ids["credit"]).estado = "CANCELADO"
                db.commit()
            cancelled = client.post("/api/v1/creditos/alertas/monitoreo", headers=headers)
            assert cancelled.status_code == 200 and cancelled.json()["alertas_resueltas"] == 1
        with SessionLocal() as db:
            assert db.query(models.AlertaCredito).filter_by(credito_id=ids["credit"], estado="RESUELTA").count() == 3
    finally:
        with SessionLocal() as db:
            if ids["credit"]:
                db.query(models.AlertaCredito).filter_by(credito_id=ids["credit"]).delete(synchronize_session=False)
                db.query(models.GestionCobranza).filter_by(credito_id=ids["credit"]).delete(synchronize_session=False)
                db.query(models.Morosidad).filter_by(credito_id=ids["credit"]).delete(synchronize_session=False)
                db.query(models.PrediccionDeMorosidad).filter_by(credito_id=ids["credit"]).delete(synchronize_session=False)
                db.query(models.TablaAmortizacion).filter_by(credito_id=ids["credit"]).delete(synchronize_session=False)
                db.query(models.Credito).filter_by(id=ids["credit"]).delete(synchronize_session=False)
            if ids["request"]: db.query(models.SolicitudCredito).filter_by(id=ids["request"]).delete(synchronize_session=False)
            if ids["evaluation"]: db.query(models.EvaluacionCampo).filter_by(id=ids["evaluation"]).delete(synchronize_session=False)
            if ids["socio"]: db.query(models.Socio).filter_by(id=ids["socio"]).delete(synchronize_session=False)
            if ids["product"]: db.query(models.ProductoCredito).filter_by(id=ids["product"]).delete(synchronize_session=False)
            if ids["user"]:
                db.query(models.Bitacora).filter_by(usuario_id=ids["user"]).delete(synchronize_session=False)
                db.query(models.Usuario).filter_by(id=ids["user"]).delete(synchronize_session=False)
            if ids["foreign_user"]:
                db.query(models.Bitacora).filter_by(usuario_id=ids["foreign_user"]).delete(synchronize_session=False)
                db.query(models.Usuario).filter_by(id=ids["foreign_user"]).delete(synchronize_session=False)
            if ids["reader"]:
                db.query(models.Bitacora).filter_by(usuario_id=ids["reader"]).delete(synchronize_session=False)
                db.query(models.Usuario).filter_by(id=ids["reader"]).delete(synchronize_session=False)
            if ids["coop"]: db.query(models.Cooperativa).filter_by(id=ids["coop"]).delete(synchronize_session=False)
            if ids["foreign_coop"]: db.query(models.Cooperativa).filter_by(id=ids["foreign_coop"]).delete(synchronize_session=False)
            db.commit()
