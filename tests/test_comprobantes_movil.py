from app.models import models
from app.core.config import settings
from app.services.comprobantes_movil import mask_account, sign_receipt, verify_receipt
from types import SimpleNamespace
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from uuid import uuid4
from sqlalchemy import delete, text
from concurrent.futures import ThreadPoolExecutor


def test_mobile_receipt_models_define_receipt_and_sequence_tables():
    assert hasattr(models, "ComprobanteTransaccion")
    assert hasattr(models, "ComprobanteSecuencia")
    assert models.ComprobanteTransaccion.__tablename__ == "comprobante_transaccion"
    assert models.ComprobanteSecuencia.__tablename__ == "comprobante_secuencia"


def test_mobile_receipt_signing_configuration_is_available():
    assert settings.COMPROBANTE_SIGNING_KEY
    assert settings.PUBLIC_API_URL


def test_receipt_signature_detects_a_tampered_signed_amount():
    receipt = SimpleNamespace(
        numero="TRF-2026-000001", tipo="TRANSFERENCIA", cooperativa_id=9, socio_id=11,
        monto=Decimal("20.00"), moneda="BOB", cuenta_origen_id=41, cuenta_destino_id=42,
        credito_id=None, numero_cuota=None,
        emitido_en=datetime(2026, 1, 2, tzinfo=timezone.utc), firma="", codigo_verificacion="",
    )
    receipt.firma, receipt.codigo_verificacion = sign_receipt(receipt, "test-key")
    assert verify_receipt(receipt, "test-key")
    receipt.monto = Decimal("21.00")
    assert not verify_receipt(receipt, "test-key")


def test_receipt_account_mask_never_exposes_full_number():
    assert mask_account("0123456789") == "****6789"
    assert mask_account("4521") == "****"


def test_receipt_issuance_uses_transactional_sequence_and_audit(monkeypatch):
    from app.services import comprobantes_movil as service

    class Session:
        row = None

        def execute(self, statement, params):
            assert "ON CONFLICT (cooperativa_id, tipo, anio)" in str(statement)
            assert params["cooperativa_id"] == 9
            assert params["tipo"] == "PAGO_CUOTA"
            return _Result(13)

        def add(self, row):
            self.row = row

        def flush(self):
            self.row.id = 37

    session = Session()
    audit = []
    monkeypatch.setattr(service, "registrar_accion", lambda *args, **kwargs: audit.append(kwargs))
    receipt = service.issue_receipt(
        session, receipt_type="PAGO_CUOTA", cooperative_id=9, member_id=11, user_id=5,
        amount=Decimal("15.00"), currency="BOB", source_account_id=41, payment_id=31,
        credit_id=99, installment_number=2,
    )
    assert receipt.numero.startswith("PAG-2026-000013")
    assert receipt.id == 37
    assert verify_receipt(receipt)
    assert len(audit) == 1 and audit[0]["accion"] == "EMITIR_COMPROBANTE_MOVIL"


def test_public_verification_is_minimal_and_masks_accounts():
    from app.api.v1.endpoints.verificacion_comprobantes import verificar_comprobante

    receipt = SimpleNamespace(
        id=37, numero="TRF-2026-000001", tipo="TRANSFERENCIA", cooperativa_id=9, socio_id=11,
        monto=Decimal("20.00"), moneda="BOB", cuenta_origen_id=41, cuenta_destino_id=42,
        credito_id=None, numero_cuota=None, glosa=None,
        emitido_en=datetime(2026, 1, 2, tzinfo=timezone.utc), firma="", codigo_verificacion="",
    )
    receipt.firma, receipt.codigo_verificacion = sign_receipt(receipt)

    class PublicSession:
        def execute(self, *_args):
            return _Result(receipt)

        def get(self, model, _id):
            if model.__name__ == "Cooperativa":
                return SimpleNamespace(nombre="Coop")
            return SimpleNamespace(numero="0012345678")

    payload = verificar_comprobante(receipt.codigo_verificacion, formato="json", db=PublicSession())
    assert payload["estado"] == "VÁLIDO"
    assert payload["cuenta_origen"] == "****5678"
    assert payload["cuenta_destino"] == "****5678"
    assert "ci" not in payload
    assert "nombre" not in payload and "apellido" not in payload


def test_public_verification_html_and_unknown_code_are_safe():
    from fastapi import HTTPException
    from fastapi.responses import HTMLResponse
    from app.api.v1.endpoints.verificacion_comprobantes import verificar_comprobante

    receipt = SimpleNamespace(
        id=37, numero="TRF-2026-000001", tipo="TRANSFERENCIA", cooperativa_id=9, socio_id=11,
        monto=Decimal("20.00"), moneda="BOB", cuenta_origen_id=41, cuenta_destino_id=42,
        credito_id=None, numero_cuota=None, glosa=None,
        emitido_en=datetime(2026, 1, 2, tzinfo=timezone.utc), firma="", codigo_verificacion="",
    )
    receipt.firma, receipt.codigo_verificacion = sign_receipt(receipt)

    class PublicSession:
        def __init__(self, row):
            self.row = row

        def execute(self, *_args):
            return _Result(self.row)

        def get(self, model, _id):
            return SimpleNamespace(nombre="Coop", numero="0012345678")

    page = verificar_comprobante(receipt.codigo_verificacion, db=PublicSession(receipt))
    assert isinstance(page, HTMLResponse)
    html = page.body.decode()
    assert "lang='es'" in html
    assert "VÁLIDO" in html
    for label in ("Estado", "Número", "Tipo", "Fecha", "Monto", "Moneda", "Cooperativa", "Cuenta origen", "Cuenta destino"):
        assert f"<dt>{label}</dt>" in html
    assert "****5678" in html
    assert "0012345678" not in page.body.decode()
    assert "socio" not in page.body.decode().lower()
    with pytest.raises(HTTPException) as error:
        verificar_comprobante("NO-SUCH-CODE", formato="json", db=PublicSession(None))
    assert error.value.status_code == 404
    assert error.value.detail == "No encontrado"
    not_found = verificar_comprobante("NO-SUCH-CODE", db=PublicSession(None))
    assert not_found.status_code == 404
    assert "No encontrado" in not_found.body.decode()
    receipt.firma = "0" * len(receipt.firma)
    invalid_page = verificar_comprobante(receipt.codigo_verificacion, db=PublicSession(receipt))
    assert "INVÁLIDO" in invalid_page.body.decode()


def test_receipt_pdf_uses_real_qr_on_authenticated_route_and_hides_foreign_receipt(transfer_case):
    from app.db.session import SessionLocal

    client, tokens, ids = transfer_case
    transfer = client.post(
        "/api/v1/ahorros/transferencias",
        json={"cuenta_origen_id": ids["accounts"][0], "cuenta_destino_id": ids["accounts"][1], "monto": "10.00"},
        headers={"Authorization": f"Bearer {tokens['mobile']}"},
    )
    assert transfer.status_code == 201, transfer.text
    receipt_id = transfer.json()["comprobante"]["id"]
    response = client.get(
        f"/api/v1/socio/comprobantes/{receipt_id}/pdf",
        headers={"Authorization": f"Bearer {tokens['mobile']}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")

    foreign = client.get(
        f"/api/v1/socio/comprobantes/{receipt_id}/pdf",
        headers={"Authorization": f"Bearer {tokens['staff']}"},
    )
    assert foreign.status_code == 404


def test_payment_receipt_pdf_renders_without_destination_account(transfer_case):
    from app.db.session import SessionLocal

    client, tokens, ids = transfer_case
    credit_id, account_id = ids["credit"], ids["accounts"][0]
    with SessionLocal() as db:
        db.query(models.CuentaAhorro).filter_by(id=account_id).update({"saldo_disponible": Decimal("1000.00")})
        db.commit()
    payment = client.post(
        f"/api/v1/socio/creditos/{credit_id}/pagos", json={"cuenta_ahorro_id": account_id},
        headers={"Authorization": f"Bearer {tokens['mobile']}"},
    )
    assert payment.status_code == 201, payment.text
    receipt = payment.json()["comprobante"]
    assert receipt["tipo"] == "PAGO_CUOTA" and receipt["cuenta_destino"] is None

    response = client.get(
        f"/api/v1/socio/comprobantes/{receipt['id']}/pdf",
        headers={"Authorization": f"Bearer {tokens['mobile']}"},
    )
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")


def test_member_detail_hides_receipts_owned_by_another_member():
    from app.api.v1.endpoints.socio import obtener_comprobante_socio
    from fastapi import HTTPException

    class Session:
        def execute(self, *_args):
            return _Result(None)

    with pytest.raises(HTTPException) as error:
        obtener_comprobante_socio(37, socio=SimpleNamespace(id=22), db=Session())
    assert error.value.status_code == 404


@pytest.fixture
def transfer_case():
    from fastapi.testclient import TestClient
    from app.core.security import create_access_token, hash_password
    from app.db.session import SessionLocal
    from app.models.models import Bitacora, Cooperativa, CuentaAhorro, Moneda, Rol, Socio, Usuario
    from main import app

    suffix = uuid4().hex[:10]
    ids = {"coop": None, "users": [], "members": [], "accounts": [], "credit": None, "request": None, "product": None, "installments": []}
    with SessionLocal() as db:
        coop = Cooperativa(nombre=f"Receipt {suffix}", estado="ACTIVO")
        db.add(coop)
        db.flush()
        ids["coop"] = coop.id
        currency = db.query(models.Moneda).filter_by(codigo_iso="BOB").one()
        tokens = {}
        for role_name, label in (("SOCIO", "mobile"), ("ADMINISTRADOR", "staff")):
            role = db.query(models.Rol).filter_by(nombre=role_name).one()
            user = models.Usuario(
                correo=f"receipt-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                rol_id=role.id, cooperativa_id=coop.id, nombre=f"Receipt {label}", estado="ACTIVO",
            )
            db.add(user)
            db.flush()
            ids["users"].append(user.id)
            member = models.Socio(
                cooperativa_id=coop.id, ci=f"R-{label[0]}-{suffix}", nombre="Receipt",
                apellido=label, estado="ACTIVO", usuario_id=user.id,
            )
            db.add(member)
            db.flush()
            ids["members"].append(member.id)
            accounts = []
            for index, balance in enumerate((Decimal("100.00"), Decimal("20.00")), start=1):
                account = models.CuentaAhorro(
                    numero=f"RCPT-{label}-{suffix}-{index}", tipo_producto="VISTA",
                    saldo_disponible=balance, saldo_bloqueado=0, estado="ACTIVA",
                    fecha_registro=date.today(), socio_id=member.id, moneda_id=currency.id,
                )
                db.add(account)
                db.flush()
                ids["accounts"].append(account.id)
                accounts.append(account)
            token, _ = create_access_token(str(user.id), role_name, coop.id)
            tokens[label] = token
        product = models.ProductoCredito(
            cooperativa_id=coop.id, codigo=f"RC-{suffix}", nombre="Receipt credit",
            moneda_id=currency.id, monto_min=Decimal("100"), monto_max=Decimal("5000"),
            plazo_min_meses=1, plazo_max_meses=24, tasa_interes_anual=Decimal("12"),
            tipo_amortizacion="FRANCES", dias_gracia_mora=0, tasa_mora_anual=Decimal("0"),
            relacion_cuota_ingreso_max=Decimal("50"), estado="ACTIVO",
        )
        db.add(product)
        db.flush()
        ids["product"] = product.id
        request_row = models.SolicitudCredito(
            monto=Decimal("1000"), plazo_meses=2, tasa_interes=Decimal("12"),
            estado="DESEMBOLSADO", socio_id=ids["members"][0], usuario_id=ids["users"][0],
            producto_credito_id=product.id, moneda_id=currency.id,
            numero_solicitud=f"RC-{suffix}", destino="CONSUMO", cooperativa_id=coop.id,
        )
        db.add(request_row)
        db.flush()
        ids["request"] = request_row.id
        credit = models.Credito(
            monto_aprobado=Decimal("1000"), saldo_pendiente=Decimal("1000"), estado="VIGENTE",
            solicitud_credito_id=request_row.id, numero_credito=f"RC-{suffix}",
            cooperativa_id=coop.id, socio_id=ids["members"][0], producto_credito_id=product.id,
            moneda_id=currency.id, tasa_interes=Decimal("12"), plazo_meses=2,
            tipo_amortizacion="FRANCES", fecha_desembolso=date.today(),
            modalidad_desembolso="CUENTA", usuario_id=ids["users"][0],
        )
        db.add(credit)
        db.flush()
        ids["credit"] = credit.id
        for number in (1, 2):
            installment = models.TablaAmortizacion(
                numero_cuota=number, fecha_vencimiento=date.today() - timedelta(days=1) if number == 1 else date.today() + timedelta(days=30),
                monto_capital=Decimal("10"), monto_interes=Decimal("5"), monto_cuota_total=Decimal("15"),
                estado_pago="PENDIENTE", credito_id=credit.id,
                saldo_inicial=Decimal("100"), saldo_final=Decimal("90"), monto_pagado=Decimal("0"),
            )
            db.add(installment)
            db.flush()
            ids["installments"].append(installment.id)
        db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, tokens, ids
    finally:
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            db.execute(text("DELETE FROM comprobante_transaccion WHERE cooperativa_id=:coop"), {"coop": ids["coop"]})
            if ids["credit"]:
                # Successful payments and their transactions reference each other; break the cycle first.
                db.execute(text("UPDATE transaccion SET pago_cuota_id=NULL WHERE credito_id=:id"), {"id": ids["credit"]})
                db.execute(delete(models.PagoCuota).where(models.PagoCuota.credito_id == ids["credit"]))
            db.execute(text("UPDATE transaccion SET transaccion_contraparte_id=NULL WHERE cuenta_ahorro_id=ANY(:ids)"), {"ids": ids["accounts"]})
            db.execute(text("DELETE FROM transaccion WHERE cuenta_ahorro_id=ANY(:ids)"), {"ids": ids["accounts"]})
            if ids["credit"]:
                db.execute(delete(models.PagoCuota).where(models.PagoCuota.credito_id == ids["credit"]))
                db.execute(delete(models.Morosidad).where(models.Morosidad.credito_id == ids["credit"]))
                db.execute(delete(models.TablaAmortizacion).where(models.TablaAmortizacion.credito_id == ids["credit"]))
                db.execute(delete(models.Credito).where(models.Credito.id == ids["credit"]))
                db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id=:coop AND tipo='PAGO_CUOTA'"), {"coop": ids["coop"]})
            if ids["request"]:
                db.execute(delete(models.SolicitudCredito).where(models.SolicitudCredito.id == ids["request"]))
            if ids["product"]:
                db.execute(delete(models.ProductoCredito).where(models.ProductoCredito.id == ids["product"]))
            db.execute(delete(models.CuentaAhorro).where(models.CuentaAhorro.id.in_(ids["accounts"])))
            db.execute(delete(models.Bitacora).where(models.Bitacora.usuario_id.in_(ids["users"])))
            db.execute(delete(models.Socio).where(models.Socio.id.in_(ids["members"])))
            db.execute(delete(models.Usuario).where(models.Usuario.id.in_(ids["users"])))
            db.execute(text("DELETE FROM comprobante_secuencia WHERE cooperativa_id=:coop"), {"coop": ids["coop"]})
            db.execute(delete(models.Cooperativa).where(models.Cooperativa.id == ids["coop"]))
            db.commit()


def test_mobile_transfer_issues_receipt_and_admin_transfer_stays_compatible(transfer_case):
    client, tokens, ids = transfer_case
    response = client.post(
        "/api/v1/ahorros/transferencias",
        json={"cuenta_origen_id": ids["accounts"][0], "cuenta_destino_id": ids["accounts"][1], "monto": "10.00"},
        headers={"Authorization": f"Bearer {tokens['mobile']}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["comprobante"]["tipo"] == "TRANSFERENCIA"
    assert body["comprobante"]["canal"] == "MOVIL"
    assert body["comprobante"]["cuenta_origen"].startswith("****")

    staff = client.post(
        "/api/v1/ahorros/transferencias",
        json={"cuenta_origen_id": ids["accounts"][2], "cuenta_destino_id": ids["accounts"][3], "monto": "10.00"},
        headers={"Authorization": f"Bearer {tokens['staff']}"},
    )
    assert staff.status_code == 201, staff.text
    assert "comprobante" not in staff.json()


def test_receipt_payload_failure_rolls_back_mobile_payment_movement_and_sequence(transfer_case, monkeypatch):
    from app.db.session import SessionLocal
    from app.services import comprobantes_movil

    client, tokens, ids = transfer_case

    def fail_payload(*_args, **_kwargs):
        raise RuntimeError("receipt rendering failed")

    monkeypatch.setattr(comprobantes_movil, "receipt_payload", fail_payload)
    member_id, cooperative_id, credit_id = ids["members"][0], ids["coop"], ids["credit"]
    account_id = ids["accounts"][0]
    with SessionLocal() as db:
        db.query(models.CuentaAhorro).filter_by(id=account_id).update({"saldo_disponible": Decimal("1000.00")})
        db.commit()
        payment_count_before = db.query(models.PagoCuota).filter_by(credito_id=credit_id).count()
        transaction_count_before = db.execute(text("SELECT count(*) FROM transaccion WHERE credito_id=:id"), {"id": credit_id}).scalar_one()
        receipt_count_before = db.query(models.ComprobanteTransaccion).filter_by(socio_id=member_id).count()
        sequence_before = db.query(models.ComprobanteSecuencia).filter_by(
            cooperativa_id=cooperative_id, tipo="PAGO_CUOTA", anio=date.today().year
        ).count()
    response = client.post(
            f"/api/v1/socio/creditos/{credit_id}/pagos", json={"cuenta_ahorro_id": account_id},
            headers={"Authorization": f"Bearer {tokens['mobile']}"},
        )
    assert response.status_code == 500
    with SessionLocal() as db:
        assert db.query(models.PagoCuota).filter_by(credito_id=credit_id).count() == payment_count_before
        assert db.execute(text("SELECT count(*) FROM transaccion WHERE credito_id=:id"), {"id": credit_id}).scalar_one() == transaction_count_before
        assert db.query(models.ComprobanteTransaccion).filter_by(socio_id=member_id).count() == receipt_count_before
        assert db.query(models.ComprobanteSecuencia).filter_by(
            cooperativa_id=cooperative_id, tipo="PAGO_CUOTA", anio=date.today().year
        ).count() == sequence_before


def test_postgres_sequence_is_gap_free_under_concurrent_issuance():
    from main import app  # noqa: F401; initializes the local test schema
    from app.db.session import SessionLocal, engine
    from app.services.comprobantes_movil import next_receipt_number

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local PostgreSQL unavailable")
    cooperative_id = 2_000_000_000 + int(uuid4().hex[:7], 16)
    receipt_type = "TRANSFERENCIA"

    def allocate_one():
        with SessionLocal() as session:
            number = next_receipt_number(session, cooperative_id, receipt_type)
            session.commit()
            return number

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(lambda _: allocate_one(), range(16)))
        assert len(set(numbers)) == 16
        assert sorted(int(number.rsplit("-", 1)[1]) for number in numbers) == list(range(1, 17))
    finally:
        with SessionLocal() as session:
            session.execute(text("DELETE FROM comprobante_secuencia WHERE cooperativa_id=:coop AND tipo=:tipo"),
                            {"coop": cooperative_id, "tipo": receipt_type})
            session.commit()


class _Result:
    def __init__(self, value):
        self.value = value

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _PaymentSession:
    def __init__(self, credit, account):
        self.credit = credit
        self.account = account
        self.execute_count = 0
        self.committed = False
        self.rolled_back = False
        self.pending = []
        self.original_balance = account.saldo_disponible
        self.sequence_value = 0
        self.movement_created = False

    def execute(self, _statement, _params=None):
        self.execute_count += 1
        if self.execute_count == 3:
            self.movement_created = True
        return _Result({1: self.credit, 2: self.account, 3: 701}[self.execute_count])

    def add(self, row):
        self.pending.append(row)

    def flush(self):
        if self.pending and getattr(self.pending[-1], "id", None) is None:
            self.pending[-1].id = 301

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True
        self.pending.clear()
        self.sequence_value = 0
        self.movement_created = False
        self.account.saldo_disponible = self.original_balance


def test_mobile_payment_receipt_failure_rolls_back_before_commit(monkeypatch):
    from app.api.v1.endpoints import creditos
    from app.schemas.schemas import PagoCuotaIn

    current = SimpleNamespace(
        id=22, numero_cuota=1, estado_pago="PENDIENTE", monto_cuota_total=Decimal("15.00"),
        monto_capital=Decimal("10.00"), monto_interes=Decimal("5.00"), fecha_vencimiento=date(2026, 1, 1),
    )
    later = SimpleNamespace(id=23, numero_cuota=2, estado_pago="PENDIENTE")
    member = SimpleNamespace(id=11)
    currency = SimpleNamespace(id=1, codigo_iso="BOB")
    application = SimpleNamespace(socio_id=11, moneda_id=1, socio=member, moneda=currency, producto=None)
    credit = SimpleNamespace(
        id=99, socio_id=11, cooperativa_id=9, solicitud_credito_id=3, estado="VIGENTE",
        numero_credito="CR-000099",
        cronograma=[current, later], saldo_pendiente=Decimal("20.00"), moneda_id=1,
        moneda=currency, solicitud=application, producto=None, socio=member,
    )
    account = SimpleNamespace(
        id=41, socio_id=11, estado="ACTIVA", moneda_id=1, tipo_producto="VISTA",
        saldo_disponible=Decimal("100.00"), numero="00004521",
    )
    session = _PaymentSession(credit, account)
    user = SimpleNamespace(id=5, cooperativa_id=9, rol=SimpleNamespace(nombre="SOCIO"))
    request = SimpleNamespace(state=SimpleNamespace(mobile_socio_id=11))
    def allocate_sequence(_db, *_args):
        session.sequence_value = 1
        return 1

    monkeypatch.setattr(creditos, "_siguiente_secuencia", allocate_sequence)
    monkeypatch.setattr(creditos, "_configuracion_mora", lambda *_: (Decimal("0"), 0))
    monkeypatch.setattr(creditos, "calcular_mora", lambda **_: {"mora": Decimal("0"), "dias_atraso": 0})
    monkeypatch.setattr(creditos, "_actualizar_morosidad_credito", lambda *args: None)
    monkeypatch.setattr(creditos, "_pago_out", lambda *_: {"id": 301, "total": "15.00"})
    monkeypatch.setattr(creditos, "registrar_accion", lambda *args, **kwargs: None)

    def fail_receipt(*_args):
        raise RuntimeError("receipt issuance failed")

    with pytest.raises(RuntimeError, match="receipt issuance failed"):
        creditos.cobrar_cuota(
            99, PagoCuotaIn(modalidad="CUENTA", cuenta_ahorro_id=41), request, user, session,
            before_commit_callback=fail_receipt,
        )
    assert session.rolled_back is True
    assert session.committed is False
    assert session.pending == []
    assert session.movement_created is False
    assert session.sequence_value == 0
    assert account.saldo_disponible == session.original_balance
