from uuid import uuid4
"""CU-W23 part 2 additive API and persistence contract tests."""
from pathlib import Path
from sqlalchemy import inspect
from app.db.session import engine
from app.models import models
from app.schemas import schemas


def test_migration_019_is_additive_and_idempotent():
    sql=(Path(__file__).parents[1]/"migrations/019_sprint8_recredito_ia.sql").read_text().lower()
    assert "create table if not exists oferta_recredito" in sql
    assert "create table if not exists prediccion_de_morosidad" in sql
    assert "add column if not exists probabilidad_mora" in sql
    assert "where estado = 'vigente'" in sql
    assert "truncate" not in sql and "delete from" not in sql


def test_models_schemas_include_advisory_model_fields_and_offers():
    assert models.OfertaRecredito.__tablename__ == "oferta_recredito"
    assert models.PrediccionDeMorosidad.__tablename__ == "prediccion_de_morosidad"
    assert {"probabilidad_mora", "nivel_riesgo", "version_modelo_mora"} <= set(models.EvaluacionCrediticia.__table__.columns.keys())
    assert "probabilidad_mora" in schemas.EvaluacionCrediticiaOut.model_fields
    assert "nivel_riesgo" in schemas.MoraCreditoOut.model_fields
    assert schemas.OfertaOut and schemas.GeneracionRecreditosOut and schemas.OfertaAceptadaOut


def test_local_migration_019_tables_and_columns_exist():
    insp=inspect(engine)
    assert insp.has_table("oferta_recredito") and insp.has_table("prediccion_de_morosidad")
    assert {"probabilidad_mora", "nivel_riesgo", "version_modelo_mora"} <= {c["name"] for c in insp.get_columns("evaluacion_crediticia")}


def test_model_card_is_declared_synthetic_and_runtime_json_is_present():
    from app.services.modelo_mora import ficha_modelo
    card=ficha_modelo()
    assert card["dataset"] == "SINTETICO" and card["n_muestras"] == 5000 and card["semilla"] == 2026
    assert (Path(__file__).parents[1]/"app/ml/MODEL_CARD.md").exists()


def test_provider_payload_contains_only_non_personal_rule_signals():
    from app.api.v1.endpoints.creditos import _llm_payload
    from types import SimpleNamespace
    evaluation=SimpleNamespace(dictamen="APROBADO", score=800, factores=[{"codigo":"AHORRO","puntos":100,"motivo":"Ahorro favorable"}], knockouts=[], nivel_riesgo="BAJO", nombre="Nombre Socio", ci="123456")
    payload=_llm_payload(evaluation)
    assert payload == {"dictamen":"APROBADO","score":800,"factores":[{"codigo":"AHORRO","puntos":100,"motivo":"Factor AHORRO aplicado"}],"knockouts":[],"nivel_riesgo":"BAJO"}
    assert "nombre" not in str(payload) and "ci" not in str(payload) and "1234" not in str(payload)


def test_provider_payload_sanitizes_untrusted_factor_and_knockout_text():
    from app.api.v1.endpoints.creditos import _llm_payload
    from types import SimpleNamespace
    private_reason="Applicant Alice Example CI 9876543 account held by Alice"
    evaluation=SimpleNamespace(dictamen="OBSERVADO",score=400,factores=[{"codigo":"ASFI","puntos":-50,"motivo":private_reason}],knockouts=[{"codigo":"IDENTIDAD","motivo":private_reason}],nivel_riesgo="MEDIO")
    payload=_llm_payload(evaluation)
    assert private_reason not in str(payload) and "Alice Example" not in str(payload) and "9876543" not in str(payload)
    assert payload["factores"] == [{"codigo":"ASFI","puntos":-50,"motivo":"Factor ASFI aplicado"}]
    assert payload["knockouts"] == [{"codigo":"IDENTIDAD","motivo":"Criterio de elegibilidad no cumplido"}]


def test_llm_provider_is_mocked_and_falls_back_on_http_error(monkeypatch):
    from app.api.v1.endpoints.creditos import _llm_explanation
    import httpx
    seen={}
    class Good:
        def raise_for_status(self): pass
        def json(self): return {"content":[{"text":"Explicación simulada"}]}
    def fake_post(url, **kwargs):
        seen["url"]=url; seen["body"]=kwargs["json"]; return Good()
    monkeypatch.setattr(httpx,"post",fake_post)
    assert _llm_explanation("anthropic","fake-key","mock-model",{"dictamen":"APROBADO"}) == "Explicación simulada"
    assert "fake-key" not in str(seen["body"])
    class Bad:
        def raise_for_status(self): raise RuntimeError("mock failure")
    monkeypatch.setattr(httpx,"post",lambda *a,**k:Bad())
    assert _llm_explanation("anthropic","fake-key","mock-model",{}) is None

# Endpoint acceptance fixtures create disposable tenants and remove every row in finally.
import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import models


@pytest.fixture
def recredito_case():
    suffix = uuid4().hex[:10]
    ids = {"coops": [], "users": [], "socios": [], "evals": [], "requests": [], "credits": [], "offers": []}
    try:
        with SessionLocal() as db:
            coop = models.Cooperativa(nombre=f"Recredito Test {suffix}", estado="ACTIVO")
            foreign = models.Cooperativa(nombre=f"Recredito Foreign {suffix}", estado="ACTIVO")
            db.add_all((coop, foreign)); db.flush()
            ids["coops"] += [coop.id, foreign.id]
            roles={r.nombre:r.id for r in db.query(models.Rol).all()}
            users={}
            for tenant, c in (("main",coop),("foreign",foreign)):
                for role in ("ADMINISTRADOR","OFICIAL_CREDITO","CAJERO"):
                    u=models.Usuario(correo=f"rec-{suffix}-{tenant}-{role.lower()}@test.invalid",contrasena=hash_password("Password123"),rol_id=roles[role],cooperativa_id=c.id,nombre=f"Private User {suffix}",estado="ACTIVO")
                    db.add(u); db.flush(); ids["users"].append(u.id)
                    token,_=create_access_token(str(u.id),role,c.id); users[(tenant,role)]=token
            currency=db.query(models.Moneda).filter(models.Moneda.codigo_iso=="BOB").one()
            product=models.ProductoCredito(cooperativa_id=coop.id,codigo=f"RC-{suffix.upper()}",nombre="Recredito test product",moneda_id=currency.id,monto_min=Decimal("100"),monto_max=Decimal("2000"),plazo_min_meses=6,plazo_max_meses=12,tasa_interes_anual=Decimal("12"),tipo_amortizacion="FRANCES",dias_gracia_mora=5,tasa_mora_anual=Decimal("0"),relacion_cuota_ingreso_max=Decimal("80"),requiere_garantia=False,estado="ACTIVO")
            db.add(product); db.flush(); ids["product"]=product.id; ids["currency"]=currency.id
            db.commit()
        from main import app
        with TestClient(app) as client:
            def member(tenant="main", *, income=Decimal("6000"), asfi="A"):
                with SessionLocal() as db:
                    coop_id=ids["coops"][0 if tenant=="main" else 1]
                    official_id=next(u.id for u in db.query(models.Usuario).filter(models.Usuario.cooperativa_id==coop_id).all() if db.get(models.Rol,u.rol_id).nombre=="OFICIAL_CREDITO")
                    socio=models.Socio(cooperativa_id=coop_id,ci=f"RC-{suffix}-{len(ids['socios'])}",nombre="Private Name",apellido="Secret",estado="ACTIVO",fecha_registro=date(2020,1,1))
                    db.add(socio); db.flush()
                    ev=models.EvaluacionCampo(ingreso_mensual=income,egreso_mensual=Decimal("1000"),cuota_deudas_mensual=Decimal("0"),capacidad_pago=income-Decimal("1000"),actividad_economica="Comercio",fuente_ingresos="INDEPENDIENTE",antiguedad_laboral_meses=36,calificacion_asfi=asfi,fecha=date.today(),usuario_id=official_id,socio_id=socio.id)
                    db.add(ev); db.flush()
                    req=models.SolicitudCredito(monto=Decimal("1000"),plazo_meses=9,tasa_interes=Decimal("12"),calificacion_asfi=asfi,tiene_deudas=False,estado="DESEMBOLSADO",socio_id=socio.id,usuario_id=official_id,evaluacion_campo_id=ev.id,producto_credito_id=ids["product"] if tenant=="main" else None,moneda_id=ids["currency"] if tenant=="main" else None,numero_solicitud=f"RC-{suffix}-{len(ids['requests'])}",destino="OTRO",cooperativa_id=coop_id)
                    db.add(req); db.flush()
                    ids["socios"].append(socio.id); ids["evals"].append(ev.id); ids["requests"].append(req.id)
                    db.commit()
                    return {"socio":socio.id,"eval":ev.id,"request":req.id,"official":official_id,"coop":coop_id}
            def credit(member_row, state="CANCELADO", *, amount=Decimal("1000"), balance=Decimal("0"), term=9):
                with SessionLocal() as db:
                    source=db.get(models.SolicitudCredito,member_row["request"])
                    source.estado="DESEMBOLSADO"
                    row=models.Credito(monto_aprobado=amount,saldo_pendiente=balance,estado=state,solicitud_credito_id=source.id,numero_credito=f"CRE-RC-{suffix}-{len(ids['credits'])}",cooperativa_id=member_row["coop"],socio_id=member_row["socio"],producto_credito_id=ids["product"] if member_row["coop"]==ids["coops"][0] else None,moneda_id=ids["currency"] if member_row["coop"]==ids["coops"][0] else None,tasa_interes=Decimal("12"),plazo_meses=term,tipo_amortizacion="FRANCES",usuario_id=member_row["official"])
                    db.add(row); db.flush(); ids["credits"].append(row.id); db.commit(); return row.id
            def payment(member_row, credit_id, days_late):
                with SessionLocal() as db:
                    installment=models.TablaAmortizacion(numero_cuota=1,fecha_vencimiento=date.today()-timedelta(days=days_late),monto_capital=Decimal("100"),monto_interes=Decimal("10"),monto_cuota_total=Decimal("110"),estado_pago="PAGADA",credito_id=credit_id,monto_pagado=Decimal("110"),fecha_pago=date.today())
                    db.add(installment); db.flush()
                    pay=models.PagoCuota(monto_capital=Decimal("100"),monto_interes_pagado=Decimal("10"),tabla_amortizacion_id=installment.id,credito_id=credit_id,cooperativa_id=member_row["coop"],numero_recibo=f"RP-{credit_id}",monto_total=Decimal("110"),dias_atraso=days_late,usuario_id=member_row["official"])
                    db.add(pay); db.commit()
            yield {"client":client,"tokens":users,"member":member,"credit":credit,"payment":payment,"ids":ids}
    finally:
        with SessionLocal() as db:
            # Remove all artifacts, including offers/predictions, even after assertion failures.
            if ids["credits"]:
                db.query(models.Morosidad).filter(models.Morosidad.credito_id.in_(ids["credits"])).delete(synchronize_session=False)
                db.query(models.PagoCuota).filter(models.PagoCuota.credito_id.in_(ids["credits"])).delete(synchronize_session=False)
                db.query(models.TablaAmortizacion).filter(models.TablaAmortizacion.credito_id.in_(ids["credits"])).delete(synchronize_session=False)
                db.query(models.PrediccionDeMorosidad).filter(models.PrediccionDeMorosidad.credito_id.in_(ids["credits"])).delete(synchronize_session=False)
                db.query(models.OfertaRecredito).filter(models.OfertaRecredito.credito_origen_id.in_(ids["credits"])).delete(synchronize_session=False)
                db.query(models.Credito).filter(models.Credito.id.in_(ids["credits"])).delete(synchronize_session=False)
            if ids["offers"]: db.query(models.OfertaRecredito).filter(models.OfertaRecredito.id.in_(ids["offers"])).delete(synchronize_session=False)
            if ids["requests"]:
                db.query(models.EvaluacionCrediticia).filter(models.EvaluacionCrediticia.solicitud_credito_id.in_(ids["requests"])).delete(synchronize_session=False)
                db.query(models.OfertaRecredito).filter(models.OfertaRecredito.solicitud_generada_id.in_(ids["requests"])).delete(synchronize_session=False)
                db.query(models.SolicitudCredito).filter(models.SolicitudCredito.id.in_(ids["requests"])).delete(synchronize_session=False)
            if ids.get("credit_evals"): db.query(models.EvaluacionCrediticia).filter(models.EvaluacionCrediticia.id.in_(ids["credit_evals"])).delete(synchronize_session=False)
            if ids["evals"]: db.query(models.EvaluacionCampo).filter(models.EvaluacionCampo.id.in_(ids["evals"])).delete(synchronize_session=False)
            if ids["users"]: db.query(models.Bitacora).filter(models.Bitacora.usuario_id.in_(ids["users"])).delete(synchronize_session=False)
            if ids["socios"]: db.query(models.Socio).filter(models.Socio.id.in_(ids["socios"])).delete(synchronize_session=False)
            if ids["product"]: db.query(models.ProductoCredito).filter(models.ProductoCredito.id==ids["product"]).delete(synchronize_session=False)
            if ids["users"]: db.query(models.Usuario).filter(models.Usuario.id.in_(ids["users"])).delete(synchronize_session=False)
            if ids["coops"]: db.query(models.Cooperativa).filter(models.Cooperativa.id.in_(ids["coops"])).delete(synchronize_session=False)
            db.commit()


def _auth(token): return {"Authorization":f"Bearer {token}"}


def test_model_card_endpoint_and_mora_prediction_persist_and_upsert(recredito_case):
    case=recredito_case; member=case["member"](); cid=case["credit"](member,"VIGENTE",amount=Decimal("1000"),balance=Decimal("500")); client=case["client"]
    headers=_auth(case["tokens"][("main","ADMINISTRADOR")])
    card=client.get("/api/v1/creditos/modelo-mora",headers=headers)
    assert card.status_code==200 and card.json()["dataset"]=="SINTETICO" and card.json()["n_muestras"]==5000
    response=client.post("/api/v1/creditos/mora/prediccion",headers=headers)
    assert response.status_code==200 and response.json()["actualizados"]==1,response.text
    with SessionLocal() as db:
        first=db.query(models.PrediccionDeMorosidad).filter_by(credito_id=cid).one()
        probability=first.probabilidad_mora
        assert first.version_modelo=="mora-logit-v1"
        row_count=db.query(models.PrediccionDeMorosidad).filter_by(credito_id=cid).count()
    again=client.post("/api/v1/creditos/mora/prediccion",headers=headers)
    assert again.status_code==200
    with SessionLocal() as db:
        assert db.query(models.PrediccionDeMorosidad).filter_by(credito_id=cid).count()==row_count==1
        assert db.query(models.PrediccionDeMorosidad).filter_by(credito_id=cid).one().probabilidad_mora==probability


def test_mora_list_without_estado_query_returns_list(recredito_case):
    case=recredito_case
    response=case["client"].get("/api/v1/creditos/mora",headers=_auth(case["tokens"][("main","ADMINISTRADOR")]))
    assert response.status_code==200,response.text
    assert isinstance(response.json(),list)


def test_evaluation_persists_advisory_probability_without_changing_reglas_dictamen(recredito_case):
    case=recredito_case; m=case["member"](); case["credit"](m)
    # New application is independent from the old paid credit.
    with SessionLocal() as db:
        source=db.get(models.SolicitudCredito,m["request"]); source.estado="DESEMBOLSADO"
        socio=db.get(models.Socio,m["socio"]); user=db.get(models.Usuario,m["official"])
        ev=models.EvaluacionCampo(ingreso_mensual=Decimal("6000"),egreso_mensual=Decimal("1000"),cuota_deudas_mensual=Decimal("0"),capacidad_pago=Decimal("5000"),actividad_economica="Comercio",fuente_ingresos="INDEPENDIENTE",antiguedad_laboral_meses=36,calificacion_asfi="A",fecha=date.today(),usuario_id=user.id,socio_id=socio.id)
        db.add(ev); db.flush(); req=models.SolicitudCredito(monto=Decimal("1200"),plazo_meses=12,tasa_interes=Decimal("18"),calificacion_asfi="A",tiene_deudas=False,estado="PENDIENTE",socio_id=socio.id,usuario_id=user.id,evaluacion_campo_id=ev.id,producto_credito_id=case["ids"]["product"],moneda_id=case["ids"]["currency"],numero_solicitud=f"NEW-{uuid4().hex[:12]}",destino="OTRO",cooperativa_id=m["coop"])
        db.add(req); db.flush(); case["ids"]["requests"].append(req.id); case["ids"]["evals"].append(ev.id); request_id=req.id; db.commit()
    response=case["client"].post(f"/api/v1/creditos/solicitudes/{request_id}/evaluacion",headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")]))
    assert response.status_code==201,response.text
    body=response.json()
    assert body["version_modelo_mora"]=="mora-logit-v1" and body["probabilidad_mora"] is not None and body["nivel_riesgo"] in {"BAJO","MEDIO","ALTO"}
    assert body["version_modelo"]=="reglas-v2" and body["dictamen"]=="APROBADO" and body["score"]==900
    from app.services.scoring import score_application
    with SessionLocal() as db:
        scored_request=db.get(models.SolicitudCredito,request_id)
        expected=score_application(scored_request,has_credit_history=True,has_current_arrears=False,today=date.today())
    assert (body["score"],body["dictamen"],body["knockouts"]) == (expected["score"],expected["dictamen"],expected["knockouts"])
    with SessionLocal() as db:
        persisted=db.query(models.EvaluacionCrediticia).filter_by(solicitud_credito_id=request_id).one()
        assert persisted.probabilidad_mora == Decimal(body["probabilidad_mora"])
        assert persisted.nivel_riesgo == body["nivel_riesgo"]
        assert persisted.version_modelo_mora == "mora-logit-v1"


def test_recredit_generation_acceptance_discard_expiry_and_tenant_roles(recredito_case):
    case=recredito_case; member=case["member"](); case["credit"](member); client=case["client"]
    writer=_auth(case["tokens"][("main","OFICIAL_CREDITO")]); reader=_auth(case["tokens"][("main","CAJERO")])
    generated=client.post("/api/v1/creditos/recreditos/generar",headers=writer)
    assert generated.status_code==200,generated.text
    out=generated.json(); assert out["generadas"]==1
    offer=out["ofertas"][0]; assert Decimal(str(offer["monto_sugerido"]))==Decimal("1500.00") and offer["plazo_meses"]==9 and offer["estado"]=="VIGENTE"
    assert date.fromisoformat(offer["fecha_vencimiento"]) == date.today() + timedelta(days=30)
    oid=offer["id"]; case["ids"]["offers"].append(oid)
    assert client.post("/api/v1/creditos/recreditos/generar",headers=writer).json()["generadas"]==0
    assert client.post(f"/api/v1/creditos/recreditos/{oid}/descartar",json={"motivo":"x"},headers=writer).status_code==422
    discarded=client.post(f"/api/v1/creditos/recreditos/{oid}/descartar",json={"motivo":"No requiere crédito"},headers=writer)
    assert discarded.status_code==200 and discarded.json()["estado"]=="DESCARTADA"
    # New qualifying member, acceptance copies latest field evaluation and marks offer accepted.
    member2=case["member"](); case["credit"](member2)
    generated2=client.post("/api/v1/creditos/recreditos/generar",headers=writer).json(); offer2=generated2["ofertas"][0]; case["ids"]["offers"].append(offer2["id"])
    accepted=client.post(f"/api/v1/creditos/recreditos/{offer2['id']}/aceptar",headers=writer)
    assert accepted.status_code==201,accepted.text
    assert accepted.json()["solicitud"]["estado"]=="PENDIENTE"
    assert accepted.json()["solicitud"]["destino_detalle"]==f"Re-crédito preaprobado (oferta #{offer2['id']})"
    case["ids"]["requests"].append(accepted.json()["solicitud"]["id"])
    assert accepted.json()["oferta"]["estado"]=="ACEPTADA"
    # A stale VIGENTE offer expires lazily when read.
    member3=case["member"](); case["credit"](member3)
    generated3=client.post("/api/v1/creditos/recreditos/generar",headers=writer).json(); stale=generated3["ofertas"][0]; case["ids"]["offers"].append(stale["id"])
    with SessionLocal() as db: db.query(models.OfertaRecredito).filter_by(id=stale["id"]).update({"fecha_vencimiento":date.today()-timedelta(days=1)}); db.commit()
    listed=client.get("/api/v1/creditos/recreditos",headers=reader)
    assert listed.status_code==200 and next(x for x in listed.json() if x["id"]==stale["id"])["estado"]=="EXPIRADA"
    forbidden=client.post("/api/v1/creditos/recreditos/generar",headers=_auth(case["tokens"][("main","CAJERO")]))
    assert forbidden.status_code==403
    foreign=case["member"]("foreign"); case["credit"](foreign)
    assert client.get("/api/v1/creditos/recreditos",headers=_auth(case["tokens"][("foreign","CAJERO")])).json()==[]


def test_recredit_active_credit_threshold_and_income_capacity_steps(recredito_case):
    case=recredito_case; member=case["member"](income=Decimal("100")); case["credit"](member,"VIGENTE",amount=Decimal("1000"),balance=Decimal("300"))
    headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")])
    response=case["client"].post("/api/v1/creditos/recreditos/generar",headers=headers)
    assert response.status_code==200,response.text
    # Initial 1,250 becomes 1,200 after flooring to 100; capacity requires stepping down in 100-unit steps.
    offer=response.json()["ofertas"][0]
    assert Decimal(str(offer["monto_sugerido"]))==Decimal("600")
    assert Decimal(str(offer["cuota_estimada"])) / Decimal("100") * 100 <= Decimal("80")


def test_in_progress_request_and_current_arrears_exclude_recredit(recredito_case):
    case=recredito_case; member=case["member"](); cid=case["credit"](member)
    headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")]); client=case["client"]
    with SessionLocal() as db:
        db.query(models.SolicitudCredito).filter_by(id=member["request"]).update({"estado":"PENDIENTE"}); db.commit()
    blocked=client.post("/api/v1/creditos/recreditos/generar",headers=headers)
    assert blocked.status_code==200 and blocked.json()["generadas"]==0
    with SessionLocal() as db:
        db.query(models.SolicitudCredito).filter_by(id=member["request"]).update({"estado":"DESEMBOLSADO"})
        db.add(models.Morosidad(credito_id=cid,dias_de_retaso=10,monto_penalizado=Decimal("1"),estado="EN_MORA")); db.commit()
    arrears=client.post("/api/v1/creditos/recreditos/generar",headers=headers)
    assert arrears.status_code==200 and arrears.json()["generadas"]==0


def test_expired_offer_cannot_be_accepted_and_template_explanation_api(monkeypatch,recredito_case):
    case=recredito_case; member=case["member"](); cid=case["credit"](member); client=case["client"]
    writer=_auth(case["tokens"][("main","OFICIAL_CREDITO")])
    created=client.post("/api/v1/creditos/recreditos/generar",headers=writer).json()["ofertas"][0]
    case["ids"]["offers"].append(created["id"])
    with SessionLocal() as db:
        db.query(models.OfertaRecredito).filter_by(id=created["id"]).update({"fecha_vencimiento":date.today()-timedelta(days=1)}); db.commit()
    expired=client.post(f"/api/v1/creditos/recreditos/{created['id']}/aceptar",headers=writer)
    assert expired.status_code==409
    # Persisted evaluation uses synthetic risk, while provider remains disabled by default.
    with SessionLocal() as db:
        request=db.get(models.SolicitudCredito,member["request"])
        row=models.EvaluacionCrediticia(solicitud_credito_id=request.id,cooperativa_id=member["coop"],version_modelo="reglas-v1",score=850,dictamen="APROBADO",factores=[{"codigo":"AHORRO","puntos":50,"motivo":"Ahorro favorable"}],knockouts=[],explicacion="OK",cuota_estimada=Decimal("777.00"),relacion_cuota_ingreso=Decimal("10"),usuario_id=member["official"],probabilidad_mora=Decimal("0.1234"),nivel_riesgo="BAJO",version_modelo_mora="mora-logit-v1")
        db.add(row); db.commit(); db.refresh(row); eval_id=row.id
    case["ids"].setdefault("credit_evals",[]).append(eval_id)
    monkeypatch.delenv("LLM_PROVIDER",raising=False); monkeypatch.delenv("LLM_API_KEY",raising=False); monkeypatch.delenv("LLM_MODEL",raising=False)
    template=client.get(f"/api/v1/creditos/evaluaciones/{eval_id}/explicacion",headers=writer)
    assert template.status_code==200 and template.json()["fuente"]=="PLANTILLA"
    assert template.json()["nivel_riesgo"]=="BAJO" and "riesgo bajo" in template.json()["texto"].lower()
    with SessionLocal() as db:
        actions=db.query(models.Bitacora).filter_by(cooperativa_id=member["coop"],accion="EXPLICAR_DICTAMEN").count()
        assert actions == 1


def test_offer_prediction_uses_persisted_member_credit_features(recredito_case,monkeypatch):
    case=recredito_case; member=case["member"](asfi="C"); credit_id=case["credit"](member)
    with SessionLocal() as db:
        evaluation=db.query(models.EvaluacionCampo).filter_by(id=member["eval"]).one()
        evaluation.cuota_deudas_mensual=Decimal("300")
        evaluation.antiguedad_laboral_meses=84
        db.commit()
    captured={}
    def fake_predict(features):
        captured.update(features)
        return {"probabilidad_mora":Decimal("0.25"),"nivel_riesgo":"MEDIO","version_modelo":"mora-logit-v1"}
    monkeypatch.setattr("app.api.v1.endpoints.creditos.predecir_mora",fake_predict)
    response=case["client"].post("/api/v1/creditos/recreditos/generar",headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")]))
    assert response.status_code==200 and response.json()["generadas"]==1
    assert captured["asfi"]=="C" and captured["antiguedad_laboral_meses"]==84
    assert captured["endeudamiento"]==Decimal("5")
    assert captured["antiguedad_socio_meses"]==date.today().year*12+date.today().month-(2020*12+1)


def test_explanation_api_uses_mocked_provider_with_pii_free_request(monkeypatch,recredito_case):
    case=recredito_case; member=case["member"](); case["credit"](member); writer=_auth(case["tokens"][("main","OFICIAL_CREDITO")])
    with SessionLocal() as db:
        request=db.get(models.SolicitudCredito,member["request"])
        row=models.EvaluacionCrediticia(solicitud_credito_id=request.id,cooperativa_id=member["coop"],version_modelo="reglas-v1",score=850,dictamen="APROBADO",factores=[{"codigo":"AHORRO","puntos":50,"motivo":"Ahorro favorable","valor":"9999.00"}],knockouts=[],explicacion="OK",cuota_estimada=Decimal("777.00"),relacion_cuota_ingreso=Decimal("10"),usuario_id=member["official"],probabilidad_mora=Decimal("0.1234"),nivel_riesgo="BAJO",version_modelo_mora="mora-logit-v1")
        db.add(row); db.commit(); db.refresh(row); eval_id=row.id
    case["ids"].setdefault("credit_evals",[]).append(eval_id)
    import httpx
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"content":[{"text":"Explicación mock segura"}]}
    seen={}
    def fake_post(url,**kwargs): seen["url"]=url; seen["body"]=kwargs["json"]; return Response()
    monkeypatch.setattr(httpx,"post",fake_post); monkeypatch.setenv("LLM_PROVIDER","anthropic"); monkeypatch.setenv("LLM_API_KEY","mock-secret"); monkeypatch.setenv("LLM_MODEL","mock-model")
    response=case["client"].get(f"/api/v1/creditos/evaluaciones/{eval_id}/explicacion",headers=writer)
    assert response.status_code==200 and response.json()["fuente"]=="LLM"
    outbound=str(seen["body"])
    for private in ("Private Name","Secret",member["socio"],"777.00","9999.00","mock-secret"):
        assert str(private) not in outbound


def test_explanation_api_falls_back_and_audits_provider_failure(monkeypatch,recredito_case):
    case=recredito_case; member=case["member"](); case["credit"](member); writer=_auth(case["tokens"][("main","OFICIAL_CREDITO")])
    with SessionLocal() as db:
        source=db.get(models.SolicitudCredito,member["request"])
        row=models.EvaluacionCrediticia(solicitud_credito_id=source.id,cooperativa_id=member["coop"],version_modelo="reglas-v1",score=750,dictamen="REVISION_MANUAL",factores=[],knockouts=[],explicacion="Manual review",usuario_id=member["official"],probabilidad_mora=Decimal("0.2"),nivel_riesgo="MEDIO",version_modelo_mora="mora-logit-v1")
        db.add(row); db.commit(); db.refresh(row); eval_id=row.id
    case["ids"].setdefault("credit_evals",[]).append(eval_id)
    import httpx
    def fail(*args,**kwargs): raise httpx.ConnectError("mock offline")
    monkeypatch.setattr(httpx,"post",fail); monkeypatch.setenv("LLM_PROVIDER","gemini"); monkeypatch.setenv("LLM_API_KEY","mock-secret"); monkeypatch.setenv("LLM_MODEL","mock-model")
    response=case["client"].get(f"/api/v1/creditos/evaluaciones/{eval_id}/explicacion",headers=writer)
    assert response.status_code==200 and response.json()["fuente"]=="PLANTILLA"
    with SessionLocal() as db:
        actions=db.query(models.Bitacora).filter_by(cooperativa_id=member["coop"],accion="EXPLICAR_DICTAMEN").count()
        assert actions>=1


@pytest.mark.parametrize(("paid_late_days","expected_offers"),[(5,1),(6,0)])
def test_paid_installment_lateness_respects_product_grace_boundary(recredito_case,paid_late_days,expected_offers):
    case=recredito_case; member=case["member"](); credit_id=case["credit"](member); case["payment"](member,credit_id,paid_late_days)
    response=case["client"].post("/api/v1/creditos/recreditos/generar",headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")]))
    assert response.status_code==200 and response.json()["generadas"]==expected_offers


def test_offer_acceptance_rejects_when_socio_already_has_request_in_progress(recredito_case):
    case=recredito_case; member=case["member"](); case["credit"](member); writer=_auth(case["tokens"][("main","OFICIAL_CREDITO")])
    generated=case["client"].post("/api/v1/creditos/recreditos/generar",headers=writer).json()
    offer=generated["ofertas"][0]; case["ids"]["offers"].append(offer["id"])
    with SessionLocal() as db:
        source=db.get(models.SolicitudCredito,member["request"])
        in_progress=models.SolicitudCredito(monto=Decimal("100"),plazo_meses=6,tasa_interes=Decimal("12"),estado="PENDIENTE",socio_id=member["socio"],usuario_id=member["official"],producto_credito_id=case["ids"]["product"],moneda_id=case["ids"]["currency"],numero_solicitud=f"PENDING-{uuid4().hex[:10]}",destino="OTRO",cooperativa_id=member["coop"])
        db.add(in_progress);db.commit();db.refresh(in_progress)
    case["ids"]["requests"].append(in_progress.id)
    response=case["client"].post(f"/api/v1/creditos/recreditos/{offer['id']}/aceptar",headers=writer)
    assert response.status_code==409


def test_credit_recredit_amount_cap_term_clamp_and_current_product_rate(recredito_case):
    case=recredito_case; member=case["member"](); case["credit"](member,amount=Decimal("2000"),term=24)
    with SessionLocal() as db:
        product=db.get(models.ProductoCredito,case["ids"]["product"])
        product.monto_max=Decimal("1300"); product.tasa_interes_anual=Decimal("15"); db.commit()
    result=case["client"].post("/api/v1/creditos/recreditos/generar",headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")])).json()
    offer=result["ofertas"][0]
    assert Decimal(str(offer["monto_sugerido"]))==Decimal("1300")
    assert offer["plazo_meses"]==12 and Decimal(str(offer["tasa_interes"]))==Decimal("15")


def test_current_arrears_on_another_credit_excludes_candidate_recredit(recredito_case):
    case=recredito_case; member=case["member"](); candidate=case["credit"](member)
    with SessionLocal() as db:
        source=db.get(models.SolicitudCredito,member["request"])
        sibling_request=models.SolicitudCredito(monto=Decimal("1000"),plazo_meses=9,tasa_interes=Decimal("12"),estado="DESEMBOLSADO",socio_id=member["socio"],usuario_id=member["official"],evaluacion_campo_id=member["eval"],producto_credito_id=case["ids"]["product"],moneda_id=case["ids"]["currency"],numero_solicitud=f"SIB-{uuid4().hex[:10]}",destino="OTRO",cooperativa_id=member["coop"])
        db.add(sibling_request);db.flush(); case["ids"]["requests"].append(sibling_request.id)
        sibling=models.Credito(monto_aprobado=Decimal("1000"),saldo_pendiente=Decimal("1000"),estado="VIGENTE",solicitud_credito_id=sibling_request.id,numero_credito=f"SIB-C-{uuid4().hex[:10]}",cooperativa_id=member["coop"],socio_id=member["socio"],producto_credito_id=case["ids"]["product"],moneda_id=case["ids"]["currency"],tasa_interes=Decimal("12"),plazo_meses=9,tipo_amortizacion="FRANCES",usuario_id=member["official"])
        db.add(sibling);db.flush(); case["ids"]["credits"].append(sibling.id)
        db.add(models.Morosidad(credito_id=sibling.id,dias_de_retaso=6,monto_penalizado=Decimal("1"),estado="EN_MORA"));db.commit()
    result=case["client"].post("/api/v1/creditos/recreditos/generar",headers=_auth(case["tokens"][("main","OFICIAL_CREDITO")]))
    assert result.status_code==200 and result.json()["generadas"]==0
