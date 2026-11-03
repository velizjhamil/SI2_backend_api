"""Plan feature access is enforced by public API dependencies."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.v1.deps import require_feature
from app.api.v1.router import api_router
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.models import (
    Bitacora, Cooperativa, EvaluacionCampo, EvaluacionCrediticia, Moneda,
    PlanSuscripcion, ProductoCredito, Rol, Socio, SolicitudCredito, Suscripcion, Usuario,
)

FEATURES = (
    "banca_movil",
    "scoring_crediticio",
    "informes_asfi",
    "respaldos_bitacora",
    "chatbot_ia",
    "notificaciones_socios",
    "recredito_ia",
)
MODULE_NAMES = {
    "banca_movil": "banca móvil y comprobantes digitales",
    "scoring_crediticio": "scoring crediticio",
    "informes_asfi": "informes ASFI",
    "respaldos_bitacora": "respaldos y bitácora",
    "chatbot_ia": "chatbot con IA y dictado por voz",
    "notificaciones_socios": "notificaciones a socios",
    "recredito_ia": "recomendación de recrédito con IA",
}
PLAN_NAMES = {"BASICO": "Básico", "CRECIMIENTO_PRO": "Crecimiento Pro", "RED_ENTERPRISE": "Red Enterprise"}
PLAN_FOR = {
    "BASICO": set(),
    "CRECIMIENTO_PRO": set(FEATURES) - {"recredito_ia"},
    "RED_ENTERPRISE": set(FEATURES),
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    for feature in FEATURES:
        def _probe(_user=Depends(require_feature(feature))):
            return {"feature": feature}
        app.add_api_route(f"/__test/features/{feature}", _probe)
    app.add_api_route(
        "/__test/features/reportes_gerenciales",
        lambda _user=Depends(require_feature("reportes_gerenciales")): {"allowed": True},
    )
    app.add_api_route(
        "/__test/features/socios_kyc",
        lambda _user=Depends(require_feature("socios_kyc")): {"allowed": True},
    )
    return TestClient(app)


class Tenant:
    def __init__(self, plan_code):
        self.plan_code = plan_code
        self.user_id = None
        self.coop_id = None
        self.socio_id = None
        self.plan_id = None

    def create(self, role="ADMINISTRADOR", with_subscription=True, linked_socio=False):
        with SessionLocal() as db:
            plan = db.execute(select(PlanSuscripcion).where(PlanSuscripcion.codigo == self.plan_code)).scalar_one_or_none()
            if plan is None:
                plan = PlanSuscripcion(codigo=self.plan_code, nombre="Plan desconocido test", moneda="BOB", estado="ACTIVO")
                db.add(plan)
                db.flush()
            self.plan_id = plan.id
            coop = Cooperativa(nombre=f"Feature {uuid4().hex}", estado="ACTIVO")
            role_row = db.execute(select(Rol).where(Rol.nombre == role)).scalar_one()
            db.add(coop)
            db.flush()
            user = Usuario(
                nombre="Feature test user", correo=f"feature-{uuid4().hex}@example.com",
                contrasena="not-used", rol_id=role_row.id, cooperativa_id=coop.id,
                estado="ACTIVO",
            )
            db.add(user)
            db.flush()
            if with_subscription:
                db.add(Suscripcion(cooperativa_id=coop.id, plan_id=plan.id, estado="ACTIVA"))
            if linked_socio:
                socio = Socio(
                    cooperativa_id=coop.id, ci=f"T{uuid4().hex[:18]}", nombre="Demo",
                    apellido="Socio", estado="ACTIVO", usuario_id=user.id,
                )
                db.add(socio)
                db.flush()
                self.socio_id = socio.id
            db.commit()
            self.coop_id, self.user_id = coop.id, user.id
            return self

    def headers(self):
        with SessionLocal() as db:
            user = db.get(Usuario, self.user_id)
            role = db.get(Rol, user.rol_id).nombre
        token, _ = create_access_token(self.user_id, role, self.coop_id)
        return {"Authorization": f"Bearer {token}"}

    def cleanup(self):
        if self.coop_id is None:
            return
        with SessionLocal() as db:
            db.execute(delete(Bitacora).where(Bitacora.cooperativa_id == self.coop_id))
            db.execute(delete(EvaluacionCrediticia).where(EvaluacionCrediticia.cooperativa_id == self.coop_id))
            db.execute(delete(SolicitudCredito).where(SolicitudCredito.cooperativa_id == self.coop_id))
            db.execute(delete(EvaluacionCampo).where(EvaluacionCampo.socio_id.in_(
                select(Socio.id).where(Socio.cooperativa_id == self.coop_id)
            )))
            db.execute(delete(ProductoCredito).where(ProductoCredito.cooperativa_id == self.coop_id))
            db.execute(delete(Socio).where(Socio.cooperativa_id == self.coop_id))
            db.execute(delete(Usuario).where((Usuario.cooperativa_id == self.coop_id) | (Usuario.id == self.user_id)))
            db.execute(delete(Suscripcion).where(Suscripcion.cooperativa_id == self.coop_id))
            db.execute(delete(Cooperativa).where(Cooperativa.id == self.coop_id))
            db.execute(delete(PlanSuscripcion).where(PlanSuscripcion.id == self.plan_id, PlanSuscripcion.codigo.like("UNKNOWN-%")))
            db.commit()


@pytest.fixture
def tenants():
    result = {code: Tenant(code).create() for code in PLAN_FOR}
    yield result
    for tenant in result.values():
        tenant.cleanup()


@pytest.mark.parametrize("feature", FEATURES)
def test_feature_dependency_allows_and_denies_by_plan(client, tenants, feature):
    for plan_code, allowed in PLAN_FOR.items():
        response = client.get(
            f"/__test/features/{feature}", headers=tenants[plan_code].headers()
        )
        if feature in allowed:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 403
            assert response.json() == {
                "detail": f"El módulo {MODULE_NAMES[feature]} no está incluido en el plan {PLAN_NAMES[plan_code]}."
            }


@pytest.mark.parametrize(
    ("path", "feature", "method", "payload"),
    [
        ("/api/v1/ahorros/mis-cuentas", "banca_movil", "get", None),
        ("/api/v1/creditos/precalificacion", "scoring_crediticio", "post", {
            "socio_id": 1, "producto_id": 1, "monto": 1000, "plazo_meses": 12,
            "ingreso_mensual": 5000, "egreso_mensual": 1000,
            "cuota_deudas_mensual": 0, "antiguedad_laboral_meses": 24,
        }),
        ("/api/v1/creditos/evaluaciones/999999999/explicacion", "scoring_crediticio", "get", None),
        ("/api/v1/informes-asfi/catalogo", "informes_asfi", "get", None),
        ("/api/v1/admin/bitacora", "respaldos_bitacora", "get", None),
        ("/api/v1/notificaciones/segmentos", "notificaciones_socios", "get", None),
        ("/api/v1/creditos/recreditos", "recredito_ia", "get", None),
    ],
)
def test_restricted_real_routes_use_the_feature_gate(client, tenants, path, feature, method, payload):
    caller = getattr(client, method)
    kwargs = {"headers": tenants["BASICO"].headers()}
    if payload is not None:
        kwargs["json"] = payload
    response = caller(path, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"] == f"El módulo {MODULE_NAMES[feature]} no está incluido en el plan Básico."


def test_basic_can_evaluate_and_resolve_credit_but_advanced_scoring_stays_restricted(client, request):
    tenant = Tenant("BASICO").create()
    request.addfinalizer(tenant.cleanup)
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        officer_role = db.execute(select(Rol).where(Rol.nombre == "OFICIAL_CREDITO")).scalar_one()
        officer = Usuario(
            nombre="Basic test officer", correo=f"basic-officer-{suffix}@example.com",
            contrasena="not-used", rol_id=officer_role.id,
            cooperativa_id=tenant.coop_id, estado="ACTIVO",
        )
        db.add(officer)
        bob_id = db.execute(select(Moneda.id).where(Moneda.codigo_iso == "BOB")).scalar_one()
        product = ProductoCredito(
            cooperativa_id=tenant.coop_id, codigo=f"BASIC-{suffix}", nombre="Basic approval test",
            moneda_id=bob_id, monto_min=Decimal("100"), monto_max=Decimal("50000"),
            plazo_min_meses=1, plazo_max_meses=60, tasa_interes_anual=Decimal("18"),
            tipo_amortizacion="FRANCES", dias_gracia_mora=0, tasa_mora_anual=Decimal("0"),
            relacion_cuota_ingreso_max=Decimal("40"), requiere_garantia=False,
            monto_aprobacion_directa=Decimal("2000"), estado="ACTIVO",
        )
        db.add(product)
        db.flush()
        socio = Socio(
            cooperativa_id=tenant.coop_id, ci=f"BASIC-{suffix}", nombre="Demo",
            apellido="Borrower", estado="ACTIVO", fecha_registro=date(2020, 1, 1),
        )
        db.add(socio)
        db.flush()
        field_eval = EvaluacionCampo(
            ingreso_mensual=Decimal("6000"), egreso_mensual=Decimal("1000"),
            cuota_deudas_mensual=Decimal("0"), capacidad_pago=Decimal("5000"),
            actividad_economica="Comercio", fuente_ingresos="INDEPENDIENTE",
            antiguedad_laboral_meses=36, calificacion_asfi="D", fecha=date.today(),
            usuario_id=officer.id, socio_id=socio.id,
        )
        db.add(field_eval)
        db.flush()
        request_row = SolicitudCredito(
            monto=Decimal("1200"), plazo_meses=12, tasa_interes=Decimal("18"),
            calificacion_asfi="D", tiene_deudas=False, estado="PENDIENTE",
            socio_id=socio.id, usuario_id=officer.id, evaluacion_campo_id=field_eval.id,
            producto_credito_id=product.id, moneda_id=bob_id,
            numero_solicitud=f"BASIC-{suffix}", destino="CAPITAL_TRABAJO",
            cooperativa_id=tenant.coop_id,
        )
        db.add(request_row)
        db.commit()
        request_id, officer_id = request_row.id, officer.id
        socio_id, product_id = socio.id, product.id

    officer_token, _ = create_access_token(officer_id, "OFICIAL_CREDITO", tenant.coop_id)
    officer_headers = {"Authorization": f"Bearer {officer_token}"}
    admin_headers = tenant.headers()
    evaluation = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/evaluacion", headers=officer_headers
    )
    assert evaluation.status_code == 201, evaluation.text
    assert evaluation.json()["dictamen"] == "REVISION_MANUAL"

    history = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/evaluaciones", headers=officer_headers
    )
    assert history.status_code == 200, history.text
    assert [item["id"] for item in history.json()] == [evaluation.json()["id"]]

    # This legacy resolver accepts EN_EVALUACION; the current scoring route sends manual reviews to committee.
    with SessionLocal() as db:
        db.query(SolicitudCredito).filter(SolicitudCredito.id == request_id).update({"estado": "EN_EVALUACION"})
        db.commit()
    resolved = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/resolucion",
        headers=admin_headers,
        json={"decision": "APROBADO", "justificacion": "Aprobado tras revisión documentada."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolucion"]["decision"] == "APROBADO"

    restricted_payload = {
        "socio_id": socio_id, "producto_id": product_id, "monto": 1200,
        "plazo_meses": 12, "ingreso_mensual": 6000, "egreso_mensual": 1000,
        "cuota_deudas_mensual": 0, "antiguedad_laboral_meses": 36,
    }
    precalificacion = client.post(
        "/api/v1/creditos/precalificacion", headers=officer_headers, json=restricted_payload
    )
    assert precalificacion.status_code == 403
    assert precalificacion.json()["detail"] == "El módulo scoring crediticio no está incluido en el plan Básico."
    explanation = client.get(
        f"/api/v1/creditos/evaluaciones/{evaluation.json()['id']}/explicacion", headers=officer_headers
    )
    assert explanation.status_code == 403
    assert explanation.json()["detail"] == "El módulo scoring crediticio no está incluido en el plan Básico."


def test_chatbot_and_both_voice_entrypoints_are_gated(client, tenants):
    headers = tenants["BASICO"].headers()
    for path, body in [
        ("/api/v1/chatbot/chat", {"mensaje": "ayuda", "historial": []}),
        ("/api/v1/reportes/comando-voz", {"texto": "reporte de cartera"}),
    ]:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "El módulo chatbot con IA y dictado por voz no está incluido en el plan Básico."
    response = client.post(
        "/api/v1/chatbot/transcribir", headers=headers,
        files={"audio": ("sample.wav", b"audio", "audio/wav")},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "El módulo chatbot con IA y dictado por voz no está incluido en el plan Básico."


def test_exceptions_unknown_plan_and_missing_subscription(client, tenants):
    app = client.app
    response = client.get("/__test/features/scoring_crediticio", headers=tenants["BASICO"].headers())
    assert response.status_code == 403

    # Unknown catalog codes fail closed to the common first-row capabilities.
    unknown = Tenant(f"UNKNOWN-{uuid4().hex}").create()
    try:
        assert client.get("/__test/features/scoring_crediticio", headers=unknown.headers()).status_code == 403
        assert client.get("/__test/features/socios_kyc", headers=unknown.headers()).status_code == 200
        denied = client.get("/__test/features/reportes_gerenciales", headers=unknown.headers())
        assert denied.status_code == 403
        assert denied.json()["detail"] == "El módulo reportes_gerenciales no está incluido en el plan Plan desconocido test."
        report = client.get("/api/v1/reportes/cartera", headers=unknown.headers())
        assert report.status_code == 403
        assert report.json()["detail"] == "El módulo reportes_gerenciales no está incluido en el plan Plan desconocido test."
    finally:
        unknown.cleanup()

    without_sub = Tenant("BASICO").create(with_subscription=False)
    try:
        assert client.get("/__test/features/scoring_crediticio", headers=without_sub.headers()).status_code == 200
    finally:
        without_sub.cleanup()

    superadmin = Tenant("BASICO").create(role="SUPERADMIN", with_subscription=False)
    no_coop = Tenant("BASICO").create(with_subscription=False)
    try:
        assert client.get("/__test/features/scoring_crediticio", headers=superadmin.headers()).status_code == 200
        # The no-cooperative exception is exercised using a user row whose tenant is NULL.
        with SessionLocal() as db:
            db.get(Usuario, no_coop.user_id).cooperativa_id = None
            db.commit()
        assert client.get("/__test/features/scoring_crediticio", headers=no_coop.headers()).status_code == 200
    finally:
        superadmin.cleanup()
        no_coop.cleanup()


def test_mobile_receipts_are_blocked_for_basic_and_allowed_for_growth(client, tenants):
    member = Tenant("BASICO").create(role="SOCIO", linked_socio=True)
    growth_member = Tenant("CRECIMIENTO_PRO").create(role="SOCIO", linked_socio=True)
    try:
        denied = client.get("/api/v1/socio/comprobantes", headers=member.headers())
        assert denied.status_code == 403
        assert denied.json()["detail"] == "El módulo banca móvil y comprobantes digitales no está incluido en el plan Básico."
        allowed = client.get("/api/v1/socio/comprobantes", headers=growth_member.headers())
        assert allowed.status_code != 403, allowed.text
    finally:
        member.cleanup()
        growth_member.cleanup()


def test_included_modules_and_field_officer_routes_remain_available(client, tenants):
    allowed_routes = [
        ("/api/v1/ahorros/mis-cuentas", "CRECIMIENTO_PRO"),
        ("/api/v1/creditos/solicitudes/999999999/evaluaciones", "CRECIMIENTO_PRO"),
        ("/api/v1/informes-asfi/catalogo", "CRECIMIENTO_PRO"),
        ("/api/v1/admin/bitacora", "CRECIMIENTO_PRO"),
        ("/api/v1/notificaciones/segmentos", "CRECIMIENTO_PRO"),
        ("/api/v1/creditos/recreditos", "RED_ENTERPRISE"),
    ]
    for path, plan_code in allowed_routes:
        response = client.get(path, headers=tenants[plan_code].headers())
        assert response.status_code != 403, f"{path}: {response.text}"

    growth_chat_voice = client.post(
        "/api/v1/chatbot/transcribir",
        headers=tenants["CRECIMIENTO_PRO"].headers(),
        files={"audio": ("invalid.bin", b"x", "application/octet-stream")},
    )
    assert growth_chat_voice.status_code == 400
    growth_report_voice = client.post(
        "/api/v1/reportes/comando-voz",
        headers=tenants["CRECIMIENTO_PRO"].headers(),
        json={"texto": "reporte de cartera"},
    )
    assert growth_report_voice.status_code != 403, growth_report_voice.text

    field_officer = Tenant("BASICO").create(role="OFICIAL_CREDITO")
    try:
        response = client.get("/api/v1/creditos/solicitudes", headers=field_officer.headers())
        assert response.status_code == 200, response.text
    finally:
        field_officer.cleanup()


def test_basic_plan_active_member_cap_returns_409(client, monkeypatch):
    import app.api.v1.endpoints.socios_kyc as socios_kyc

    monkeypatch.setattr(socios_kyc, "LIMITE_SOCIOS_BASICO", 1, raising=False)
    tenant = Tenant("BASICO").create()
    try:
        first = client.post("/api/v1/socios/registro", headers=tenant.headers(), json={
            "ci": f"C{uuid4().hex[:19]}", "nombre": "Socio", "apellido": "Uno"
        })
        assert first.status_code == 201, first.text
        second = client.post("/api/v1/socios/registro", headers=tenant.headers(), json={
            "ci": f"C{uuid4().hex[:19]}", "nombre": "Socio", "apellido": "Dos"
        })
        assert second.status_code == 409
        assert second.json()["detail"] == "Se alcanzó el límite de 1 socios activos del plan Básico."
    finally:
        tenant.cleanup()


def _crear_socio_para_limite(tenant, estado):
    with SessionLocal() as db:
        socio = Socio(
            cooperativa_id=tenant.coop_id, ci=f"L{uuid4().hex[:18]}",
            nombre="Demo", apellido="Member", estado=estado,
        )
        db.add(socio)
        db.commit()
        return socio.id


def test_reactivar_socio_sobre_el_limite_rechaza_sin_modificarlo(client, monkeypatch, request):
    import app.api.v1.endpoints.socios_kyc as socios_kyc

    monkeypatch.setattr(socios_kyc, "LIMITE_SOCIOS_BASICO", 1)
    tenant = Tenant("BASICO").create()
    request.addfinalizer(tenant.cleanup)
    _crear_socio_para_limite(tenant, "ACTIVO")
    inactivo_id = _crear_socio_para_limite(tenant, "INACTIVO")

    response = client.put(
        f"/api/v1/socios/{inactivo_id}", headers=tenant.headers(),
        json={"estado": "ACTIVO", "nombre": "No debe guardarse"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Se alcanzó el límite de 1 socios activos del plan Básico."
    with SessionLocal() as db:
        socio = db.get(Socio, inactivo_id)
        assert socio.estado == "INACTIVO"
        assert socio.nombre == "Demo"


def test_editar_socio_activo_con_el_limite_alcanzado_no_requiere_cupo(client, monkeypatch, request):
    import app.api.v1.endpoints.socios_kyc as socios_kyc

    monkeypatch.setattr(socios_kyc, "LIMITE_SOCIOS_BASICO", 1)
    tenant = Tenant("BASICO").create()
    request.addfinalizer(tenant.cleanup)
    socio_id = _crear_socio_para_limite(tenant, "ACTIVO")

    response = client.put(
        f"/api/v1/socios/{socio_id}", headers=tenant.headers(),
        json={"estado": "ACTIVO", "nombre": "Socio editado"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["estado"] == "ACTIVO"
    assert response.json()["nombre"] == "Socio editado"


def test_reactivar_socio_con_cupo_disponible_permite_el_cambio(client, monkeypatch, request):
    import app.api.v1.endpoints.socios_kyc as socios_kyc

    monkeypatch.setattr(socios_kyc, "LIMITE_SOCIOS_BASICO", 1)
    tenant = Tenant("BASICO").create()
    request.addfinalizer(tenant.cleanup)
    socio_id = _crear_socio_para_limite(tenant, "INACTIVO")

    response = client.put(
        f"/api/v1/socios/{socio_id}", headers=tenant.headers(),
        json={"estado": "ACTIVO"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["estado"] == "ACTIVO"


def test_basic_plan_catalog_does_not_advertise_asfi(client):
    from app.services.suscripciones import PLANES_DEFAULT

    tenant = Tenant("BASICO").create()
    with SessionLocal() as db:
        plan = db.execute(select(PlanSuscripcion).where(PlanSuscripcion.codigo == "BASICO")).scalar_one()
        original_features, original_incluye = plan.features, plan.incluye
        defaults = next(item for item in PLANES_DEFAULT if item["codigo"] == "BASICO")
        plan.features, plan.incluye = defaults["features"], defaults["incluye"]
        db.commit()
    try:
        response = client.get("/api/v1/suscripciones/planes", headers=tenant.headers())
        assert response.status_code == 200
        plan_data = next(item for item in response.json() if item["codigo"] == "BASICO")
        assert "ASFI" not in str(plan_data.get("features", []))
        assert "ASFI" not in str(plan_data.get("incluye", []))
    finally:
        with SessionLocal() as db:
            plan = db.execute(select(PlanSuscripcion).where(PlanSuscripcion.codigo == "BASICO")).scalar_one()
            plan.features, plan.incluye = original_features, original_incluye
            db.commit()
        tenant.cleanup()
