"""Voice-command report interpretation and HTML report exports."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.services.comando_voz_reportes import (
    ComandoInvalido, detectar_formato, detectar_periodo, detectar_tipo, interpretar_comando, normalizar,
)
from test_reportes_api import app, auth, report_case  # noqa: F401  -- fixture re-export

HOY = date(2026, 11, 2)


def _usuario(rol="ADMINISTRADOR", cooperativa_id=1):
    return SimpleNamespace(rol=SimpleNamespace(nombre=rol), cooperativa_id=cooperativa_id)


@pytest.mark.parametrize("frase, tipo", [
    ("Genera el reporte de cartera en mora", "mora"),
    ("Quiero ver la morosidad", "mora"),
    ("reporte diario de cartera", "cartera"),
    ("Dame el reporte de créditos", "cartera"),
    ("captaciones de ahorro y DPF", "captaciones"),
    ("Indicadores de gestión", "indicadores"),
    ("muéstrame el ROE", "indicadores"),
    ("Balance de comprobación de octubre", "balance-comprobacion"),
    ("balance general del año pasado", "balance-general"),
    ("Estado de resultados de este año", "estado-resultados"),
    ("libro diario de este mes", "libro-diario"),
    ("Libro mayor de la cuenta 111", "libro-mayor"),
    ("Resumen ejecutivo", "resumen-ejecutivo"),
    ("dame el resumen", "resumen-ejecutivo"),
    ("hola qué tal", None),
])
def test_detects_report_type(frase, tipo):
    assert detectar_tipo(normalizar(frase)) == tipo


@pytest.mark.parametrize("frase, formato", [
    ("en PDF", "pdf"), ("en p d f", "pdf"), ("en CSV", "csv"), ("en CCV", "csv"),
    ("para Excel", "csv"), ("en HTML", "html"), ("como página web", "html"), ("sin formato", None),
])
def test_detects_format(frase, formato):
    assert detectar_formato(normalizar(frase)) == formato


@pytest.mark.parametrize("frase, desde, hasta", [
    ("cartera", None, HOY),
    ("al 30 de septiembre", None, date(2026, 9, 30)),
    ("al 30 de diciembre", None, date(2025, 12, 30)),
    ("al 15/10/2026", None, date(2026, 10, 15)),
    ("ayer", None, date(2026, 11, 1)),
    ("del mes pasado", date(2026, 10, 1), date(2026, 10, 31)),
    ("de este mes", date(2026, 11, 1), HOY),
    ("de el mes de agosto", date(2026, 8, 1), date(2026, 8, 31)),
    ("del año pasado", date(2025, 1, 1), date(2025, 12, 31)),
    ("del año 2025", date(2025, 1, 1), date(2025, 12, 31)),
    ("de este año", date(2026, 1, 1), HOY),
    ("de diciembre", date(2025, 12, 1), date(2025, 12, 31)),
    ("del 1 al 15 de octubre", date(2026, 10, 1), date(2026, 10, 15)),
    ("del 15 de diciembre al 10 de enero", date(2025, 12, 15), date(2026, 1, 10)),
    ("de los últimos 7 días", date(2026, 10, 27), HOY),
    ("libro mayor de caja de octubre", date(2026, 10, 1), date(2026, 10, 31)),
    ("libro mayor de caja", None, HOY),
    ("al 5 de mayo", None, date(2026, 5, 5)),
])
def test_detects_period(frase, desde, hasta):
    periodo = detectar_periodo(normalizar(frase), HOY)
    assert (periodo.desde, periodo.hasta) == (desde, hasta)


@pytest.mark.parametrize("frase", ["al 30 de noviembre de 2026", "al 31 de febrero", "del año 2027"])
def test_rejects_future_or_impossible_dates(frase):
    with pytest.raises(ComandoInvalido):
        detectar_periodo(normalizar(frase), HOY)


def test_interprets_cutoff_report_with_currency_and_download():
    result = interpretar_comando("Descarga el reporte de cartera en dólares al 30 de septiembre en PDF",
                                 _usuario(), db=None, hoy=HOY)
    assert result["reconocido"] is True
    assert result["endpoint"] == "/reportes/cartera/export"
    assert result["parametros"] == {"fecha_corte": "2026-09-30", "moneda_id": 2}
    assert result["formato"] == "pdf"
    assert result["accion"] == "descargar"
    assert result["descripcion"] == "Reporte de cartera al 30/09/2026 · Dólares (USD)"


def test_interprets_range_report_defaulting_to_current_month():
    result = interpretar_comando("muéstrame el estado de resultados", _usuario("CONTADOR"), db=None, hoy=HOY)
    assert result["parametros"] == {"desde": "2026-11-01", "hasta": "2026-11-02", "moneda": "BOB"}
    assert result["formato"] is None and result["accion"] == "mostrar"
    consolidado = interpretar_comando("balance general consolidado", _usuario("CONTADOR"), db=None, hoy=HOY)
    assert consolidado["parametros"]["moneda"] == "CONSOLIDADO"


def test_accounting_reports_are_denied_to_credit_officer_with_suggestions():
    result = interpretar_comando("libro diario en PDF", _usuario("OFICIAL_CREDITO"), db=None, hoy=HOY)
    assert result["reconocido"] is False
    assert "Administrador o Contador" in result["mensaje"]
    assert result["sugerencias"] and all("Libro" not in s for s in result["sugerencias"])


def test_unknown_command_and_user_without_cooperative():
    assert interpretar_comando("hola", _usuario(), db=None, hoy=HOY)["reconocido"] is False
    sin_coop = interpretar_comando("cartera", _usuario(cooperativa_id=None), db=None, hoy=HOY)
    assert sin_coop["reconocido"] is False and sin_coop["sugerencias"] == []


EXPORTS = [
    ("/api/v1/reportes/cartera/export", {}),
    ("/api/v1/reportes/mora/export", {}),
    ("/api/v1/reportes/captaciones/export", {}),
    ("/api/v1/reportes/indicadores/export", {}),
    ("/api/v1/reportes/resumen-ejecutivo/export", {}),
    ("/api/v1/contabilidad/libro-diario/export", {}),
    ("/api/v1/contabilidad/balance-comprobacion/export", {}),
    # The fixture has no USD exchange rate, so CONSOLIDADO statements would be rejected.
    ("/api/v1/contabilidad/balance-general/export", {"fecha_corte": date.today().isoformat(), "moneda": "BOB"}),
    ("/api/v1/contabilidad/estado-resultados/export",
     {"desde": date.today().replace(day=1).isoformat(), "hasta": date.today().isoformat(), "moneda": "BOB"}),
]


@pytest.mark.parametrize("path, params", EXPORTS)
def test_every_report_exports_csv_pdf_and_html(report_case, path, params):  # noqa: F811
    with TestClient(app) as client:
        responses = {fmt: client.get(path, params={**params, "formato": fmt}, headers=auth(report_case))
                     for fmt in ("csv", "pdf", "html")}
    for fmt, response in responses.items():
        assert response.status_code == 200, (fmt, response.text)
        assert response.headers["content-disposition"].endswith(f'.{fmt}"')
    assert responses["pdf"].content.startswith(b"%PDF")
    html = responses["html"]
    assert html.headers["content-type"].startswith("text/html")
    assert html.text.startswith("<!doctype html>") and "<script" not in html.text
    assert '<div class="coop">Reportes ' in html.text


def test_html_escapes_report_content():
    from app.services.report_exports import build_report_html
    content = build_report_html(title="<b>x</b>", period="hoy", cooperative="Coop & <Socios>",
                                sections=[{"headers": ["Concepto", "Monto"], "rows": [["<script>", "10"]]}])
    assert b"<script>" not in content and b"&lt;script&gt;" in content and b"Coop &amp; &lt;Socios&gt;" in content


def test_voice_command_endpoint_round_trip(report_case):  # noqa: F811
    with TestClient(app) as client:
        command = client.post("/api/v1/reportes/comando-voz", headers=auth(report_case),
                              json={"texto": "libro mayor de la cuenta 111.01 de este mes en HTML"})
        assert command.status_code == 200, command.text
        body = command.json()
        assert body["reconocido"] is True and body["tipo"] == "libro-mayor" and body["formato"] == "html"
        assert body["nombre"].startswith("Libro mayor — 111.01")
        export = client.get(f"/api/v1{body['endpoint']}", headers=auth(report_case),
                            params={**body["parametros"], "formato": body["formato"]})
    assert export.status_code == 200, export.text
    assert "Libro mayor" in export.text


def test_voice_command_requires_authentication():
    with TestClient(app) as client:
        # HTTPBearer answers 401 or 403 depending on the FastAPI version.
        assert client.post("/api/v1/reportes/comando-voz", json={"texto": "cartera"}).status_code in (401, 403)
