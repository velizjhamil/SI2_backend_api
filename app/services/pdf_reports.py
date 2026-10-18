"""Shared Unicode PDF rendering for downloadable financial reports."""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from fpdf import FPDF


FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
AMOUNT_HEADER_PARTS = (
    "monto", "saldo", "debe", "haber", "capital", "importe", "prevision", "cartera",
    "valor", "total", "sumas", "suma", "amount",
)
COUNT_HEADER_PARTS = ("numero", "orden", "dias", "cuentas", "creditos", "certificados")
CENT = Decimal("0.01")

PDF_HEADERS = {
    "fecha": "Fecha", "numero": "N.º comprobante", "tipo": "Tipo", "estado": "Estado",
    "orden": "Orden", "cuenta_codigo": "Código de cuenta", "cuenta_nombre": "Cuenta",
    "codigo": "Código", "nombre": "Cuenta", "naturaleza": "Naturaleza", "glosa": "Glosa",
    "debe": "Debe", "haber": "Haber", "saldo": "Saldo", "sumas_debe": "Debe",
    "sumas_haber": "Haber", "saldo_deudor": "Saldo deudor", "saldo_acreedor": "Saldo acreedor",
    "seccion": "Sección", "sección": "Sección", "concepto": "Concepto", "valor": "Valor",
    "monto": "Monto", "clave": "Código", "indicador": "Indicador", "definicion": "Definición",
    "definición": "Definición", "motivo": "Observación", "mensaje": "Observación",
    "tipo_fila": "Tipo de registro", "numero_credito": "N.º de crédito", "socio_ci": "C.I. socio",
    "socio_nombre": "Socio", "producto": "Producto", "tipo_credito_asfi": "Tipo de crédito ASFI",
    "sector_productivo": "Sector productivo", "moneda": "Moneda", "saldo_capital": "Saldo de capital",
    "dias_mora": "Días de mora", "categoria": "Categoría", "porcentaje": "Porcentaje",
    "base_prevision": "Base de previsión", "prevision_especifica": "Previsión específica",
    "prevision_ciclica": "Previsión cíclica", "garantia_hipotecaria_aplicada": "Garantía hipotecaria aplicada",
    "ci": "C.I.", "monto_desembolsado": "Monto desembolsado", "fecha_desembolso": "Fecha de desembolso",
    "fecha_vencimiento_final": "Vencimiento final", "prevision": "Previsión",
}
PDF_CONCEPTS = {
    "cartera_bruta": "Cartera bruta", "cartera_en_mora": "Cartera en mora",
    "indice_mora": "Índice de mora", "creditos": "N.º de créditos", "cuentas_ahorro": "N.º de cuentas de ahorro",
    "saldo_ahorro": "Saldo de ahorro", "certificados_dpf": "N.º de certificados DPF",
    "capital_dpf": "Capital de DPF", "captaciones": "Captaciones totales",
    "liquidez": "Liquidez (%)", "cobertura_previsiones": "Cobertura de previsiones (%)",
    "roa": "ROA (%)", "roe": "ROE (%)",
}


class ReportPDF(FPDF):
    def __init__(self, *, title, period, cooperative, currency, generated_by, orientation, page_format="A4"):
        super().__init__(orientation=orientation, unit="mm", format=page_format)
        self.report_title = title
        self.period = period
        self.cooperative = cooperative or "Cooperativa"
        self.currency = currency or "No especificada"
        self.generated_by = generated_by or "Usuario"
        self.generated_at = datetime.now(ZoneInfo("America/La_Paz")).strftime("%d/%m/%Y %H:%M:%S")
        self.set_margins(10, 10, 10)
        self.set_auto_page_break(auto=True, margin=14)
        self.alias_nb_pages()
        self.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))

    def header(self):
        self.set_font("DejaVu", "B", 12)
        self.cell(0, 7, self.cooperative, new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "B", 10)
        self.cell(0, 6, self.report_title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 8)
        self.cell(0, 5, f"Período: {self.period} | Moneda: {self.currency}", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 5, f"Generado: {self.generated_at} (Bolivia) | Generado por: {self.generated_by}",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-10)
        self.set_font("DejaVu", "", 8)
        self.cell(0, 5, f"Página {self.page_no()} de {{nb}}", align="C")


def _is_amount_column(header):
    normalized = str(header).strip().lower()
    if any(part in normalized for part in COUNT_HEADER_PARTS):
        return False
    return any(part in normalized for part in AMOUNT_HEADER_PARTS)


def _decimal_value(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _format_cell(value, column_type):
    if value is None:
        return ""
    if column_type == "percentage":
        number = _decimal_value(value)
        if number is not None:
            return f"{number.quantize(CENT, rounding=ROUND_HALF_UP):.2f}%"
        text = str(value)
        return text if text in {"", "None"} or text.endswith("%") else f"{text}%"
    if column_type == "count":
        number = _decimal_value(value)
        return str(int(number)) if number is not None and number == number.to_integral_value() else str(value)
    number = _decimal_value(value)
    if column_type == "amount" and number is not None:
        return str(number.quantize(CENT, rounding=ROUND_HALF_UP))
    return str(value)


def _wrap_text(pdf, text, width):
    """Wrap at measured font widths and split oversized words without dropping text."""
    usable = max(1.0, width - 2.0)
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if pdf.get_string_width(candidate) <= usable:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            chunk = ""
            for character in word:
                candidate = chunk + character
                if chunk and pdf.get_string_width(candidate) > usable:
                    lines.append(chunk)
                    chunk = character
                else:
                    chunk = candidate
            current = chunk
        lines.append(current)
    return lines or [""]


def _preferred_widths(pdf, headers, rows, column_types, available_width):
    minimum = 14.0
    max_preferred = min(72.0, available_width)
    preferences = []
    pdf.set_font("DejaVu", "B", 8)
    header_widths = [pdf.get_string_width(str(header)) + 4.0 for header in headers]
    pdf.set_font("DejaVu", "", 7)
    content_widths = []
    for column in range(len(headers)):
        max_width = 0.0
        for row_index, row in enumerate(rows):
            value = _format_cell(row[column] if column < len(row) else None, column_types[column])
            for line in value.splitlines() or [""]:
                max_width = max(max_width, pdf.get_string_width(line) + 4.0)
        content_widths.append(max_width)
    preferences = [min(max_preferred, max(minimum, header_widths[i], content_widths[i]))
                   for i in range(len(headers))]
    total = sum(preferences)
    if total <= 0:
        return [available_width / max(len(headers), 1)] * len(headers)
    # Proportional scaling retains wider name/glosa columns while guaranteeing the table fits the page.
    return [width * available_width / total for width in preferences]


def _draw_row(pdf, values, widths, column_types, *, bold=False, fill=False):
    font_style = "B" if bold else ""
    pdf.set_font("DejaVu", font_style, 7)
    rendered = [_format_cell(values[i] if i < len(values) else None, column_types[i])
                for i in range(len(widths))]
    wrapped = [_wrap_text(pdf, text, widths[i]) for i, text in enumerate(rendered)]
    line_height = 3.8
    row_height = max(len(lines) for lines in wrapped) * line_height + 2.0
    if fill:
        pdf.set_fill_color(235, 241, 247)
    x_start, y_start = pdf.get_x(), pdf.get_y()
    for i, width in enumerate(widths):
        x = x_start + sum(widths[:i])
        if fill:
            pdf.rect(x, y_start, width, row_height, style="FD")
        else:
            pdf.rect(x, y_start, width, row_height)
        pdf.set_font("DejaVu", font_style, 7)
        align = "R" if column_types[i] in {"amount", "count", "percentage"} else "L"
        lines = wrapped[i]
        for line_index, line in enumerate(lines):
            line_width = pdf.get_string_width(line)
            if align == "R":
                x_text = x + width - 1.0 - line_width
            elif align == "C":
                x_text = x + max(1.0, (width - line_width) / 2.0)
            else:
                x_text = x + 1.0
            pdf.text(x_text, y_start + 1.0 + (line_index + 0.8) * line_height, line)
    pdf.set_xy(x_start, y_start + row_height)
    return row_height


def _draw_heading(pdf, headers, widths, bottom):
    pdf.set_font("DejaVu", "B", 8)
    wrapped = [_wrap_text(pdf, header, widths[i]) for i, header in enumerate(headers)]
    line_height = 4.0
    height = max(len(lines) for lines in wrapped) * line_height + 2.0
    if pdf.get_y() + height > bottom:
        pdf.add_page()
    x_start, y_start = pdf.get_x(), pdf.get_y()
    for i, width in enumerate(widths):
        x = x_start + sum(widths[:i])
        pdf.set_fill_color(220, 228, 238)
        pdf.rect(x, y_start, width, height, style="FD")
        for line_index, line in enumerate(wrapped[i]):
            line_width = pdf.get_string_width(line)
            pdf.text(x + max(1.0, (width - line_width) / 2.0),
                     y_start + 1.0 + (line_index + 0.8) * line_height, line)
    pdf.set_xy(x_start, y_start + height)
    return height


def _is_total_row(row):
    label = next((str(value).strip().lower() for value in row if value not in (None, "")), "")
    return label.startswith(("total", "subtotal", "resultado"))


def build_report_pdf(*, title, period, sections, cooperative=None, currency=None,
                     generated_by=None, orientation=None, empty=False):
    """Render supplied report rows; callers own the single source-data execution."""
    sections = list(sections)
    max_columns = max((len(section.get("headers", [])) for section in sections), default=0)
    page_orientation = orientation or ("L" if max_columns > 6 else "P")
    page_format = "A3" if page_orientation == "L" and max_columns > 10 else "A4"
    pdf = ReportPDF(title=title, period=period, cooperative=cooperative, currency=currency,
                    generated_by=generated_by, orientation=page_orientation, page_format=page_format)
    pdf.add_page()
    available_width = pdf.w - pdf.l_margin - pdf.r_margin
    bottom = pdf.h - pdf.b_margin
    rendered_rows = 0
    for section in sections:
        source_headers = [str(header) for header in section.get("headers", [])]
        rows = [list(row) for row in section.get("rows", [])]
        totals = [list(row) for row in section.get("totals", [])]
        # PDF-only presentation metadata never changes the caller's CSV rows/headers.
        keep = [i for i in range(len(source_headers)) if any(
            i < len(row) and row[i] not in (None, "") for row in rows + totals)]
        if not keep:
            keep = list(range(len(source_headers)))
        source_headers = [source_headers[i] for i in keep]
        rows = [[row[i] if i < len(row) else None for i in keep] for row in rows]
        totals = [[row[i] if i < len(row) else None for i in keep] for row in totals]
        row_types = [[row[i] if i < len(row) else "text" for i in keep]
                     for row in section.get("row_types", [])]
        headers = [PDF_HEADERS.get(header.strip().lower(), header) for header in source_headers]
        rows = [[PDF_CONCEPTS.get(str(value), value) for value in row] for row in rows]
        totals = [[PDF_CONCEPTS.get(str(value), value) for value in row] for row in totals]
        all_rows = rows + totals
        if not headers or not all_rows:
            continue
        if section.get("title"):
            pdf.set_font("DejaVu", "B", 9)
            pdf.multi_cell(0, 6, str(section["title"]))
            pdf.set_x(pdf.l_margin)
        rendered_rows += len(all_rows)
        provided_types = section.get("column_types", [])
        column_types = [provided_types[source_i] if source_i < len(provided_types) else
                        ("amount" if _is_amount_column(header) else "text")
                        for source_i, header in zip(keep, headers)]
        # Human-friendly headings such as "Debe" still mean amounts unless the caller marks a code/text column.
        column_types = ["amount" if t == "text" and _is_amount_column(h) else t for t, h in zip(column_types, headers)]
        widths = _preferred_widths(pdf, headers, all_rows, column_types, available_width)

        def heading():
            _draw_heading(pdf, headers, widths, bottom)

        heading()
        for row_index, row in enumerate(rows):
            # Measure before deciding whether to paginate; rows remain intact and headings repeat.
            pdf.set_font("DejaVu", "", 7)
            row_column_types = row_types[row_index] if row_index < len(row_types) else column_types
            wrapped = [_wrap_text(pdf, _format_cell(row[i] if i < len(row) else None, row_column_types[i]), widths[i])
                       for i in range(len(widths))]
            row_height = max(len(lines) for lines in wrapped) * 3.8 + 2.0
            if pdf.get_y() + row_height > bottom:
                pdf.add_page()
                heading()
            _draw_row(pdf, row, widths, row_column_types, bold=_is_total_row(row), fill=_is_total_row(row))
        for total in totals:
            pdf.set_font("DejaVu", "", 7)
            wrapped = [_wrap_text(pdf, _format_cell(total[i] if i < len(total) else None, column_types[i]), widths[i])
                       for i in range(len(widths))]
            row_height = max(len(lines) for lines in wrapped) * 3.8 + 2.0
            if pdf.get_y() + row_height > bottom:
                pdf.add_page()
                heading()
            _draw_row(pdf, total, widths, column_types, bold=True, fill=True)
        pdf.ln(2)
    if empty or rendered_rows == 0:
        pdf.set_font("DejaVu", "", 10)
        pdf.multi_cell(0, 7, "Sin datos para el período seleccionado")
    result = pdf.output()
    return bytes(result)
