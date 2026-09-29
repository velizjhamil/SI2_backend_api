"""ASFI portfolio classification and reserve schedules, versioned to the cited circular."""

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
CENT = Decimal("0.01")

# RNSF Libro 3 Título II Capítulo IV Sección 2; thresholds are days in arrears.
MORA_LIMITS = {
    "VIVIENDA": (30, 90, 180, 270, 360),
    "MICROCREDITO": (5, 30, 55, 75, 90),
    "MICROCREDITO_AGROPECUARIO": (40, 60, 105, 155, 180),
}
VIVIENDA_TYPES = {"VIVIENDA_HIPOTECARIA", "VIVIENDA_SIN_GARANTIA"}
MICRO_TYPES = {"MICROCREDITO", "MICROCREDITO_AGROPECUARIO"}

# RNSF Libro 3 Título II Capítulo IV Sección 3 Art. 1, Circular ASFI/954/26
# (06/2026). Values are percentages, keyed by category/currency/product column.
PREVISIONES_ESPECIFICAS = {
    "circular": "ASFI/954/26 (06/2026), RNSF Libro 3 Título II Capítulo IV Sección 3 Art. 1",
    "version": "2026-06",
    "tasas": {
        "A": {"MN": {"MICRO_PRODUCTIVO": "0", "MICRO_NO_PRODUCTIVO": "0.25", "VIVIENDA_HIPOTECARIA": "0.25", "VIVIENDA_SIN_GARANTIA": "3", "CONSUMO": "3"},
              "ME": {"MICRO": "2.5", "VIVIENDA_HIPOTECARIA": "2.5", "VIVIENDA_SIN_GARANTIA": "7", "CONSUMO": "7"}},
        "B": {"MN": {"MICRO_PRODUCTIVO": "2.5", "MICRO_NO_PRODUCTIVO": "5", "VIVIENDA_HIPOTECARIA": "5", "VIVIENDA_SIN_GARANTIA": "6.5", "CONSUMO": "6.5"},
              "ME": {"MICRO": "5", "VIVIENDA_HIPOTECARIA": "5", "VIVIENDA_SIN_GARANTIA": "12", "CONSUMO": "12"}},
        "C": {"MN": {"TODOS": "20"}, "ME": {"TODOS": "20"}},
        "D": {"MN": {"TODOS": "50"}, "ME": {"TODOS": "50"}},
        "E": {"MN": {"TODOS": "80"}, "ME": {"TODOS": "80"}},
        "F": {"MN": {"TODOS": "100"}, "ME": {"TODOS": "100"}},
    },
}

# RNSF Libro 3 Título II Capítulo IV Sección 3 Art. 8, only category A.
PREVISIONES_CICLICAS = {
    "circular": "ASFI/954/26 (06/2026), RNSF Libro 3 Título II Capítulo IV Sección 3 Art. 8",
    "version": "2026-06",
    "tasas": {
        "MN": {"VIVIENDA": Decimal("1.05"), "CONSUMO": Decimal("1.45"), "MICRO": Decimal("1.10")},
        "ME": {"VIVIENDA": Decimal("1.80"), "CONSUMO": Decimal("2.60"), "MICRO": Decimal("1.90")},
    },
}


def categoria_por_mora(tipo_credito_asfi: str | None, dias_mora: int) -> str:
    if tipo_credito_asfi in VIVIENDA_TYPES:
        schedule = MORA_LIMITS["VIVIENDA"]
    elif tipo_credito_asfi == "MICROCREDITO_AGROPECUARIO":
        schedule = MORA_LIMITS["MICROCREDITO_AGROPECUARIO"]
    elif tipo_credito_asfi in {"MICROCREDITO", "CONSUMO"}:
        # Consumption uses the Art. 8.1 microcredit schedule: an explicit interpretation.
        schedule = MORA_LIMITS["MICROCREDITO"]
    else:
        raise ValueError("tipo de crédito ASFI no clasificado")
    days = max(0, int(dias_mora))
    for category, limit in zip("ABCDE", schedule):
        if days <= limit:
            return category
    return "F"


def grupo_moneda(codigo_moneda: str) -> str:
    code = codigo_moneda.upper()
    return "MN" if code in {"BOB", "UFV", "MNUFV", "MN"} else "ME"


def porcentaje_prevision_especifica(categoria: str, tipo_credito_asfi: str,
                                    moneda: str, sector_productivo: bool = False) -> Decimal:
    currency_group = grupo_moneda(moneda)
    if categoria in "CDEF":
        return Decimal(PREVISIONES_ESPECIFICAS["tasas"][categoria][currency_group]["TODOS"])
    if tipo_credito_asfi in MICRO_TYPES:
        column = ("MICRO_PRODUCTIVO" if sector_productivo else "MICRO_NO_PRODUCTIVO") if currency_group == "MN" else "MICRO"
    elif tipo_credito_asfi in VIVIENDA_TYPES:
        column = tipo_credito_asfi
    elif tipo_credito_asfi == "CONSUMO":
        column = "CONSUMO"
    else:
        raise ValueError("tipo de crédito ASFI no clasificado")
    return Decimal(PREVISIONES_ESPECIFICAS["tasas"][categoria][currency_group][column])


def base_prevision_hipotecaria(capital: Decimal, valor_comercial: Decimal) -> Decimal:
    """Base after the 15% real-estate deduction and 50% collateral recognition."""
    p = Decimal(capital)
    m = min(p, max(ZERO, Decimal(valor_comercial) * Decimal("0.85")))
    return max(ZERO, p - Decimal("0.50") * m)


def monto_prevision(base: Decimal, porcentaje: Decimal) -> Decimal:
    return (Decimal(base) * Decimal(porcentaje) / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)


def porcentaje_prevision_ciclica(categoria: str, tipo_credito_asfi: str, moneda: str) -> Decimal:
    if categoria != "A":
        return ZERO
    family = "VIVIENDA" if tipo_credito_asfi in VIVIENDA_TYPES else "CONSUMO" if tipo_credito_asfi == "CONSUMO" else "MICRO"
    return PREVISIONES_CICLICAS["tasas"][grupo_moneda(moneda)][family]
