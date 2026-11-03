"""Interpretación de comandos de voz para reportes.

El navegador transcribe la voz (Web Speech API) y envía el texto; aquí se
resuelve de forma determinista qué reporte exportar, en qué formato y con qué
parámetros (fechas, moneda, cuenta). No se ejecuta ningún reporte: se devuelve
la ruta de exportación y sus parámetros para que el cliente la descargue con
los mismos permisos que el resto de la API.
"""

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import Usuario

ROLES_GERENCIALES = frozenset({"ADMINISTRADOR", "CONTADOR", "OFICIAL_CREDITO"})
ROLES_CONTABILIDAD = frozenset({"ADMINISTRADOR", "CONTADOR"})
FORMATOS = ("csv", "pdf", "html")
NOMBRES_ROL = {"ADMINISTRADOR": "Administrador", "CONTADOR": "Contador", "OFICIAL_CREDITO": "Oficial de crédito"}


@dataclass(frozen=True)
class TipoReporte:
    tipo: str
    nombre: str
    endpoint: str
    roles: frozenset
    periodo: str          # "corte" (una fecha) o "rango" (desde/hasta)
    moneda: str | None    # "id" (moneda_id), "modo" (BOB/USD/CONSOLIDADO) o None
    ejemplo: str


CATALOGO = {
    t.tipo: t for t in (
        TipoReporte("resumen-ejecutivo", "Resumen ejecutivo", "/reportes/resumen-ejecutivo/export",
                    ROLES_GERENCIALES, "corte", "id", "Resumen ejecutivo en PDF"),
        TipoReporte("cartera", "Reporte de cartera", "/reportes/cartera/export",
                    ROLES_GERENCIALES, "corte", "id", "Reporte de cartera en dólares en HTML"),
        TipoReporte("mora", "Reporte de morosidad", "/reportes/mora/export",
                    ROLES_GERENCIALES, "corte", "id", "Reporte de morosidad del mes pasado en PDF"),
        TipoReporte("captaciones", "Reporte de captaciones", "/reportes/captaciones/export",
                    ROLES_GERENCIALES, "corte", "id", "Descarga las captaciones al 30 de septiembre en CSV"),
        TipoReporte("indicadores", "Indicadores de gestión", "/reportes/indicadores/export",
                    ROLES_GERENCIALES, "corte", None, "Indicadores de gestión de hoy en PDF"),
        TipoReporte("balance-general", "Balance general", "/contabilidad/balance-general/export",
                    ROLES_CONTABILIDAD, "corte", "modo", "Balance general del año pasado en PDF"),
        TipoReporte("estado-resultados", "Estado de resultados", "/contabilidad/estado-resultados/export",
                    ROLES_CONTABILIDAD, "rango", "modo", "Estado de resultados de este año en HTML"),
        TipoReporte("balance-comprobacion", "Balance de comprobación",
                    "/contabilidad/balance-comprobacion/export",
                    ROLES_CONTABILIDAD, "rango", "id", "Balance de comprobación de octubre en CSV"),
        TipoReporte("libro-diario", "Libro diario", "/contabilidad/libro-diario/export",
                    ROLES_CONTABILIDAD, "rango", "id", "Libro diario de este mes en PDF"),
        TipoReporte("libro-mayor", "Libro mayor", "/contabilidad/libro-mayor/export",
                    ROLES_CONTABILIDAD, "rango", "id", "Libro mayor de la cuenta 111 en CSV"),
    )
}

# Frases específicas primero; luego palabras sueltas en orden de prioridad
# ("cartera en mora" es morosidad; "reporte diario de cartera" es cartera).
_CLAVES_FUERTES = (
    (r"resumen (ejecutivo|gerencial|general)", "resumen-ejecutivo"),
    (r"balance de comprobacion|sumas y saldos", "balance-comprobacion"),
    (r"balance general|situacion financiera", "balance-general"),
    (r"estado de resultados|perdidas y ganancias", "estado-resultados"),
    (r"libro diario", "libro-diario"),
    (r"libro mayor", "libro-mayor"),
    (r"indicadores de gestion", "indicadores"),
)
_CLAVES_DEBILES = (
    (r"\bmora\b|\bmoros|\batras|\bvencid|\bimpag", "mora"),
    (r"\bcaptacion|\bahorro|\bdeposito|\bplazo fijo|\bdpf\b", "captaciones"),
    (r"\bindicador|\bkpi|\bliquidez\b|\broa\b|\broe\b|\brentabilidad", "indicadores"),
    (r"\bcartera\b|\bcredito|\bprestamo|\bcolocacion", "cartera"),
    (r"\bcomprobacion\b", "balance-comprobacion"),
    (r"\bbalance\b", "balance-general"),
    (r"\bresultados\b|\bganancias\b|\bperdidas\b", "estado-resultados"),
    (r"\bdiario\b|\basientos\b", "libro-diario"),
    (r"\bmayor\b", "libro-mayor"),
    (r"\bresumen\b", "resumen-ejecutivo"),
)
_FORMATO_CLAVES = (
    ("pdf", r"\bp\s?d\s?f\b|\bpe de efe\b"),
    ("csv", r"\bc\s?[sc]\s?v\b|\bexcel\b|hoja de calculo|\bce ese uve\b|separado por comas"),
    ("html", r"\bh\s?t\s?m\s?l\b|\ba?che te eme ele\b|\bpagina web\b|\bweb\b"),
)
_DESCARGAR = r"\bdescarg|\bbaja(r|me)?\b|\bguard|\bexport"

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
# Fin de palabra obligatorio: "mayor" (libro mayor) no es el mes "mayo".
_MES = "(" + "|".join(MESES) + r")\b"
_DIA = r"(\d{1,2}|primero)"
_ANIO = r"(?:\s+(?:de|del)\s+(?:ano\s+)?(\d{4}))?"


class ComandoInvalido(ValueError):
    """El comando nombra un período imposible (fecha futura, día inexistente)."""


def normalizar(texto: str) -> str:
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                          if unicodedata.category(c) != "Mn")
    # Conserva separadores de fecha y de códigos de cuenta (/, -, .).
    limpio = re.sub(r"[^a-z0-9/.\- ]+", " ", sin_acentos)
    limpio = re.sub(r"(?<!\d)[.\-](?!\d)", " ", limpio)
    return re.sub(r"\s+", " ", limpio).strip()


def detectar_tipo(texto: str) -> str | None:
    for patron, tipo in _CLAVES_FUERTES + _CLAVES_DEBILES:
        if re.search(patron, texto):
            return tipo
    return None


def detectar_formato(texto: str) -> str | None:
    for formato, patron in _FORMATO_CLAVES:
        if re.search(patron, texto):
            return formato
    return None


def detectar_moneda(texto: str) -> str | None:
    if re.search(r"\bdolar|\busd\b", texto):
        return "USD"
    if re.search(r"\bbolivian|\bbs\b|\bbob\b", texto):
        return "BOB"
    if re.search(r"\bconsolidad|todas las monedas", texto):
        return "CONSOLIDADO"
    return None


def _fin_mes(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def _fecha(dia: str, mes: int, anio: str | None, hoy: date) -> date:
    numero = 1 if dia == "primero" else int(dia)
    year = int(anio) if anio else hoy.year
    try:
        resultado = date(year, mes, numero)
    except ValueError as exc:
        raise ComandoInvalido(f"La fecha {numero}/{mes}/{year} no existe.") from exc
    if anio is None and resultado > hoy:
        # "al 30 de diciembre" dicho en noviembre se refiere al año anterior.
        resultado = date(year - 1, mes, numero)
    if resultado > hoy:
        raise ComandoInvalido("La fecha indicada es futura; los reportes solo pueden generarse hasta hoy.")
    return resultado


def _numerica(dia: str, mes: str, anio: str) -> date:
    year = int(anio) + (2000 if len(anio) == 2 else 0)
    try:
        return date(year, int(mes), int(dia))
    except ValueError as exc:
        raise ComandoInvalido(f"La fecha {dia}/{mes}/{anio} no existe.") from exc


@dataclass(frozen=True)
class Periodo:
    desde: date | None    # None: el comando nombró una sola fecha (o ninguna)
    hasta: date
    explicito: bool


def detectar_periodo(texto: str, hoy: date) -> Periodo:
    """Extrae el período nombrado; sin mención devuelve hoy (no explícito)."""
    rango = re.search(
        rf"(?:\bdel?|\bdesde(?: el)?)\s+{_DIA}(?:\s+de\s+{_MES}{_ANIO})?\s+"
        rf"(?:al|hasta(?: el)?|a)\s+{_DIA}\s+de\s+{_MES}{_ANIO}", texto)
    if rango:
        d1, m1, a1, d2, m2, a2 = rango.groups()
        hasta = _fecha(d2, MESES[m2], a2, hoy)
        mes_desde = MESES[m1 or m2]
        # Sin año propio, el inicio cae en el año de la fecha final (o el anterior si su mes es posterior).
        anio_desde = a1 or str(hasta.year if mes_desde <= hasta.month else hasta.year - 1)
        desde = _fecha(d1, mes_desde, anio_desde, hoy)
        return _validar(Periodo(desde, hasta, True), hoy)

    numericas = [_numerica(d, m, a) for d, m, a in
                 re.findall(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b", texto)]
    numericas += [_numerica(d, m, a) for a, m, d in
                  re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", texto)]
    if len(numericas) >= 2:
        return _validar(Periodo(min(numericas[:2]), max(numericas[:2]), True), hoy)
    if numericas:
        return _validar(Periodo(None, numericas[0], True), hoy)

    unica = re.search(rf"{_DIA}\s+de\s+{_MES}{_ANIO}", texto)
    if unica:
        return Periodo(None, _fecha(unica.group(1), MESES[unica.group(2)], unica.group(3), hoy), True)

    if re.search(r"\bante ?ayer\b|\bantes de ayer\b", texto):
        return Periodo(None, hoy - timedelta(days=2), True)
    if re.search(r"\bayer\b", texto):
        return Periodo(None, hoy - timedelta(days=1), True)
    if re.search(r"\bhoy\b|\bal dia\b", texto):
        return Periodo(None, hoy, True)

    ultimos = re.search(r"\bultimos?\s+(\d+)\s+dias\b", texto)
    if ultimos:
        return Periodo(hoy - timedelta(days=max(int(ultimos.group(1)), 1) - 1), hoy, True)
    if re.search(r"\b(ultima semana|semana pasada|semana anterior)\b", texto):
        return Periodo(hoy - timedelta(days=6), hoy, True)
    if re.search(r"\besta semana\b", texto):
        return Periodo(hoy - timedelta(days=hoy.weekday()), hoy, True)

    if re.search(r"\bmes (pasado|anterior)\b", texto):
        fin = hoy.replace(day=1) - timedelta(days=1)
        return Periodo(fin.replace(day=1), fin, True)
    if re.search(rf"\b(este|del|el) mes\b(?! de {_MES})|\bmes actual\b", texto):
        return Periodo(hoy.replace(day=1), hoy, True)
    if re.search(r"\b(ano|gestion|ejercicio) (pasad[oa]|anterior)\b", texto):
        return Periodo(date(hoy.year - 1, 1, 1), date(hoy.year - 1, 12, 31), True)
    if re.search(r"\b(este|del|el) ano\b(?!\s+\d{4})|\bano actual\b|\b(esta|la) gestion\b(?!\s+\d{4})"
                 r"|\bgestion actual\b", texto):
        return Periodo(date(hoy.year, 1, 1), hoy, True)

    mes = re.search(rf"\b{_MES}{_ANIO}", texto)
    if mes:
        numero, anio = MESES[mes.group(1)], mes.group(2)
        year = int(anio) if anio else (hoy.year if numero <= hoy.month else hoy.year - 1)
        inicio = date(year, numero, 1)
        if inicio > hoy:
            raise ComandoInvalido("El mes indicado es futuro; los reportes solo pueden generarse hasta hoy.")
        return Periodo(inicio, min(_fin_mes(year, numero), hoy), True)

    anio = re.search(r"\b(?:ano |gestion |ejercicio )?(20\d{2})\b", texto)
    if anio:
        year = int(anio.group(1))
        if year > hoy.year:
            raise ComandoInvalido("El año indicado es futuro; los reportes solo pueden generarse hasta hoy.")
        return Periodo(date(year, 1, 1), min(date(year, 12, 31), hoy), True)

    return Periodo(None, hoy, False)


def _validar(periodo: Periodo, hoy: date) -> Periodo:
    hasta = min(periodo.hasta, hoy) if periodo.desde else periodo.hasta
    if periodo.desde is None and hasta > hoy:
        raise ComandoInvalido("La fecha indicada es futura; los reportes solo pueden generarse hasta hoy.")
    if periodo.desde and periodo.desde > hasta:
        raise ComandoInvalido("La fecha inicial del período es posterior a la final.")
    return Periodo(periodo.desde, hasta, periodo.explicito)


def _buscar_cuenta(db: Session, tenant: int, texto: str):
    """Cuenta del libro mayor por código ("cuenta 111", "111.01") o por nombre ("caja")."""
    codigo = re.search(r"\bcuenta\s+(?:numero\s+|nro\s+|codigo\s+)?(\d[\d .]*\d|\d)\b", texto) \
        or re.search(r"\b(\d{3}(?:[ .]?\d{2})?)\b", texto)
    if codigo:
        digitos = re.sub(r"\D", "", codigo.group(1))
        candidatos = {
            1: [f"{digitos}00.00"], 2: [f"{digitos}0.00"], 3: [f"{digitos}.00"],
            5: [f"{digitos[:3]}.{digitos[3:]}"],
        }.get(len(digitos), [])
        if candidatos:
            fila = db.execute(text("""
                SELECT id, codigo, nombre FROM plan_cuenta
                WHERE (cooperativa_id=:tenant OR cooperativa_id IS NULL) AND codigo=ANY(:codigos)
                ORDER BY cooperativa_id NULLS LAST LIMIT 1
            """), {"tenant": tenant, "codigos": candidatos}).mappings().first()
            if fila:
                return fila
    cuentas = db.execute(text("""
        SELECT id, codigo, nombre FROM plan_cuenta
        WHERE (cooperativa_id=:tenant OR cooperativa_id IS NULL) AND estado='ACTIVA'
    """), {"tenant": tenant}).mappings().all()
    mejor = None
    for cuenta in cuentas:
        nombre = normalizar(cuenta["nombre"])
        if len(nombre) >= 4 and re.search(rf"\b{re.escape(nombre)}\b", texto):
            if mejor is None or (len(nombre), -len(cuenta["codigo"])) > (len(normalizar(mejor["nombre"])),
                                                                          -len(mejor["codigo"])):
                mejor = cuenta
    return mejor


def _dmy(valor: date) -> str:
    return valor.strftime("%d/%m/%Y")


def _rol(usuario: Usuario) -> str | None:
    return usuario.rol.nombre if usuario.rol else None


def reportes_permitidos(usuario: Usuario) -> list[TipoReporte]:
    if usuario.cooperativa_id is None:
        return []
    return [t for t in CATALOGO.values() if _rol(usuario) in t.roles]


def _no_reconocido(texto: str, mensaje: str, usuario: Usuario) -> dict:
    return {"reconocido": False, "transcripcion": texto, "mensaje": mensaje,
            "sugerencias": [t.ejemplo for t in reportes_permitidos(usuario)]}


def interpretar_comando(texto: str, usuario: Usuario, db: Session, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    permitidos = reportes_permitidos(usuario)
    if not permitidos:
        return _no_reconocido(texto, "Tu usuario no tiene acceso a reportes de una cooperativa.", usuario)

    normal = normalizar(texto)
    tipo = detectar_tipo(normal)
    if tipo is None:
        return _no_reconocido(texto, "No identifiqué qué reporte necesitas. Prueba con uno de estos comandos:",
                              usuario)
    reporte = CATALOGO[tipo]
    if reporte not in permitidos:
        roles = " o ".join(sorted(NOMBRES_ROL.get(r, r) for r in reporte.roles))
        return _no_reconocido(texto, f"El {reporte.nombre.lower()} está disponible solo para {roles}.", usuario)

    try:
        periodo = detectar_periodo(normal, hoy)
    except ComandoInvalido as exc:
        return _no_reconocido(texto, str(exc), usuario)

    moneda = detectar_moneda(normal)
    parametros: dict = {}
    if reporte.periodo == "corte":
        parametros["fecha_corte"] = periodo.hasta.isoformat()
        periodo_texto = f"al {_dmy(periodo.hasta)}"
        sufijo = periodo.hasta.isoformat()
    else:
        desde = periodo.desde or periodo.hasta.replace(day=1)
        parametros.update(desde=desde.isoformat(), hasta=periodo.hasta.isoformat())
        periodo_texto = f"del {_dmy(desde)} al {_dmy(periodo.hasta)}"
        sufijo = f"{desde.isoformat()}-{periodo.hasta.isoformat()}"

    if reporte.moneda == "id":
        parametros["moneda_id"] = 2 if moneda == "USD" else 1
        moneda_texto = "Dólares (USD)" if moneda == "USD" else "Bolivianos (BOB)"
    elif reporte.moneda == "modo":
        # BOB por defecto: el consolidado exige tipo de cambio USD registrado y se pide explícitamente.
        parametros["moneda"] = moneda or "BOB"
        moneda_texto = {"USD": "Dólares (USD)", "CONSOLIDADO": "Consolidado"}.get(moneda, "Bolivianos (BOB)")
    else:
        moneda_texto = "Bolivianos (BOB)"

    if tipo == "balance-general":
        parametros["nivel"] = 3
    elif tipo == "balance-comprobacion":
        parametros["nivel"] = 4
    elif tipo == "libro-mayor":
        cuenta = _buscar_cuenta(db, usuario.cooperativa_id, normal)
        if cuenta is None:
            return _no_reconocido(
                texto, "Para el libro mayor indica la cuenta, por ejemplo «libro mayor de la cuenta 111» "
                       "o «libro mayor de caja».", usuario)
        parametros["cuenta_id"] = cuenta["id"]
        sufijo = f"{cuenta['codigo']}-{sufijo}"

    nombre = reporte.nombre
    if tipo == "libro-mayor":
        nombre = f"Libro mayor — {cuenta['codigo']} {cuenta['nombre']}"
    formato = detectar_formato(normal)
    return {
        "reconocido": True,
        "transcripcion": texto,
        "tipo": tipo,
        "nombre": nombre,
        "descripcion": f"{nombre} {periodo_texto} · {moneda_texto}",
        "endpoint": reporte.endpoint,
        "parametros": parametros,
        "formato": formato,
        "accion": "descargar" if re.search(_DESCARGAR, normal) else "mostrar",
        "archivo": f"{tipo}-{sufijo}",
        "formatos": list(FORMATOS),
    }
