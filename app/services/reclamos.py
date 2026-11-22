"""Pure rules for customer claims (Punto de Reclamo, Ley 393)."""

from datetime import date, timedelta

# RNSF Libro 4°, Título I, Capítulo I, Sección 4, Art. 3.
PLAZO_RESPUESTA_DIAS_HABILES = 5
PLAZO_MAXIMO_PRIMERA_AMPLIACION = 10
DIAS_POR_AMPLIACION_ADICIONAL = 5
AMPLIACIONES_ADICIONALES = 2


def sumar_dias_habiles(inicio: date, dias: int) -> date:
    """Add business days (Monday to Friday); public holidays are not considered."""
    fecha = inicio
    restantes = dias
    while restantes > 0:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            restantes -= 1
    return fecha
