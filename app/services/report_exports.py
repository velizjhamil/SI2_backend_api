"""Downloadable report documents: PDF and standalone HTML built from the same sections."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from fastapi.responses import Response

from app.services.pdf_reports import _format_cell, _is_total_row, build_report_pdf, prepare_sections

# Formats rendered from report sections; CSV stays in each endpoint (it keeps raw rows).
DOCUMENT_FORMATS = ("pdf", "html")
EXPORT_FORMAT_PATTERN = "^(csv|pdf|html)$"
NUMERIC_TYPES = {"amount", "count", "percentage"}

_HTML_STYLE = """
:root { --ink:#0b1c30; --muted:#64748b; --line:#e2e8f0; --brand:#064e3b; --head:#ecfdf5; --total:#f1f5f9; }
* { box-sizing:border-box; }
body { margin:0; padding:32px 24px; background:#f8fafc; color:var(--ink);
       font:13px/1.5 "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; }
main { max-width:1100px; margin:0 auto; background:#fff; border:1px solid var(--line);
       border-radius:14px; padding:28px 32px; }
header { border-bottom:3px solid var(--brand); padding-bottom:14px; margin-bottom:20px; }
header .coop { font-size:12px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; color:var(--brand); }
header h1 { margin:4px 0 8px; font-size:22px; }
header dl { display:flex; flex-wrap:wrap; gap:4px 24px; margin:0; font-size:12px; color:var(--muted); }
header dt { display:inline; font-weight:600; }
header dd { display:inline; margin:0 0 0 4px; }
h2 { font-size:14px; margin:24px 0 8px; color:var(--brand); }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th, td { border:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); font-weight:700; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
tr.total td { background:var(--total); font-weight:700; }
.empty { padding:24px; text-align:center; color:var(--muted); border:1px dashed var(--line); border-radius:10px; }
footer { margin-top:24px; font-size:11px; color:var(--muted); }
@media print { body { background:#fff; padding:0; } main { border:0; padding:0; } }
"""


def _html_table(section):
    headers, column_types = section["headers"], section["column_types"]
    head = "".join(
        f'<th class="num">{escape(str(h))}</th>' if t in NUMERIC_TYPES else f"<th>{escape(str(h))}</th>"
        for h, t in zip(headers, column_types))

    def row_html(row, types, total):
        cells = "".join(
            f'<td class="num">{escape(_format_cell(row[i] if i < len(row) else None, types[i]))}</td>'
            if types[i] in NUMERIC_TYPES else
            f"<td>{escape(_format_cell(row[i] if i < len(row) else None, types[i]))}</td>"
            for i in range(len(headers)))
        return f'<tr class="total">{cells}</tr>' if total else f"<tr>{cells}</tr>"

    body = [row_html(row, section["row_types"][index] if index < len(section["row_types"]) else column_types,
                     _is_total_row(row))
            for index, row in enumerate(section["rows"])]
    body += [row_html(total, column_types, True) for total in section["totals"]]
    title = f"<h2>{escape(str(section['title']))}</h2>" if section["title"] else ""
    return (f'{title}<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def build_report_html(*, title, period, sections, cooperative=None, currency=None,
                      generated_by=None, orientation=None, empty=False):
    """Self-contained HTML document with the same content as build_report_pdf."""
    prepared = prepare_sections(list(sections))
    generated_at = datetime.now(ZoneInfo("America/La_Paz")).strftime("%d/%m/%Y %H:%M:%S")
    cooperative = cooperative or "Cooperativa"
    content = "".join(_html_table(section) for section in prepared)
    if empty or not prepared:
        content = '<p class="empty">Sin datos para el período seleccionado</p>'
    meta = (("Período", period), ("Moneda", currency or "No especificada"),
            ("Generado", f"{generated_at} (Bolivia)"), ("Generado por", generated_by or "Usuario"))
    meta_html = "".join(f"<div><dt>{escape(k)}:</dt><dd>{escape(str(v))}</dd></div>" for k, v in meta)
    document = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — {escape(cooperative)}</title>
<style>{_HTML_STYLE}</style>
</head>
<body>
<main>
<header>
<div class="coop">{escape(cooperative)}</div>
<h1>{escape(title)}</h1>
<dl>{meta_html}</dl>
</header>
{content}
<footer>Documento generado automáticamente por el sistema de la cooperativa.</footer>
</main>
</body>
</html>
"""
    return document.encode("utf-8")


def report_response(formato: str, filename: str, **report):
    """Render report sections as a PDF or HTML attachment named ``filename.<formato>``."""
    if formato == "html":
        return Response(build_report_html(**report), media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.html"'})
    return Response(build_report_pdf(**report), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})
