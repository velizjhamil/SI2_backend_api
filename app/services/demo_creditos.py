import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.models import (
    Credito,
    CuentaAhorro,
    Moneda,
    ProductoCredito,
    Socio,
    SolicitudCredito,
    TablaAmortizacion,
    Usuario,
)

logger = logging.getLogger(__name__)


def asegurar_credito_demo_socio(db: Session, socio: Socio) -> Credito | None:
    """Asegura que un socio tenga al menos un crédito activo con cronograma para pruebas en la app móvil.
    
    Si el socio ya tiene créditos vigentes o en mora, retorna el primero.
    Si no tiene ninguno, crea un crédito de demostración con cuotas (1 pagada, 1 vencida, 4 pendientes)
    y asegura una cuenta de ahorros activa con saldo disponible suficiente para pagar.
    """
    # 1. Verificar si ya existe un crédito
    existing = db.execute(
        select(Credito)
        .options(
            joinedload(Credito.producto),
            joinedload(Credito.moneda),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
            selectinload(Credito.cronograma),
        )
        .where(Credito.socio_id == socio.id)
        .order_by(Credito.estado.notin_(("VIGENTE", "EN_MORA")), Credito.id)
    ).unique().scalars().first()

    if existing is not None:
        return existing

    try:
        coop_id = socio.cooperativa_id or 1
        # 2. Obtener producto de crédito y moneda
        product = db.execute(
            select(ProductoCredito)
            .where(ProductoCredito.cooperativa_id == coop_id, ProductoCredito.estado == "ACTIVO")
            .order_by(ProductoCredito.id)
        ).scalars().first()

        if product is None:
            product = db.execute(select(ProductoCredito).where(ProductoCredito.estado == "ACTIVO")).scalars().first()

        if product is None:
            logger.warning("No hay productos de crédito activos para generar crédito demo")
            return None

        # 3. Obtener usuario oficial
        usuario = db.get(Usuario, socio.usuario_id) if socio.usuario_id else None
        if usuario is None:
            usuario = db.execute(select(Usuario).where(Usuario.cooperativa_id == coop_id)).scalars().first()
        if usuario is None:
            usuario = db.execute(select(Usuario)).scalars().first()

        actor_id = usuario.id if usuario else 1

        # 4. Asegurar cuenta de ahorros activa con saldo
        cuenta = db.execute(
            select(CuentaAhorro)
            .where(CuentaAhorro.socio_id == socio.id, CuentaAhorro.estado == "ACTIVA")
        ).scalars().first()

        if cuenta is None:
            cuenta = CuentaAhorro(
                numero=f"CA-{socio.id:03d}-DEMO",
                tipo_producto="VISTA",
                moneda_id=product.moneda_id,
                saldo_disponible=Decimal("5000.00"),
                saldo_bloqueado=Decimal("0.00"),
                estado="ACTIVA",
                socio_id=socio.id,
                fecha_registro=date.today() - timedelta(days=90),
            )
            db.add(cuenta)
            db.flush()
        elif Decimal(str(cuenta.saldo_disponible or 0)) < Decimal("1000.00"):
            cuenta.saldo_disponible = Decimal("5000.00")
            db.flush()

        # 5. Crear Solicitud de Crédito
        monto_total = Decimal("3000.00")
        solicitud = SolicitudCredito(
            numero_solicitud=f"SOL-{socio.id:04d}-{date.today().strftime('%m%d')}",
            monto=monto_total,
            plazo_meses=6,
            tasa_interes=product.tasa_interes_anual,
            calificacion_asfi="A",
            tiene_deudas=False,
            estado="APROBADA",
            socio_id=socio.id,
            usuario_id=actor_id,
            producto_credito_id=product.id,
            moneda_id=product.moneda_id,
            cooperativa_id=coop_id,
            destino="CAPITAL_TRABAJO",
            destino_detalle="Compra de mercadería y capital de operación",
            canal_origen="MOVIL",
        )
        db.add(solicitud)
        db.flush()

        # 6. Crear Crédito
        credit_num = f"CRE-{socio.id:04d}-{date.today().strftime('%m%d')}"
        credito = Credito(
            solicitud_credito_id=solicitud.id,
            numero_credito=credit_num,
            monto_aprobado=monto_total,
            saldo_pendiente=Decimal("2500.00"),
            estado="VIGENTE",
            cooperativa_id=coop_id,
            socio_id=socio.id,
            producto_credito_id=product.id,
            moneda_id=product.moneda_id,
            tasa_interes=product.tasa_interes_anual,
            plazo_meses=6,
            tipo_amortizacion="FRANCES",
            fecha_desembolso=date.today() - timedelta(days=60),
            modalidad_desembolso="CUENTA",
            cuenta_desembolso_id=cuenta.id,
            usuario_id=actor_id,
        )
        db.add(credito)
        db.flush()

        # 7. Crear Cronograma de Pagos (6 cuotas)
        # Cuota 1: Pagada hace 30 días
        # Cuota 2: Vencida hace 5 días (genera mora en cálculo)
        # Cuotas 3 a 6: Pendientes futuras
        fechas = [
            date.today() - timedelta(days=35),  # 1: Pagada
            date.today() - timedelta(days=5),   # 2: Vencida
            date.today() + timedelta(days=25),  # 3: Pendiente próxima
            date.today() + timedelta(days=55),  # 4: Pendiente
            date.today() + timedelta(days=85),  # 5: Pendiente
            date.today() + timedelta(days=115), # 6: Pendiente
        ]

        monto_cap = Decimal("500.00")
        for i, f_venc in enumerate(fechas, start=1):
            f_pago = datetime.combine(f_venc, datetime.min.time(), tzinfo=timezone.utc) if i == 1 else None
            est_pago = "PAGADA" if i == 1 else "PENDIENTE"
            m_pagado = Decimal("535.00") if i == 1 else Decimal("0.00")
            interes = Decimal("35.00") - Decimal(f"{i * 2}.00")
            cuota_tot = monto_cap + interes

            cuota = TablaAmortizacion(
                numero_cuota=i,
                fecha_vencimiento=f_venc,
                monto_capital=monto_cap,
                monto_interes=interes,
                monto_cuota_total=cuota_tot,
                estado_pago=est_pago,
                credito_id=credito.id,
                saldo_inicial=monto_total - (monto_cap * (i - 1)),
                saldo_final=monto_total - (monto_cap * i),
                monto_pagado=m_pagado,
                fecha_pago=f_pago,
            )
            db.add(cuota)

        db.commit()

        # Recargar con relaciones
        return db.execute(
            select(Credito)
            .options(
                joinedload(Credito.producto),
                joinedload(Credito.moneda),
                joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
                joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
                selectinload(Credito.cronograma),
            )
            .where(Credito.id == credito.id)
        ).unique().scalar_one()

    except Exception as e:
        db.rollback()
        logger.error("Error asegurando crédito demo para socio %s: %s", socio.id, e, exc_info=True)
        return None
