"""Lógica de suscripciones institucionales (SaaS).

Fuente de verdad de los planes que el frontend muestra en la tarjeta y el
badge de suscripción. Incluye el catálogo por defecto y utilidades para
asegurar que cada cooperativa (tenant) tenga una suscripción asignada.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Cooperativa, PlanSuscripcion, Suscripcion

logger = logging.getLogger(__name__)

DEFAULT_PLAN_CODIGO = "RED_ENTERPRISE"

PLANES_DEFAULT: list[dict] = [
    {
        "codigo": "BASICO",
        "nombre": "Básico",
        "descripcion": "Para cooperativas de ahorro y crédito en etapa inicial",
        "features": [
            {"text": "Hasta 500 socios", "icon": "group"},
            {"text": "Módulos de ahorros y créditos", "icon": "savings"},
            {"text": "Reportes regulatorios ASFI", "icon": "assessment"},
        ],
        "incluye": [
            "Gestión de socios y KYC",
            "Ahorros, aportes y créditos",
            "Comprobantes y contabilidad básica",
            "Soporte por correo",
        ],
        "precio_mensual": Decimal("990.00"),
        "limite_usuarios": 10,
    },
    {
        "codigo": "CRECIMIENTO_PRO",
        "nombre": "Crecimiento Pro",
        "descripcion": "Para cooperativas en expansión con operación multicanal",
        "features": [
            {"text": "Socios ilimitados", "icon": "group"},
            {"text": "Banca móvil y comprobantes digitales", "icon": "smartphone"},
            {"text": "Scoring crediticio e informes ASFI", "icon": "analytics"},
        ],
        "incluye": [
            "Todo lo de Básico, más:",
            "Integración con banca móvil",
            "Motor de scoring crediticio",
            "Respaldo automático y bitácora de auditoría",
        ],
        "precio_mensual": Decimal("2490.00"),
        "limite_usuarios": 100,
    },
    {
        "codigo": DEFAULT_PLAN_CODIGO,
        "nombre": "Red Enterprise",
        "descripcion": (
            "Para federaciones, redes intercooperativas y grandes uniones de "
            "crédito fiduciarias"
        ),
        "features": [
            {"text": "Multi-tenant federado ilimitado", "icon": "corporate_fare"},
            {"text": "Almacenamiento HSM sin límite", "icon": "database"},
            {"text": "Cluster dedicado de alta disponibilidad", "icon": "dns"},
        ],
        "incluye": [
            "Aislamiento de bases de datos por tenant",
            "Matriz regulatoria Maker-Checker",
            "Integración API Core interbancaria",
            "SLA garantizado 99.99% con HSM",
        ],
        "precio_mensual": Decimal("4900.00"),
        "limite_usuarios": None,
    },
]


def get_default_plan(db: Session) -> PlanSuscripcion | None:
    return db.execute(
        select(PlanSuscripcion).where(PlanSuscripcion.codigo == DEFAULT_PLAN_CODIGO)
    ).scalar_one_or_none()


def get_suscripcion(db: Session, cooperativa_id: int) -> Suscripcion | None:
    return db.execute(
        select(Suscripcion).where(Suscripcion.cooperativa_id == cooperativa_id)
    ).scalar_one_or_none()


def ensure_suscripcion(
    db: Session, cooperativa_id: int, *, plan: PlanSuscripcion | None = None
) -> Suscripcion | None:
    """Asegura que la cooperativa tenga una suscripción; no persiste por sí sola.

    Devuelve la suscripción existente (sin cambios) o crea una nueva ligada al
    plan indicado / al plan por defecto. El llamador decide cuándo hacer commit.
    """
    existente = get_suscripcion(db, cooperativa_id)
    if existente is not None:
        return existente

    plan = plan or get_default_plan(db)
    if plan is None:
        logger.warning(
            "No hay plan por defecto para asignar a la cooperativa %s", cooperativa_id
        )
        return None

    suscripcion = Suscripcion(
        cooperativa_id=cooperativa_id,
        plan_id=plan.id,
        estado="ACTIVA",
        fecha_inicio=date.today(),
        fecha_fin=None,
    )
    db.add(suscripcion)
    return suscripcion


def upsert_planes(db: Session) -> None:
    """Crea/actualiza el catálogo de planes por código (idempotente)."""
    existentes = {
        plan.codigo: plan
        for plan in db.execute(select(PlanSuscripcion)).scalars().all()
    }
    for datos in PLANES_DEFAULT:
        plan = existentes.get(datos["codigo"])
        if plan is None:
            plan = PlanSuscripcion(**datos, moneda="BOB", estado="ACTIVO")
            db.add(plan)
        else:
            plan.nombre = datos["nombre"]
            plan.descripcion = datos["descripcion"]
            plan.features = datos["features"]
            plan.incluye = datos["incluye"]
            plan.precio_mensual = datos["precio_mensual"]
            plan.limite_usuarios = datos["limite_usuarios"]
            plan.estado = "ACTIVO"
    db.flush()


def seed_suscripciones(db: Session) -> None:
    """Catálogo de planes + suscripción por defecto para cada cooperativa."""
    upsert_planes(db)
    plan_default = get_default_plan(db)
    if plan_default is None:
        return

    cooperativas_ids = db.execute(
        select(Cooperativa.id)
        .outerjoin(Suscripcion, Suscripcion.cooperativa_id == Cooperativa.id)
        .where(Suscripcion.id.is_(None))
    ).scalars().all()

    for cooperativa_id in cooperativas_ids:
        db.add(
            Suscripcion(
                cooperativa_id=cooperativa_id,
                plan_id=plan_default.id,
                estado="ACTIVA",
                fecha_inicio=date.today(),
                fecha_fin=None,
            )
        )
    db.flush()
