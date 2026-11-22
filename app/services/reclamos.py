"""Pure rules for customer claims (Punto de Reclamo, Ley 393)."""

from datetime import date, timedelta

# RNSF Libro 4°, Título I, Capítulo I, Sección 4, Art. 3.
PLAZO_RESPUESTA_DIAS_HABILES = 5
PLAZO_MAXIMO_PRIMERA_AMPLIACION = 10
DIAS_POR_AMPLIACION_ADICIONAL = 5
AMPLIACIONES_ADICIONALES = 2


# Bolivian national public holidays with a fixed date (month, day).
FERIADOS_FIJOS = {(1, 1), (1, 22), (5, 1), (6, 21), (8, 6), (11, 2), (12, 25)}
# Movable national holidays as day offsets from Easter Sunday:
# Carnival Monday and Tuesday, Good Friday and Corpus Christi.
FERIADOS_MOVILES_DESDE_PASCUA = (-48, -47, -2, 60)


def _domingo_de_pascua(anio: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = anio % 19, anio // 100, anio % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    mes = (h + l - 7 * m + 90) // 25
    dia = (h + l - 7 * m + 33 * mes + 19) % 32
    return date(anio, mes, dia)


def es_feriado_nacional(fecha: date) -> bool:
    if (fecha.month, fecha.day) in FERIADOS_FIJOS:
        return True
    pascua = _domingo_de_pascua(fecha.year)
    return any(fecha == pascua + timedelta(days=offset) for offset in FERIADOS_MOVILES_DESDE_PASCUA)


def es_dia_habil(fecha: date) -> bool:
    return fecha.weekday() < 5 and not es_feriado_nacional(fecha)


def sumar_dias_habiles(inicio: date, dias: int) -> date:
    """Add business days: Monday to Friday, skipping Bolivian national public holidays."""
    fecha = inicio
    restantes = dias
    while restantes > 0:
        fecha += timedelta(days=1)
        if es_dia_habil(fecha):
            restantes -= 1
    return fecha
