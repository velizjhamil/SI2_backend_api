"""Gestionar notificaciones: destinatarios por segmento y mensajes personalizados.

Solo reciben notificaciones los socios ACTIVO con una cuenta de usuario ACTIVO
(la bandeja vive en el portal web del socio). Los socios del segmento sin
cuenta se informan como omitidos.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

TIPOS = ("RECORDATORIO_PAGO", "PROMOCION", "AVISO")
DESTINOS = ("SOCIO", "TODOS", "CUOTAS_POR_VENCER", "EN_MORA")
DIAS_POR_VENCER = 7
PLACEHOLDERS = ("nombre", "nombre_completo", "monto_cuota", "fecha_cuota", "cooperativa")
_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

# Próxima cuota impaga por socio (la más antigua), con su saldo pendiente.
_PROXIMA_CUOTA = """
    SELECT DISTINCT ON (c.socio_id) c.socio_id, ta.fecha_vencimiento,
           ta.monto_cuota_total - COALESCE(ta.monto_pagado, 0) AS saldo
    FROM tabla_amortizacion ta
    JOIN credito c ON c.id = ta.credito_id
    WHERE c.cooperativa_id = :tenant AND c.estado = 'VIGENTE'
      AND COALESCE(ta.estado_pago, 'PENDIENTE') NOT IN ('PAGADO', 'PAGADA')
    ORDER BY c.socio_id, ta.fecha_vencimiento, ta.numero_cuota
"""

_SEGMENTOS = {
    "TODOS": "TRUE",
    "SOCIO": "s.id = ANY(:socio_ids)",
    "CUOTAS_POR_VENCER": "pc.fecha_vencimiento BETWEEN :hoy AND :limite",
    "EN_MORA": "pc.fecha_vencimiento < :hoy",
}


def _filas(db: Session, tenant: int, destino: str, socio_ids: list[int], dias: int, hoy: date):
    return db.execute(text(f"""
        WITH proxima AS ({_PROXIMA_CUOTA})
        SELECT s.id AS socio_id, s.nombre, s.apellido, s.ci, s.usuario_id,
               (u.id IS NOT NULL AND u.estado = 'ACTIVO') AS tiene_cuenta,
               pc.fecha_vencimiento AS fecha_cuota, pc.saldo AS monto_cuota
        FROM socio s
        LEFT JOIN usuario u ON u.id = s.usuario_id
        LEFT JOIN proxima pc ON pc.socio_id = s.id
        WHERE s.cooperativa_id = :tenant AND s.estado = 'ACTIVO' AND {_SEGMENTOS[destino]}
        ORDER BY s.apellido, s.nombre, s.id
    """), {"tenant": tenant, "socio_ids": socio_ids, "hoy": hoy,
           "limite": hoy + timedelta(days=dias)}).mappings().all()


def resolver_destinatarios(db: Session, tenant: int, destino: str, socio_ids: list[int] | None = None,
                           dias: int = DIAS_POR_VENCER, hoy: date | None = None):
    """(destinatarios con cuenta, cantidad de socios del segmento omitidos por no tener cuenta)."""
    filas = _filas(db, tenant, destino, list(socio_ids or []), dias, hoy or date.today())
    con_cuenta = [f for f in filas if f["tiene_cuenta"]]
    return con_cuenta, len(filas) - len(con_cuenta)


def contar_segmentos(db: Session, tenant: int, dias: int = DIAS_POR_VENCER, hoy: date | None = None) -> dict:
    resultado = {}
    for destino in ("TODOS", "CUOTAS_POR_VENCER", "EN_MORA"):
        con_cuenta, omitidos = resolver_destinatarios(db, tenant, destino, dias=dias, hoy=hoy)
        resultado[destino] = {"destinatarios": len(con_cuenta), "sin_cuenta": omitidos}
    return resultado


def buscar_socios(db: Session, tenant: int, q: str, limite: int = 20):
    patron = f"%{q.strip()}%"
    return db.execute(text("""
        SELECT s.id, s.nombre, s.apellido, s.ci,
               (u.id IS NOT NULL AND u.estado = 'ACTIVO') AS tiene_cuenta
        FROM socio s LEFT JOIN usuario u ON u.id = s.usuario_id
        WHERE s.cooperativa_id = :tenant AND s.estado = 'ACTIVO'
          AND (s.nombre || ' ' || s.apellido ILIKE :q OR s.apellido || ' ' || s.nombre ILIKE :q
               OR s.ci ILIKE :q)
        ORDER BY s.apellido, s.nombre LIMIT :limite
    """), {"tenant": tenant, "q": patron, "limite": limite}).mappings().all()


def _monto(valor) -> str:
    return f"Bs. {Decimal(str(valor)).quantize(Decimal('0.01')):,.2f}"


def personalizar(mensaje: str, fila, cooperativa: str | None) -> str:
    """Reemplaza {nombre}, {monto_cuota}, etc. con los datos del socio; sin cuota queda «—»."""
    valores = {
        "nombre": fila["nombre"] or "",
        "nombre_completo": f"{fila['nombre'] or ''} {fila['apellido'] or ''}".strip(),
        "monto_cuota": _monto(fila["monto_cuota"]) if fila["monto_cuota"] is not None else "—",
        "fecha_cuota": fila["fecha_cuota"].strftime("%d/%m/%Y") if fila["fecha_cuota"] else "—",
        "cooperativa": cooperativa or "la cooperativa",
    }
    return _PLACEHOLDER.sub(lambda m: valores[m.group(1)], mensaje)
