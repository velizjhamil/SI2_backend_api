from datetime import date
import csv
from decimal import Decimal
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from app.api.v1.router import api_router
from app.api.v1.endpoints import estados_financieros
from app.api.v1.endpoints.informes_asfi import _get_prevision_contable
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models

from app.services.informes_asfi import (
    base_prevision_hipotecaria,
    monto_prevision,
    PREVISIONES_ESPECIFICAS,
    categoria_por_mora,
    porcentaje_prevision_especifica,
    porcentaje_prevision_ciclica,
)


@pytest.mark.parametrize(
    ("tipo", "dias", "categoria"),
    [
        ("MICROCREDITO", 5, "A"), ("MICROCREDITO", 6, "B"),
        ("MICROCREDITO", 30, "B"), ("MICROCREDITO", 31, "C"),
        ("MICROCREDITO", 55, "C"), ("MICROCREDITO", 56, "D"),
        ("MICROCREDITO", 75, "D"), ("MICROCREDITO", 76, "E"),
        ("MICROCREDITO", 90, "E"), ("MICROCREDITO", 91, "F"),
        ("MICROCREDITO_AGROPECUARIO", 40, "A"),
        ("MICROCREDITO_AGROPECUARIO", 41, "B"),
        ("MICROCREDITO_AGROPECUARIO", 60, "B"),
        ("MICROCREDITO_AGROPECUARIO", 61, "C"),
        ("MICROCREDITO_AGROPECUARIO", 105, "C"),
        ("MICROCREDITO_AGROPECUARIO", 106, "D"),
        ("MICROCREDITO_AGROPECUARIO", 155, "D"),
        ("MICROCREDITO_AGROPECUARIO", 156, "E"),
        ("MICROCREDITO_AGROPECUARIO", 180, "E"),
        ("MICROCREDITO_AGROPECUARIO", 181, "F"),
        ("VIVIENDA_HIPOTECARIA", 30, "A"), ("VIVIENDA_HIPOTECARIA", 31, "B"),
        ("VIVIENDA_HIPOTECARIA", 90, "B"), ("VIVIENDA_HIPOTECARIA", 91, "C"),
        ("VIVIENDA_HIPOTECARIA", 180, "C"), ("VIVIENDA_HIPOTECARIA", 181, "D"),
        ("VIVIENDA_HIPOTECARIA", 270, "D"), ("VIVIENDA_HIPOTECARIA", 271, "E"),
        ("VIVIENDA_HIPOTECARIA", 360, "E"), ("VIVIENDA_HIPOTECARIA", 361, "F"),
        ("CONSUMO", 5, "A"), ("CONSUMO", 6, "B"), ("CONSUMO", 91, "F"),
    ],
)
def test_categoria_por_mora_limites_asfi(tipo, dias, categoria):
    assert categoria_por_mora(tipo, dias) == categoria


def test_categoria_al_dia_es_a_y_tipo_no_clasificado_es_error():
    assert categoria_por_mora("MICROCREDITO", 0) == "A"
    with pytest.raises(ValueError, match="tipo de crédito ASFI"):
        categoria_por_mora(None, 0)


def test_tabla_prevision_especifica_versionada_cubre_porcentajes_reglamentarios():
    assert PREVISIONES_ESPECIFICAS["circular"] == "ASFI/954/26 (06/2026), RNSF Libro 3 Título II Capítulo IV Sección 3 Art. 1"
    assert porcentaje_prevision_especifica("A", "MICROCREDITO", "BOB", True) == Decimal("0")
    assert porcentaje_prevision_especifica("A", "MICROCREDITO", "USD", True) == Decimal("2.5")
    assert porcentaje_prevision_especifica("A", "MICROCREDITO", "BOB", False) == Decimal("0.25")
    assert porcentaje_prevision_especifica("B", "MICROCREDITO", "BOB", False) == Decimal("5")
    assert porcentaje_prevision_especifica("A", "VIVIENDA_HIPOTECARIA", "BOB", False) == Decimal("0.25")
    assert porcentaje_prevision_especifica("A", "VIVIENDA_SIN_GARANTIA", "BOB", False) == Decimal("3")
    assert porcentaje_prevision_especifica("A", "VIVIENDA_HIPOTECARIA", "USD", False) == Decimal("2.5")
    assert porcentaje_prevision_especifica("A", "VIVIENDA_SIN_GARANTIA", "USD", False) == Decimal("7")
    assert porcentaje_prevision_especifica("A", "CONSUMO", "BOB", False) == Decimal("3")
    assert porcentaje_prevision_especifica("B", "CONSUMO", "USD", False) == Decimal("12")
    assert porcentaje_prevision_especifica("F", "CONSUMO", "BOB", False) == Decimal("100")


@pytest.mark.parametrize(("tipo", "moneda", "categoria", "productivo", "esperada"), [
    ("MICROCREDITO", "BOB", "A", True, "0"),
    ("MICROCREDITO", "BOB", "A", False, "0.25"),
    ("MICROCREDITO_AGROPECUARIO", "BOB", "B", True, "2.5"),
    ("MICROCREDITO_AGROPECUARIO", "BOB", "B", False, "5"),
    ("MICROCREDITO", "USD", "A", True, "2.5"),
    ("MICROCREDITO_AGROPECUARIO", "USD", "B", False, "5"),
    ("VIVIENDA_HIPOTECARIA", "BOB", "A", False, "0.25"),
    ("VIVIENDA_HIPOTECARIA", "BOB", "B", False, "5"),
    ("VIVIENDA_SIN_GARANTIA", "BOB", "A", False, "3"),
    ("VIVIENDA_SIN_GARANTIA", "BOB", "B", False, "6.5"),
    ("VIVIENDA_HIPOTECARIA", "USD", "A", False, "2.5"),
    ("VIVIENDA_HIPOTECARIA", "USD", "B", False, "5"),
    ("VIVIENDA_SIN_GARANTIA", "USD", "A", False, "7"),
    ("VIVIENDA_SIN_GARANTIA", "USD", "B", False, "12"),
    ("CONSUMO", "BOB", "A", False, "3"),
    ("CONSUMO", "BOB", "B", False, "6.5"),
    ("CONSUMO", "USD", "A", False, "7"),
    ("CONSUMO", "USD", "B", False, "12"),
])
def test_prevision_specifica_por_tipo_moneda_categoria_y_productividad(tipo, moneda, categoria, productivo, esperada):
    assert porcentaje_prevision_especifica(categoria, tipo, moneda, productivo) == Decimal(esperada)


@pytest.mark.parametrize("categoria,esperada", [("C", "20"), ("D", "50"), ("E", "80"), ("F", "100")])
@pytest.mark.parametrize("tipo", ["MICROCREDITO", "MICROCREDITO_AGROPECUARIO", "CONSUMO",
                                    "VIVIENDA_HIPOTECARIA", "VIVIENDA_SIN_GARANTIA"])
@pytest.mark.parametrize("moneda", ["BOB", "USD"])
def test_prevision_specifica_cat_c_a_f_es_comun_a_tipo_y_moneda(tipo, moneda, categoria, esperada):
    assert porcentaje_prevision_especifica(categoria, tipo, moneda) == Decimal(esperada)


def test_garantia_hipotecaria_aplica_deduccion_reglamentaria_y_redondeo():
    base = base_prevision_hipotecaria(Decimal("1000"), Decimal("1000"))
    assert base == Decimal("575.000")
    assert monto_prevision(base, porcentaje_prevision_especifica("B", "VIVIENDA_HIPOTECARIA", "BOB")) == Decimal("28.75")


@pytest.mark.parametrize(
    ("tipo", "moneda", "esperada"),
    [("VIVIENDA_HIPOTECARIA", "BOB", "1.05"), ("VIVIENDA_SIN_GARANTIA", "USD", "1.80"),
     ("CONSUMO", "BOB", "1.45"), ("CONSUMO", "USD", "2.60"),
     ("MICROCREDITO", "BOB", "1.10"), ("MICROCREDITO_AGROPECUARIO", "USD", "1.90")],
)
def test_prevision_ciclica_solo_categoria_a_por_tipo_y_moneda(tipo, moneda, esperada):
    assert porcentaje_prevision_ciclica("A", tipo, moneda) == Decimal(esperada)
    assert porcentaje_prevision_ciclica("B", tipo, moneda) == Decimal("0")


def test_calificacion_cartera_route_is_registered():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    assert "/api/v1/informes-asfi/calificacion-cartera" in app.openapi()["paths"]


def test_prevision_contable_uses_w32_rolled_account_139_by_currency(monkeypatch):
    class Result:
        def __init__(self, values):
            self.values = values
        def scalars(self):
            return self
        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.calls = 0
        def execute(self, statement):
            self.calls += 1
            return Result([1] if self.calls == 1 else [(1, "BOB")])

    monkeypatch.setattr(estados_financieros, "_posted_lines", lambda *args, **kwargs: [
        {"moneda_id": 1},
    ])
    monkeypatch.setattr(estados_financieros, "_rolled_accounts", lambda *args, **kwargs: [
        {"account": {"codigo": "139.01"}, "amount": Decimal("-125.40")},
        {"account": {"codigo": "100.01"}, "amount": Decimal("300.00")},
    ])
    assert _get_prevision_contable(DB(), 7, date(2026, 6, 30)) == {"BOB": Decimal("125.40")}


def test_all_w34_report_routes_are_registered():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    paths = set(app.openapi()["paths"])
    assert {
        "/api/v1/informes-asfi/estados-financieros",
        "/api/v1/informes-asfi/cartera-deudores",
        "/api/v1/informes-asfi/generar",
        "/api/v1/informes-asfi/reportes",
        "/api/v1/informes-asfi/reportes/{reporte_id}",
        "/api/v1/informes-asfi/reportes/{reporte_id}/export",
        "/api/v1/informes-asfi/catalogo",
    } <= paths


def _db_available():
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="PostgreSQL no disponible")
def test_legacy_report_id_one_is_invisible_to_cooperative_tenant():
    from fastapi.testclient import TestClient
    from main import app

    suffix = uuid4().hex[:12]
    with SessionLocal() as db:
        cooperative = models.Cooperativa(nombre=f"ASFI tenant {suffix}", razon_social="Test Ltda.", estado="ACTIVO")
        db.add(cooperative)
        db.flush()
        role_id = db.query(models.Rol.id).filter(models.Rol.nombre == "CONTADOR").scalar()
        user = models.Usuario(correo=f"asfi-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                              rol_id=role_id, cooperativa_id=cooperative.id, nombre="ASFI Counter", estado="ACTIVO")
        db.add(user)
        db.flush()
        token, _ = create_access_token(str(user.id), "CONTADOR", cooperative.id)
        user_id, coop_id = user.id, cooperative.id
        db.commit()
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            history = client.get("/api/v1/informes-asfi/reportes", headers=headers)
            assert history.status_code == 200, history.text
            assert history.json() == []
            detail = client.get("/api/v1/informes-asfi/reportes/1", headers=headers)
            assert detail.status_code == 404
            export = client.get("/api/v1/informes-asfi/reportes/1/export", headers=headers)
            assert export.status_code == 404
    finally:
        with SessionLocal() as db:
            db.query(models.Usuario).filter(models.Usuario.id == user_id).delete(synchronize_session=False)
            db.query(models.Cooperativa).filter(models.Cooperativa.id == coop_id).delete(synchronize_session=False)
            db.commit()


@pytest.mark.skipif(not _db_available(), reason="PostgreSQL no disponible")
def test_generation_replacement_integrity_export_and_future_cutoff(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from app.api.v1.endpoints import informes_asfi

    suffix = uuid4().hex[:12]
    with SessionLocal() as db:
        cooperative = models.Cooperativa(nombre=f"ASFI generation {suffix}", razon_social="Test Ltda.", estado="ACTIVO")
        db.add(cooperative)
        db.flush()
        role_id = db.query(models.Rol.id).filter(models.Rol.nombre == "ADMINISTRADOR").scalar()
        user = models.Usuario(correo=f"asfi-gen-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                              rol_id=role_id, cooperativa_id=cooperative.id, nombre="ASFI Admin", estado="ACTIVO")
        db.add(user)
        db.flush()
        token, _ = create_access_token(str(user.id), "ADMINISTRADOR", cooperative.id)
        user_id, coop_id = user.id, cooperative.id
        db.commit()
    monkeypatch.setattr(informes_asfi, "registrar_accion", lambda *args, **kwargs: None)
    created_ids = []
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            body = {"tipo": "CALIFICACION_CARTERA", "fecha_corte": "2026-06-30"}
            first = client.post("/api/v1/informes-asfi/generar", headers=headers, json=body)
            assert first.status_code == 201, first.text
            created_ids.append(first.json()["id"])
            second = client.post("/api/v1/informes-asfi/generar", headers=headers, json=body)
            assert second.status_code == 201, second.text
            created_ids.append(second.json()["id"])
            history = client.get("/api/v1/informes-asfi/reportes?tipo=CALIFICACION_CARTERA", headers=headers)
            assert history.status_code == 200
            assert [r["estado"] for r in history.json()] == ["GENERADO", "REEMPLAZADO"]
            detail = client.get(f"/api/v1/informes-asfi/reportes/{created_ids[0]}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["integridad_ok"] is True
            export = client.get(f"/api/v1/informes-asfi/reportes/{created_ids[0]}/export", headers=headers)
            assert export.status_code == 200
            assert export.content.startswith(b"\xef\xbb\xbf")
            future = client.post("/api/v1/informes-asfi/generar", headers=headers,
                                 json={"tipo": "CALIFICACION_CARTERA", "fecha_corte": "2999-01-01"})
            assert future.status_code == 422
        with SessionLocal() as db:
            db.query(models.Reporte).filter(models.Reporte.id == created_ids[0]).update(
                {models.Reporte.contenido: {"tampered": True}}, synchronize_session=False)
            db.commit()
        with TestClient(app) as client:
            detail = client.get(f"/api/v1/informes-asfi/reportes/{created_ids[0]}",
                                headers={"Authorization": f"Bearer {token}"})
            assert detail.status_code == 200
            assert detail.json()["integridad_ok"] is False
    finally:
        with SessionLocal() as db:
            if created_ids:
                db.query(models.Reporte).filter(models.Reporte.id.in_(created_ids)).delete(synchronize_session=False)
            db.query(models.Usuario).filter(models.Usuario.id == user_id).delete(synchronize_session=False)
            db.query(models.Cooperativa).filter(models.Cooperativa.id == coop_id).delete(synchronize_session=False)
            db.commit()


def test_catalogo_has_out_of_scope_reasons_and_sources():
    from app.api.v1.endpoints.informes_asfi import catalogo
    from types import SimpleNamespace

    result = catalogo(SimpleNamespace(rol=SimpleNamespace(nombre="CONTADOR"), cooperativa_id=1))
    entries = {item["codigo"]: item for item in result}
    assert entries["CAP"]["implementado"] is False
    assert "ponderación" in entries["CAP"]["motivo"]
    assert entries["CIC_OFICIAL"]["implementado"] is False
    assert entries["CIC_OFICIAL"]["fuente_url"].startswith("https://")
    assert entries["UIF"]["motivo"]


def test_w34_mora_ignora_gracia_interna_en_previews_y_snapshot_payloads(monkeypatch):
    from app.api.v1.endpoints import informes_asfi

    cutoff = date(2026, 6, 30)
    credit = SimpleNamespace(
        solicitud_credito_id=1, estado="VIGENTE", numero_credito="ASFI-001",
        producto=SimpleNamespace(tipo_credito_asfi="MICROCREDITO", nombre="Micro",
                                 sector_productivo=False, tasa_mora_anual=Decimal("0"),
                                 dias_gracia_mora=3),
        solicitud=SimpleNamespace(producto=None, moneda=None, garantias=[]),
        socio=SimpleNamespace(ci="123", nombre="Ana", apellido="Test"),
        moneda=SimpleNamespace(codigo_iso="BOB"), saldo_pendiente=Decimal("1000"),
        monto_aprobado=Decimal("1000"), fecha_desembolso=date(2026, 1, 1),
        cronograma=[SimpleNamespace(estado_pago="PENDIENTE", monto_capital=Decimal("100"),
                                    monto_cuota_total=Decimal("100"),
                                    fecha_vencimiento=date(2026, 6, 24))],
    )

    class Result:
        def unique(self): return self
        def scalars(self): return self
        def all(self): return [credit]

    class DB:
        def execute(self, _statement): return Result()

    monkeypatch.setattr(informes_asfi, "_unclassified_credit_warnings", lambda *args: [])
    monkeypatch.setattr(informes_asfi, "_get_prevision_contable", lambda *args: {})
    user = SimpleNamespace(rol=SimpleNamespace(nombre="CONTADOR"), cooperativa_id=7)
    db = DB()

    rating_preview = informes_asfi.calificacion_cartera(cutoff, user, db)
    debtors_preview = informes_asfi._debtors_data(db, 7, cutoff)
    rating_snapshot = informes_asfi._payload("CALIFICACION_CARTERA", cutoff, user, db)
    debtors_snapshot = informes_asfi._payload("CARTERA_DEUDORES", cutoff, user, db)

    for row in (rating_preview["creditos"][0], rating_snapshot["creditos"][0]):
        assert (row["dias_mora"], row["categoria"]) == (6, "B")
    for payload in (debtors_preview, debtors_snapshot):
        row = payload["cartera_deudores"][0]
        assert (row["dias_mora"], row["categoria"]) == (6, "B")
    assert "gracia" not in rating_preview["criterio"].lower()


@pytest.mark.parametrize(("tipo", "payload", "headers", "expected"), [
    ("CALIFICACION_CARTERA", {
        "creditos": [{"numero_credito": "C-1", "socio": {"ci": "1", "nombre": "Ana"},
                      "producto": "Micro", "tipo_credito_asfi": "MICROCREDITO", "sector_productivo": False,
                      "moneda": "BOB", "saldo_capital": "100.00", "porcentaje": "5.00",
                      "base_prevision": "100.00", "garantia_hipotecaria_aplicada": False,
                      "dias_mora": 6, "categoria": "B", "prevision_especifica": "5.00",
                      "prevision_ciclica": "0.00"}],
        "por_categoria": [{"categoria": "B", "moneda": "BOB", "creditos": 1,
                            "saldo_capital": "100.00", "prevision_especifica": "5.00",
                            "prevision_ciclica": "0.00"}],
    }, ["tipo_fila", "numero_credito", "socio_ci", "socio_nombre", "producto", "tipo_credito_asfi",
        "sector_productivo", "moneda", "saldo_capital", "dias_mora", "categoria", "porcentaje",
        "base_prevision", "prevision_especifica", "prevision_ciclica", "garantia_hipotecaria_aplicada"],
       [["CREDITO", "C-1", "1", "Ana", "Micro", "MICROCREDITO", "False", "BOB", "100.00", "6",
         "B", "5.00", "100.00", "5.00", "0.00", "False"],
        ["TOTAL", "", "", "", "", "", "", "BOB", "100.00", "", "B", "", "", "5.00", "0.00", ""]]),
    ("CARTERA_DEUDORES", {"cartera_deudores": [{"ci": "1", "nombre": "Ana", "numero_credito": "C-1",
        "tipo_credito_asfi": "MICROCREDITO", "moneda": "BOB", "monto_desembolsado": "150.00",
        "saldo": "100.00", "fecha_desembolso": "2026-01-01", "fecha_vencimiento_final": "2026-12-31", "dias_mora": 6,
        "categoria": "B", "prevision": "5.00"}]},
        ["ci", "nombre", "numero_credito", "tipo_credito_asfi", "moneda", "monto_desembolsado", "saldo",
         "fecha_desembolso", "fecha_vencimiento_final", "dias_mora", "categoria", "prevision"],
        [["1", "Ana", "C-1", "MICROCREDITO", "BOB", "150.00", "100.00", "2026-01-01", "2026-12-31", "6", "B", "5.00"]]),
    ("ESTADOS_FINANCIEROS", {"balance_general": {"secciones": {"ACTIVO": {"1": {
        "codigo": "1", "nombre": "Caja", "monto": "50.00", "cuentas": []}}}},
        "estado_resultados": {"lineas": [{"componentes": [{"codigo": "4", "nombre": "Ingresos",
        "monto": "10.00"}]}]}},
        ["seccion", "codigo", "nombre", "monto"],
        [["balance_general", "1", "Caja", "50.00"], ["estado_resultados", "4", "Ingresos", "10.00"]]),
])
def test_exporta_csv_tabular_por_tipo_con_bom_y_nombre(tipo, payload, headers, expected, monkeypatch):
    from app.api.v1.endpoints import informes_asfi

    report = SimpleNamespace(id=42, tipo=tipo, periodo=date(2026, 6, 30), contenido=payload)
    monkeypatch.setattr(informes_asfi, "_get_tenant_report", lambda *args: report)
    user = SimpleNamespace(rol=SimpleNamespace(nombre="CONTADOR"), cooperativa_id=7)
    response = informes_asfi.exportar_reporte(42, user, None)

    assert response.body.startswith(b"\xef\xbb\xbf")
    assert 'filename="informe-asfi-42.csv"' in response.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert rows[0] == headers
    assert rows[1:] == expected
