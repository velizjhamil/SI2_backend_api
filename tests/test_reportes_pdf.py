"""Real-route tests for CSV/PDF report export negotiation."""

import subprocess
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import xml.etree.ElementTree as ET
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from main import app
from test_reportes_api import auth, report_case
from test_comprobantes_api import voucher_case


def test_libro_diario_export_pdf_and_unchanged_csv(report_case):
    case = report_case
    _set_pdf_test_cooperative_name(case)
    today = date.today()
    query = f"?desde={today}&hasta={today}"
    with TestClient(app) as client:
        default_csv = client.get("/api/v1/contabilidad/libro-diario/export" + query, headers=auth(case))
        explicit_csv = client.get("/api/v1/contabilidad/libro-diario/export" + query + "&formato=csv", headers=auth(case))
        pdf = client.get("/api/v1/contabilidad/libro-diario/export" + query + "&formato=pdf", headers=auth(case))
        invalid = client.get("/api/v1/contabilidad/libro-diario/export" + query + "&formato=xlsx", headers=auth(case))

    assert default_csv.status_code == explicit_csv.status_code == 200
    assert default_csv.content == explicit_csv.content
    assert default_csv.content.startswith(b"\xef\xbb\xbf")
    assert default_csv.content.startswith(b"\xef\xbb\xbf" +
        b"fecha,numero,tipo,estado,orden,cuenta_codigo,cuenta_nombre,glosa,debe,haber")
    assert default_csv.content == _legacy_csv_golden("diario", case, today)
    assert b"511.01" in default_csv.content
    assert default_csv.headers["content-disposition"] == explicit_csv.headers["content-disposition"]
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.headers["content-disposition"] == default_csv.headers["content-disposition"].replace(".csv", ".pdf")
    assert pdf.content.startswith(b"%PDF")
    text = subprocess.run(["pdftotext", "-", "-"], input=pdf.content, check=True,
                          capture_output=True).stdout.decode("utf-8")
    assert "Libro diario" in text
    assert "Período" in text
    assert "Página 1 de" in text
    assert "Peña" in text
    _save_sample("libro-diario.pdf", pdf.content)
    assert invalid.status_code == 422


def _set_pdf_test_cooperative_name(case):
    from app.db.session import SessionLocal
    from app.models import models
    with SessionLocal() as db:
        db.query(models.Cooperativa).filter_by(id=case["coop_id"]).update({"nombre": "Cooperativa Peña"})
        db.commit()


def _save_sample(filename, content):
    if os.getenv("SAVE_REPORT_PDF_SAMPLES") == "1":
        output = Path("/tmp/claude-1000/-home-yimy-proyectos-si2/94206d22-4790-40a4-81fb-8e534b1b11dd/scratchpad/pdf_samples")
        output.mkdir(parents=True, exist_ok=True)
        (output / filename).write_bytes(content)


def _legacy_csv_golden(report, case, today):
    """Full byte templates captured from d6223fb production CSV routes."""
    from app.db.session import SessionLocal
    from app.models import models

    with SessionLocal() as db:
        voucher_number = db.query(models.ComprobanteContable).filter_by(
            id=case["ids"]["vouchers"][0]).one().numero
    day = today.isoformat()
    templates = {
        "diario": [
            "fecha,numero,tipo,estado,orden,cuenta_codigo,cuenta_nombre,glosa,debe,haber",
            f"{day},{voucher_number},INGRESO,REGISTRADO,,111.01,Billetes y monedas nominativo por moneda,,100.00,0.00",
            f"{day},{voucher_number},INGRESO,REGISTRADO,,211.00,OBLIGACIONES CON EL PUBLICO A LA VISTA,,0.00,50.00",
            f"{day},{voucher_number},INGRESO,REGISTRADO,,212.00,OBLIGACIONES CON EL PUBLICO POR CUENTAS DE AHORROS,,0.00,25.00",
            f"{day},{voucher_number},INGRESO,REGISTRADO,,139.01,Previsión específica para incobrabilidad de cartera vigente,,0.00,10.00",
            f"{day},{voucher_number},INGRESO,REGISTRADO,,311.01,Capital pagado (Sociedades Anónimas – Bancos y Fondos Financieros Privados),,0.00,5.00",
            f"{day},{voucher_number},INGRESO,REGISTRADO,,511.01,Intereses disponibilidades,,0.00,10.00",
        ],
        "mayor": ["fecha,numero,tipo,estado,cuenta_codigo,glosa,debe,haber,saldo",
                  f"{day},{voucher_number},INGRESO,REGISTRADO,111.01,,100.00,0.00,100.00"],
        "trial": [
            "codigo,nombre,naturaleza,sumas_debe,sumas_haber,saldo_deudor,saldo_acreedor",
            "111.01,Billetes y monedas nominativo por moneda,DEUDORA,100.00,0.00,100.00,0.00",
            "139.01,Previsión específica para incobrabilidad de cartera vigente,ACREEDORA,0.00,10.00,0.00,10.00",
            "211.00,OBLIGACIONES CON EL PUBLICO A LA VISTA,ACREEDORA,0.00,50.00,0.00,50.00",
            "212.00,OBLIGACIONES CON EL PUBLICO POR CUENTAS DE AHORROS,ACREEDORA,0.00,25.00,0.00,25.00",
            "311.01,Capital pagado (Sociedades Anónimas – Bancos y Fondos Financieros Privados),ACREEDORA,0.00,5.00,0.00,5.00",
            "511.01,Intereses disponibilidades,ACREEDORA,0.00,10.00,0.00,10.00",
        ],
        "balance": [
            "Sección,Código,Cuenta,Saldo", "ACTIVO,110.00,DISPONIBILIDADES,100.00",
            "ACTIVO,111.00,CAJA,100.00", "ACTIVO,130.00,CARTERA,-10.00",
            "ACTIVO,139.00,PREVISIÓN PARA INCOBRABILIDAD DE CARTERA,-10.00",
            "PASIVO,210.00,OBLIGACIONES CON EL PUBLICO,75.00",
            "PASIVO,211.00,OBLIGACIONES CON EL PUBLICO A LA VISTA,50.00",
            "PASIVO,212.00,OBLIGACIONES CON EL PUBLICO POR CUENTAS DE AHORROS,25.00",
            "PATRIMONIO,310.00,CAPITAL SOCIAL,5.00", "PATRIMONIO,311.00,CAPITAL PAGADO,5.00",
            "PATRIMONIO,RESULTADO_GESTION,Resultado de la gestión,10.00",
        ],
        "income": [
            "Código,Concepto,Monto", "510.00,INGRESOS FINANCIEROS,10.00",
            ",Resultado financiero bruto,10.00", ",Resultado de operación bruto,10.00",
            ",Resultado de operación después de incobrables,10.00",
            ",Resultado de operación neto,10.00",
            ",Resultado después de ajuste por diferencia de cambio y mantenimiento de valor,10.00",
            ",Resultado neto del ejercicio antes de ajustes de gestiones anteriores,10.00",
            ",Resultado antes de impuestos y ajuste contable por efecto de la inflación,10.00",
            ",Resultado antes de impuestos,10.00", ",Resultado neto de la gestión,10.00",
        ],
        "cartera": ["Sección,Concepto,Valor,Saldo,Mensaje", "Total,cartera_bruta,100.00",
                    "Total,cartera_en_mora,100.00", "Total,indice_mora,100.00", "Total,creditos,1"],
        "captaciones": [
            "Sección,Concepto,Monto", "Ahorros,VISTA,75.55", "DPF,30,0.00", "DPF,31-60,200.25",
            "DPF,61-90,0.00", "DPF,91-180,0.00", "DPF,181-360,0.00", "DPF,361-720,0.00",
            "DPF,721-1080,0.00", "DPF,>1080,0.00",
        ],
        "indicadores": [
            "Clave,Indicador,Valor,Motivo,Definición",
            "liquidez,Liquidez,133.33,,Disponibilidades (110) / obligaciones a la vista y de ahorro (211+212) × 100.",
            "cobertura_previsiones,Cobertura de previsiones,10.00,,|Previsión para incobrabilidad de cartera (139)| / cartera en mora convertida a BOB × 100.",
            "roa,{roa},,Resultado de gestión YTD × 365 / días transcurridos del año / activo total × 100.",
            "roe,{roe},,Resultado de gestión YTD × 365 / días transcurridos del año / patrimonio × 100.",
        ],
    }
    days = (today - today.replace(month=1, day=1)).days + 1
    roa = (Decimal("365000") / Decimal(days * 90)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    roe = (Decimal("365000") / Decimal(days * 15)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    lines = [line.format(roa=f"ROA,{roa:.2f}", roe=f"ROE,{roe:.2f}") for line in templates[report]]
    return b"\xef\xbb\xbf" + "\r\n".join(lines).encode("utf-8") + b"\r\n"


def _assert_pdf_and_csv(client, url, case, title):
    _set_pdf_test_cooperative_name(case)
    default_csv = client.get(url, headers=auth(case))
    explicit_csv = client.get(url + "&formato=csv", headers=auth(case))
    pdf = client.get(url + "&formato=pdf", headers=auth(case))
    invalid = client.get(url + "&formato=xlsx", headers=auth(case))
    assert default_csv.status_code == explicit_csv.status_code == 200
    assert default_csv.content == explicit_csv.content
    route = next((name for path, name in (
        ("/libro-mayor/export", "mayor"), ("/balance-comprobacion/export", "trial"),
        ("/balance-general/export", "balance"), ("/estado-resultados/export", "income"),
        ("/cartera/export", "cartera"), ("/captaciones/export", "captaciones"),
        ("/indicadores/export", "indicadores")) if path in url), None)
    if route:
        query_dates = parse_qs(urlparse(url).query)
        day = date.fromisoformat((query_dates.get("fecha_corte") or query_dates.get("hasta"))[0])
        assert default_csv.content == _legacy_csv_golden(route, case, day)
    assert default_csv.content.startswith(b"\xef\xbb\xbf")
    raw_header_oracles = {
        "/libro-mayor/export": b"fecha,numero,tipo,estado,cuenta_codigo,glosa,debe,haber,saldo",
        "/balance-comprobacion/export": b"codigo,nombre,naturaleza,sumas_debe,sumas_haber,saldo_deudor,saldo_acreedor",
        "/balance-general/export": "Sección,Código,Cuenta,Saldo".encode(),
        "/estado-resultados/export": "Código,Concepto,Monto".encode(),
        "/cartera/export": "Sección,Concepto,Valor,Saldo,Mensaje".encode(),
        "/captaciones/export": "Sección,Concepto,Monto".encode(),
        "/indicadores/export": "Clave,Indicador,Valor,Motivo,Definición".encode(),
    }
    for path, oracle in raw_header_oracles.items():
        if path in url:
            assert default_csv.content.startswith(b"\xef\xbb\xbf" + oracle)
            break
    if "/cartera/export" in url:
        assert all(value in default_csv.content for value in
                   (b"cartera_bruta", b"cartera_en_mora", b"indice_mora", b"creditos"))
    if "/captaciones/export" in url:
        assert b"VISTA,75.55" in default_csv.content and b"31-60,200.25" in default_csv.content
    if "/indicadores/export" in url:
        assert all(value in default_csv.content for value in (b"liquidez", b"roa", b"roe"))
    assert default_csv.headers["content-disposition"] == explicit_csv.headers["content-disposition"]
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.headers["content-disposition"] == default_csv.headers["content-disposition"].replace(".csv", ".pdf")
    assert pdf.content.startswith(b"%PDF")
    extracted = subprocess.run(["pdftotext", "-", "-"], input=pdf.content, check=True,
                               capture_output=True).stdout.decode("utf-8")
    presentation_oracles = {
        "/libro-mayor/export": ("Fecha", "N.º comprobante", "Código de cuenta", "Debe", "Haber", "Saldo"),
        "/balance-comprobacion/export": ("Código", "Naturaleza", "Saldo deudor", "Saldo acreedor"),
        "/balance-general/export": ("Sección", "Código", "Cuenta", "Saldo"),
        "/estado-resultados/export": ("Código", "Concepto", "Monto"),
        "/cartera/export": ("Cartera bruta", "Índice de mora", "N.º de créditos"),
        "/captaciones/export": ("Tipo de cuenta", "Saldo", "Plazo", "Capital", "Captaciones totales"),
        "/indicadores/export": ("Código", "Indicador", "Valor", "Liquidez (%)", "ROA (%)"),
    }
    for path, labels in presentation_oracles.items():
        if path in url:
            missing = [label for label in labels if label not in extracted]
            assert not missing, (path, missing, extracted)
            break
    assert title in extracted
    assert "Período" in extracted
    assert "Peña" in extracted
    assert "Página 1 de" in extracted
    assert invalid.status_code == 422


def test_libro_mayor_export_pdf_and_unchanged_csv(report_case):
    from app.db.session import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as db:
        account_id = db.execute(text("SELECT id FROM plan_cuenta WHERE codigo='111.01' AND cooperativa_id IS NULL")).scalar_one()
    url = f"/api/v1/contabilidad/libro-mayor/export?cuenta_id={account_id}&desde={date.today()}&hasta={date.today()}"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Libro mayor")


def test_balance_comprobacion_export_pdf_and_unchanged_csv(report_case):
    url = f"/api/v1/contabilidad/balance-comprobacion/export?desde={date.today()}&hasta={date.today()}"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Balance de comprobación")


def test_balance_general_export_pdf_and_unchanged_csv(report_case):
    url = f"/api/v1/contabilidad/balance-general/export?fecha_corte={date.today()}&moneda=BOB"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Balance general")
        if os.getenv("SAVE_REPORT_PDF_SAMPLES") == "1":
            response = client.get(url + "&formato=pdf", headers=auth(report_case))
            _save_sample("balance-general.pdf", response.content)


def test_estado_resultados_export_pdf_and_unchanged_csv(report_case):
    url = f"/api/v1/contabilidad/estado-resultados/export?desde={date.today().replace(month=1, day=1)}&hasta={date.today()}&moneda=BOB"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Estado de resultados")


def test_cartera_export_pdf_and_unchanged_csv(report_case):
    _set_pdf_test_cooperative_name(report_case)
    url = f"/api/v1/reportes/cartera/export?fecha_corte={date.today()}"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Cartera")
        sample = client.get(url + "&formato=pdf", headers=auth(report_case))
        text = subprocess.run(["pdftotext", "-", "-"], input=sample.content, check=True,
                              capture_output=True).stdout.decode("utf-8")
        assert "Advertencias" not in text
        _save_sample("cartera.pdf", sample.content)


def test_captaciones_export_pdf_and_unchanged_csv(report_case):
    url = f"/api/v1/reportes/captaciones/export?fecha_corte={date.today()}"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Captaciones")


def test_indicadores_export_pdf_and_unchanged_csv(report_case):
    url = f"/api/v1/reportes/indicadores/export?fecha_corte={date.today()}"
    with TestClient(app) as client:
        _assert_pdf_and_csv(client, url, report_case, "Indicadores de gestión")


def test_asfi_snapshot_export_pdf_uses_persisted_content(report_case, monkeypatch):
    import json
    from app.db.session import SessionLocal
    from app.models import models
    import app.api.v1.endpoints.informes_asfi as asfi

    case = report_case
    with SessionLocal() as db:
        entity = models.Reporte(tipo="CALIFICACION_CARTERA", formato="JSON",
            parametros="{}", usuario_id=case["ids"]["users"][0], cooperativa_id=case["coop_id"],
            periodo=date.today(), estado="GENERADO",
            contenido={"creditos": [{"numero_credito": "SNAP-1", "socio": {"ci": "123", "nombre": "Niño Peña"},
                "producto": "Préstamo", "tipo_credito_asfi": "CONSUMO", "sector_productivo": False,
                "moneda": "BOB", "saldo_capital": "10.00", "dias_mora": 0, "categoria": "A",
                "porcentaje": "1.00", "base_prevision": "10.00", "prevision_especifica": "0.10",
                "prevision_ciclica": "0.10", "garantia_hipotecaria_aplicada": False}], "por_categoria": []})
        db.add(entity); db.commit(); db.refresh(entity)
        report_id = entity.id
    monkeypatch.setattr(asfi, "_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("snapshot must not regenerate")))
    url = f"/api/v1/informes-asfi/reportes/{report_id}/export"
    try:
        with TestClient(app) as client:
            default_csv = client.get(url, headers=auth(case))
            explicit_csv = client.get(url + "?formato=csv", headers=auth(case))
            pdf = client.get(url + "?formato=pdf", headers=auth(case))
            invalid = client.get(url + "?formato=xlsx", headers=auth(case))
        assert default_csv.status_code == explicit_csv.status_code == 200
        assert default_csv.content == explicit_csv.content
        assert default_csv.content.startswith(b"\xef\xbb\xbf")
        expected_csv = ("\ufefftipo_fila,numero_credito,socio_ci,socio_nombre,producto,tipo_credito_asfi,sector_productivo,moneda,saldo_capital,dias_mora,categoria,porcentaje,base_prevision,prevision_especifica,prevision_ciclica,garantia_hipotecaria_aplicada\r\n"
                "CREDITO,SNAP-1,123,Niño Peña,Préstamo,CONSUMO,False,BOB,10.00,0,A,1.00,10.00,0.10,0.10,False\r\n").encode("utf-8")
        assert default_csv.content == expected_csv
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.content.startswith(b"%PDF")
        extracted = subprocess.run(["pdftotext", "-", "-"], input=pdf.content, check=True,
                                   capture_output=True).stdout.decode("utf-8")
        assert default_csv.content.startswith(b"\xef\xbb\xbf" +
            b"tipo_fila,numero_credito,socio_ci,socio_nombre,producto,tipo_credito_asfi,sector_productivo,moneda,saldo_capital,dias_mora,categoria,porcentaje,base_prevision,prevision_especifica,prevision_ciclica,garantia_hipotecaria_aplicada")
        assert "Niño" in extracted and "Peña" in extracted
        assert "SNAP-1" in extracted
        normalized = " ".join(extracted.split())
        assert "N.º de crédito" in normalized and "Saldo de capital" in normalized
        assert invalid.status_code == 422
    finally:
        with SessionLocal() as db:
            db.query(models.Reporte).filter(models.Reporte.id == report_id).delete(synchronize_session=False)
            db.commit()


def test_all_exports_preserve_access_denial_between_csv_and_pdf(voucher_case):
    today = date.today()
    urls = [
        f"/api/v1/contabilidad/libro-diario/export?desde={today}&hasta={today}",
        f"/api/v1/contabilidad/libro-mayor/export?cuenta_id={voucher_case['ids']['official']['111.01']}&desde={today}&hasta={today}",
        f"/api/v1/contabilidad/balance-comprobacion/export?desde={today}&hasta={today}",
        f"/api/v1/contabilidad/balance-general/export?fecha_corte={today}&moneda=BOB",
        f"/api/v1/contabilidad/estado-resultados/export?desde={today.replace(month=1, day=1)}&hasta={today}&moneda=BOB",
        f"/api/v1/reportes/cartera/export?fecha_corte={today}",
        f"/api/v1/reportes/captaciones/export?fecha_corte={today}",
        f"/api/v1/reportes/indicadores/export?fecha_corte={today}",
        "/api/v1/informes-asfi/reportes/1/export",
    ]
    headers = {"Authorization": f"Bearer {voucher_case['tokens'][2]}"}
    with TestClient(app) as client:
        for url in urls:
            csv_response = client.get(url, headers=headers)
            separator = "&" if "?" in url else "?"
            pdf_response = client.get(url + separator + "formato=pdf", headers=headers)
            assert (csv_response.status_code, csv_response.json()) == (pdf_response.status_code, pdf_response.json())
            assert csv_response.status_code == 403


def test_asfi_snapshot_export_preserves_cross_tenant_404(voucher_case):
    from app.db.session import SessionLocal
    from app.models import models

    ids = voucher_case["ids"]
    with SessionLocal() as db:
        report = models.Reporte(tipo="CALIFICACION_CARTERA", formato="JSON", parametros="{}",
            usuario_id=ids["users"][0], cooperativa_id=ids["coops"][0], periodo=date.today(),
            estado="GENERADO", contenido={"creditos": []})
        db.add(report); db.commit(); db.refresh(report)
        report_id = report.id
    headers = {"Authorization": f"Bearer {voucher_case['tokens'][1]}"}
    url = f"/api/v1/informes-asfi/reportes/{report_id}/export"
    try:
        with TestClient(app) as client:
            csv_response = client.get(url, headers=headers)
            pdf_response = client.get(url + "?formato=pdf", headers=headers)
        assert csv_response.status_code == pdf_response.status_code == 404
        assert csv_response.json() == pdf_response.json() == {"detail": "Reporte no encontrado"}
    finally:
        with SessionLocal() as db:
            db.query(models.Reporte).filter(models.Reporte.id == report_id).delete(synchronize_session=False)
            db.commit()


def test_libro_diario_pdf_wraps_long_unicode_account_and_right_aligns_amounts(report_case):
    from app.db.session import SessionLocal
    from app.models import models

    case = report_case
    _set_pdf_test_cooperative_name(case)
    label = "Caja de ahorro Peñón Ñandú con denominación extraordinariamente extensa para reportes financieros"
    with SessionLocal() as db:
        parent = db.query(models.PlanCuenta).filter_by(codigo="111.00", cooperativa_id=None).one()
        account = models.PlanCuenta(codigo=f"111.98.{case['coop_id']}", nombre=label, nivel=5,
            tipo=parent.tipo, naturaleza=parent.naturaleza, es_regularizadora=False, es_oficial=False,
            cooperativa_id=case["coop_id"], estado="ACTIVA", acepta_movimientos=True,
            plan_cuenta_padre_id=parent.id)
        db.add(account); db.flush()
        details = []
        for amount in (Decimal("1234.50"), Decimal("9.00")):
            detail = models.DetalleAsiento(debe=amount, haber=0,
                comprobante_contable_id=case["ids"]["vouchers"][0], plan_cuenta_id=account.id,
                glosa="Ajuste Ñandú de cierre", orden=90 + len(details))
            db.add(detail); db.flush(); details.append(detail.id)
        account_id = account.id
        case["ids"]["details"].extend(details)
        db.commit()
    try:
        query = f"?desde={date.today()}&hasta={date.today()}&formato=pdf"
        with TestClient(app) as client:
            response = client.get("/api/v1/contabilidad/libro-diario/export" + query, headers=auth(case))
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"%PDF")
        _save_sample("libro-diario.pdf", response.content)
        layout = subprocess.run(["pdftotext", "-layout", "-", "-"], input=response.content,
                                check=True, capture_output=True).stdout.decode("utf-8")
        normalized = " ".join(layout.split())
        label_tokens = label.split()
        extracted_words = normalized.split()
        previous = -1
        for token in label_tokens:
            position = extracted_words.index(token, previous + 1)
            assert position > previous
            previous = position
        assert "1234.50" in layout and "9.00" in layout
        bbox = subprocess.run(["pdftotext", "-bbox", "-", "-"], input=response.content,
                              check=True, capture_output=True).stdout
        root = ET.fromstring(bbox)
        words = [word for word in root.iter() if word.tag.endswith("word")]
        amount_edges = [float(word.attrib["xMax"]) for word in words if word.text in {"1234.50", "9.00"}]
        assert len(amount_edges) == 2
        assert abs(amount_edges[0] - amount_edges[1]) < 1.0
        glosa_header_x = next(float(word.attrib["xMin"]) for word in words if word.text == "Glosa")
        name_words = [word for word in words if word.text in {"Peñón", "extraordinariamente"}]
        assert len(name_words) >= 2
        assert max(float(word.attrib["xMax"]) for word in name_words) < glosa_header_x
    finally:
        with SessionLocal() as db:
            db.query(models.DetalleAsiento).filter(models.DetalleAsiento.id.in_(details)).delete(synchronize_session=False)
            db.query(models.PlanCuenta).filter_by(id=account_id).delete(synchronize_session=False)
            db.commit()


def test_pdf_builder_formats_and_renders_supplied_totals():
    from app.services.pdf_reports import build_report_pdf

    content = build_report_pdf(title="Balance con subtotales", period="2026",
        sections=[{"headers": ["Cuenta", "Monto"],
                   "rows": [[f"Ingresos Ñandú {index}", Decimal("9")] for index in range(70)],
                   "totals": [["Total general", Decimal("9")]]}],
        cooperative="Cooperativa Peña", currency="BOB", generated_by="Prueba")
    extracted = subprocess.run(["pdftotext", "-", "-"], input=content, check=True,
                               capture_output=True).stdout.decode("utf-8")
    assert "9.00" in extracted
    assert "Total general" in extracted
    assert extracted.count("Cuenta") >= 2


def test_pdf_exports_use_spanish_headers_but_keep_csv_header_oracles(report_case):
    """PDF labels are friendly while the legacy CSV schema stays raw."""
    today = date.today()
    from app.db.session import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as db:
        account_id = db.execute(text("SELECT id FROM plan_cuenta WHERE codigo='111.01' AND cooperativa_id IS NULL")).scalar_one()
    urls = {
        "diario": f"/api/v1/contabilidad/libro-diario/export?desde={today}&hasta={today}",
        "mayor": f"/api/v1/contabilidad/libro-mayor/export?cuenta_id={account_id}&desde={today}&hasta={today}",
        "balance": f"/api/v1/contabilidad/balance-comprobacion/export?desde={today}&hasta={today}",
        "portfolio": f"/api/v1/reportes/cartera/export?fecha_corte={today}",
    }
    raw_headers = {
        "diario": b"fecha,numero,tipo,estado,orden,cuenta_codigo,cuenta_nombre,glosa,debe,haber",
        "mayor": b"fecha,numero,tipo,estado,cuenta_codigo,glosa,debe,haber,saldo",
        "balance": b"codigo,nombre,naturaleza,sumas_debe,sumas_haber,saldo_deudor,saldo_acreedor",
        "portfolio": b"Secci\xc3\xb3n,Concepto,Valor,Saldo,Mensaje",
    }
    with TestClient(app) as client:
        for name, url in urls.items():
            csv_response = client.get(url, headers=auth(report_case))
            pdf_response = client.get(url + "&formato=pdf", headers=auth(report_case))
            assert csv_response.status_code == pdf_response.status_code == 200
            assert csv_response.content.startswith(b"\xef\xbb\xbf" + raw_headers[name])
            text = subprocess.run(["pdftotext", "-", "-"], input=pdf_response.content, check=True,
                                  capture_output=True).stdout.decode("utf-8")
            expected = {
                "diario": ("Fecha", "N.º comprobante", "Debe", "Haber"),
                "mayor": ("Fecha", "N.º comprobante", "Saldo"),
                "balance": ("Código", "Naturaleza", "Saldo deudor"),
                "portfolio": ("Cartera bruta", "Índice de mora", "N.º de créditos"),
            }[name]
            assert all(label in text for label in expected), (name, [label for label in expected if label not in text], text)
            if name == "portfolio":
                assert "Moneda 1" not in text
            if name in {"diario", "mayor"}:
                assert "BOB (Bolivianos)" in text
                assert "Moneda 1" not in text
            if name == "diario":
                assert "Total del período" in text
            elif name == "mayor":
                assert "Total del período" in text
            elif name == "balance":
                assert "Total general" in text


def test_pdf_builder_respects_semantic_text_alignment_for_account_codes():
    from app.services.pdf_reports import build_report_pdf

    content = build_report_pdf(title="Balance", period="2026", sections=[{
        "headers": ["Código", "Cuenta", "Debe", "Índice", "Créditos"],
        "rows": [["211.00", "Ahorros", "1234.50", "12.50", 3],
                 ["111.98.4675", "Jerárquica", "1.00", "0.50", 12]],
        "column_types": ["text", "text", "amount", "percentage", "count"],
    }])
    xml = subprocess.run(["pdftotext", "-bbox", "-", "-"], input=content, check=True,
                         capture_output=True).stdout.decode("utf-8")
    root = ET.fromstring(xml)
    words = [(node.text, float(node.attrib["xMin"]), float(node.attrib["xMax"]))
             for node in root.iter() if node.tag.endswith("word")]
    code = next(word for word in words if word[0] == "211.00")
    account = next(word for word in words if word[0] == "Ahorros")
    code_heading = next(word for word in words if word[0] == "Código")
    amount = next(word for word in words if word[0] == "1234.50")
    assert code[1] < code_heading[1]
    assert code[1] < account[1]
    assert amount[1] > account[1]
    text = subprocess.run(["pdftotext", "-", "-"], input=content, check=True,
                          capture_output=True).stdout.decode("utf-8")
    normalized = " ".join(text.split())
    assert "111.98.4675" in normalized and "12.50%" in normalized
    assert "3.00" not in normalized and "12.00" not in normalized


def test_book_major_pdf_totals_use_displayed_rows_and_final_row_balance(report_case, monkeypatch):
    from app.db.session import SessionLocal
    from app.models import models
    import app.api.v1.endpoints.libros as libros

    with SessionLocal() as db:
        account_id = db.query(models.PlanCuenta).filter_by(codigo="111.01", cooperativa_id=None).one().id
    monkeypatch.setattr(libros, "_book_major", lambda *args, **kwargs: {
        "totales": {"debe": "888.00", "haber": "777.00"}, "saldo_inicial": "4.00",
        "saldo_final": "999.00", "movimientos": [{"fecha": date.today(),
            "comprobante": {"numero": "GOLDEN-1", "tipo": "INGRESO", "estado": "REGISTRADO"},
            "cuenta_codigo": "111.01", "glosa": "Fixture", "debe": "1.25", "haber": "2.50", "saldo": "7.25"}],
    })
    with TestClient(app) as client:
        response = client.get(f"/api/v1/contabilidad/libro-mayor/export?cuenta_id={account_id}&desde={date.today()}&hasta={date.today()}&formato=pdf", headers=auth(report_case))
    extracted = subprocess.run(["pdftotext", "-", "-"], input=response.content, check=True,
                               capture_output=True).stdout.decode("utf-8")
    assert response.status_code == 200
    assert "888.00" not in extracted and "777.00" not in extracted and "999.00" not in extracted
    assert "1.25" in extracted and "2.50" in extracted and extracted.count("7.25") == 2


def test_trial_balance_pdf_totals_sum_displayed_records(report_case, monkeypatch):
    import app.api.v1.endpoints.libros as libros

    monkeypatch.setattr(libros, "balance_comprobacion", lambda *args, **kwargs: {
        "items": [
            {"codigo": "111.01", "nombre": "Cuenta A", "naturaleza": "DEUDORA",
             "sumas": {"debe": "2.10", "haber": "0.00"},
             "saldos": {"deudor": "2.10", "acreedor": "0.00"}},
            {"codigo": "211.00", "nombre": "Cuenta B", "naturaleza": "ACREEDORA",
             "sumas": {"debe": "0.00", "haber": "2.10"},
             "saldos": {"deudor": "0.00", "acreedor": "2.10"}},
        ], "totales": {"sumas_debe": "900.00", "sumas_haber": "800.00",
                      "saldo_deudor": "700.00", "saldo_acreedor": "600.00"},
    })
    with TestClient(app) as client:
        response = client.get(f"/api/v1/contabilidad/balance-comprobacion/export?desde={date.today()}&hasta={date.today()}&formato=pdf", headers=auth(report_case))
    extracted = subprocess.run(["pdftotext", "-", "-"], input=response.content, check=True,
                               capture_output=True).stdout.decode("utf-8")
    assert response.status_code == 200
    assert all(value not in extracted for value in ("900.00", "800.00", "700.00", "600.00"))
    assert extracted.count("2.10") >= 4
