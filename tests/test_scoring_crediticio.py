"""Tests for CU-W23 credit scoring and automated dictamen."""

from pathlib import Path
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.schemas import schemas


def _db_disponible():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
_MIGRATION = Path(__file__).parents[1] / "migrations" / "016_sprint7_scoring_crediticio.sql"


def test_credit_scoring_models_and_schemas_are_defined():
    assert getattr(models, "EvaluacionCrediticia", None) is not None
    assert {
        "solicitud_credito_id",
        "cooperativa_id",
        "version_modelo",
        "score",
        "dictamen",
        "factores",
        "knockouts",
        "explicacion",
        "cuota_estimada",
        "relacion_cuota_ingreso",
        "resolucion",
        "resolucion_justificacion",
        "resolucion_usuario_id",
        "resolucion_fecha",
    } <= set(models.EvaluacionCrediticia.__table__.columns.keys())
    assert schemas.FactorOut
    assert schemas.KnockoutOut
    assert schemas.EvaluacionCrediticiaOut


def test_credit_scoring_migration_declares_history_checks_and_index():
    sql = _MIGRATION.read_text(encoding="utf-8").lower()
    assert "create table if not exists evaluacion_crediticia" in sql
    assert "check (score between 0 and 1000)" in sql
    assert "('aprobado', 'rechazado', 'revision_manual')" in sql
    assert "solicitud_credito_id, fecha desc" in sql


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_scoring_migration_table_is_applied():
    with engine.connect() as connection:
        relation = connection.execute(
            text("SELECT to_regclass('public.evaluacion_crediticia')")
        ).scalar_one()
    assert relation == "evaluacion_crediticia"


def _months_before(day: date, months: int) -> date:
    absolute_month = day.year * 12 + day.month - 1 - months
    year, month_zero_based = divmod(absolute_month, 12)
    month = month_zero_based + 1
    return date(year, month, min(day.day, 28))


def _request(
    *,
    income=Decimal("6000.00"),
    expense=Decimal("1000.00"),
    debts=Decimal("0.00"),
    rating="A",
    labor_months=36,
    member_months=24,
    max_ratio=Decimal("40.00"),
    amount=Decimal("1200.00"),
    amortization="FRANCES",
    currency_id=1,
):
    today = date(2026, 1, 1)
    evaluation = None
    if income is not None:
        evaluation = SimpleNamespace(
            ingreso_mensual=income,
            egreso_mensual=expense,
            cuota_deudas_mensual=debts,
            capacidad_pago=income - expense - debts,
            calificacion_asfi=rating,
            antiguedad_laboral_meses=labor_months,
        )
    return (
        SimpleNamespace(
            monto=amount,
            plazo_meses=12,
            tasa_interes=Decimal("18.00"),
            producto=SimpleNamespace(
                tipo_amortizacion=amortization,
                relacion_cuota_ingreso_max=max_ratio,
            ),
            moneda_id=currency_id,
            evaluacion=evaluation,
            socio=SimpleNamespace(fecha_registro=_months_before(today, member_months)),
        ),
        today,
    )


def _score(request, today, *, savings=(), has_credit_history=False, has_current_arrears=False):
    from app.services.scoring import score_application

    return score_application(
        request,
        savings_accounts=savings,
        has_credit_history=has_credit_history,
        has_current_arrears=has_current_arrears,
        today=today,
    )


def _points(result, code):
    return next(factor["puntos"] for factor in result["factores"] if factor["codigo"] == code)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(Decimal("20"), 300), (Decimal("30"), 210), (Decimal("40"), 120),
     (Decimal("40.004"), 0), (Decimal("40.01"), 0)],
)
def test_capacity_ratio_points_cover_each_band(ratio, expected):
    request, today = _request(income=Decimal("11002.00") / ratio)
    result = _score(request, today)
    assert _points(result, "CAPACIDAD_PAGO") == expected


@pytest.mark.parametrize(
    ("rating", "expected"), [("A", 200), ("B", 150), ("C", 80), ("D", 20), ("E", 0), ("F", 0), (None, 0)],
)
def test_asfi_points_cover_every_rating(rating, expected):
    request, today = _request(rating=rating)
    assert _points(_score(request, today), "CALIFICACION_ASFI") == expected


@pytest.mark.parametrize(
    ("months", "expected"), [(36, 100), (24, 80), (12, 60), (6, 30), (5, 0)],
)
def test_labor_seniority_points_cover_every_band(months, expected):
    request, today = _request(labor_months=months)
    assert _points(_score(request, today), "ANTIGUEDAD_LABORAL") == expected


@pytest.mark.parametrize(
    ("debt_percent", "expected"), [(0, 100), (10, 80), (Decimal("10.004"), 50),
                                    (20, 50), (30, 20), (31, 0)],
)
def test_debt_ratio_points_cover_every_band(debt_percent, expected):
    income = Decimal("1000.00")
    debts = income * Decimal(debt_percent) / Decimal("100")
    request, today = _request(income=income, expense=Decimal("100.00"), debts=debts)
    assert _points(_score(request, today), "ENDEUDAMIENTO") == expected


@pytest.mark.parametrize(
    ("months", "expected"), [(24, 100), (12, 70), (6, 40), (5, 10)],
)
def test_member_seniority_points_cover_every_band(months, expected):
    request, today = _request(member_months=months)
    assert _points(_score(request, today), "ANTIGUEDAD_SOCIO") == expected


@pytest.mark.parametrize(
    ("ratio_percent", "expected"), [(20, 100), (10, 70), (5, 40), (1, 20), (0, 0)],
)
def test_savings_points_cover_every_band_and_only_active_request_currency(ratio_percent, expected):
    request, today = _request()
    savings = [
        SimpleNamespace(saldo_disponible=Decimal("1200") * Decimal(ratio_percent) / 100,
                        estado="ACTIVA", moneda_id=1),
        SimpleNamespace(saldo_disponible=Decimal("1200"), estado="INACTIVA", moneda_id=1),
        SimpleNamespace(saldo_disponible=Decimal("1200"), estado="ACTIVA", moneda_id=2),
    ]
    assert _points(_score(request, today, savings=savings), "AHORRO") == expected


@pytest.mark.parametrize(
    ("has_credit_history", "has_current_arrears", "expected"),
    [(False, False, 50), (True, False, 100), (True, True, 0)],
)
def test_internal_credit_history_points_cover_all_outcomes(has_credit_history, has_current_arrears, expected):
    request, today = _request()
    result = _score(
        request,
        today,
        has_credit_history=has_credit_history,
        has_current_arrears=has_current_arrears,
    )
    assert _points(result, "HISTORIAL_INTERNO") == expected


@pytest.mark.parametrize(
    ("request_kwargs", "score_kwargs", "expected_codes", "expected_verdict"),
    [
        ({"income": Decimal("100.00"), "expense": Decimal("60.00")}, {},
         ["CUOTA_SUPERA_CAPACIDAD", "RATIO_SUPERA_MAXIMO"], "RECHAZADO"),
        ({"rating": "E"}, {}, ["CALIFICACION_ASFI_CRITICA"], "RECHAZADO"),
        ({"rating": "F"}, {}, ["CALIFICACION_ASFI_CRITICA"], "RECHAZADO"),
        ({"income": None}, {}, ["SIN_EVALUACION"], "RECHAZADO"),
        ({"income": Decimal("0.00")}, {},
         ["CUOTA_SUPERA_CAPACIDAD", "SIN_EVALUACION"], "RECHAZADO"),
        ({}, {"has_credit_history": True, "has_current_arrears": True},
         ["MORA_VIGENTE"], "REVISION_MANUAL"),
        ({"income": Decimal("10000.00"), "expense": Decimal("100.00"),
          "max_ratio": Decimal("1.00")}, {}, ["RATIO_SUPERA_MAXIMO"], "REVISION_MANUAL"),
    ],
)
def test_knockouts_are_listed_and_take_precedence(request_kwargs, score_kwargs, expected_codes, expected_verdict):
    request, today = _request(**request_kwargs)
    result = _score(request, today, **score_kwargs)
    assert [item["codigo"] for item in result["knockouts"]] == expected_codes
    assert result["dictamen"] == expected_verdict


def test_scoring_reuses_request_rate_snapshot_for_installment_and_returns_explanation():
    request, today = _request()
    request.tasa_interes = Decimal("18.00")
    request.producto.tasa_interes_anual = Decimal("99.00")
    result = _score(request, today)
    assert result["cuota_estimada"] == Decimal("110.02")
    assert result["version_modelo"] == "reglas-v1"
    assert "APROBADO" in result["explicacion"]
    assert result["score"] == sum(item["puntos"] for item in result["factores"])


@pytest.fixture(scope="module")
def scoring_client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def scoring_fixture():
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        coops = [
            models.Cooperativa(nombre=f"Scoring Test {suffix} {n}", estado="ACTIVO")
            for n in (1, 2)
        ]
        db.add_all(coops)
        db.flush()
        role_ids = {role.nombre: role.id for role in db.query(models.Rol).all()}
        users = {}
        for coop_number, coop in enumerate(coops, start=1):
            for role_name in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO", "CONTADOR"):
                user = models.Usuario(
                    correo=f"scoring-{suffix}-{coop_number}-{role_name.lower()}@test.invalid",
                    contrasena=hash_password("Password123"),
                    rol_id=role_ids[role_name],
                    cooperativa_id=coop.id,
                    nombre=f"Scoring {role_name} {coop_number}",
                    estado="ACTIVO",
                )
                db.add(user)
                users[(coop_number, role_name)] = user
        bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
        product = models.ProductoCredito(
            cooperativa_id=coops[0].id,
            codigo=f"SCORE-{suffix.upper()}",
            nombre="Scoring Fixture Product",
            moneda_id=bob_id,
            monto_min=Decimal("100.00"),
            monto_max=Decimal("50000.00"),
            plazo_min_meses=1,
            plazo_max_meses=60,
            tasa_interes_anual=Decimal("18.00"),
            tipo_amortizacion="FRANCES",
            dias_gracia_mora=0,
            tasa_mora_anual=Decimal("0.00"),
            relacion_cuota_ingreso_max=Decimal("40.00"),
            requiere_garantia=False,
            estado="ACTIVO",
        )
        db.add(product)
        db.flush()
        result = {
            "suffix": suffix,
            "coop_ids": [coop.id for coop in coops],
            "user_ids": [user.id for user in users.values()],
            "users": {},
            "product_id": product.id,
            "bob_id": bob_id,
            "socio_ids": [],
            "request_ids": [],
            "evaluation_ids": [],
        }
        for key, user in users.items():
            token, _ = create_access_token(str(user.id), key[1], user.cooperativa_id)
            result["users"][key] = {"id": user.id, "token": token}
        db.commit()

    def make_request(*, coop_number=1, state="PENDIENTE", product_enabled=True, rating="A"):
        with SessionLocal() as db:
            socio = models.Socio(
                cooperativa_id=result["coop_ids"][coop_number - 1],
                ci=f"SC-{suffix.upper()}-{len(result['socio_ids']) + 1}",
                nombre="Scoring",
                apellido="Applicant",
                estado="ACTIVO",
                fecha_registro=date(2020, 1, 1),
            )
            db.add(socio)
            db.flush()
            evaluator = db.query(models.Usuario).filter(
                models.Usuario.id == result["users"][(coop_number, "OFICIAL_CREDITO")]["id"]
            ).one()
            evaluation = models.EvaluacionCampo(
                ingreso_mensual=Decimal("6000.00"),
                egreso_mensual=Decimal("1000.00"),
                cuota_deudas_mensual=Decimal("0.00"),
                capacidad_pago=Decimal("5000.00"),
                actividad_economica="Comercio",
                fuente_ingresos="INDEPENDIENTE",
                antiguedad_laboral_meses=36,
                calificacion_asfi=rating,
                fecha=date.today(),
                usuario_id=evaluator.id,
                socio_id=socio.id,
            )
            db.add(evaluation)
            db.flush()
            request_row = models.SolicitudCredito(
                monto=Decimal("1200.00"),
                plazo_meses=12,
                tasa_interes=Decimal("18.00"),
                calificacion_asfi=rating,
                tiene_deudas=False,
                estado=state,
                socio_id=socio.id,
                usuario_id=evaluator.id,
                evaluacion_campo_id=evaluation.id,
                producto_credito_id=result["product_id"] if product_enabled else None,
                moneda_id=result["bob_id"] if product_enabled else None,
                cooperativa_id=result["coop_ids"][coop_number - 1],
                numero_solicitud=f"SCORE-{suffix}-{len(result['request_ids']) + 1}",
                destino="CAPITAL_TRABAJO",
            )
            db.add(request_row)
            db.commit()
            values = {
                "request_id": request_row.id,
                "socio_id": socio.id,
                "evaluation_id": evaluation.id,
            }
            result["socio_ids"].append(socio.id)
            result["request_ids"].append(request_row.id)
            result["evaluation_ids"].append(evaluation.id)
            return values

    result["make_request"] = make_request
    try:
        yield result
    finally:
        with SessionLocal() as db:
            db.query(models.EvaluacionCrediticia).filter(
                models.EvaluacionCrediticia.solicitud_credito_id.in_(result["request_ids"])
            ).delete(synchronize_session=False)
            db.query(models.SolicitudCredito).filter(
                models.SolicitudCredito.id.in_(result["request_ids"])
            ).delete(synchronize_session=False)
            db.query(models.EvaluacionCampo).filter(
                models.EvaluacionCampo.id.in_(result["evaluation_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Bitacora).filter(
                models.Bitacora.usuario_id.in_(result["user_ids"])
            ).delete(synchronize_session=False)
            db.query(models.CuentaAhorro).filter(
                models.CuentaAhorro.socio_id.in_(result["socio_ids"])
            ).delete(synchronize_session=False)
            db.query(models.ProductoCredito).filter(
                models.ProductoCredito.id == result["product_id"]
            ).delete(synchronize_session=False)
            db.query(models.Socio).filter(
                models.Socio.id.in_(result["socio_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Usuario).filter(
                models.Usuario.id.in_(result["user_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Cooperativa).filter(
                models.Cooperativa.id.in_(result["coop_ids"])
            ).delete(synchronize_session=False)
            db.commit()


def test_scoring_evaluation_routes_are_registered():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/creditos/solicitudes/{solicitud_id}/evaluacion"]
    assert "get" in paths["/api/v1/creditos/solicitudes/{solicitud_id}/evaluaciones"]
    assert "post" in paths["/api/v1/creditos/solicitudes/{solicitud_id}/resolucion"]


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_evaluation_updates_request_persists_history_and_adds_latest_summary(
    scoring_client, scoring_fixture
):
    request = scoring_fixture["make_request"]()
    token = scoring_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    before = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{request['request_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert before.status_code == 200, before.text
    assert before.json()["ultima_evaluacion"] is None
    response = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluacion",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    evaluation = response.json()
    assert evaluation["dictamen"] == "APROBADO"
    assert evaluation["version_modelo"] == "reglas-v1"
    assert evaluation["cuota_estimada"] == "110.02"
    assert len(evaluation["factores"]) == 7
    assert evaluation["resolucion"] is None

    detail = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{request['request_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["estado"] == "APROBADO"
    assert detail.json()["ultima_evaluacion"] == {
        "id": evaluation["id"],
        "score": evaluation["score"],
        "dictamen": "APROBADO",
        "fecha": evaluation["fecha"],
    }
    history = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluaciones",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history.status_code == 200, history.text
    assert [item["id"] for item in history.json()] == [evaluation["id"]]
    contador_token = scoring_fixture["users"][(1, "CONTADOR")]["token"]
    contador_history = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluaciones",
        headers={"Authorization": f"Bearer {contador_token}"},
    )
    assert contador_history.status_code == 200, contador_history.text

    with SessionLocal() as db:
        assert db.query(models.Bitacora).filter(
            models.Bitacora.usuario_id == scoring_fixture["users"][(1, "OFICIAL_CREDITO")]["id"],
            models.Bitacora.modulo == "CREDITOS",
            models.Bitacora.accion == "EVALUAR_SOLICITUD",
            models.Bitacora.descripcion.contains(str(request["request_id"])),
        ).count() == 1


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_evaluation_rechecks_state_roles_and_tenant(scoring_client, scoring_fixture):
    foreign = scoring_fixture["make_request"](coop_number=2)
    no_product = scoring_fixture["make_request"](product_enabled=False)
    final = scoring_fixture["make_request"](state="RECHAZADO")
    reader_token = scoring_fixture["users"][(1, "CAJERO")]["token"]
    local_token = scoring_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]

    denied = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{final['request_id']}/evaluacion",
        headers={"Authorization": f"Bearer {reader_token}"},
    )
    assert denied.status_code == 403
    hidden = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{foreign['request_id']}/evaluacion",
        headers={"Authorization": f"Bearer {local_token}"},
    )
    assert hidden.status_code == 404
    missing_product = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{no_product['request_id']}/evaluacion",
        headers={"Authorization": f"Bearer {local_token}"},
    )
    assert missing_product.status_code == 409
    wrong_tenant = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{foreign['request_id']}/evaluaciones",
        headers={"Authorization": f"Bearer {local_token}"},
    )
    assert wrong_tenant.status_code == 404
    non_evaluable = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{final['request_id']}/evaluacion",
        headers={"Authorization": f"Bearer {local_token}"},
    )
    assert non_evaluable.status_code == 409


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_manual_evaluation_can_be_re_evaluated_and_history_is_newest_first(
    scoring_client, scoring_fixture
):
    request = scoring_fixture["make_request"](rating="D")
    token = scoring_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    first = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluacion", headers=headers
    )
    assert first.status_code == 201, first.text
    assert first.json()["dictamen"] == "REVISION_MANUAL"
    second = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluacion", headers=headers
    )
    assert second.status_code == 201, second.text
    history = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluaciones", headers=headers
    )
    assert history.status_code == 200, history.text
    assert [item["id"] for item in history.json()] == [second.json()["id"], first.json()["id"]]


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_admin_resolves_manual_review_with_justification_and_audit(scoring_client, scoring_fixture):
    request = scoring_fixture["make_request"](rating="D")
    officer_token = scoring_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    admin = scoring_fixture["users"][(1, "ADMINISTRADOR")]
    evaluated = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluacion",
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert evaluated.status_code == 201, evaluated.text
    assert evaluated.json()["dictamen"] == "REVISION_MANUAL"

    response = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{request['request_id']}/resolucion",
        json={"decision": "APROBADO", "justificacion": "Aprobado tras revisión del comité."},
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert response.status_code == 200, response.text
    resolution = response.json()["resolucion"]
    assert resolution["decision"] == "APROBADO"
    assert resolution["justificacion"] == "Aprobado tras revisión del comité."
    assert resolution["usuario"]["id"] == admin["id"]
    detail = scoring_client.get(
        f"/api/v1/creditos/solicitudes/{request['request_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["estado"] == "APROBADO"
    with SessionLocal() as db:
        assert db.query(models.Bitacora).filter(
            models.Bitacora.usuario_id == admin["id"],
            models.Bitacora.modulo == "CREDITOS",
            models.Bitacora.accion == "RESOLVER_SOLICITUD",
            models.Bitacora.descripcion.contains(str(request["request_id"])),
        ).count() == 1


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_resolution_rejects_wrong_role_tenant_short_reason_and_non_manual_state(
    scoring_client, scoring_fixture
):
    manual = scoring_fixture["make_request"](rating="D")
    regular = scoring_fixture["make_request"]()
    officer_token = scoring_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    reader_token = scoring_fixture["users"][(1, "CAJERO")]["token"]
    admin_token = scoring_fixture["users"][(1, "ADMINISTRADOR")]["token"]
    foreign_admin_token = scoring_fixture["users"][(2, "ADMINISTRADOR")]["token"]
    for request in (manual, regular):
        response = scoring_client.post(
            f"/api/v1/creditos/solicitudes/{request['request_id']}/evaluacion",
            headers={"Authorization": f"Bearer {officer_token}"},
        )
        assert response.status_code == 201, response.text

    route = f"/api/v1/creditos/solicitudes/{manual['request_id']}/resolucion"
    payload = {"decision": "RECHAZADO", "justificacion": "Revisión negativa documentada."}
    denied = scoring_client.post(route, json=payload, headers={"Authorization": f"Bearer {reader_token}"})
    assert denied.status_code == 403
    hidden = scoring_client.post(
        route, json=payload, headers={"Authorization": f"Bearer {foreign_admin_token}"}
    )
    assert hidden.status_code == 404
    short = scoring_client.post(
        route,
        json={"decision": "RECHAZADO", "justificacion": "No aplica"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert short.status_code == 400
    not_manual = scoring_client.post(
        f"/api/v1/creditos/solicitudes/{regular['request_id']}/resolucion",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert not_manual.status_code == 409
