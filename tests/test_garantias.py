"""Contract tests for CU-W22 guarantees."""
from pathlib import Path
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import inspect, text

from app.db.session import engine
from app.models import models
from app.schemas import schemas


def _crear_fixture_garantias_impl(ids):
    from app.core.security import create_access_token, hash_password
    from app.db.session import SessionLocal

    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        coop = models.Cooperativa(nombre=f"Guarantee Test {suffix}", estado="ACTIVO")
        db.add(coop); db.flush(); ids["coop"] = coop.id
        roles = {role.nombre: role.id for role in db.query(models.Rol).filter(
            models.Rol.nombre.in_(["OFICIAL_CREDITO", "ADMINISTRADOR"])).all()}
        oficial = models.Usuario(correo=f"guar-o-{suffix}@test.invalid", contrasena=hash_password("Password123"),
            rol_id=roles["OFICIAL_CREDITO"], cooperativa_id=coop.id, nombre="Oficial Garantias", estado="ACTIVO")
        admin = models.Usuario(correo=f"guar-a-{suffix}@test.invalid", contrasena=hash_password("Password123"),
            rol_id=roles["ADMINISTRADOR"], cooperativa_id=coop.id, nombre="Admin Garantias", estado="ACTIVO")
        admin_two = models.Usuario(correo=f"guar-a2-{suffix}@test.invalid", contrasena=hash_password("Password123"),
            rol_id=roles["ADMINISTRADOR"], cooperativa_id=coop.id, nombre="Admin Verificador", estado="ACTIVO")
        db.add_all([oficial, admin, admin_two]); db.flush()
        ids["oficial"], ids["admin"], ids["admin_two"] = oficial.id, admin.id, admin_two.id
        bob = db.query(models.Moneda).filter_by(codigo_iso="BOB").one()
        product = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"G-{suffix}", nombre="Garantias",
            moneda_id=bob.id, monto_min=Decimal("100"), monto_max=Decimal("10000"), plazo_min_meses=1,
            plazo_max_meses=36, tasa_interes_anual=Decimal("12"), tipo_amortizacion="FRANCES",
            dias_gracia_mora=0, tasa_mora_anual=Decimal("0"), relacion_cuota_ingreso_max=Decimal("80"),
            requiere_garantia=True, estado="ACTIVO")
        db.add(product); db.flush(); ids["producto"] = product.id
        socio = models.Socio(cooperativa_id=coop.id, ci=f"G-{suffix}", nombre="Member", apellido="One",
            estado="ACTIVO", fecha_registro=date(2020, 1, 1))
        db.add(socio); db.flush(); ids["socio"] = socio.id
        evaluation = models.EvaluacionCampo(ingreso_mensual=Decimal("2000"), egreso_mensual=Decimal("500"),
            capacidad_pago=Decimal("1500"), cuota_deudas_mensual=Decimal("0"), actividad_economica="Comercio",
            fuente_ingresos="INDEPENDIENTE", antiguedad_laboral_meses=24, calificacion_asfi="A",
            fecha=date.today(), usuario_id=oficial.id, socio_id=socio.id)
        db.add(evaluation); db.flush(); ids["evaluacion"] = evaluation.id
        request = models.SolicitudCredito(monto=Decimal("1000"), plazo_meses=12, tasa_interes=Decimal("12"),
            estado="PENDIENTE", socio_id=socio.id, usuario_id=oficial.id, evaluacion_campo_id=evaluation.id,
            producto_credito_id=product.id, moneda_id=bob.id, cooperativa_id=coop.id,
            numero_solicitud=f"G-{suffix}", destino="OTRO")
        db.add(request); db.flush(); ids["solicitud"] = request.id
        db.commit()
        ids["bob"] = bob.id
        ids["oficial_token"] = create_access_token(str(oficial.id), "OFICIAL_CREDITO", coop.id)[0]
        ids["admin_token"] = create_access_token(str(admin.id), "ADMINISTRADOR", coop.id)[0]
        ids["admin_two_token"] = create_access_token(str(admin_two.id), "ADMINISTRADOR", coop.id)[0]
    return ids


def _crear_fixture_garantias():
    ids = {name: None for name in ("coop", "oficial", "admin", "admin_two", "socio", "producto", "evaluacion", "solicitud")}
    try:
        return _crear_fixture_garantias_impl(ids)
    except BaseException:
        _limpiar_fixture_garantias(ids)
        raise


def _limpiar_fixture_garantias(ids):
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        if ids.get("solicitud"):
            db.query(models.Garantia).filter_by(solicitud_credito_id=ids["solicitud"]).delete(synchronize_session=False)
            db.query(models.EvaluacionCrediticia).filter_by(solicitud_credito_id=ids["solicitud"]).delete(synchronize_session=False)
            db.query(models.SolicitudCredito).filter_by(id=ids["solicitud"]).delete(synchronize_session=False)
        if ids.get("evaluacion"):
            db.query(models.EvaluacionCampo).filter_by(id=ids["evaluacion"]).delete(synchronize_session=False)
        if ids.get("producto"):
            db.query(models.ProductoCredito).filter_by(id=ids["producto"]).delete(synchronize_session=False)
        if ids.get("socio"):
            db.query(models.Socio).filter_by(id=ids["socio"]).delete(synchronize_session=False)
        user_ids = [ids[k] for k in ("oficial", "admin", "admin_two") if ids.get(k)]
        audit_scope = []
        if user_ids:
            audit_scope.append(models.Bitacora.usuario_id.in_(user_ids))
        if ids.get("coop"):
            audit_scope.append(models.Bitacora.cooperativa_id == ids["coop"])
        if audit_scope:
            from sqlalchemy import or_
            db.query(models.Bitacora).filter(or_(*audit_scope)).delete(synchronize_session=False)
        if user_ids:
            db.query(models.Usuario).filter(models.Usuario.id.in_(user_ids)).delete(synchronize_session=False)
        if ids.get("coop"):
            db.query(models.Cooperativa).filter_by(id=ids["coop"]).delete(synchronize_session=False)
        db.commit()


def test_collateral_registration_list_update_delete_and_haircut():
    from fastapi.testclient import TestClient
    from main import app

    ids = _crear_fixture_garantias()
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {ids['oficial_token']}"}
            url = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/garantias"
            created = client.post(url, headers=headers, json={"tipo": "HIPOTECARIA", "descripcion": "Casa urbana",
                "moneda_id": ids["bob"], "valor_comercial": "1000.01"})
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["valor_realizable"] == "700.01"
            assert body["estado"] == "REGISTRADA"
            pledged = client.post(url, headers=headers, json={"tipo": "PRENDARIA", "descripcion": "Vehiculo",
                "moneda_id": ids["bob"], "valor_comercial": "1000.00"})
            assert pledged.status_code == 201 and pledged.json()["valor_realizable"] == "500.00"
            personal = client.post(url, headers=headers, json={"tipo": "PERSONAL", "descripcion": "Aval personal",
                "avalista": {"nombre": "Avalista Uno", "ci": "AV-1", "ingreso_mensual": "500.00",
                    "relacion": "Familiar"}})
            assert personal.status_code == 201, personal.text
            assert personal.json()["valor_comercial"] is None
            assert personal.json()["valor_realizable"] == "1000.00"
            listed = client.get(url, headers=headers)
            assert listed.status_code == 200 and len(listed.json()) == 3
            edited = client.put(f"/api/v1/creditos/garantias/{body['id']}", headers=headers,
                json={"valor_comercial": "1200.00"})
            assert edited.status_code == 200, edited.text
            assert edited.json()["valor_realizable"] == "840.00"
            deleted = client.delete(f"/api/v1/creditos/garantias/{body['id']}", headers=headers)
            assert deleted.status_code == 204
            remaining = client.get(url, headers=headers).json()
            assert len(remaining) == 2 and all(item["id"] != body["id"] for item in remaining)
    finally:
        _limpiar_fixture_garantias(ids)


def test_personal_guarantor_ci_matching_borrower_is_rejected_on_create_and_update():
    from fastapi.testclient import TestClient
    from main import app
    from app.db.session import SessionLocal

    ids = _crear_fixture_garantias()
    try:
        with SessionLocal() as db:
            borrower = db.query(models.Socio).filter_by(ci="5551234").one()
            db.get(models.SolicitudCredito, ids["solicitud"]).socio_id = borrower.id
            db.commit()

        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {ids['oficial_token']}"}
            url = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/garantias"
            for ci in ("5551234", " 5551234 ", " 555 1234 "):
                response = client.post(url, headers=headers, json={"tipo": "PERSONAL",
                    "descripcion": "Aval del mismo socio", "avalista": {"nombre": "Socio Uno", "ci": ci,
                        "ingreso_mensual": "1000.00", "relacion": "Solicitante"}})
                assert response.status_code == 400, response.text

            created = client.post(url, headers=headers, json={"tipo": "PERSONAL",
                "descripcion": "Aval inicial", "avalista": {"nombre": "Avalista Uno", "ci": "OTRO-1",
                    "ingreso_mensual": "1000.00", "relacion": "Familiar"}})
            assert created.status_code == 201, created.text
            updated = client.put(f"/api/v1/creditos/garantias/{created.json()['id']}", headers=headers,
                json={"avalista": {"nombre": "Socio Uno", "ci": " 555 1234 ",
                    "ingreso_mensual": "1000.00", "relacion": "Solicitante"}})
            assert updated.status_code == 400, updated.text
    finally:
        _limpiar_fixture_garantias(ids)


def test_maker_checker_and_coverage_only_count_verified_guarantees():
    from fastapi.testclient import TestClient
    from main import app

    ids = _crear_fixture_garantias()
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {ids['oficial_token']}"}
            url = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/garantias"
            created = client.post(url, headers=headers, json={"tipo": "HIPOTECARIA", "descripcion": "Casa",
                "moneda_id": ids["bob"], "valor_comercial": "1000.00"})
            assert created.status_code == 201, created.text
            guarantee_id = created.json()["id"]
            coverage_url = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/cobertura"
            before = client.get(coverage_url, headers=headers)
            assert before.status_code == 200, before.text
            assert before.json()["valor_realizable_verificado"] == "0.00"
            assert before.json()["valor_realizable_pendiente"] == "700.00"
            request_out = client.get(f"/api/v1/creditos/solicitudes/{ids['solicitud']}", headers=headers)
            assert request_out.status_code == 200 and request_out.json()["cobertura"]["cumple"] is False
            same_user = client.post(f"/api/v1/creditos/garantias/{guarantee_id}/verificacion", headers=headers,
                json={"decision": "VERIFICADA", "observacion": "Documentación revisada"})
            assert same_user.status_code == 403
            admin_headers = {"Authorization": f"Bearer {ids['admin_token']}"}
            verified = client.post(f"/api/v1/creditos/garantias/{guarantee_id}/verificacion", headers=admin_headers,
                json={"decision": "VERIFICADA", "observacion": "Documentación revisada"})
            assert verified.status_code == 200, verified.text
            assert verified.json()["estado"] == "VERIFICADA"
            assert verified.json()["usuario_verificacion"]["id"] == ids["admin"]
            after = client.get(coverage_url, headers=headers).json()
            assert after["valor_realizable_verificado"] == "700.00"
            assert after["valor_realizable_pendiente"] == "0.00"
            assert after["porcentaje_cobertura"] == "70.00"
            assert after["cumple"] is False
    finally:
        _limpiar_fixture_garantias(ids)


def test_admin_cannot_verify_own_guarantee_but_second_admin_can():
    from fastapi.testclient import TestClient
    from main import app

    ids = _crear_fixture_garantias()
    guarantee_id = None
    try:
        with TestClient(app) as client:
            url = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/garantias"
            admin_headers = {"Authorization": f"Bearer {ids['admin_token']}"}
            created = client.post(url, headers=admin_headers, json={"tipo": "HIPOTECARIA", "descripcion": "Casa",
                "moneda_id": ids["bob"], "valor_comercial": "1000.00"})
            assert created.status_code == 201, created.text
            guarantee_id = created.json()["id"]
            verify_url = f"/api/v1/creditos/garantias/{guarantee_id}/verificacion"
            body = {"decision": "VERIFICADA", "observacion": "Verificación aprobada"}
            self_verify = client.post(verify_url, headers=admin_headers, json=body)
            assert self_verify.status_code == 403, self_verify.text
            second_admin = {"Authorization": f"Bearer {ids['admin_two_token']}"}
            allowed = client.post(verify_url, headers=second_admin, json=body)
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["estado"] == "VERIFICADA"
            assert allowed.json()["usuario_verificacion"]["id"] == ids["admin_two"]
    finally:
        _limpiar_fixture_garantias(ids)


def test_evaluation_endpoint_routes_insufficient_required_coverage_to_committee():
    from fastapi.testclient import TestClient
    from main import app

    ids = _crear_fixture_garantias()
    try:
        with TestClient(app) as client:
            official = {"Authorization": f"Bearer {ids['oficial_token']}"}
            admin = {"Authorization": f"Bearer {ids['admin_token']}"}
            guarantees = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/garantias"
            created = client.post(guarantees, headers=official, json={"tipo": "HIPOTECARIA", "descripcion": "Casa",
                "moneda_id": ids["bob"], "valor_comercial": "1000.00"})
            assert created.status_code == 201, created.text
            guarantee_id = created.json()["id"]
            verified = client.post(f"/api/v1/creditos/garantias/{guarantee_id}/verificacion", headers=admin,
                json={"decision": "VERIFICADA", "observacion": "Documentación verificada"})
            assert verified.status_code == 200, verified.text
            evaluated = client.post(f"/api/v1/creditos/solicitudes/{ids['solicitud']}/evaluacion", headers=official)
            assert evaluated.status_code == 201, evaluated.text
            result = evaluated.json()
            assert result["dictamen"] == "REVISION_MANUAL"
            assert any(item["codigo"] == "GARANTIA_INSUFICIENTE" for item in result["knockouts"])
            request_out = client.get(f"/api/v1/creditos/solicitudes/{ids['solicitud']}", headers=official)
            assert request_out.status_code == 200, request_out.text
            assert request_out.json()["estado"] == "EN_COMITE"
    finally:
        _limpiar_fixture_garantias(ids)


def test_scoring_adds_guarantee_knockout_without_changing_factor_points():
    from datetime import date
    from types import SimpleNamespace
    from app.services.scoring import score_application

    application = SimpleNamespace(
        monto=Decimal("1200.00"), plazo_meses=12, tasa_interes=Decimal("12.00"), moneda_id=1,
        evaluacion=SimpleNamespace(ingreso_mensual=Decimal("6000"), egreso_mensual=Decimal("1000"),
            cuota_deudas_mensual=Decimal("0"), calificacion_asfi="A", antiguedad_laboral_meses=36),
        producto=SimpleNamespace(relacion_cuota_ingreso_max=Decimal("80"), tipo_amortizacion="FRANCES",
            requiere_garantia=True, cobertura_minima_garantia=Decimal("100.00")),
        socio=SimpleNamespace(fecha_registro=date(2020, 1, 1)),
        garantias=[SimpleNamespace(estado="VERIFICADA", valor_realizable=Decimal("700.00"))],
    )
    result = score_application(application, today=date(2026, 9, 27))
    assert result["version_modelo"] == "reglas-v2"
    assert result["dictamen"] == "REVISION_MANUAL"
    assert any(item["codigo"] == "GARANTIA_INSUFICIENTE" and item["efecto"] == "REVISION_MANUAL"
        for item in result["knockouts"])
    assert [factor["puntos"] for factor in result["factores"]] == [300, 200, 100, 100, 100, 0, 50]


def test_payoff_release_marks_only_verified_guarantees_liberada():
    from types import SimpleNamespace
    from app.api.v1.endpoints.creditos import _marcar_garantias_liberadas

    verified = SimpleNamespace(estado="VERIFICADA", fecha_liberacion=None)
    pending = SimpleNamespace(estado="REGISTRADA", fecha_liberacion=None)
    rejected = SimpleNamespace(estado="RECHAZADA", fecha_liberacion=None)
    _marcar_garantias_liberadas([verified, pending, rejected])
    assert verified.estado == "LIBERADA" and verified.fecha_liberacion is not None
    assert pending.estado == "REGISTRADA" and pending.fecha_liberacion is None
    assert rejected.estado == "RECHAZADA" and rejected.fecha_liberacion is None


def test_alert_validation_detail_strips_pydantic_value_error_prefix():
    from datetime import date, timedelta
    import pytest
    from fastapi import HTTPException
    from app.api.v1.endpoints.creditos import _validar_payload_alerta
    from app.schemas.schemas import GestionCreateIn

    with pytest.raises(HTTPException) as caught:
        _validar_payload_alerta(GestionCreateIn, {"tipo_contacto": "LLAMADA",
            "resultado_gestion": "Contacto realizado",
            "fecha_compromiso_pago": str(date.today() - timedelta(days=1))})
    assert caught.value.status_code == 400
    assert not caught.value.detail.startswith("Value error, ")


def test_invalid_guarantee_and_verification_payloads_return_400_string_detail():
    from fastapi.testclient import TestClient
    from main import app

    ids = _crear_fixture_garantias()
    try:
        with TestClient(app) as client:
            official = {"Authorization": f"Bearer {ids['oficial_token']}"}
            admin = {"Authorization": f"Bearer {ids['admin_token']}"}
            base = f"/api/v1/creditos/solicitudes/{ids['solicitud']}/garantias"
            missing_currency = client.post(base, headers=official, json={"tipo": "PRENDARIA",
                "descripcion": "Vehiculo sin moneda", "valor_comercial": "1000.00"})
            assert missing_currency.status_code == 400, missing_currency.text
            assert isinstance(missing_currency.json()["detail"], str)
            self_aval = client.post(base, headers=official, json={"tipo": "PERSONAL",
                "descripcion": "Aval propio", "avalista": {"nombre": "Socio Uno", "ci": "BORROWER",
                    "ingreso_mensual": "1000.00", "relacion": "Solicitante", "socio_id": ids["socio"]}})
            assert self_aval.status_code == 400 and isinstance(self_aval.json()["detail"], str)
            invalid_verification = client.post(f"/api/v1/creditos/garantias/999999/verificacion", headers=admin,
                json={"decision": "APPROVED", "observacion": "No valido"})
            assert invalid_verification.status_code == 400, invalid_verification.text
            assert isinstance(invalid_verification.json()["detail"], str)
    finally:
        _limpiar_fixture_garantias(ids)


def test_guarantee_fixture_setup_failure_cleans_committed_rows(monkeypatch):
    import pytest
    from sqlalchemy import text
    from app.core import security
    from app.db.session import engine

    with engine.connect() as connection:
        before = connection.execute(text("SELECT COUNT(*) FROM cooperativa WHERE nombre LIKE 'Guarantee Test %'")).scalar_one()
    def fail_token(*args, **kwargs):
        raise RuntimeError("injected fixture setup error")
    monkeypatch.setattr(security, "create_access_token", fail_token)
    with pytest.raises(RuntimeError, match="injected fixture setup error"):
        _crear_fixture_garantias()
    with engine.connect() as connection:
        after = connection.execute(text("SELECT COUNT(*) FROM cooperativa WHERE nombre LIKE 'Guarantee Test %'")).scalar_one()
    assert after == before


def test_product_create_and_update_round_trip_guarantee_threshold_as_strings():
    from fastapi.testclient import TestClient
    from app.db.session import SessionLocal
    from main import app

    ids = _crear_fixture_garantias()
    product_id = None
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {ids['admin_token']}"}
            suffix = uuid4().hex[:8].upper()
            created = client.post("/api/v1/creditos/productos", headers=headers, json={
                "codigo": f"GT-{suffix}", "nombre": "Guarantee threshold test", "moneda_id": ids["bob"],
                "monto_min": "100.00", "monto_max": "10000.00", "plazo_min_meses": 1,
                "plazo_max_meses": 36, "tasa_interes_anual": "12.00", "tipo_amortizacion": "FRANCES",
                "relacion_cuota_ingreso_max": "80.00", "requiere_garantia": True,
                "cobertura_minima_garantia": "123.45",
            })
            assert created.status_code == 201, created.text
            product_id = created.json()["id"]
            assert created.json()["cobertura_minima_garantia"] == "123.45"
            updated = client.put(f"/api/v1/creditos/productos/{product_id}", headers=headers,
                json={"cobertura_minima_garantia": "150.25"})
            assert updated.status_code == 200, updated.text
            assert updated.json()["cobertura_minima_garantia"] == "150.25"
    finally:
        if product_id is not None:
            with SessionLocal() as db:
                db.query(models.ProductoCredito).filter_by(id=product_id).delete(synchronize_session=False)
                db.commit()
        _limpiar_fixture_garantias(ids)


def test_guarantee_migration_models_and_product_schemas_are_declared():
    migration = Path(__file__).parents[1] / "migrations" / "022_sprint10_garantias.sql"
    assert migration.exists()
    sql = migration.read_text(encoding="utf-8").lower()
    assert "create table if not exists garantia" in sql
    assert "cobertura_minima_garantia" in sql
    assert models.Garantia.__tablename__ == "garantia"
    assert "socio_avalista_id" in models.Garantia.__table__.columns
    assert schemas.GarantiaOut and schemas.CoberturaOut
    assert "cobertura_minima_garantia" in schemas.ProductoCreditoCreate.model_fields
    assert "cobertura_minima_garantia" in schemas.ProductoCreditoUpdate.model_fields
    assert "cobertura_minima_garantia" in schemas.ProductoCreditoOut.model_fields


def test_guarantee_migration_is_replayable_and_preserves_custom_product_value():
    migration = Path(__file__).parents[1] / "migrations" / "022_sprint10_garantias.sql"
    sql = migration.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql(sql)
    with engine.begin() as connection:
        product_id = connection.execute(text("SELECT id FROM producto_credito ORDER BY id LIMIT 1")).scalar_one()
        previous = connection.execute(text(
            "SELECT cobertura_minima_garantia FROM producto_credito WHERE id=:id"), {"id": product_id}
        ).scalar_one()
        connection.execute(text(
            "UPDATE producto_credito SET cobertura_minima_garantia=37.25 WHERE id=:id"), {"id": product_id}
        )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(sql)
        with engine.connect() as connection:
            actual = connection.execute(text(
                "SELECT cobertura_minima_garantia FROM producto_credito WHERE id=:id"), {"id": product_id}
            ).scalar_one()
            assert str(actual) == "37.25"
            assert connection.execute(text("SELECT COUNT(*) FROM garantia")).scalar_one() >= 0
    finally:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE producto_credito SET cobertura_minima_garantia=:value WHERE id=:id"),
                {"value": previous, "id": product_id},
            )
