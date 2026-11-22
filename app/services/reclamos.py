"""Pure rules for customer claims (Punto de Reclamo, Ley 393)."""

from datetime import date, timedelta

PLAZO_RESPUESTA_DIAS_HABILES = 5


def sumar_dias_habiles(inicio: date, dias: int) -> date:
    """Add business days (Monday to Friday); public holidays are not considered."""
    fecha = inicio
    restantes = dias
    while restantes > 0:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            restantes -= 1
    return fecha
