"""Cooperative credit-product catalog (CU-W20)."""

import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from typing import Any
from pydantic import ValidationError
from sqlalchemy import Date, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_current_user, get_db, require_admin
from app.api.v1.endpoints.ahorros import MONTO_MINIMO_APERTURA, _siguiente_secuencia
from app.core.bitacora import registrar_accion
from app.services.amortizacion import calcular_cuota_inicial, generar_plan_pagos
from app.services.cobro_cuotas import calcular_mora, resumir_morosidad
from app.services.scoring import score_application
from app.models.models import (
    Credito,
    CuentaAhorro,
    EvaluacionCampo,
    EvaluacionCrediticia,
    Moneda,
    ProductoCredito,
    PagoCuota,
    Morosidad,
    Socio,
    SolicitudCredito,
    TablaAmortizacion,
    Usuario,
    PrediccionDeMorosidad,
    OfertaRecredito,
    AlertaCredito,
    GestionCobranza,
    Garantia,
    Bitacora,
    VotoComite,
    Cooperativa,
)
from app.schemas.schemas import (
    MonedaOut,
    ProductoResumen,
    ProductoResumen,
    ProductoCreditoCreate,
    ProductoCreditoEstadoUpdate,
    ProductoCreditoOut,
    ProductoCreditoUpdate,
    SolicitudSimulacionIn,
    SolicitudSimulacionOut,
    SolicitudAnulacionIn,
    SolicitudCreditoCreate,
    SolicitudCreditoUpdate,
    SolicitudOut,
    SocioResumen,
    EvaluacionCrediticiaOut,
    ResolucionSolicitudIn,
    PlanPagosOut,
    CreditoDetalleOut,
    CreditoOut,
    DesembolsoCreditoIn,
    DeudaCuotaOut,
    PagoCuotaIn,
    PagoOut,
    MoraCreditoOut,
    OfertaOut, GeneracionRecreditosOut, OfertaAceptadaOut, DescartarRecreditoIn,
    VotoComiteIn, VotoOut, ComiteItemOut, ActaOut,
    AlertaOut, GestionOut, GestionCreateIn, DescartarAlertaIn,
    GarantiaCreateIn, GarantiaUpdateIn, GarantiaVerificacionIn, GarantiaOut, CoberturaOut,
)
from app.services.alertas_mora import construir_candidatos_alerta, reconciliar_alertas, candidato_riesgo_alto

router = APIRouter()
ROLES_LECTURA_PRODUCTOS = {"ADMINISTRADOR", "CONTADOR", "CAJERO", "OFICIAL_CREDITO"}
CENTAVO = Decimal("0.01")
DESTINOS_SOLICITUD = {
    "CAPITAL_TRABAJO",
    "ACTIVO_FIJO",
    "CONSUMO",
    "VIVIENDA",
    "EDUCACION",
    "SALUD",
    "REFINANCIAMIENTO",
    "OTRO",
}
FUENTES_INGRESOS = {"DEPENDIENTE", "INDEPENDIENTE", "MIXTO"}
CALIFICACIONES_ASFI = {"A", "B", "C", "D", "E", "F"}
ROLES_ESCRITURA_SOLICITUD = {"ADMINISTRADOR", "OFICIAL_CREDITO"}
ROLES_EVALUACION_CREDITICIA = {"ADMINISTRADOR", "OFICIAL_CREDITO"}
ESTADOS_SOLICITUD = {
    "PENDIENTE",
    "OBSERVADA",
    "EN_EVALUACION",
    "EN_COMITE",
    "APROBADO",
    "RECHAZADO",
    "DESEMBOLSADO",
    "ANULADA",
}
ESTADOS_EDITABLES_SOLICITUD = {"PENDIENTE", "OBSERVADA"}
VOTOS_COMITE_REQUERIDOS = 3
ROLES_VOTANTES_COMITE = {"ADMINISTRADOR", "OFICIAL_CREDITO", "CONTADOR"}


def _estado_despues_evaluacion(dictamen: str, monto: Decimal, limite_directo: Decimal) -> str:
    if dictamen == "RECHAZADO":
        return "RECHAZADO"
    if dictamen == "REVISION_MANUAL" or monto > limite_directo:
        return "EN_COMITE"
    return "APROBADO"


def _validar_lector(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None or usuario.rol.nombre not in ROLES_LECTURA_PRODUCTOS:
        raise HTTPException(status_code=403, detail="Operación reservada al personal de la cooperativa")
    return usuario.cooperativa_id


def _validar_escritor_solicitud(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None or usuario.rol.nombre not in ROLES_ESCRITURA_SOLICITUD:
        raise HTTPException(status_code=403, detail="Operación reservada a oficiales de crédito y administradores")
    return usuario.cooperativa_id


def _validar_admin_cooperativa(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None or usuario.rol.nombre != "ADMINISTRADOR":
        raise HTTPException(status_code=403, detail="Operación reservada a administradores de la cooperativa")
    return usuario.cooperativa_id


def _validar_evaluador_crediticio(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None or usuario.rol.nombre not in ROLES_EVALUACION_CREDITICIA:
        raise HTTPException(status_code=403, detail="Operación reservada a oficiales de crédito y administradores")
    return usuario.cooperativa_id


def _obtener_producto(db: Session, cooperativa_id: int, producto_id: int) -> ProductoCredito:
    producto = db.execute(
        select(ProductoCredito)
        .options(joinedload(ProductoCredito.moneda))
        .where(
            ProductoCredito.id == producto_id,
            ProductoCredito.cooperativa_id == cooperativa_id,
        )
    ).scalar_one_or_none()
    if producto is None:
        raise HTTPException(status_code=404, detail="Producto crediticio no encontrado")
    return producto


def _validar_datos_producto(db: Session, datos: dict) -> None:
    nombre = datos.get("nombre")
    if not isinstance(nombre, str) or not nombre.strip() or len(nombre.strip()) > 100:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio y debe tener hasta 100 caracteres")
    if db.get(Moneda, datos.get("moneda_id")) is None:
        raise HTTPException(status_code=400, detail="La moneda indicada no existe")
    if datos.get("tipo_amortizacion") not in {"FRANCES", "ALEMAN"}:
        raise HTTPException(status_code=400, detail="El tipo de amortización debe ser FRANCES o ALEMAN")

    monto_min = datos.get("monto_min")
    monto_max = datos.get("monto_max")
    if not isinstance(monto_min, Decimal) or not isinstance(monto_max, Decimal):
        raise HTTPException(status_code=400, detail="Los montos deben ser valores decimales válidos")
    if monto_min <= 0 or monto_min > monto_max or monto_max > Decimal("999999999999.99"):
        raise HTTPException(status_code=400, detail="El rango de montos no es válido")

    plazo_min = datos.get("plazo_min_meses")
    plazo_max = datos.get("plazo_max_meses")
    if not isinstance(plazo_min, int) or not isinstance(plazo_max, int) or plazo_min <= 0 or plazo_min > plazo_max:
        raise HTTPException(status_code=400, detail="El rango de plazos debe ser positivo y coherente")

    for field, label in (("tasa_interes_anual", "La tasa anual"), ("tasa_mora_anual", "La tasa de mora")):
        value = datos.get(field)
        if not isinstance(value, Decimal) or value < 0 or value > 100:
            raise HTTPException(status_code=400, detail=f"{label} debe estar entre 0 y 100")
    ratio = datos.get("relacion_cuota_ingreso_max")
    if not isinstance(ratio, Decimal) or ratio <= 0 or ratio > 100:
        raise HTTPException(status_code=400, detail="La relación cuota-ingreso debe ser mayor a 0 y hasta 100")
    grace_days = datos.get("dias_gracia_mora")
    if not isinstance(grace_days, int) or grace_days < 0:
        raise HTTPException(status_code=400, detail="Los días de gracia no pueden ser negativos")

    for value, label in ((monto_min, "El monto mínimo"), (monto_max, "El monto máximo"),
                         (datos["tasa_interes_anual"], "La tasa anual"),
                         (datos["tasa_mora_anual"], "La tasa de mora"), (ratio, "La relación cuota-ingreso")):
        if value != value.quantize(Decimal("0.01")):
            raise HTTPException(status_code=400, detail=f"{label} admite hasta dos decimales")


def _a_centavos(value: Decimal) -> Decimal:
    return value.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _cuota_estimada(monto: Decimal, plazo_meses: int, tasa_anual: Decimal, amortizacion: str) -> Decimal:
    return calcular_cuota_inicial(monto, plazo_meses, tasa_anual, amortizacion)


def _intereses_estimados(
    monto: Decimal,
    plazo_meses: int,
    tasa_anual: Decimal,
    amortizacion: str,
) -> Decimal:
    plan = generar_plan_pagos(
        monto=monto,
        tasa_anual=tasa_anual,
        plazo_meses=plazo_meses,
        tipo_amortizacion=amortizacion,
        fecha_desembolso=date.today(),
    )
    return plan["total_interes"]


def _cobertura_solicitud_out(solicitud: SolicitudCredito) -> dict | None:
    producto = solicitud.producto
    if producto is None:
        return None
    verificadas = sum((Decimal(g.valor_realizable) for g in solicitud.garantias if g.estado == "VERIFICADA"), Decimal("0.00"))
    pendientes = sum((Decimal(g.valor_realizable) for g in solicitud.garantias if g.estado == "REGISTRADA"), Decimal("0.00"))
    monto = Decimal(solicitud.monto)
    porcentaje = (verificadas / monto * Decimal("100")).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    requiere = bool(producto.requiere_garantia)
    minimo = Decimal(producto.cobertura_minima_garantia)
    return {"requiere_garantia": requiere, "cobertura_minima": minimo, "monto_solicitado": monto,
        "valor_realizable_verificado": verificadas, "valor_realizable_pendiente": pendientes,
        "porcentaje_cobertura": porcentaje, "cumple": not requiere or porcentaje >= minimo}


def _solicitud_out(solicitud: SolicitudCredito) -> dict:
    socio = solicitud.socio
    producto = solicitud.producto
    evaluacion = solicitud.evaluacion
    cuota = None
    relacion = None
    supera = None
    if producto is not None:
        cuota = _cuota_estimada(
            solicitud.monto,
            solicitud.plazo_meses,
            producto.tasa_interes_anual,
            producto.tipo_amortizacion,
        )
        if evaluacion is not None and evaluacion.ingreso_mensual > 0:
            relacion = _a_centavos(cuota / evaluacion.ingreso_mensual * Decimal("100"))
            supera = relacion > producto.relacion_cuota_ingreso_max
    latest_evaluation = next(iter(solicitud.evaluaciones_crediticias), None)
    return {
        "id": solicitud.id,
        "numero_solicitud": solicitud.numero_solicitud,
        "fecha_solicitud": solicitud.fecha_solicitud,
        "fecha_actualizacion": solicitud.fecha_actualizacion,
        "estado": solicitud.estado,
        "ronda_comite": solicitud.ronda_comite,
        "resultado_comite": solicitud.resultado_comite,
        "socio": {
            "id": socio.id,
            "nombre_completo": f"{socio.nombre} {socio.apellido}",
            "ci": socio.ci,
            "estado": socio.estado,
        },
        "producto": None if producto is None else {
            "id": producto.id,
            "codigo": producto.codigo,
            "nombre": producto.nombre,
            "tipo_amortizacion": producto.tipo_amortizacion,
            "relacion_cuota_ingreso_max": producto.relacion_cuota_ingreso_max,
        },
        "moneda": solicitud.moneda,
        "monto": solicitud.monto,
        "plazo_meses": solicitud.plazo_meses,
        "tasa_interes": solicitud.tasa_interes,
        "destino": solicitud.destino,
        "destino_detalle": solicitud.destino_detalle,
        "observaciones": solicitud.observaciones,
        "motivo_anulacion": solicitud.motivo_anulacion,
        "tiene_deudas": solicitud.tiene_deudas,
        "cuota_estimada": cuota,
        "relacion_cuota_ingreso": relacion,
        "supera_relacion_maxima": supera,
        "evaluacion": None if evaluacion is None else {
            "id": evaluacion.id,
            "ingreso_mensual": evaluacion.ingreso_mensual,
            "egreso_mensual": evaluacion.egreso_mensual,
            "cuota_deudas_mensual": evaluacion.cuota_deudas_mensual,
            "capacidad_pago": evaluacion.capacidad_pago,
            "actividad_economica": evaluacion.actividad_economica,
            "fuente_ingresos": evaluacion.fuente_ingresos,
            "antiguedad_laboral_meses": evaluacion.antiguedad_laboral_meses,
            "calificacion_asfi": evaluacion.calificacion_asfi,
            "coordenadas": evaluacion.coordenadas,
            "observaciones": evaluacion.observaciones,
            "fecha": evaluacion.fecha,
        },
        "oficial": {"id": solicitud.oficial.id, "nombre": solicitud.oficial.nombre},
        "cobertura": _cobertura_solicitud_out(solicitud),
        "ultima_evaluacion": None if latest_evaluation is None else {
            "id": latest_evaluation.id,
            "score": latest_evaluation.score,
            "dictamen": latest_evaluation.dictamen,
            "fecha": latest_evaluation.fecha,
        },
    }


@router.get("/socios/buscar", response_model=list[SocioResumen])
def buscar_socios_credito(
    q: str = Query(...),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    termino = q.strip()
    if len(termino) < 3:
        raise HTTPException(status_code=400, detail="La búsqueda debe tener al menos 3 caracteres")
    socios = db.execute(
        select(Socio)
        .where(
            Socio.cooperativa_id == cooperativa_id,
            Socio.estado == "ACTIVO",
            or_(Socio.ci.ilike(f"{termino}%"), Socio.nombre.ilike(f"%{termino}%"), Socio.apellido.ilike(f"%{termino}%")),
        )
        .order_by(Socio.apellido, Socio.nombre, Socio.id)
        .limit(20)
    ).scalars().all()
    return [
        SocioResumen(
            id=socio.id,
            nombre_completo=f"{socio.nombre} {socio.apellido}",
            ci=socio.ci,
            estado=socio.estado,
        )
        for socio in socios
    ]


@router.post("/solicitudes/simulacion", response_model=SolicitudSimulacionOut)
def simular_solicitud_credito(
    body: SolicitudSimulacionIn,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    producto = _obtener_producto(db, cooperativa_id, body.producto_id)
    if producto.estado != "ACTIVO":
        raise HTTPException(status_code=400, detail="El producto crediticio no está activo")
    if body.plazo_meses <= 0:
        raise HTTPException(status_code=400, detail="El plazo debe ser mayor a cero")
    if body.monto <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero")
    if any(value is not None and value < 0 for value in (
        body.ingreso_mensual, body.egreso_mensual, body.cuota_deudas_mensual
    )):
        raise HTTPException(status_code=400, detail="Los ingresos, egresos y deudas no pueden ser negativos")

    cuota = _cuota_estimada(body.monto, body.plazo_meses, producto.tasa_interes_anual, producto.tipo_amortizacion)
    total_intereses = _intereses_estimados(
        body.monto, body.plazo_meses, producto.tasa_interes_anual, producto.tipo_amortizacion
    )
    dentro_de_monto = producto.monto_min <= body.monto <= producto.monto_max
    dentro_de_plazo = producto.plazo_min_meses <= body.plazo_meses <= producto.plazo_max_meses
    errores = []
    if not dentro_de_monto:
        errores.append("El monto está fuera del rango permitido para el producto")
    if not dentro_de_plazo:
        errores.append("El plazo está fuera del rango permitido para el producto")

    capacidad_pago = None
    relacion = None
    supera = None
    if body.ingreso_mensual is not None:
        egreso = body.egreso_mensual or Decimal("0.00")
        deudas = body.cuota_deudas_mensual or Decimal("0.00")
        capacidad_pago = _a_centavos(body.ingreso_mensual - egreso - deudas)
        if body.ingreso_mensual > 0:
            relacion = _a_centavos(cuota / body.ingreso_mensual * Decimal("100"))
            supera = relacion > producto.relacion_cuota_ingreso_max

    return {
        "producto": ProductoResumen(
            id=producto.id,
            codigo=producto.codigo,
            nombre=producto.nombre,
            tipo_amortizacion=producto.tipo_amortizacion,
            relacion_cuota_ingreso_max=producto.relacion_cuota_ingreso_max,
        ),
        "moneda": producto.moneda,
        "monto": body.monto,
        "plazo_meses": body.plazo_meses,
        "tasa_interes": producto.tasa_interes_anual,
        "cuota_estimada": cuota,
        "total_intereses_estimado": total_intereses,
        "capacidad_pago": capacidad_pago,
        "relacion_cuota_ingreso": relacion,
        "relacion_maxima": producto.relacion_cuota_ingreso_max,
        "supera_relacion_maxima": supera,
        "dentro_de_rangos": dentro_de_monto and dentro_de_plazo,
        "errores": errores,
    }


def _validar_datos_solicitud(producto: ProductoCredito, datos: dict) -> None:
    if producto.estado != "ACTIVO":
        raise HTTPException(status_code=400, detail="El producto crediticio no está activo")
    monto = datos.get("monto")
    plazo = datos.get("plazo_meses")
    if monto is None or monto < producto.monto_min or monto > producto.monto_max:
        raise HTTPException(status_code=400, detail="El monto está fuera del rango permitido para el producto")
    if plazo is None or plazo < producto.plazo_min_meses or plazo > producto.plazo_max_meses:
        raise HTTPException(status_code=400, detail="El plazo está fuera del rango permitido para el producto")
    destino = datos.get("destino")
    if destino not in DESTINOS_SOLICITUD:
        raise HTTPException(status_code=400, detail="El destino de la solicitud no es válido")
    detalle = datos.get("destino_detalle")
    if destino == "OTRO" and (not isinstance(detalle, str) or not detalle.strip()):
        raise HTTPException(status_code=400, detail="Debe detallar el destino cuando selecciona OTRO")
    evaluacion = datos.get("evaluacion") or {}
    if evaluacion.get("fuente_ingresos") not in FUENTES_INGRESOS:
        raise HTTPException(status_code=400, detail="La fuente de ingresos no es válida")
    if evaluacion.get("calificacion_asfi") not in CALIFICACIONES_ASFI:
        raise HTTPException(status_code=400, detail="La calificación ASFI no es válida")
    for field, label in (
        ("ingreso_mensual", "El ingreso mensual"),
        ("egreso_mensual", "El egreso mensual"),
        ("cuota_deudas_mensual", "La cuota de deudas mensual"),
    ):
        value = evaluacion.get(field)
        if value is None or value < 0:
            raise HTTPException(status_code=400, detail=f"{label} no puede ser negativo")
    if evaluacion.get("antiguedad_laboral_meses") is None or evaluacion["antiguedad_laboral_meses"] < 0:
        raise HTTPException(status_code=400, detail="La antigüedad laboral no puede ser negativa")
    actividad = evaluacion.get("actividad_economica")
    if not isinstance(actividad, str) or not actividad.strip() or len(actividad.strip()) > 150:
        raise HTTPException(status_code=400, detail="La actividad económica es obligatoria y debe tener hasta 150 caracteres")


@router.post("/solicitudes", response_model=SolicitudOut, status_code=status.HTTP_201_CREATED)
def crear_solicitud_credito(
    body: SolicitudCreditoCreate,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_escritor_solicitud(usuario)
    socio = db.execute(
        select(Socio).where(
            Socio.id == body.socio_id,
            Socio.cooperativa_id == cooperativa_id,
        )
    ).scalar_one_or_none()
    if socio is None:
        raise HTTPException(status_code=404, detail="Socio no encontrado")
    if socio.estado != "ACTIVO":
        raise HTTPException(status_code=409, detail="El socio no está activo")
    producto = db.execute(
        select(ProductoCredito)
        .options(joinedload(ProductoCredito.moneda))
        .where(
            ProductoCredito.id == body.producto_id,
            ProductoCredito.cooperativa_id == cooperativa_id,
        )
    ).scalar_one_or_none()
    if producto is None:
        raise HTTPException(status_code=404, detail="Producto crediticio no encontrado")

    datos = body.model_dump()
    datos["evaluacion"] = body.evaluacion.model_dump()
    _validar_datos_solicitud(producto, datos)
    if db.execute(
        select(SolicitudCredito.id).where(
            SolicitudCredito.socio_id == socio.id,
            SolicitudCredito.estado.in_(("PENDIENTE", "OBSERVADA", "EN_EVALUACION", "EN_COMITE")),
        )
    ).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="El socio ya tiene una solicitud en curso")

    evaluacion_data = body.evaluacion.model_dump()
    capacidad_pago = (
        evaluacion_data["ingreso_mensual"]
        - evaluacion_data["egreso_mensual"]
        - evaluacion_data["cuota_deudas_mensual"]
    )
    evaluacion = EvaluacionCampo(
        ingreso_mensual=evaluacion_data["ingreso_mensual"],
        egreso_mensual=evaluacion_data["egreso_mensual"],
        cuota_deudas_mensual=evaluacion_data["cuota_deudas_mensual"],
        capacidad_pago=capacidad_pago,
        actividad_economica=evaluacion_data["actividad_economica"].strip(),
        fuente_ingresos=evaluacion_data["fuente_ingresos"],
        antiguedad_laboral_meses=evaluacion_data["antiguedad_laboral_meses"],
        calificacion_asfi=evaluacion_data["calificacion_asfi"],
        fecha=func.current_date(),
        coordenadas=evaluacion_data.get("coordenadas"),
        observaciones=evaluacion_data.get("observaciones"),
        usuario_id=usuario.id,
        socio_id=socio.id,
    )
    db.add(evaluacion)
    db.flush()
    numero = _siguiente_secuencia(db, cooperativa_id, "SOLICITUD_CREDITO")
    solicitud = SolicitudCredito(
        monto=body.monto,
        plazo_meses=body.plazo_meses,
        tasa_interes=producto.tasa_interes_anual,
        calificacion_asfi=evaluacion.calificacion_asfi,
        tiene_deudas=evaluacion.cuota_deudas_mensual > 0,
        estado="PENDIENTE",
        socio_id=socio.id,
        usuario_id=usuario.id,
        evaluacion_campo_id=evaluacion.id,
        producto_credito_id=producto.id,
        moneda_id=producto.moneda_id,
        numero_solicitud=f"SOL-{numero:06d}",
        destino=body.destino,
        destino_detalle=body.destino_detalle.strip() if body.destino_detalle else None,
        observaciones=body.observaciones,
        cooperativa_id=cooperativa_id,
    )
    db.add(solicitud)
    db.flush()
    registrar_accion(
        db,
        accion="REGISTRAR_SOLICITUD",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Solicitud de crédito registrada: {solicitud.id} ({solicitud.numero_solicitud})",
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_solicitud_credito_un_socio_en_curso" in str(exc.orig):
            raise HTTPException(status_code=409, detail="El socio ya tiene una solicitud en curso") from exc
        raise
    db.refresh(solicitud)
    return _solicitud_out(solicitud)


def _obtener_solicitud(db: Session, cooperativa_id: int, solicitud_id: int) -> SolicitudCredito:
    solicitud = db.execute(
        select(SolicitudCredito)
        .options(
            joinedload(SolicitudCredito.socio),
            joinedload(SolicitudCredito.producto).joinedload(ProductoCredito.moneda),
            joinedload(SolicitudCredito.moneda),
            joinedload(SolicitudCredito.evaluacion),
            joinedload(SolicitudCredito.oficial),
        )
        .where(
            SolicitudCredito.id == solicitud_id,
            SolicitudCredito.cooperativa_id == cooperativa_id,
        )
    ).scalar_one_or_none()
    if solicitud is None:
        raise HTTPException(status_code=404, detail="Solicitud de crédito no encontrada")
    return solicitud


def _evaluacion_crediticia_out(evaluacion: EvaluacionCrediticia) -> dict:
    resolucion = None
    if evaluacion.resolucion is not None:
        resolucion = {
            "decision": evaluacion.resolucion,
            "justificacion": evaluacion.resolucion_justificacion,
            "fecha": evaluacion.resolucion_fecha,
            "usuario": {
                "id": evaluacion.resolucion_usuario.id,
                "nombre": evaluacion.resolucion_usuario.nombre,
            },
        }
    return {
        "id": evaluacion.id,
        "solicitud_id": evaluacion.solicitud_credito_id,
        "version_modelo": evaluacion.version_modelo,
        "score": evaluacion.score,
        "dictamen": evaluacion.dictamen,
        "factores": evaluacion.factores,
        "knockouts": evaluacion.knockouts,
        "explicacion": evaluacion.explicacion,
        "cuota_estimada": evaluacion.cuota_estimada,
        "relacion_cuota_ingreso": evaluacion.relacion_cuota_ingreso,
        "fecha": evaluacion.fecha,
        "usuario": {"id": evaluacion.usuario.id, "nombre": evaluacion.usuario.nombre},
        "resolucion": resolucion,
        "probabilidad_mora": evaluacion.probabilidad_mora,
        "nivel_riesgo": evaluacion.nivel_riesgo,
        "version_modelo_mora": evaluacion.version_modelo_mora,
    }


def _voto_comite_out(voto: VotoComite) -> dict:
    return {
        "id": voto.id,
        "ronda": voto.ronda,
        "usuario": {
            "id": voto.usuario.id,
            "nombre": voto.usuario.nombre,
            "rol": voto.usuario.rol.nombre,
        },
        "voto": voto.voto,
        "comentario": voto.comentario,
        "fecha": voto.fecha,
    }


def _evaluacion_reciente_comite(db: Session, solicitud: SolicitudCredito, cooperativa_id: int):
    return db.execute(
        select(EvaluacionCrediticia)
        .options(
            joinedload(EvaluacionCrediticia.usuario).joinedload(Usuario.rol),
            joinedload(EvaluacionCrediticia.resolucion_usuario).joinedload(Usuario.rol),
        )
        .where(
            EvaluacionCrediticia.solicitud_credito_id == solicitud.id,
            EvaluacionCrediticia.cooperativa_id == cooperativa_id,
        )
        .order_by(EvaluacionCrediticia.fecha.desc(), EvaluacionCrediticia.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _votos_ronda_comite(db: Session, solicitud: SolicitudCredito, ronda: int):
    return db.execute(
        select(VotoComite)
        .options(joinedload(VotoComite.usuario).joinedload(Usuario.rol))
        .where(
            VotoComite.solicitud_credito_id == solicitud.id,
            VotoComite.cooperativa_id == solicitud.cooperativa_id,
            VotoComite.ronda == ronda,
        )
        .order_by(VotoComite.fecha, VotoComite.id)
    ).scalars().all()


def _puede_votar_comite(solicitud: SolicitudCredito, usuario: Usuario, votos: list[VotoComite]):
    if usuario.rol is None or usuario.rol.nombre not in ROLES_VOTANTES_COMITE:
        return False, "El rol del usuario no puede votar en el comité"
    if solicitud.usuario_id == usuario.id:
        return False, "El oficial que registró la solicitud no puede votar"
    if any(voto.usuario_id == usuario.id for voto in votos):
        return False, "El usuario ya votó en esta ronda"
    if solicitud.estado != "EN_COMITE":
        return False, "La solicitud no está en comité"
    return True, None


def _resultado_votacion_comite(votos: list[VotoComite]) -> str | None:
    if len(votos) < VOTOS_COMITE_REQUERIDOS:
        return None
    aprobaciones = sum(voto.voto == "APROBAR" for voto in votos)
    rechazos = sum(voto.voto == "RECHAZAR" for voto in votos)
    if aprobaciones >= 2:
        return "APROBADO"
    if rechazos >= 2:
        return "RECHAZADO"
    return "OBSERVADA"


def _comite_item_out(db: Session, solicitud: SolicitudCredito, usuario: Usuario) -> dict:
    votos = _votos_ronda_comite(db, solicitud, solicitud.ronda_comite)
    evaluacion = _evaluacion_reciente_comite(db, solicitud, solicitud.cooperativa_id)
    puede_votar, motivo = _puede_votar_comite(solicitud, usuario, votos)
    return {
        "solicitud": _solicitud_out(solicitud),
        "evaluacion": None if evaluacion is None else _evaluacion_crediticia_out(evaluacion),
        "ronda": solicitud.ronda_comite,
        "votos": [_voto_comite_out(voto) for voto in votos],
        "votos_requeridos": VOTOS_COMITE_REQUERIDOS,
        "puede_votar": puede_votar,
        "motivo_no_puede_votar": motivo,
    }


@router.get("/comite", response_model=list[ComiteItemOut])
def listar_comite(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    solicitudes = db.execute(
        select(SolicitudCredito)
        .options(
            joinedload(SolicitudCredito.socio),
            joinedload(SolicitudCredito.producto).joinedload(ProductoCredito.moneda),
            joinedload(SolicitudCredito.moneda),
            joinedload(SolicitudCredito.evaluacion),
            joinedload(SolicitudCredito.oficial),
        )
        .where(
            SolicitudCredito.cooperativa_id == cooperativa_id,
            SolicitudCredito.estado == "EN_COMITE",
        )
        .order_by(SolicitudCredito.fecha_solicitud, SolicitudCredito.id)
    ).scalars().all()
    return [_comite_item_out(db, solicitud, usuario) for solicitud in solicitudes]


@router.post(
    "/comite/{solicitud_id}/votos",
    response_model=ComiteItemOut,
    status_code=status.HTTP_201_CREATED,
)
def votar_comite(
    solicitud_id: int,
    body: dict,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    if usuario.rol is None or usuario.rol.nombre not in ROLES_VOTANTES_COMITE:
        raise HTTPException(status_code=403, detail="El rol del usuario no puede votar en el comité")
    try:
        voto_in = VotoComiteIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="El voto o comentario no es válido") from exc

    solicitud = db.execute(
        select(SolicitudCredito)
        .options(
            joinedload(SolicitudCredito.socio),
            joinedload(SolicitudCredito.producto).joinedload(ProductoCredito.moneda),
            joinedload(SolicitudCredito.moneda),
            joinedload(SolicitudCredito.evaluacion),
            joinedload(SolicitudCredito.oficial),
        )
        .where(
            SolicitudCredito.id == solicitud_id,
            SolicitudCredito.cooperativa_id == cooperativa_id,
        )
        .with_for_update(of=SolicitudCredito)
    ).scalar_one_or_none()
    if solicitud is None:
        raise HTTPException(status_code=404, detail="Solicitud de crédito no encontrada")
    if solicitud.estado != "EN_COMITE":
        raise HTTPException(status_code=409, detail="La solicitud no está en comité")
    if solicitud.usuario_id == usuario.id:
        raise HTTPException(status_code=403, detail="El oficial que registró la solicitud no puede votar")
    votos = _votos_ronda_comite(db, solicitud, solicitud.ronda_comite)
    if any(existing.usuario_id == usuario.id for existing in votos):
        raise HTTPException(status_code=409, detail="El usuario ya votó en esta ronda")

    nuevo_voto = VotoComite(
        solicitud_credito_id=solicitud.id,
        cooperativa_id=cooperativa_id,
        ronda=solicitud.ronda_comite,
        usuario_id=usuario.id,
        voto=voto_in.voto,
        comentario=voto_in.comentario,
    )
    db.add(nuevo_voto)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if "uq_voto_comite_solicitud_ronda_usuario" in str(exc.orig):
            raise HTTPException(status_code=409, detail="El usuario ya votó en esta ronda") from exc
        raise

    registrar_accion(
        db,
        accion="VOTAR_COMITE",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Voto registrado para solicitud {solicitud.id}, ronda {solicitud.ronda_comite}: {voto_in.voto}",
        request=request,
    )
    votos = _votos_ronda_comite(db, solicitud, solicitud.ronda_comite)
    resultado = _resultado_votacion_comite(votos)
    if resultado is not None:
        solicitud.estado = resultado
        solicitud.resultado_comite = resultado
        solicitud.fecha_resolucion_comite = func.now()
        solicitud.fecha_actualizacion = func.now()
        registrar_accion(
            db,
            accion="RESOLVER_COMITE",
            modulo="CREDITOS",
            usuario_id=usuario.id,
            cooperativa_id=cooperativa_id,
            descripcion=f"Solicitud {solicitud.id} resuelta por comité en ronda {solicitud.ronda_comite}: {resultado}",
            request=request,
        )
    db.commit()
    db.refresh(solicitud)
    return _comite_item_out(db, solicitud, usuario)


@router.get(
    "/solicitudes/{solicitud_id}/acta-comite",
    response_model=ActaOut,
)
def obtener_acta_comite(
    solicitud_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.ronda_comite < 1:
        raise HTTPException(status_code=409, detail="La solicitud nunca llegó al comité")
    cooperativa = db.execute(
        select(Cooperativa).where(Cooperativa.id == cooperativa_id)
    ).scalar_one()
    votos = db.execute(
        select(VotoComite)
        .options(joinedload(VotoComite.usuario).joinedload(Usuario.rol))
        .where(
            VotoComite.solicitud_credito_id == solicitud.id,
            VotoComite.cooperativa_id == cooperativa_id,
        )
        .order_by(VotoComite.ronda, VotoComite.fecha, VotoComite.id)
    ).scalars().all()
    votos_por_ronda: dict[int, list[VotoComite]] = {}
    for voto in votos:
        votos_por_ronda.setdefault(voto.ronda, []).append(voto)

    rondas = []
    for numero_ronda in range(1, solicitud.ronda_comite + 1):
        votos_ronda = votos_por_ronda.get(numero_ronda, [])
        resultado = None
        fecha_resolucion = None
        if len(votos_ronda) >= VOTOS_COMITE_REQUERIDOS:
            resultado = _resultado_votacion_comite(votos_ronda)
            fecha_resolucion = votos_ronda[VOTOS_COMITE_REQUERIDOS - 1].fecha
        if numero_ronda == solicitud.ronda_comite and solicitud.resultado_comite is not None:
            resultado = solicitud.resultado_comite
            fecha_resolucion = solicitud.fecha_resolucion_comite
        rondas.append({
            "ronda": numero_ronda,
            "votos": [_voto_comite_out(voto) for voto in votos_ronda],
            "resultado": resultado,
            "fecha_resolucion": fecha_resolucion,
        })
    evaluacion = _evaluacion_reciente_comite(db, solicitud, cooperativa_id)
    return {
        "solicitud": _solicitud_out(solicitud),
        "rondas": rondas,
        "evaluacion": None if evaluacion is None else _evaluacion_crediticia_out(evaluacion),
        "cooperativa": {"id": cooperativa.id, "nombre": cooperativa.nombre},
    }


@router.post(
    "/solicitudes/{solicitud_id}/evaluacion",
    response_model=EvaluacionCrediticiaOut,
    status_code=status.HTTP_201_CREATED,
)
def evaluar_solicitud_credito(
    solicitud_id: int,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_evaluador_crediticio(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.estado not in {"PENDIENTE", "OBSERVADA", "EN_EVALUACION"}:
        raise HTTPException(status_code=409, detail="La solicitud no está disponible para evaluación")
    if solicitud.producto is None:
        raise HTTPException(status_code=409, detail="La solicitud no tiene producto crediticio")

    historial = db.execute(
        text(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM credito c
                    JOIN solicitud_credito previa ON previa.id = c.solicitud_credito_id
                    WHERE previa.socio_id = :socio_id
                      AND previa.id <> :solicitud_id
                ) AS tiene_credito_previo,
                EXISTS (
                    SELECT 1
                    FROM morosidad m
                    JOIN credito c ON c.id = m.credito_id
                    JOIN solicitud_credito previa ON previa.id = c.solicitud_credito_id
                    WHERE previa.socio_id = :socio_id
                      AND previa.id <> :solicitud_id
                      AND m.estado = 'EN_MORA'
                ) AS tiene_mora_vigente,
                (
                    SELECT count(*)
                    FROM pago_cuota p
                    JOIN credito c ON c.id = p.credito_id
                    JOIN producto_credito pc ON pc.id = c.producto_credito_id
                    JOIN solicitud_credito previa ON previa.id = c.solicitud_credito_id
                    WHERE previa.socio_id = :socio_id
                      AND previa.id <> :solicitud_id
                      AND p.dias_atraso > pc.dias_gracia_mora
                ) AS atrasos_previos
            """
        ),
        {"socio_id": solicitud.socio_id, "solicitud_id": solicitud.id},
    ).mappings().one()
    ahorros = db.execute(
        select(CuentaAhorro).where(
            CuentaAhorro.socio_id == solicitud.socio_id,
            CuentaAhorro.moneda_id == solicitud.moneda_id,
            CuentaAhorro.estado == "ACTIVA",
        )
    ).scalars().all()
    resultado = score_application(
        solicitud,
        savings_accounts=ahorros,
        has_credit_history=historial["tiene_credito_previo"],
        has_current_arrears=historial["tiene_mora_vigente"],
    )
    mora_resultado = predecir_mora(_features_mora(solicitud, resultado, ahorros, historial))
    evaluacion = EvaluacionCrediticia(
        solicitud_credito_id=solicitud.id,
        cooperativa_id=cooperativa_id,
        version_modelo=resultado["version_modelo"],
        score=resultado["score"],
        dictamen=resultado["dictamen"],
        factores=resultado["factores"],
        knockouts=resultado["knockouts"],
        explicacion=resultado["explicacion"],
        cuota_estimada=resultado["cuota_estimada"],
        relacion_cuota_ingreso=resultado["relacion_cuota_ingreso"],
        usuario_id=usuario.id,
        probabilidad_mora=mora_resultado["probabilidad_mora"],
        nivel_riesgo=mora_resultado["nivel_riesgo"],
        version_modelo_mora=mora_resultado["version_modelo"],
    )
    estado_anterior = solicitud.estado
    nuevo_estado = _estado_despues_evaluacion(
        resultado["dictamen"], solicitud.monto, solicitud.producto.monto_aprobacion_directa
    )
    solicitud.estado = nuevo_estado
    if nuevo_estado == "EN_COMITE" and estado_anterior != "EN_COMITE":
        solicitud.ronda_comite += 1
        solicitud.resultado_comite = None
        solicitud.fecha_resolucion_comite = None
        registrar_accion(
            db,
            accion="DERIVAR_COMITE",
            modulo="CREDITOS",
            usuario_id=usuario.id,
            cooperativa_id=cooperativa_id,
            descripcion=f"Solicitud derivada al comité de crédito: {solicitud.id}, ronda {solicitud.ronda_comite}",
            request=request,
        )
    solicitud.fecha_actualizacion = func.now()
    db.add(evaluacion)
    registrar_accion(
        db,
        accion="EVALUAR_SOLICITUD",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Evaluación crediticia registrada para solicitud: {solicitud.id}",
        request=request,
    )
    db.commit()
    db.refresh(evaluacion)
    return _evaluacion_crediticia_out(evaluacion)


@router.get(
    "/solicitudes/{solicitud_id}/evaluaciones",
    response_model=list[EvaluacionCrediticiaOut],
)
def listar_evaluaciones_crediticias(
    solicitud_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    evaluaciones = db.execute(
        select(EvaluacionCrediticia)
        .options(
            joinedload(EvaluacionCrediticia.usuario),
            joinedload(EvaluacionCrediticia.resolucion_usuario),
        )
        .where(
            EvaluacionCrediticia.solicitud_credito_id == solicitud.id,
            EvaluacionCrediticia.cooperativa_id == cooperativa_id,
        )
        .order_by(EvaluacionCrediticia.fecha.desc(), EvaluacionCrediticia.id.desc())
    ).scalars().all()
    return [_evaluacion_crediticia_out(item) for item in evaluaciones]


@router.post(
    "/solicitudes/{solicitud_id}/resolucion",
    response_model=EvaluacionCrediticiaOut,
)
def resolver_solicitud_crediticia(
    solicitud_id: int,
    body: ResolucionSolicitudIn,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.estado == "EN_COMITE":
        raise HTTPException(status_code=409, detail="La solicitud se resuelve en comité")
    ultima_evaluacion = db.execute(
        select(EvaluacionCrediticia)
        .options(
            joinedload(EvaluacionCrediticia.usuario),
            joinedload(EvaluacionCrediticia.resolucion_usuario),
        )
        .where(
            EvaluacionCrediticia.solicitud_credito_id == solicitud.id,
            EvaluacionCrediticia.cooperativa_id == cooperativa_id,
        )
        .order_by(EvaluacionCrediticia.fecha.desc(), EvaluacionCrediticia.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if solicitud.estado != "EN_EVALUACION" or ultima_evaluacion is None or ultima_evaluacion.dictamen != "REVISION_MANUAL":
        raise HTTPException(status_code=409, detail="La solicitud no tiene una evaluación pendiente de resolución")
    justificacion = body.justificacion.strip()
    if len(justificacion) < 10:
        raise HTTPException(status_code=400, detail="La justificación debe tener al menos 10 caracteres")

    ultima_evaluacion.resolucion = body.decision
    ultima_evaluacion.resolucion_justificacion = justificacion
    ultima_evaluacion.resolucion_usuario_id = usuario.id
    ultima_evaluacion.resolucion_fecha = func.now()
    solicitud.estado = body.decision
    solicitud.fecha_actualizacion = func.now()
    registrar_accion(
        db,
        accion="RESOLVER_SOLICITUD",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Solicitud de crédito resuelta: {solicitud.id} ({body.decision})",
        request=request,
    )
    db.commit()
    db.refresh(ultima_evaluacion)
    return _evaluacion_crediticia_out(ultima_evaluacion)


@router.get("/solicitudes", response_model=list[SolicitudOut])
def listar_solicitudes_credito(
    estado: str | None = Query(None),
    socio_ci: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    if estado is not None and estado not in ESTADOS_SOLICITUD:
        raise HTTPException(status_code=400, detail="El estado de la solicitud no es válido")
    if desde is not None and hasta is not None and desde > hasta:
        raise HTTPException(status_code=400, detail="El rango de fechas no es válido")
    query = (
        select(SolicitudCredito)
        .options(
            joinedload(SolicitudCredito.socio),
            joinedload(SolicitudCredito.producto).joinedload(ProductoCredito.moneda),
            joinedload(SolicitudCredito.moneda),
            joinedload(SolicitudCredito.evaluacion),
            joinedload(SolicitudCredito.oficial),
        )
        .where(SolicitudCredito.cooperativa_id == cooperativa_id)
    )
    if estado is not None:
        query = query.where(SolicitudCredito.estado == estado)
    if socio_ci is not None:
        query = query.join(SolicitudCredito.socio).where(Socio.ci == socio_ci)
    if desde is not None:
        query = query.where(cast(SolicitudCredito.fecha_solicitud, Date) >= desde)
    if hasta is not None:
        query = query.where(cast(SolicitudCredito.fecha_solicitud, Date) <= hasta)
    solicitudes = db.execute(
        query.order_by(SolicitudCredito.fecha_solicitud.desc(), SolicitudCredito.id.desc())
    ).unique().scalars().all()
    return [_solicitud_out(solicitud) for solicitud in solicitudes]


@router.get("/solicitudes/{solicitud_id}", response_model=SolicitudOut)
def obtener_solicitud_credito(
    solicitud_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    return _solicitud_out(_obtener_solicitud(db, cooperativa_id, solicitud_id))


@router.get("/solicitudes/{solicitud_id}/plan-pagos", response_model=PlanPagosOut)
def obtener_plan_pagos_solicitud(
    solicitud_id: int,
    fecha_desembolso: date | None = Query(None),
    fecha_primer_vencimiento: date | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.estado != "APROBADO":
        raise HTTPException(status_code=409, detail="La solicitud no está aprobada")
    if solicitud.producto is None:
        raise HTTPException(status_code=409, detail="La solicitud no tiene producto crediticio")

    fecha_desembolso = fecha_desembolso or date.today()
    if fecha_primer_vencimiento is None:
        plan = generar_plan_pagos(
            monto=solicitud.monto,
            tasa_anual=solicitud.tasa_interes,
            plazo_meses=solicitud.plazo_meses,
            tipo_amortizacion=solicitud.producto.tipo_amortizacion,
            fecha_desembolso=fecha_desembolso,
        )
        fecha_primer_vencimiento = plan["fecha_primer_vencimiento"]
    else:
        dias = (fecha_primer_vencimiento - fecha_desembolso).days
        if not 15 <= dias <= 45:
            raise HTTPException(status_code=400, detail="El primer vencimiento debe ser entre 15 y 45 días después del desembolso")
        plan = generar_plan_pagos(
            monto=solicitud.monto,
            tasa_anual=solicitud.tasa_interes,
            plazo_meses=solicitud.plazo_meses,
            tipo_amortizacion=solicitud.producto.tipo_amortizacion,
            fecha_desembolso=fecha_desembolso,
            fecha_primer_vencimiento=fecha_primer_vencimiento,
        )

    if solicitud.moneda is None:
        raise HTTPException(status_code=409, detail="La solicitud no tiene una moneda asignada")
    return {
        **plan,
        "moneda": MonedaOut.model_validate(solicitud.moneda),
    }


@router.put("/solicitudes/{solicitud_id}", response_model=SolicitudOut)
def actualizar_solicitud_credito(
    solicitud_id: int,
    body: SolicitudCreditoUpdate,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_escritor_solicitud(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.estado not in ESTADOS_EDITABLES_SOLICITUD:
        raise HTTPException(status_code=409, detail="La solicitud ya no se puede modificar")

    cambios = body.model_dump(exclude_unset=True)
    evaluacion_cambios = cambios.pop("evaluacion", None)
    if any(value is None for key, value in cambios.items() if key not in {"destino_detalle", "observaciones"}):
        raise HTTPException(status_code=400, detail="Los campos enviados no pueden ser nulos")
    producto_id = cambios.get("producto_id", solicitud.producto_credito_id)
    cambiar_producto = producto_id != solicitud.producto_credito_id
    if producto_id is None:
        raise HTTPException(status_code=400, detail="Debe seleccionar un producto crediticio")
    producto = db.execute(
        select(ProductoCredito)
        .options(joinedload(ProductoCredito.moneda))
        .where(
            ProductoCredito.id == producto_id,
            ProductoCredito.cooperativa_id == cooperativa_id,
        )
    ).scalar_one_or_none()
    if producto is None:
        raise HTTPException(status_code=404, detail="Producto crediticio no encontrado")

    if evaluacion_cambios is not None and not isinstance(evaluacion_cambios, dict):
        raise HTTPException(status_code=400, detail="La evaluación socioeconómica no es válida")
    eval_values = {}
    if solicitud.evaluacion is not None:
        eval_values = {
            "ingreso_mensual": solicitud.evaluacion.ingreso_mensual,
            "egreso_mensual": solicitud.evaluacion.egreso_mensual,
            "cuota_deudas_mensual": solicitud.evaluacion.cuota_deudas_mensual,
            "actividad_economica": solicitud.evaluacion.actividad_economica,
            "fuente_ingresos": solicitud.evaluacion.fuente_ingresos,
            "antiguedad_laboral_meses": solicitud.evaluacion.antiguedad_laboral_meses,
            "calificacion_asfi": solicitud.evaluacion.calificacion_asfi,
            "coordenadas": solicitud.evaluacion.coordenadas,
            "observaciones": solicitud.evaluacion.observaciones,
        }
    if evaluacion_cambios:
        if any(value is None for key, value in evaluacion_cambios.items() if key not in {"coordenadas", "observaciones"}):
            raise HTTPException(status_code=400, detail="Los campos de evaluación enviados no pueden ser nulos")
        eval_values.update(evaluacion_cambios)

    merged = {
        "monto": cambios.get("monto", solicitud.monto),
        "plazo_meses": cambios.get("plazo_meses", solicitud.plazo_meses),
        "destino": cambios.get("destino", solicitud.destino),
        "destino_detalle": cambios.get("destino_detalle", solicitud.destino_detalle),
        "evaluacion": eval_values,
    }
    _validar_datos_solicitud(producto, merged)
    if not eval_values:
        raise HTTPException(status_code=400, detail="Debe proporcionar la evaluación socioeconómica")

    for field in ("monto", "plazo_meses", "destino", "destino_detalle", "observaciones"):
        if field in cambios:
            setattr(solicitud, field, cambios[field].strip() if field == "destino_detalle" and cambios[field] else cambios[field])
    if solicitud.evaluacion is None:
        evaluacion = EvaluacionCampo(
            socio_id=solicitud.socio_id,
            usuario_id=usuario.id,
            fecha=func.current_date(),
        )
        db.add(evaluacion)
        solicitud.evaluacion = evaluacion
    else:
        evaluacion = solicitud.evaluacion
    for field in (
        "ingreso_mensual",
        "egreso_mensual",
        "cuota_deudas_mensual",
        "actividad_economica",
        "fuente_ingresos",
        "antiguedad_laboral_meses",
        "calificacion_asfi",
        "coordenadas",
        "observaciones",
    ):
        if field in (evaluacion_cambios or {}):
            value = eval_values[field]
            setattr(evaluacion, field, value.strip() if field == "actividad_economica" and value else value)
    evaluacion.capacidad_pago = (
        eval_values["ingreso_mensual"] - eval_values["egreso_mensual"] - eval_values["cuota_deudas_mensual"]
    )
    evaluacion.socio_id = solicitud.socio_id
    solicitud.producto_credito_id = producto.id
    if {"producto_id", "monto", "plazo_meses"} & cambios.keys():
        solicitud.tasa_interes = producto.tasa_interes_anual
    if cambiar_producto:
        solicitud.moneda_id = producto.moneda_id
    solicitud.calificacion_asfi = eval_values["calificacion_asfi"]
    solicitud.tiene_deudas = eval_values["cuota_deudas_mensual"] > 0
    solicitud.fecha_actualizacion = func.now()
    registrar_accion(
        db,
        accion="ACTUALIZAR_SOLICITUD",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Solicitud de crédito actualizada: {solicitud.id} ({solicitud.numero_solicitud})",
        request=request,
    )
    db.commit()
    db.refresh(solicitud)
    return _solicitud_out(_obtener_solicitud(db, cooperativa_id, solicitud.id))


@router.patch("/solicitudes/{solicitud_id}/anulacion", response_model=SolicitudOut)
def anular_solicitud_credito(
    solicitud_id: int,
    body: SolicitudAnulacionIn,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_escritor_solicitud(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.estado not in ESTADOS_EDITABLES_SOLICITUD:
        raise HTTPException(status_code=409, detail="La solicitud ya no se puede anular")
    motivo = body.motivo.strip()
    if len(motivo) < 5:
        raise HTTPException(status_code=400, detail="El motivo de anulación debe tener al menos 5 caracteres")
    solicitud.estado = "ANULADA"
    solicitud.motivo_anulacion = motivo
    solicitud.fecha_actualizacion = func.now()
    registrar_accion(
        db,
        accion="ANULAR_SOLICITUD",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Solicitud de crédito anulada: {solicitud.id} ({solicitud.numero_solicitud})",
        request=request,
    )
    db.commit()
    db.refresh(solicitud)
    return _solicitud_out(_obtener_solicitud(db, cooperativa_id, solicitud.id))


def _obtener_garantia(db: Session, cooperativa_id: int, garantia_id: int) -> Garantia:
    garantia = db.execute(
        select(Garantia).options(joinedload(Garantia.moneda), joinedload(Garantia.usuario_registro),
            joinedload(Garantia.usuario_verificacion), joinedload(Garantia.solicitud),
            joinedload(Garantia.solicitud).joinedload(SolicitudCredito.socio),
            joinedload(Garantia.solicitud).joinedload(SolicitudCredito.oficial))
        .where(Garantia.id == garantia_id, Garantia.cooperativa_id == cooperativa_id)
    ).scalar_one_or_none()
    if garantia is None:
        raise HTTPException(status_code=404, detail="Garantía no encontrada")
    return garantia


def _garantia_out(db: Session, garantia: Garantia) -> dict:
    registrar = garantia.usuario_registro
    verificador = garantia.usuario_verificacion
    avalista = None
    if garantia.tipo == "PERSONAL":
        avalista = {"nombre": garantia.avalista_nombre, "ci": garantia.avalista_ci,
            "ingreso_mensual": garantia.avalista_ingreso_mensual, "relacion": garantia.avalista_relacion,
            "telefono": garantia.avalista_telefono, "socio_id": garantia.socio_avalista_id}
    return {"id": garantia.id, "tipo": garantia.tipo, "descripcion": garantia.descripcion,
        "moneda": garantia.moneda, "valor_comercial": garantia.valor_comercial,
        "valor_realizable": garantia.valor_realizable, "documento_referencia": garantia.documento_referencia,
        "avalista": avalista, "estado": garantia.estado,
        "observacion_verificacion": garantia.observacion_verificacion,
        "usuario_registro": {"id": registrar.id, "nombre": registrar.nombre},
        "usuario_verificacion": None if verificador is None else {"id": verificador.id, "nombre": verificador.nombre},
        "fecha_registro": garantia.fecha_registro, "fecha_verificacion": garantia.fecha_verificacion,
        "fecha_liberacion": garantia.fecha_liberacion}


def _valor_realizable(tipo: str, valor_comercial: Decimal | None, ingreso: Decimal | None, monto: Decimal) -> Decimal:
    if tipo == "HIPOTECARIA":
        value = valor_comercial * Decimal("0.70")
    elif tipo == "PRENDARIA":
        value = valor_comercial * Decimal("0.50")
    else:
        value = min(ingreso * Decimal("12") * Decimal("0.30"), monto)
    return value.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _marcar_garantias_liberadas(garantias: list[Garantia]) -> int:
    liberadas = 0
    for garantia in garantias:
        if garantia.estado == "VERIFICADA":
            garantia.estado = "LIBERADA"
            garantia.fecha_liberacion = func.now()
            liberadas += 1
    return liberadas


def _asignar_avalista(garantia: Garantia, avalista) -> None:
    garantia.avalista_nombre = avalista.nombre.strip()
    garantia.avalista_ci = avalista.ci.strip()
    garantia.avalista_ingreso_mensual = avalista.ingreso_mensual
    garantia.avalista_relacion = avalista.relacion.strip()
    garantia.avalista_telefono = avalista.telefono
    garantia.socio_avalista_id = avalista.socio_id


def _normalizar_ci(ci: str) -> str:
    return "".join(ci.split()).upper()


def _validar_payload_garantia(schema, payload):
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        detail = "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        raise HTTPException(status_code=400, detail=detail) from exc


@router.post("/solicitudes/{solicitud_id}/garantias", response_model=GarantiaOut, status_code=201)
def registrar_garantia(solicitud_id: int, payload: Any = Body(...), request: Request = None,
    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    body = _validar_payload_garantia(GarantiaCreateIn, payload)
    cooperativa_id = _validar_escritor_solicitud(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    if solicitud.estado not in ESTADOS_EDITABLES_SOLICITUD:
        raise HTTPException(status_code=409, detail="La solicitud no permite registrar garantías")
    moneda_id = solicitud.moneda_id if body.tipo == "PERSONAL" else body.moneda_id
    if moneda_id != solicitud.moneda_id:
        raise HTTPException(status_code=400, detail="La moneda de la garantía debe coincidir con la solicitud")
    if body.tipo == "PERSONAL" and body.avalista.socio_id == solicitud.socio_id:
        raise HTTPException(status_code=400, detail="El socio solicitante no puede ser su propio avalista")
    if body.tipo == "PERSONAL" and _normalizar_ci(body.avalista.ci) == _normalizar_ci(solicitud.socio.ci):
        raise HTTPException(status_code=400, detail="El socio solicitante no puede ser su propio avalista")
    if body.tipo == "PERSONAL" and body.avalista.socio_id is not None:
        socio_aval = db.get(Socio, body.avalista.socio_id)
        if socio_aval is None or socio_aval.cooperativa_id != cooperativa_id:
            raise HTTPException(status_code=400, detail="El socio avalista no pertenece a esta cooperativa")
    realizable = _valor_realizable(body.tipo, body.valor_comercial,
        body.avalista.ingreso_mensual if body.avalista else None, solicitud.monto)
    garantia = Garantia(cooperativa_id=cooperativa_id, solicitud_credito_id=solicitud.id,
        tipo=body.tipo, descripcion=body.descripcion.strip(), moneda_id=moneda_id,
        valor_comercial=body.valor_comercial, valor_realizable=realizable,
        documento_referencia=body.documento_referencia, usuario_registro_id=usuario.id)
    if body.avalista:
        _asignar_avalista(garantia, body.avalista)
    db.add(garantia); db.flush()
    registrar_accion(db, accion="REGISTRAR_GARANTIA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=cooperativa_id, descripcion=f"Garantía registrada: {garantia.id}", request=request)
    db.commit(); db.refresh(garantia)
    return _garantia_out(db, _obtener_garantia(db, cooperativa_id, garantia.id))


@router.get("/solicitudes/{solicitud_id}/garantias", response_model=list[GarantiaOut])
def listar_garantias_solicitud(solicitud_id: int, usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)):
    cooperativa_id = _validar_lector(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    rows = db.execute(select(Garantia).options(joinedload(Garantia.moneda),
        joinedload(Garantia.usuario_registro), joinedload(Garantia.usuario_verificacion))
        .where(Garantia.solicitud_credito_id == solicitud.id, Garantia.cooperativa_id == cooperativa_id)
        .order_by(Garantia.id)).scalars().all()
    return [_garantia_out(db, row) for row in rows]


@router.put("/garantias/{garantia_id}", response_model=GarantiaOut)
def actualizar_garantia(garantia_id: int, body: GarantiaUpdateIn, request: Request,
    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    cooperativa_id = _validar_escritor_solicitud(usuario)
    garantia = _obtener_garantia(db, cooperativa_id, garantia_id)
    if garantia.estado != "REGISTRADA" or garantia.solicitud.estado not in ESTADOS_EDITABLES_SOLICITUD:
        raise HTTPException(status_code=409, detail="La garantía o solicitud ya no permite edición")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return _garantia_out(db, garantia)
    if garantia.tipo == "PERSONAL":
        if any(k in changes for k in ("moneda_id", "valor_comercial")):
            raise HTTPException(status_code=400, detail="La garantía personal no admite moneda ni valor comercial")
        avalista = body.avalista
        if avalista is not None:
            if avalista.socio_id == garantia.solicitud.socio_id:
                raise HTTPException(status_code=400, detail="El socio solicitante no puede ser su propio avalista")
            if _normalizar_ci(avalista.ci) == _normalizar_ci(garantia.solicitud.socio.ci):
                raise HTTPException(status_code=400, detail="El socio solicitante no puede ser su propio avalista")
            if avalista.socio_id is not None:
                socio_aval = db.get(Socio, avalista.socio_id)
                if socio_aval is None or socio_aval.cooperativa_id != cooperativa_id:
                    raise HTTPException(status_code=400, detail="El socio avalista no pertenece a esta cooperativa")
            _asignar_avalista(garantia, avalista)
    else:
        if "avalista" in changes:
            raise HTTPException(status_code=400, detail="La garantía no admite datos de avalista")
        if "moneda_id" in changes:
            if changes["moneda_id"] != garantia.solicitud.moneda_id:
                raise HTTPException(status_code=400, detail="La moneda de la garantía debe coincidir con la solicitud")
            garantia.moneda_id = changes["moneda_id"]
        if "valor_comercial" in changes:
            garantia.valor_comercial = changes["valor_comercial"]
    if "descripcion" in changes and changes["descripcion"] is not None:
        garantia.descripcion = changes["descripcion"].strip()
    if "documento_referencia" in changes:
        garantia.documento_referencia = changes["documento_referencia"]
    garantia.valor_realizable = _valor_realizable(garantia.tipo, garantia.valor_comercial,
        garantia.avalista_ingreso_mensual, garantia.solicitud.monto)
    registrar_accion(db, accion="ACTUALIZAR_GARANTIA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=cooperativa_id, descripcion=f"Garantía actualizada: {garantia.id}", request=request)
    db.commit(); db.refresh(garantia)
    return _garantia_out(db, _obtener_garantia(db, cooperativa_id, garantia.id))


@router.delete("/garantias/{garantia_id}", status_code=204)
def eliminar_garantia(garantia_id: int, request: Request, usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)):
    cooperativa_id = _validar_escritor_solicitud(usuario)
    garantia = _obtener_garantia(db, cooperativa_id, garantia_id)
    if garantia.estado != "REGISTRADA" or garantia.solicitud.estado not in ESTADOS_EDITABLES_SOLICITUD:
        raise HTTPException(status_code=409, detail="La garantía o solicitud ya no permite eliminación")
    registrar_accion(db, accion="ELIMINAR_GARANTIA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=cooperativa_id, descripcion=f"Garantía eliminada: {garantia.id}", request=request)
    db.delete(garantia); db.commit()
    return None


def _calcular_cobertura(db: Session, solicitud: SolicitudCredito) -> dict:
    sums = db.execute(select(Garantia.estado, func.coalesce(func.sum(Garantia.valor_realizable), 0))
        .where(Garantia.solicitud_credito_id == solicitud.id,
            Garantia.cooperativa_id == solicitud.cooperativa_id)
        .group_by(Garantia.estado)).all()
    values = {state: Decimal(total) for state, total in sums}
    verified = values.get("VERIFICADA", Decimal("0.00"))
    pending = values.get("REGISTRADA", Decimal("0.00"))
    amount = Decimal(solicitud.monto)
    percentage = (verified / amount * Decimal("100")).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    product = solicitud.producto
    required = bool(product and product.requiere_garantia)
    minimum = Decimal(product.cobertura_minima_garantia) if product else Decimal("100.00")
    return {"requiere_garantia": required, "cobertura_minima": minimum, "monto_solicitado": amount,
        "valor_realizable_verificado": verified, "valor_realizable_pendiente": pending,
        "porcentaje_cobertura": percentage, "cumple": not required or percentage >= minimum}


@router.post("/garantias/{garantia_id}/verificacion", response_model=GarantiaOut)
def verificar_garantia(garantia_id: int, payload: Any = Body(...), request: Request = None,
    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    body = _validar_payload_garantia(GarantiaVerificacionIn, payload)
    cooperativa_id = _validar_lector(usuario)
    if usuario.rol is None or usuario.rol.nombre != "ADMINISTRADOR":
        raise HTTPException(status_code=403, detail="Operación reservada a administradores de la cooperativa")
    garantia = _obtener_garantia(db, cooperativa_id, garantia_id)
    if garantia.usuario_registro_id == usuario.id:
        raise HTTPException(status_code=403, detail="Quien registra la garantía no puede verificarla")
    if garantia.estado != "REGISTRADA":
        raise HTTPException(status_code=409, detail="La garantía ya fue verificada")
    garantia.estado = body.decision
    garantia.observacion_verificacion = body.observacion.strip()
    garantia.usuario_verificacion_id = usuario.id
    garantia.fecha_verificacion = func.now()
    registrar_accion(db, accion="VERIFICAR_GARANTIA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=cooperativa_id, descripcion=f"Garantía {garantia.estado.lower()}: {garantia.id}", request=request)
    db.commit(); db.refresh(garantia)
    return _garantia_out(db, _obtener_garantia(db, cooperativa_id, garantia.id))


@router.get("/solicitudes/{solicitud_id}/cobertura", response_model=CoberturaOut)
def obtener_cobertura(solicitud_id: int, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    cooperativa_id = _validar_lector(usuario)
    solicitud = _obtener_solicitud(db, cooperativa_id, solicitud_id)
    return _calcular_cobertura(db, solicitud)


@router.get("/creditos/{credito_id}/garantias", response_model=list[GarantiaOut])
def listar_garantias_credito(credito_id: int, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    cooperativa_id = _validar_lector(usuario)
    credito = db.execute(select(Credito).where(Credito.id == credito_id,
        Credito.cooperativa_id == cooperativa_id)).scalar_one_or_none()
    if credito is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    return listar_garantias_solicitud(credito.solicitud_credito_id, usuario, db)


@router.post("/productos", response_model=ProductoCreditoOut, status_code=status.HTTP_201_CREATED)
def crear_producto_crediticio(
    body: ProductoCreditoCreate,
    request: Request,
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    codigo = body.codigo.strip().upper()
    if re.fullmatch(r"[A-Z0-9-]{2,20}", codigo) is None:
        raise HTTPException(status_code=400, detail="El código debe tener 2 a 20 caracteres: letras, números o guiones")
    datos = body.model_dump(exclude={"codigo"})
    datos["nombre"] = body.nombre.strip()
    _validar_datos_producto(db, datos)
    if db.execute(
        select(ProductoCredito.id).where(
            ProductoCredito.cooperativa_id == cooperativa_id,
            ProductoCredito.codigo == codigo,
        )
    ).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Ya existe un producto con ese código")

    producto = ProductoCredito(cooperativa_id=cooperativa_id, codigo=codigo, **datos)
    producto.fecha_actualizacion = func.now()
    db.add(producto)
    db.flush()
    registrar_accion(
        db,
        accion="CREAR_PRODUCTO",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Producto crediticio creado: {producto.id} ({producto.codigo})",
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_producto_credito_cooperativa_codigo" in str(exc.orig):
            raise HTTPException(status_code=409, detail="Ya existe un producto con ese código") from exc
        raise
    db.refresh(producto)
    return producto


@router.put("/productos/{producto_id}", response_model=ProductoCreditoOut)
def actualizar_producto_crediticio(
    producto_id: int,
    body: ProductoCreditoUpdate,
    request: Request,
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    producto = _obtener_producto(db, cooperativa_id, producto_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return producto
    if any(value is None for key, value in changes.items() if key != "descripcion"):
        raise HTTPException(status_code=400, detail="Los campos enviados no pueden ser nulos")

    values = {
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "moneda_id": producto.moneda_id,
        "monto_min": producto.monto_min,
        "monto_max": producto.monto_max,
        "plazo_min_meses": producto.plazo_min_meses,
        "plazo_max_meses": producto.plazo_max_meses,
        "tasa_interes_anual": producto.tasa_interes_anual,
        "tipo_amortizacion": producto.tipo_amortizacion,
        "dias_gracia_mora": producto.dias_gracia_mora,
        "tasa_mora_anual": producto.tasa_mora_anual,
        "relacion_cuota_ingreso_max": producto.relacion_cuota_ingreso_max,
        "requiere_garantia": producto.requiere_garantia,
        "monto_aprobacion_directa": producto.monto_aprobacion_directa,
    }
    values.update(changes)
    if "nombre" in changes and changes["nombre"] is not None:
        values["nombre"] = changes["nombre"].strip()
    _validar_datos_producto(db, values)

    for field, value in changes.items():
        setattr(producto, field, values[field])
    producto.fecha_actualizacion = func.now()
    registrar_accion(
        db,
        accion="ACTUALIZAR_PRODUCTO",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Producto crediticio actualizado: {producto.id} ({producto.codigo})",
        request=request,
    )
    db.commit()
    db.refresh(producto)
    return producto


@router.patch("/productos/{producto_id}/estado", response_model=ProductoCreditoOut)
def actualizar_estado_producto_crediticio(
    producto_id: int,
    body: ProductoCreditoEstadoUpdate,
    request: Request,
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    producto = _obtener_producto(db, cooperativa_id, producto_id)
    if body.estado not in {"ACTIVO", "INACTIVO"}:
        raise HTTPException(status_code=400, detail="El estado debe ser ACTIVO o INACTIVO")
    if producto.estado != body.estado:
        producto.estado = body.estado
        producto.fecha_actualizacion = func.now()
        registrar_accion(
            db,
            accion="ACTIVAR_PRODUCTO" if body.estado == "ACTIVO" else "DESACTIVAR_PRODUCTO",
            modulo="CREDITOS",
            usuario_id=usuario.id,
            cooperativa_id=cooperativa_id,
            descripcion=f"Producto crediticio {body.estado.lower()}: {producto.id} ({producto.codigo})",
            request=request,
        )
        db.commit()
        db.refresh(producto)
    return producto


@router.get("/productos", response_model=list[ProductoCreditoOut])
def listar_productos_crediticios(
    estado: str | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    if estado is not None and estado not in {"ACTIVO", "INACTIVO"}:
        raise HTTPException(status_code=400, detail="El estado debe ser ACTIVO o INACTIVO")
    query = (
        select(ProductoCredito)
        .options(joinedload(ProductoCredito.moneda))
        .where(ProductoCredito.cooperativa_id == cooperativa_id)
        .order_by(ProductoCredito.codigo)
    )
    if estado is not None:
        query = query.where(ProductoCredito.estado == estado)
    return db.execute(query).scalars().all()


@router.get("/productos/{producto_id}", response_model=ProductoCreditoOut)
def obtener_producto_crediticio(
    producto_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    return _obtener_producto(db, cooperativa_id, producto_id)


def _cuota_credito_out(cuota) -> dict:
    return {
        "numero": cuota.numero_cuota,
        "fecha_vencimiento": cuota.fecha_vencimiento,
        "saldo_inicial": cuota.saldo_inicial,
        "capital": cuota.monto_capital,
        "interes": cuota.monto_interes,
        "cuota": cuota.monto_cuota_total,
        "saldo_final": cuota.saldo_final,
        "estado_pago": cuota.estado_pago or "PENDIENTE",
    }


def _credito_detalle_out(credito: Credito) -> dict:
    cuotas = list(credito.cronograma)
    pendiente = next((row for row in cuotas if row.estado_pago == "PENDIENTE"), None)
    solicitud = credito.solicitud
    socio = credito.socio or solicitud.socio
    producto = credito.producto or solicitud.producto
    moneda = credito.moneda or solicitud.moneda
    return {
        "id": credito.id,
        "numero_credito": credito.numero_credito,
        "estado": credito.estado or "VIGENTE",
        "socio": {
            "id": socio.id,
            "nombre_completo": f"{socio.nombre} {socio.apellido}",
            "ci": socio.ci,
        },
        "producto": None if producto is None else {
            "id": producto.id,
            "codigo": producto.codigo,
            "nombre": producto.nombre,
        },
        "moneda": None if moneda is None else MonedaOut.model_validate(moneda),
        "monto_aprobado": credito.monto_aprobado,
        "saldo_pendiente": credito.saldo_pendiente,
        "tasa_interes": credito.tasa_interes if credito.tasa_interes is not None else solicitud.tasa_interes,
        "plazo_meses": credito.plazo_meses if credito.plazo_meses is not None else solicitud.plazo_meses,
        "tipo_amortizacion": credito.tipo_amortizacion if credito.tipo_amortizacion is not None else (None if producto is None else producto.tipo_amortizacion),
        "fecha_desembolso": credito.fecha_desembolso,
        "modalidad_desembolso": credito.modalidad_desembolso,
        "cuenta_desembolso": None if credito.cuenta_desembolso is None else {
            "id": credito.cuenta_desembolso.id,
            "numero": credito.cuenta_desembolso.numero,
        },
        "solicitud": {
            "id": solicitud.id,
            "numero_solicitud": solicitud.numero_solicitud,
        },
        "proxima_cuota": None if pendiente is None else _cuota_credito_out(pendiente),
        "cuotas_pagadas": sum(row.estado_pago == "PAGADA" for row in cuotas),
        "cuotas_totales": len(cuotas),
        "estado_mora": None if credito.mora is None else credito.mora.estado,
        "dias_de_retaso": None if credito.mora is None else credito.mora.dias_de_retaso,
        "cronograma": [_cuota_credito_out(row) for row in cuotas],
        "transaccion_desembolso_id": credito.transaccion_desembolso_id,
        "usuario": None if credito.usuario is None else {
            "id": credito.usuario.id,
            "nombre": credito.usuario.nombre,
        },
    }


def _credito_lista_out(credito: Credito) -> dict:
    detalle = _credito_detalle_out(credito)
    return {key: detalle[key] for key in (
        "id", "numero_credito", "estado", "socio", "producto", "moneda",
        "monto_aprobado", "saldo_pendiente", "tasa_interes", "plazo_meses",
        "tipo_amortizacion", "fecha_desembolso", "modalidad_desembolso",
        "cuenta_desembolso", "solicitud", "proxima_cuota", "cuotas_pagadas",
        "cuotas_totales", "estado_mora", "dias_de_retaso",
    )}


def _credito_cooperativa_filter(cooperativa_id: int):
    return or_(
        Credito.cooperativa_id == cooperativa_id,
        Credito.solicitud.has(SolicitudCredito.cooperativa_id == cooperativa_id),
        Credito.socio.has(Socio.cooperativa_id == cooperativa_id),
        Credito.producto.has(ProductoCredito.cooperativa_id == cooperativa_id),
    )


@router.get("/creditos", response_model=list[CreditoOut])
def listar_creditos(
    estado: str | None = Query(None),
    socio_ci: str | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    coop_id = _validar_lector(usuario)
    query = (
        select(Credito)
        .join(SolicitudCredito, Credito.solicitud_credito_id == SolicitudCredito.id)
        .options(
            joinedload(Credito.socio), joinedload(Credito.producto), joinedload(Credito.moneda),
            joinedload(Credito.cuenta_desembolso), joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
            selectinload(Credito.cronograma),
        )
        .where(_credito_cooperativa_filter(coop_id))
        .order_by(Credito.id.desc())
    )
    if estado is not None:
        query = query.where(Credito.estado == estado)
    if socio_ci is not None:
        query = query.join(Socio, Socio.id == SolicitudCredito.socio_id).where(Socio.ci == socio_ci)
    creditos = db.execute(query).unique().scalars().all()
    return [_credito_lista_out(credito) for credito in creditos]


@router.get("/creditos/{credito_id}", response_model=CreditoDetalleOut)
def obtener_credito(
    credito_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    coop_id = _validar_lector(usuario)
    credito = db.execute(
        select(Credito)
        .join(SolicitudCredito, Credito.solicitud_credito_id == SolicitudCredito.id)
        .options(
            joinedload(Credito.socio), joinedload(Credito.producto), joinedload(Credito.moneda),
            joinedload(Credito.cuenta_desembolso), joinedload(Credito.usuario),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
            selectinload(Credito.cronograma),
        )
        .where(Credito.id == credito_id, _credito_cooperativa_filter(coop_id))
    ).unique().scalar_one_or_none()
    if credito is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    return _credito_detalle_out(credito)


@router.post("/mora/actualizar")
def actualizar_mora_creditos(
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_evaluador_crediticio(usuario)
    creditos = db.execute(
        select(Credito)
        .options(
            joinedload(Credito.socio), joinedload(Credito.producto),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            selectinload(Credito.cronograma),
        )
        .where(Credito.estado == "VIGENTE", _credito_cooperativa_filter(cooperativa_id))
        .order_by(Credito.id)
    ).unique().scalars().all()
    fecha = date.today()
    estados = []
    for credito in creditos:
        mora = _actualizar_morosidad_credito(db, credito, fecha)
        estados.append(mora.estado)
    registrar_accion(
        db, accion="ACTUALIZAR_MORA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Actualización de mora para {len(creditos)} créditos vigentes",
        request=request,
    )
    db.commit()
    return {
        "actualizados": len(creditos),
        "en_mora": sum(estado == "EN_MORA" for estado in estados),
        "al_dia": sum(estado == "AL_DIA" for estado in estados),
        "fecha": fecha,
    }


@router.get("/mora", response_model=list[MoraCreditoOut])
def listar_morosidad_creditos(
    estado: str | None = Query(None, pattern="^(EN_MORA|AL_DIA)$"),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    consulta = (
        select(Morosidad)
        .join(Credito, Credito.id == Morosidad.credito_id)
        .options(
            joinedload(Morosidad.credito).joinedload(Credito.socio),
            joinedload(Morosidad.credito).joinedload(Credito.moneda),
            joinedload(Morosidad.credito).joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
            joinedload(Morosidad.credito).joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
            joinedload(Morosidad.credito).joinedload(Credito.producto),
            joinedload(Morosidad.credito).joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            joinedload(Morosidad.credito).selectinload(Credito.cronograma),
        )
        .where(_credito_cooperativa_filter(cooperativa_id))
        .order_by(Morosidad.dias_de_retaso.desc(), Credito.id)
    )
    if estado is not None:
        consulta = consulta.where(Morosidad.estado == estado)
    filas = db.execute(consulta).unique().scalars().all()
    fecha = date.today()
    resultado = []
    for mora in filas:
        credito = mora.credito
        solicitud = credito.solicitud
        socio = credito.socio or solicitud.socio
        moneda = credito.moneda or solicitud.moneda
        tasa_mora, dias_gracia = _configuracion_mora(credito)
        resumen = resumir_morosidad(
            cuotas=credito.cronograma, fecha=fecha,
            tasa_mora_anual=tasa_mora, dias_gracia_mora=dias_gracia,
        )
        prediction = db.execute(select(PrediccionDeMorosidad).where(PrediccionDeMorosidad.credito_id == credito.id)).scalar_one_or_none()
        resultado.append({
            "probabilidad_mora": None if prediction is None else prediction.probabilidad_mora,
            "nivel_riesgo": None if prediction is None else prediction.nivel_riesgo,
            "credito_id": credito.id,
            "numero_credito": credito.numero_credito,
            "socio": {
                "id": socio.id,
                "nombre_completo": f"{socio.nombre} {socio.apellido}",
                "ci": socio.ci,
            },
            "estado_mora": mora.estado,
            "dias_de_retaso": mora.dias_de_retaso,
            "monto_penalizado": mora.monto_penalizado,
            "cuotas_vencidas": resumen["cuotas_vencidas"],
            "monto_vencido": resumen["monto_vencido"],
            "saldo_pendiente": credito.saldo_pendiente,
            "moneda": None if moneda is None else MonedaOut.model_validate(moneda),
            "fecha_actualizacion": mora.fecha_actualizacion,
        })
    return resultado


@router.get("/creditos/{credito_id}/deuda", response_model=DeudaCuotaOut)
def obtener_deuda_cuota(
    credito_id: int,
    fecha: date | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    coop_id = _validar_lector(usuario)
    credito = db.execute(
        select(Credito)
        .options(
            joinedload(Credito.socio),
            joinedload(Credito.producto),
            joinedload(Credito.moneda),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
            selectinload(Credito.cronograma),
        )
        .where(Credito.id == credito_id, _credito_cooperativa_filter(coop_id))
    ).unique().scalar_one_or_none()
    if credito is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    if credito.estado != "VIGENTE":
        raise HTTPException(status_code=409, detail="El crédito no está VIGENTE")

    cuota = next(
        (
            row for row in sorted(credito.cronograma, key=lambda row: row.numero_cuota)
            if (row.estado_pago or "PENDIENTE") not in {"PAGADA", "PAGADO"}
        ),
        None,
    )
    if cuota is None:
        raise HTTPException(status_code=409, detail="El crédito no tiene cuotas pendientes")

    producto = credito.producto or credito.solicitud.producto
    mora_datos = calcular_mora(
        capital_cuota=cuota.monto_capital,
        tasa_mora_anual=Decimal("0.00") if producto is None else producto.tasa_mora_anual,
        fecha_vencimiento=cuota.fecha_vencimiento,
        fecha_pago=fecha or date.today(),
        dias_gracia_mora=0 if producto is None else producto.dias_gracia_mora,
    )
    mora = mora_datos["mora"]
    return {
        "credito_id": credito.id,
        "numero_credito": credito.numero_credito,
        "cuota": _cuota_credito_out(cuota),
        "dias_atraso": mora_datos["dias_atraso"],
        "dias_gracia": 0 if producto is None else producto.dias_gracia_mora,
        "en_mora": mora_datos["en_mora"],
        "mora": mora,
        "total_a_pagar": cuota.monto_cuota_total + mora,
        "moneda": None if credito.moneda is None else MonedaOut.model_validate(credito.moneda),
    }


def _configuracion_mora(credito: Credito) -> tuple[Decimal, int]:
    producto = credito.producto or credito.solicitud.producto
    if producto is None:
        return Decimal("0.00"), 0
    return producto.tasa_mora_anual, producto.dias_gracia_mora


def _actualizar_morosidad_credito(db: Session, credito: Credito, fecha: date) -> Morosidad:
    tasa_mora, dias_gracia = _configuracion_mora(credito)
    resumen = resumir_morosidad(
        cuotas=credito.cronograma,
        fecha=fecha,
        tasa_mora_anual=tasa_mora,
        dias_gracia_mora=dias_gracia,
    )
    mora = db.execute(
        select(Morosidad).where(Morosidad.credito_id == credito.id).with_for_update()
    ).scalar_one_or_none()
    if mora is None:
        mora = Morosidad(credito_id=credito.id, dias_de_retaso=0)
        db.add(mora)
    mora.estado = resumen["estado"]
    mora.dias_de_retaso = resumen["dias_de_retaso"]
    mora.monto_penalizado = resumen["monto_penalizado"]
    mora.fecha_actualizacion = func.now()
    return mora


def _pago_out(db: Session, pago: PagoCuota) -> dict:
    credito = pago.credito or db.execute(
        select(Credito)
        .join(TablaAmortizacion, TablaAmortizacion.credito_id == Credito.id)
        .where(TablaAmortizacion.id == pago.tabla_amortizacion_id)
    ).scalar_one()
    solicitud = credito.solicitud
    socio = credito.socio or solicitud.socio
    moneda = credito.moneda or solicitud.moneda
    movimiento = db.execute(
        text("""
            SELECT t.cuenta_ahorro_id, t.declaracion_jurada_uif_id,
                   c.nombre AS caja_nombre
            FROM transaccion t
            LEFT JOIN control_caja cc ON cc.id = t.control_caja_id
            LEFT JOIN caja c ON c.id = cc.caja_id
            WHERE t.id = COALESCE(
                :transaccion_id,
                (SELECT t2.id FROM transaccion t2 WHERE t2.pago_cuota_id = :pago_id
                 ORDER BY t2.id DESC LIMIT 1)
            )
            LIMIT 1
        """),
        {"transaccion_id": pago.transaccion_id, "pago_id": pago.id},
    ).mappings().first()
    cuenta = None
    if movimiento is not None and movimiento["cuenta_ahorro_id"] is not None:
        cuenta_row = db.get(CuentaAhorro, movimiento["cuenta_ahorro_id"])
        if cuenta_row is not None:
            cuenta = {"id": cuenta_row.id, "numero": cuenta_row.numero}
    total = pago.monto_total
    if total is None:
        total = pago.monto_capital + pago.monto_interes_pagado + (pago.monto_mora or Decimal("0.00"))
    return {
        "id": pago.id,
        "numero_recibo": pago.numero_recibo,
        "fecha": pago.fecha or datetime.now(),
        "modalidad": pago.modalidad,
        "credito": {"id": credito.id, "numero_credito": credito.numero_credito},
        "socio": {
            "id": socio.id,
            "nombre_completo": f"{socio.nombre} {socio.apellido}",
            "ci": socio.ci,
        },
        "numero_cuota": pago.cuota.numero_cuota,
        "capital": pago.monto_capital,
        "interes": pago.monto_interes_pagado,
        "mora": pago.monto_mora or Decimal("0.00"),
        "total": total,
        "dias_atraso": pago.dias_atraso or 0,
        "moneda": None if moneda is None else MonedaOut.model_validate(moneda),
        "saldo_pendiente_credito": credito.saldo_pendiente,
        "credito_estado": credito.estado or "VIGENTE",
        "cuenta": cuenta,
        "caja_nombre": None if movimiento is None else movimiento["caja_nombre"],
        "usuario": None if pago.usuario is None else {
            "id": pago.usuario.id,
            "nombre": pago.usuario.nombre,
        },
        "declaracion_uif_id": None if movimiento is None else movimiento["declaracion_jurada_uif_id"],
    }


@router.post(
    "/creditos/{credito_id}/pagos",
    response_model=PagoOut,
    status_code=status.HTTP_201_CREATED,
)
def cobrar_cuota(
    credito_id: int,
    body: PagoCuotaIn,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    rol = usuario.rol.nombre if usuario.rol is not None else None
    if body.modalidad == "EFECTIVO" and rol not in {"CAJERO", "OFICIAL_CREDITO", "ADMINISTRADOR"}:
        raise HTTPException(status_code=403, detail="Operación reservada al personal de caja")
    if body.modalidad == "CUENTA" and rol not in {"OFICIAL_CREDITO", "ADMINISTRADOR"}:
        raise HTTPException(status_code=403, detail="Operación reservada a oficiales de crédito y administradores")

    cooperativa_id = usuario.cooperativa_id
    credito = db.execute(
        select(Credito)
        .options(
            joinedload(Credito.socio), joinedload(Credito.producto), joinedload(Credito.moneda),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
            joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
            selectinload(Credito.cronograma),
        )
        .where(Credito.id == credito_id, _credito_cooperativa_filter(cooperativa_id))
        .with_for_update(of=Credito)
    ).unique().scalar_one_or_none()
    if credito is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    if credito.estado != "VIGENTE":
        raise HTTPException(status_code=409, detail="El crédito no está VIGENTE")
    cuota = next(
        (
            row for row in sorted(credito.cronograma, key=lambda row: row.numero_cuota)
            if (row.estado_pago or "PENDIENTE") not in {"PAGADA", "PAGADO"}
        ),
        None,
    )
    if cuota is None:
        raise HTTPException(status_code=409, detail="El crédito no tiene cuotas pendientes")

    fecha_pago = date.today()
    tasa_mora, dias_gracia = _configuracion_mora(credito)
    mora_datos = calcular_mora(
        capital_cuota=cuota.monto_capital,
        tasa_mora_anual=tasa_mora,
        fecha_vencimiento=cuota.fecha_vencimiento,
        fecha_pago=fecha_pago,
        dias_gracia_mora=dias_gracia,
    )
    mora = mora_datos["mora"]
    total = cuota.monto_cuota_total + mora
    cuenta = None
    control = None
    declaracion_id = None
    fraccionada = False

    if body.modalidad == "CUENTA":
        if body.cuenta_ahorro_id is None:
            raise HTTPException(status_code=400, detail="Debe indicar una cuenta de ahorro")
        cuenta = db.execute(
            select(CuentaAhorro)
            .options(joinedload(CuentaAhorro.moneda))
            .where(
                CuentaAhorro.id == body.cuenta_ahorro_id,
                CuentaAhorro.socio_id == (credito.socio_id or credito.solicitud.socio_id),
                CuentaAhorro.estado == "ACTIVA",
                CuentaAhorro.moneda_id == (credito.moneda_id or credito.solicitud.moneda_id),
            )
            .with_for_update(of=CuentaAhorro)
        ).unique().scalar_one_or_none()
        if cuenta is None:
            raise HTTPException(status_code=400, detail="La cuenta debe estar activa y pertenecer al socio en la misma moneda")
        saldo_minimo = MONTO_MINIMO_APERTURA[cuenta.tipo_producto]
        if cuenta.saldo_disponible - total < saldo_minimo:
            raise HTTPException(
                status_code=400,
                detail=f"Saldo insuficiente: debe mantener un saldo mínimo de {saldo_minimo:.2f}",
            )
    else:
        from app.api.v1.endpoints.caja import _sesion_abierta
        from app.services.uif import exigir_declaracion_si_corresponde

        control = _sesion_abierta(db, usuario, bloquear=True)
        if control.caja.cooperativa_id != cooperativa_id:
            raise HTTPException(status_code=400, detail="El usuario no tiene una sesión de caja abierta")
        moneda = credito.moneda or credito.solicitud.moneda
        fraccionada = exigir_declaracion_si_corresponde(
            db, declaracion=body.declaracion_uif,
            socio_id=credito.socio_id or credito.solicitud.socio_id,
            moneda_id=moneda.id, moneda_iso=moneda.codigo_iso, monto=total,
        )
        if body.declaracion_uif is not None:
            from app.services.uif import registrar_declaracion
            declaracion_id = registrar_declaracion(
                db, declaracion=body.declaracion_uif, tipo_operacion="PAGO_CUOTA",
                monto=total, moneda_id=moneda.id,
                socio_id=credito.socio_id or credito.solicitud.socio_id,
                usuario_id=usuario.id, cooperativa_id=cooperativa_id,
                fraccionada=fraccionada,
            )

    recibo = _siguiente_secuencia(db, cooperativa_id, "PAGO_CUOTA")
    pago = PagoCuota(
        monto_capital=cuota.monto_capital,
        monto_interes_pagado=cuota.monto_interes,
        monto_mora=mora,
        tabla_amortizacion_id=cuota.id,
        credito_id=credito.id,
        cooperativa_id=cooperativa_id,
        numero_recibo=f"REC-{recibo:06d}",
        modalidad=body.modalidad,
        monto_total=total,
        dias_atraso=mora_datos["dias_atraso"],
        usuario_id=usuario.id,
    )
    db.add(pago)
    db.flush()

    cuota.estado_pago = "PAGADA"
    cuota.fecha_pago = func.now()
    cuota.monto_pagado = total
    credito.saldo_pendiente = max(credito.saldo_pendiente - cuota.monto_capital, Decimal("0.00"))
    restantes = [
        row for row in credito.cronograma
        if row.id != cuota.id and (row.estado_pago or "PENDIENTE") not in {"PAGADA", "PAGADO"}
    ]
    if not restantes:
        credito.estado = "CANCELADO"
        garantias_verificadas = db.execute(select(Garantia).where(
            Garantia.solicitud_credito_id == credito.solicitud_credito_id,
            Garantia.cooperativa_id == cooperativa_id,
            Garantia.estado == "VERIFICADA",
        ).with_for_update()).scalars().all()
        _marcar_garantias_liberadas(garantias_verificadas)
        for garantia in garantias_verificadas:
            registrar_accion(db, accion="LIBERAR_GARANTIA", modulo="CREDITOS", usuario_id=usuario.id,
                cooperativa_id=cooperativa_id, descripcion=f"Garantía liberada por cancelación: {garantia.id}", request=request)

    canal = "VENTANILLA" if body.modalidad == "EFECTIVO" else "WEB"
    transaction_id = db.execute(
        text("""
            INSERT INTO transaccion (
                tipo, monto, canal, control_caja_id, moneda_id, cuenta_ahorro_id,
                pago_cuota_id, declaracion_jurada_uif_id, credito_id
            ) VALUES (
                'PAGO_CUOTA', :monto, :canal, :control_id, :moneda_id, :cuenta_id,
                :pago_id, :declaracion_id, :credito_id
            ) RETURNING id
        """),
        {
            "monto": total, "canal": canal,
            "control_id": None if control is None else control.id,
            "moneda_id": (credito.moneda or credito.solicitud.moneda).id,
            "cuenta_id": None if cuenta is None else cuenta.id,
            "pago_id": pago.id, "declaracion_id": declaracion_id,
            "credito_id": credito.id,
        },
    ).scalar_one()
    pago.transaccion_id = transaction_id
    if body.modalidad == "CUENTA":
        cuenta.saldo_disponible -= total
    else:
        control.saldo_sistema += total
    _actualizar_morosidad_credito(db, credito, fecha_pago)
    registrar_accion(
        db, accion="COBRAR_CUOTA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Cobro {pago.numero_recibo} de cuota {cuota.numero_cuota} del crédito {credito.numero_credito}",
        request=request,
    )
    db.flush()
    payload = _pago_out(db, pago)
    db.commit()
    return payload


@router.get("/creditos/{credito_id}/pagos", response_model=list[PagoOut])
def listar_pagos_credito(
    credito_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    coop_id = _validar_lector(usuario)
    credito = db.execute(
        select(Credito).where(
            Credito.id == credito_id, _credito_cooperativa_filter(coop_id)
        )
    ).scalar_one_or_none()
    if credito is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    pagos = db.execute(
        select(PagoCuota)
        .join(TablaAmortizacion, PagoCuota.tabla_amortizacion_id == TablaAmortizacion.id)
        .options(
            joinedload(PagoCuota.cuota), joinedload(PagoCuota.credito),
            joinedload(PagoCuota.usuario),
        )
        .where(or_(PagoCuota.credito_id == credito.id, TablaAmortizacion.credito_id == credito.id))
        .order_by(PagoCuota.fecha, PagoCuota.id)
    ).unique().scalars().all()
    return [_pago_out(db, pago) for pago in pagos]


@router.get("/pagos/{pago_id}", response_model=PagoOut)
def obtener_pago(
    pago_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    coop_id = _validar_lector(usuario)
    pago = db.execute(
        select(PagoCuota)
        .join(TablaAmortizacion, PagoCuota.tabla_amortizacion_id == TablaAmortizacion.id)
        .join(Credito, Credito.id == TablaAmortizacion.credito_id)
        .options(
            joinedload(PagoCuota.cuota), joinedload(PagoCuota.credito),
            joinedload(PagoCuota.usuario),
        )
        .where(PagoCuota.id == pago_id, _credito_cooperativa_filter(coop_id))
    ).unique().scalar_one_or_none()
    if pago is None:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return _pago_out(db, pago)


@router.post(
    "/solicitudes/{solicitud_id}/desembolso",
    response_model=CreditoDetalleOut,
    status_code=status.HTTP_201_CREATED,
)
def desembolsar_solicitud_credito(
    solicitud_id: int,
    body: DesembolsoCreditoIn,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None:
        raise HTTPException(status_code=403, detail="Operación reservada al personal de la cooperativa")
    coop_id = usuario.cooperativa_id
    if body.modalidad == "CUENTA" and usuario.rol.nombre not in {"OFICIAL_CREDITO", "ADMINISTRADOR"}:
        raise HTTPException(status_code=403, detail="Operación reservada a oficiales de crédito y administradores")
    if body.modalidad == "EFECTIVO" and usuario.rol.nombre not in {"CAJERO", "OFICIAL_CREDITO", "ADMINISTRADOR"}:
        raise HTTPException(status_code=403, detail="Operación reservada al personal de caja")

    solicitud = db.execute(
        select(SolicitudCredito)
        .options(joinedload(SolicitudCredito.socio), joinedload(SolicitudCredito.producto),
                 joinedload(SolicitudCredito.moneda))
        .where(SolicitudCredito.id == solicitud_id, SolicitudCredito.cooperativa_id == coop_id)
        .with_for_update(of=SolicitudCredito)
    ).unique().scalar_one_or_none()
    if solicitud is None:
        raise HTTPException(status_code=404, detail="Solicitud de crédito no encontrada")
    if solicitud.estado != "APROBADO":
        raise HTTPException(status_code=409, detail="La solicitud debe estar APROBADO para desembolsar")
    if solicitud.producto is None or solicitud.moneda is None:
        raise HTTPException(status_code=409, detail="La solicitud no tiene producto o moneda")
    if solicitud.producto.cooperativa_id != coop_id or solicitud.socio.cooperativa_id != coop_id:
        raise HTTPException(status_code=404, detail="Solicitud de crédito no encontrada")
    if solicitud.producto.tipo_amortizacion not in {"FRANCES", "ALEMAN"}:
        raise HTTPException(status_code=400, detail="El producto no tiene un tipo de amortización válido")

    hoy = date.today()
    primer_vencimiento = body.fecha_primer_vencimiento
    if primer_vencimiento is not None and not 15 <= (primer_vencimiento - hoy).days <= 45:
        raise HTTPException(status_code=400, detail="El primer vencimiento debe ser entre 15 y 45 días desde el desembolso")

    cuenta = None
    control = None
    fraccionada = False
    if body.modalidad == "CUENTA":
        if body.cuenta_ahorro_id is None:
            raise HTTPException(status_code=400, detail="Debe indicar una cuenta de ahorro")
        cuenta = db.execute(
            select(CuentaAhorro)
            .options(joinedload(CuentaAhorro.moneda), joinedload(CuentaAhorro.socio))
            .where(CuentaAhorro.id == body.cuenta_ahorro_id,
                   CuentaAhorro.socio_id == solicitud.socio_id,
                   CuentaAhorro.estado == "ACTIVA",
                   CuentaAhorro.moneda_id == solicitud.moneda_id)
            .with_for_update(of=CuentaAhorro)
        ).unique().scalar_one_or_none()
        if cuenta is None:
            raise HTTPException(status_code=400, detail="La cuenta debe estar activa y pertenecer al socio en la misma moneda")
    else:
        from app.api.v1.endpoints.caja import _resumen_sesion_arqueo, _sesion_abierta
        from app.services.uif import exigir_declaracion_si_corresponde

        control = _sesion_abierta(db, usuario, bloquear=True)
        if control.caja.cooperativa_id != coop_id:
            raise HTTPException(status_code=400, detail="El usuario no tiene una sesión de caja abierta")
        resumen = _resumen_sesion_arqueo(db, control)
        efectivo = next(
            (moneda.saldo_teorico for moneda in resumen.monedas if moneda.moneda.id == solicitud.moneda_id),
            Decimal("0.00"),
        )
        if solicitud.monto > efectivo:
            raise HTTPException(status_code=400, detail="Efectivo insuficiente en caja")
        fraccionada = exigir_declaracion_si_corresponde(
            db,
            declaracion=body.declaracion_uif,
            socio_id=solicitud.socio_id,
            moneda_id=solicitud.moneda_id,
            moneda_iso=solicitud.moneda.codigo_iso,
            monto=solicitud.monto,
        )

    plan = generar_plan_pagos(
        monto=solicitud.monto,
        tasa_anual=solicitud.tasa_interes,
        plazo_meses=solicitud.plazo_meses,
        tipo_amortizacion=solicitud.producto.tipo_amortizacion,
        fecha_desembolso=hoy,
        fecha_primer_vencimiento=primer_vencimiento,
    )
    correlativo = _siguiente_secuencia(db, coop_id, "CREDITO")
    credito = Credito(
        monto_aprobado=solicitud.monto,
        saldo_pendiente=solicitud.monto,
        estado="VIGENTE",
        solicitud_credito_id=solicitud.id,
        numero_credito=f"CRE-{correlativo:06d}",
        cooperativa_id=coop_id,
        socio_id=solicitud.socio_id,
        producto_credito_id=solicitud.producto_credito_id,
        moneda_id=solicitud.moneda_id,
        tasa_interes=solicitud.tasa_interes,
        plazo_meses=solicitud.plazo_meses,
        tipo_amortizacion=solicitud.producto.tipo_amortizacion,
        fecha_desembolso=hoy,
        modalidad_desembolso=body.modalidad,
        cuenta_desembolso_id=cuenta.id if cuenta else None,
        usuario_id=usuario.id,
    )
    db.add(credito)
    db.flush()
    db.add_all([
        TablaAmortizacion(
            numero_cuota=row["numero"], fecha_vencimiento=row["fecha_vencimiento"],
            monto_capital=row["capital"], monto_interes=row["interes"],
            monto_cuota_total=row["cuota"], estado_pago="PENDIENTE",
            credito_id=credito.id, saldo_inicial=row["saldo_inicial"],
            saldo_final=row["saldo_final"], monto_pagado=Decimal("0.00"),
        ) for row in plan["cuotas"]
    ])

    declaracion_id = None
    if body.modalidad == "EFECTIVO" and body.declaracion_uif is not None:
        from app.services.uif import registrar_declaracion
        declaracion_id = registrar_declaracion(
            db, declaracion=body.declaracion_uif, tipo_operacion="DESEMBOLSO_CREDITO",
            monto=solicitud.monto, moneda_id=solicitud.moneda_id,
            socio_id=solicitud.socio_id, usuario_id=usuario.id,
            cooperativa_id=coop_id, fraccionada=fraccionada,
        )

    if body.modalidad == "CUENTA":
        cuenta.saldo_disponible += solicitud.monto
        tx = db.execute(text("""
            INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id, credito_id)
            VALUES ('DESEMBOLSO_CREDITO', :monto, 'WEB', :moneda_id, :cuenta_id, :credito_id)
            RETURNING id
        """), {"monto": solicitud.monto, "moneda_id": solicitud.moneda_id,
               "cuenta_id": cuenta.id, "credito_id": credito.id}).scalar_one()
    else:
        control.saldo_sistema -= solicitud.monto
        tx = db.execute(text("""
            INSERT INTO transaccion (tipo, monto, canal, control_caja_id, moneda_id,
                retirante_tipo, retirante_nombre, retirante_ci, declaracion_jurada_uif_id, credito_id)
            VALUES ('RETIRO', :monto, 'VENTANILLA', :control_id, :moneda_id,
                'TITULAR', :nombre, :ci, :declaracion_id, :credito_id)
            RETURNING id
        """), {"monto": solicitud.monto, "control_id": control.id,
               "moneda_id": solicitud.moneda_id, "nombre": f"{solicitud.socio.nombre} {solicitud.socio.apellido}",
               "ci": solicitud.socio.ci, "declaracion_id": declaracion_id,
               "credito_id": credito.id}).scalar_one()
    credito.transaccion_desembolso_id = tx
    solicitud.estado = "DESEMBOLSADO"
    registrar_accion(
        db, accion="DESEMBOLSAR_CREDITO", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=coop_id,
        descripcion=f"Desembolso {credito.numero_credito} de {solicitud.monto} en modalidad {body.modalidad}",
        request=request,
    )
    db.flush()
    detalle = db.execute(
        select(Credito)
        .options(joinedload(Credito.socio), joinedload(Credito.producto),
                 joinedload(Credito.moneda), joinedload(Credito.cuenta_desembolso),
                 joinedload(Credito.solicitud), joinedload(Credito.usuario),
                 selectinload(Credito.cronograma))
        .where(Credito.id == credito.id)
    ).unique().scalar_one()
    payload = _credito_detalle_out(detalle)
    db.commit()
    return payload

# Synthetic mora model endpoints (strictly informational; reglas-v1 remains authoritative).
from app.services.modelo_mora import ficha_modelo, predecir_mora


def _features_mora(solicitud, resultado, ahorros, historial):
    ingreso = Decimal(str(solicitud.evaluacion.ingreso_mensual)) if solicitud.evaluacion else Decimal("0")
    cuota_deudas = Decimal(str(solicitud.evaluacion.cuota_deudas_mensual)) if solicitud.evaluacion else Decimal("0")
    ahorro = sum((Decimal(str(a.saldo_disponible)) for a in ahorros if a.estado == "ACTIVA" and a.moneda_id == solicitud.moneda_id), Decimal("0"))
    importe = Decimal(str(solicitud.monto))
    return {
        "ratio_cuota_ingreso": resultado["relacion_cuota_ingreso"] or Decimal("0"),
        "asfi": solicitud.evaluacion.calificacion_asfi if solicitud.evaluacion and solicitud.evaluacion.calificacion_asfi else "A",
        "antiguedad_laboral_meses": solicitud.evaluacion.antiguedad_laboral_meses or 0 if solicitud.evaluacion else 0,
        "endeudamiento": cuota_deudas / ingreso * Decimal("100") if ingreso > 0 else Decimal("0"),
        "antiguedad_socio_meses": max(0, (date.today().year - solicitud.socio.fecha_registro.year) * 12 + date.today().month - solicitud.socio.fecha_registro.month),
        "ahorro_ratio": ahorro / importe * Decimal("100") if importe > 0 else Decimal("0"),
        "atrasos_previos": int(historial.get("atrasos_previos", 0)),
    }


@router.get("/modelo-mora")
def obtener_ficha_modelo_mora(usuario: Usuario = Depends(get_current_user)):
    _validar_lector(usuario)
    return ficha_modelo()


def _refrescar_predicciones_mora(db: Session, credits: list[Credito]) -> dict[str, int]:
    """Shared side-effect-free-with-respect-to-request refresh logic used by both workflows."""
    counts = {"actualizados": 0, "bajo": 0, "medio": 0, "alto": 0}
    for credit in credits:
        req = credit.solicitud
        ev = req.evaluacion if req else None
        if ev is None or credit.socio is None:
            continue
        try:
            installment = _cuota_estimada(Decimal(str(credit.monto_aprobado)), credit.plazo_meses,
                Decimal(str(credit.tasa_interes)), credit.tipo_amortizacion)
        except (ValueError, TypeError):
            continue
        income = Decimal(str(ev.ingreso_mensual or 0))
        ratio = installment / income * Decimal("100") if income else Decimal("0")
        grace = credit.producto.dias_gracia_mora if credit.producto else 0
        late = db.execute(text("SELECT count(*) FROM pago_cuota WHERE credito_id=:id AND dias_atraso > :grace"),
            {"id": credit.id, "grace": grace}).scalar_one()
        savings = db.execute(select(func.coalesce(func.sum(CuentaAhorro.saldo_disponible), 0)).where(
            CuentaAhorro.socio_id == credit.socio_id, CuentaAhorro.moneda_id == credit.moneda_id,
            CuentaAhorro.estado == "ACTIVA")).scalar_one()
        principal = Decimal(str(credit.monto_aprobado or 0))
        savings_ratio = Decimal(str(savings)) / principal * 100 if principal else Decimal("0")
        age = max(0, (date.today().year - credit.socio.fecha_registro.year) * 12 + date.today().month - credit.socio.fecha_registro.month)
        features = {"ratio_cuota_ingreso": ratio, "asfi": ev.calificacion_asfi or "A",
            "antiguedad_laboral_meses": ev.antiguedad_laboral_meses or 0,
            "endeudamiento": Decimal(str(ev.cuota_deudas_mensual or 0)) / income * 100 if income else Decimal("0"),
            "antiguedad_socio_meses": age, "ahorro_ratio": savings_ratio, "atrasos_previos": int(late)}
        predicted = predecir_mora(features)
        row = db.execute(select(PrediccionDeMorosidad).where(PrediccionDeMorosidad.credito_id == credit.id)).scalar_one_or_none()
        if row is None:
            row = PrediccionDeMorosidad(credito_id=credit.id)
            db.add(row)
        row.probabilidad_mora = predicted["probabilidad_mora"]
        row.nivel_riesgo = predicted["nivel_riesgo"]
        row.version_modelo = predicted["version_modelo"]
        row.fecha = func.now()
        counts["actualizados"] += 1
        counts[predicted["nivel_riesgo"].lower()] += 1
    return counts


@router.post("/mora/prediccion")
def actualizar_predicciones_mora(
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_escritor_solicitud(usuario)
    credits = db.execute(select(Credito).options(joinedload(Credito.socio), joinedload(Credito.producto), joinedload(Credito.solicitud).joinedload(SolicitudCredito.evaluacion)).where(Credito.cooperativa_id == cooperativa_id, Credito.estado == "VIGENTE")).unique().scalars().all()
    counts = _refrescar_predicciones_mora(db, credits)
    registrar_accion(db, accion="PREDECIR_MORA", modulo="CREDITOS", usuario_id=usuario.id, cooperativa_id=cooperativa_id, descripcion=f"Predicción informativa de mora actualizada para {counts['actualizados']} créditos vigentes", request=request)
    db.commit()
    return counts


def _alerta_out(db: Session, alerta: AlertaCredito) -> dict:
    credito = db.execute(select(Credito).options(
        joinedload(Credito.socio), joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
        joinedload(Credito.cronograma),
    ).where(Credito.id == alerta.credito_id)).unique().scalar_one()
    socio = credito.socio or credito.solicitud.socio
    cuota = next((item for item in credito.cronograma if item.id == alerta.tabla_amortizacion_id), None)
    usuario = db.get(Usuario, alerta.usuario_cierre_id) if alerta.usuario_cierre_id else None
    gestion = db.get(GestionCobranza, alerta.gestion_id) if alerta.gestion_id else None
    gestion_user = db.get(Usuario, gestion.usuario_id) if gestion and gestion.usuario_id else None
    return {
        "id": alerta.id, "tipo": alerta.tipo, "severidad": alerta.severidad,
        "estado": alerta.estado, "mensaje": alerta.mensaje,
        "credito": {"id": credito.id, "numero_credito": credito.numero_credito},
        "socio": {"id": socio.id, "nombre_completo": f"{socio.nombre} {socio.apellido}", "ci": socio.ci},
        "cuota": None if cuota is None else {"numero": cuota.numero_cuota,
            "fecha_vencimiento": cuota.fecha_vencimiento, "cuota": cuota.monto_cuota_total},
        "datos": alerta.datos or {}, "fecha_creacion": alerta.fecha_creacion,
        "fecha_cierre": alerta.fecha_cierre,
        "usuario_cierre": None if usuario is None else {"id": usuario.id, "nombre": usuario.nombre},
        "comentario_cierre": alerta.comentario_cierre,
        "gestion": None if gestion is None else {"id": gestion.id, "tipo_contacto": gestion.tipo_contacto,
            "resultado_gestion": gestion.resultado_gestion,
            "fecha_compromiso_pago": gestion.fecha_compromiso_pago, "fecha": gestion.fecha,
            "usuario": None if gestion_user is None else {"id": gestion_user.id, "nombre": gestion_user.nombre},
            "alerta_id": gestion.alerta_id},
    }


def _validar_payload_alerta(schema, payload):
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        messages = [error["msg"].removeprefix("Value error, ") for error in exc.errors()]
        detail = "; ".join(messages)
        raise HTTPException(status_code=400, detail=detail) from exc


@router.post("/alertas/monitoreo")
def ejecutar_monitoreo_mora(request: Request, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    coop_id = _validar_evaluador_crediticio(usuario)
    fecha = date.today()
    creditos = db.execute(select(Credito).options(
        joinedload(Credito.socio), joinedload(Credito.producto),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.evaluacion),
        selectinload(Credito.cronograma),
    ).where(Credito.cooperativa_id == coop_id, Credito.estado == "VIGENTE")).unique().scalars().all()
    _refrescar_predicciones_mora(db, creditos)
    ids = [c.id for c in creditos]
    existing = db.execute(select(AlertaCredito).where(
        AlertaCredito.cooperativa_id == coop_id, AlertaCredito.estado.in_(("ACTIVA", "ATENDIDA", "DESCARTADA"))
    )).scalars().all()
    created = resolved = 0
    for credito in creditos:
        mora = _actualizar_morosidad_credito(db, credito, fecha)
        _, grace = _configuracion_mora(credito)
        candidates = construir_candidatos_alerta(credito.cronograma, fecha,
            dias_gracia_mora=grace, en_mora=mora.estado == "EN_MORA")
        prediction = db.execute(select(PrediccionDeMorosidad).where(
            PrediccionDeMorosidad.credito_id == credito.id
        )).scalar_one_or_none()
        if prediction is not None:
            risk_candidate = candidato_riesgo_alto(prediction.nivel_riesgo,
                eligible=credito.solicitud is not None and credito.solicitud.evaluacion is not None,
                probability=prediction.probabilidad_mora)
            if risk_candidate is not None:
                candidates.append(risk_candidate)
        rows = [row for row in existing if row.credito_id == credito.id]
        _, to_resolve, to_create = reconciliar_alertas(rows, candidates)
        for row in to_resolve:
            row.estado = "RESUELTA"; row.fecha_cierre = func.now(); resolved += 1
        for candidate in to_create:
            db.add(AlertaCredito(cooperativa_id=coop_id, credito_id=credito.id,
                tabla_amortizacion_id=candidate["tabla_amortizacion_id"], tipo=candidate["tipo"],
                severidad=candidate["severidad"], mensaje=candidate["mensaje"], datos=candidate["datos"]))
            created += 1
    # Close ACTIVE alerts for credits that became cancelled or otherwise left VIGENTE.
    active_other = db.execute(select(AlertaCredito).join(Credito, Credito.id == AlertaCredito.credito_id)
        .where(AlertaCredito.cooperativa_id == coop_id, AlertaCredito.estado == "ACTIVA", Credito.estado != "VIGENTE")).scalars().all()
    for row in active_other:
        row.estado = "RESUELTA"; row.fecha_cierre = func.now(); resolved += 1
    registrar_accion(db, accion="MONITOREO_MORA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=coop_id, descripcion=f"Monitoreo de mora ejecutado para {len(creditos)} créditos", request=request)
    db.flush()
    counts = {severity: db.execute(select(func.count()).select_from(AlertaCredito).where(
        AlertaCredito.cooperativa_id == coop_id, AlertaCredito.estado == "ACTIVA",
        AlertaCredito.severidad == severity)).scalar_one() for severity in ("INFO", "ADVERTENCIA", "CRITICA")}
    db.commit()
    return {"fecha": fecha, "creditos_monitoreados": len(creditos), "alertas_creadas": created,
        "alertas_resueltas": resolved, "activas_por_severidad": counts}


@router.get("/alertas", response_model=list[AlertaOut])
def listar_alertas_mora(estado: str = Query("ACTIVA"), tipo: str | None = None,
    severidad: str | None = None, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    coop_id = _validar_lector(usuario)
    query = select(AlertaCredito).where(AlertaCredito.cooperativa_id == coop_id, AlertaCredito.estado == estado)
    if tipo: query = query.where(AlertaCredito.tipo == tipo)
    if severidad: query = query.where(AlertaCredito.severidad == severidad)
    rows = db.execute(query.order_by((AlertaCredito.severidad == "CRITICA").desc(), AlertaCredito.fecha_creacion.desc())).scalars().all()
    return [_alerta_out(db, row) for row in rows]


@router.post("/alertas/{alerta_id}/atender", response_model=AlertaOut)
def atender_alerta_mora(alerta_id: int, payload: Any = Body(...), request: Request = None,
    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = _validar_payload_alerta(GestionCreateIn, payload)
    coop_id = _validar_evaluador_crediticio(usuario)
    alerta = db.execute(select(AlertaCredito).where(AlertaCredito.id == alerta_id,
        AlertaCredito.cooperativa_id == coop_id).with_for_update()).scalar_one_or_none()
    if alerta is None: raise HTTPException(status_code=404, detail="Alerta no encontrada")
    if alerta.estado != "ACTIVA": raise HTTPException(status_code=409, detail="La alerta ya no está activa")
    credito = db.get(Credito, alerta.credito_id)
    gestion = GestionCobranza(tipo_contacto=payload.tipo_contacto, resultado_gestion=payload.resultado_gestion,
        fecha_compromiso_pago=payload.fecha_compromiso_pago, credito_id=credito.id,
        cooperativa_id=coop_id, usuario_id=usuario.id, alerta_id=alerta.id)
    db.add(gestion); db.flush()
    alerta.estado = "ATENDIDA"; alerta.fecha_cierre = func.now(); alerta.usuario_cierre_id = usuario.id; alerta.gestion_id = gestion.id
    registrar_accion(db, accion="ATENDER_ALERTA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=coop_id, descripcion=f"Alerta {alerta.id} atendida", request=request)
    db.commit(); db.refresh(alerta)
    return _alerta_out(db, alerta)


@router.post("/alertas/{alerta_id}/descartar", response_model=AlertaOut)
def descartar_alerta_mora(alerta_id: int, payload: Any = Body(...), request: Request = None,
    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = _validar_payload_alerta(DescartarAlertaIn, payload)
    coop_id = _validar_evaluador_crediticio(usuario)
    alerta = db.execute(select(AlertaCredito).where(AlertaCredito.id == alerta_id,
        AlertaCredito.cooperativa_id == coop_id).with_for_update()).scalar_one_or_none()
    if alerta is None: raise HTTPException(status_code=404, detail="Alerta no encontrada")
    if alerta.estado != "ACTIVA": raise HTTPException(status_code=409, detail="La alerta ya no está activa")
    alerta.estado = "DESCARTADA"; alerta.fecha_cierre = func.now(); alerta.usuario_cierre_id = usuario.id
    alerta.comentario_cierre = payload.comentario
    registrar_accion(db, accion="DESCARTAR_ALERTA", modulo="CREDITOS", usuario_id=usuario.id,
        cooperativa_id=coop_id, descripcion=f"Alerta {alerta.id} descartada", request=request)
    db.commit(); db.refresh(alerta)
    return _alerta_out(db, alerta)


@router.get("/creditos/{credito_id}/gestiones", response_model=list[GestionOut])
def listar_gestiones_cobranza(credito_id: int, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    coop_id = _validar_lector(usuario)
    credito = db.execute(select(Credito).where(Credito.id == credito_id, Credito.cooperativa_id == coop_id)).scalar_one_or_none()
    if credito is None: raise HTTPException(status_code=404, detail="Crédito no encontrado")
    rows = db.execute(select(GestionCobranza).where(GestionCobranza.credito_id == credito_id,
        (GestionCobranza.cooperativa_id == coop_id) | (GestionCobranza.cooperativa_id.is_(None))).order_by(GestionCobranza.fecha.desc())).scalars().all()
    return [{"id": row.id, "tipo_contacto": row.tipo_contacto, "resultado_gestion": row.resultado_gestion,
        "fecha_compromiso_pago": row.fecha_compromiso_pago, "fecha": row.fecha,
        "usuario": None if not row.usuario else {"id": row.usuario.id, "nombre": row.usuario.nombre},
        "alerta_id": row.alerta_id} for row in rows]


@router.get("/monitoreo/resumen")
def obtener_resumen_monitoreo_mora(usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    coop_id = _validar_lector(usuario)
    creditos = db.execute(select(Credito).options(
        joinedload(Credito.moneda), joinedload(Credito.socio),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
        selectinload(Credito.cronograma), joinedload(Credito.producto),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
    ).where(Credito.cooperativa_id == coop_id, Credito.estado == "VIGENTE")).unique().scalars().all()
    portfolio = {}
    risk_counts = {key: 0 for key in ("BAJO", "MEDIO", "ALTO", "SIN_PREDICCION")}
    top = []
    for credito in creditos:
        currency_row = credito.moneda or (credito.solicitud.moneda if credito.solicitud else None)
        currency = currency_row.codigo_iso if currency_row else "N/A"
        row = portfolio.setdefault(currency, {"moneda": currency, "cartera_total": Decimal("0.00"), "cartera_en_mora": Decimal("0.00")})
        balance = Decimal(str(credito.saldo_pendiente or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        row["cartera_total"] += balance
        tasa, grace = _configuracion_mora(credito)
        summary = resumir_morosidad(cuotas=credito.cronograma, fecha=date.today(), tasa_mora_anual=tasa, dias_gracia_mora=grace)
        if summary["estado"] == "EN_MORA": row["cartera_en_mora"] += balance
        prediction = db.execute(select(PrediccionDeMorosidad).where(PrediccionDeMorosidad.credito_id == credito.id)).scalar_one_or_none()
        if prediction is None:
            risk_counts["SIN_PREDICCION"] += 1
        else:
            risk_counts[prediction.nivel_riesgo if prediction.nivel_riesgo in risk_counts else "SIN_PREDICCION"] += 1
            socio = credito.socio or credito.solicitud.socio
            top.append({"credito_id": credito.id, "numero_credito": credito.numero_credito,
                "socio": {"id": socio.id, "nombre_completo": f"{socio.nombre} {socio.apellido}"},
                "probabilidad_mora": prediction.probabilidad_mora, "nivel_riesgo": prediction.nivel_riesgo,
                "saldo_pendiente": balance})
    for row in portfolio.values():
        row["cartera_total"] = row["cartera_total"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        row["cartera_en_mora"] = row["cartera_en_mora"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        row["indice_mora"] = (row["cartera_en_mora"] / row["cartera_total"] * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if row["cartera_total"] else Decimal("0.00")
    severities = {severity: db.execute(select(func.count()).select_from(AlertaCredito).where(
        AlertaCredito.cooperativa_id == coop_id, AlertaCredito.estado == "ACTIVA", AlertaCredito.severidad == severity)).scalar_one()
        for severity in ("INFO", "ADVERTENCIA", "CRITICA")}
    last = db.execute(select(func.max(Bitacora.fecha_hora)).where(Bitacora.cooperativa_id == coop_id,
        Bitacora.modulo == "CREDITOS", Bitacora.accion == "MONITOREO_MORA")).scalar_one()
    top.sort(key=lambda item: Decimal(str(item["probabilidad_mora"])), reverse=True)
    for row in portfolio.values():
        row["cartera_total"] = f"{row['cartera_total']:.2f}"
        row["cartera_en_mora"] = f"{row['cartera_en_mora']:.2f}"
        row["indice_mora"] = f"{row['indice_mora']:.2f}"
    for item in top:
        item["saldo_pendiente"] = f"{item['saldo_pendiente']:.2f}"
    return {"por_moneda": list(portfolio.values()), "creditos_por_riesgo": risk_counts,
        "alertas_activas": severities, "top_riesgo": top[:5], "ultima_ejecucion": last}


@router.get("/evaluaciones/{evaluacion_id}/explicacion")
def explicar_evaluacion_mora(evaluacion_id: int, request: Request, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    coop_id = _validar_lector(usuario)
    evaluacion = db.execute(select(EvaluacionCrediticia).where(EvaluacionCrediticia.id == evaluacion_id, EvaluacionCrediticia.cooperativa_id == coop_id)).scalar_one_or_none()
    if evaluacion is None:
        raise HTTPException(status_code=404, detail="Evaluación crediticia no encontrada")
    risk = evaluacion.nivel_riesgo
    reasons = [f.get("motivo", f.get("descripcion", "")) for f in (evaluacion.factores or []) if f.get("motivo")]
    template = f"Dictamen {evaluacion.dictamen} con score {evaluacion.score}. " + " ".join(reasons)
    if risk:
        template += f" El modelo sintético estima riesgo {risk.lower()} de mora."
    result = {"fuente": "PLANTILLA", "texto": template, "nivel_riesgo": risk, "probabilidad_mora": evaluacion.probabilidad_mora}
    import os
    provider, key, model = (os.getenv("LLM_PROVIDER"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL"))
    if provider in {"anthropic", "gemini"} and key and model:
        # Provider data is restricted to rule outcomes, factor codes/points/reasons and synthetic risk only.
        text_out = _llm_explanation(provider, key, model, _llm_payload(evaluacion))
        if text_out:
            result.update(fuente="LLM", texto=text_out)
    registrar_accion(db, accion="EXPLICAR_DICTAMEN", modulo="CREDITOS", usuario_id=usuario.id,
                     cooperativa_id=coop_id, descripcion=f"Explicación de evaluación {evaluacion.id} generada mediante {result['fuente']}", request=request)
    db.commit()
    return result


def _llm_payload(evaluacion):
    def safe_factor(factor):
        code = factor.get("codigo")
        return {"codigo": code, "puntos": factor.get("puntos"), "motivo": f"Factor {code} aplicado" if isinstance(code, str) and code.isascii() and code.replace("_", "").isalnum() else "Factor de evaluación aplicado"}
    def safe_knockout(knockout):
        code = knockout.get("codigo")
        return {"codigo": code, "motivo": "Criterio de elegibilidad no cumplido"}
    return {"dictamen": evaluacion.dictamen, "score": evaluacion.score, "factores": [safe_factor(f) for f in (evaluacion.factores or [])], "knockouts": [safe_knockout(k) for k in (evaluacion.knockouts or [])], "nivel_riesgo": evaluacion.nivel_riesgo}


def _llm_explanation(provider, key, model, payload):
    try:
        import httpx
        prompt = "Explica en español de forma breve y respetuosa este dictamen: " + str(payload)
        if provider == "anthropic":
            response = httpx.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, json={"model": model, "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]}, timeout=15)
            response.raise_for_status()
            text_out = response.json()["content"][0]["text"]
        elif provider == "gemini":
            response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}", json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            response.raise_for_status()
            text_out = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return None
        return text_out.strip() if isinstance(text_out, str) and text_out.strip() else None
    except Exception:
        return None

from datetime import timedelta
from sqlalchemy.orm import joinedload as _joinedload


def _oferta_out(db, offer):
    socio = db.get(Socio, offer.socio_id)
    credit = db.get(Credito, offer.credito_origen_id)
    product = db.get(ProductoCredito, offer.producto_credito_id)
    request = db.get(SolicitudCredito, offer.solicitud_generada_id) if offer.solicitud_generada_id else None
    currency = db.get(Moneda, product.moneda_id) if product else None
    if offer.estado == "VIGENTE" and offer.fecha_vencimiento < date.today():
        offer.estado = "EXPIRADA"
        db.flush()
    return {"id":offer.id,"socio":{"id":socio.id,"nombre_completo":f"{socio.nombre} {socio.apellido}","ci":socio.ci},"credito_origen":{"id":credit.id,"numero_credito":credit.numero_credito,"estado":credit.estado},"producto":{"id":product.id,"codigo":product.codigo,"nombre":product.nombre},"moneda":currency.codigo_iso if currency else None,"monto_sugerido":offer.monto_sugerido,"plazo_meses":offer.plazo_meses,"tasa_interes":offer.tasa_interes,"cuota_estimada":offer.cuota_estimada,"probabilidad_mora":offer.probabilidad_mora,"nivel_riesgo":offer.nivel_riesgo,"motivos":offer.motivos,"estado":offer.estado,"fecha_generacion":offer.fecha_generacion,"fecha_vencimiento":offer.fecha_vencimiento,"solicitud_generada":None if request is None else {"id":request.id,"numero_solicitud":request.numero_solicitud}}


@router.post("/recreditos/generar", response_model=GeneracionRecreditosOut)
def generar_ofertas_recredito(request: Request, usuario: Usuario=Depends(get_current_user), db: Session=Depends(get_db)):
    coop_id=_validar_escritor_solicitud(usuario)
    credits=db.execute(select(Credito).options(joinedload(Credito.socio),joinedload(Credito.producto),joinedload(Credito.solicitud).joinedload(SolicitudCredito.evaluacion)).where(Credito.cooperativa_id==coop_id, Credito.estado.in_(("VIGENTE","CANCELADO")))).unique().scalars().all()
    generated=[]; omitted=0
    for credit in credits:
        product=credit.producto; socio=credit.socio; source=credit.solicitud
        if not product or product.estado!="ACTIVO" or not socio or not source: omitted+=1; continue
        if credit.estado=="VIGENTE" and (credit.monto_aprobado-credit.saldo_pendiente)/credit.monto_aprobado < Decimal("0.70"): omitted+=1; continue
        db.execute(text("UPDATE oferta_recredito SET estado='EXPIRADA' WHERE socio_id=:socio_id AND estado='VIGENTE' AND fecha_vencimiento < :today"), {"socio_id": socio.id, "today": date.today()})
        if db.execute(select(OfertaRecredito.id).where(OfertaRecredito.socio_id==socio.id,OfertaRecredito.estado=="VIGENTE")).scalar_one_or_none(): omitted+=1; continue
        if db.execute(select(SolicitudCredito.id).where(SolicitudCredito.socio_id==socio.id,SolicitudCredito.estado.in_(("PENDIENTE","OBSERVADA","EN_EVALUACION","EN_COMITE")))).scalar_one_or_none(): omitted+=1; continue
        if db.execute(select(Morosidad.id).join(Credito, Credito.id == Morosidad.credito_id).where(Credito.socio_id == socio.id, Morosidad.estado == "EN_MORA")).scalar_one_or_none(): omitted+=1; continue
        late=db.execute(text("SELECT count(*) FROM pago_cuota p JOIN tabla_amortizacion t ON t.id=p.tabla_amortizacion_id WHERE p.credito_id=:cid AND p.dias_atraso > :grace"),{"cid":credit.id,"grace":product.dias_gracia_mora}).scalar_one()
        if late: omitted+=1; continue
        evaluation=db.execute(select(EvaluacionCampo).where(EvaluacionCampo.socio_id==socio.id).order_by(EvaluacionCampo.fecha.desc(),EvaluacionCampo.id.desc())).scalars().first()
        if evaluation is None or evaluation.ingreso_mensual<=0: omitted+=1; continue
        amount=credit.monto_aprobado*(Decimal("1.50") if credit.estado=="CANCELADO" else Decimal("1.25"))
        amount=min(amount,product.monto_max).quantize(Decimal("1"),rounding="ROUND_DOWN")
        amount=(amount/Decimal("100")).to_integral_value(rounding="ROUND_DOWN")*Decimal("100")
        term=min(max(credit.plazo_meses or product.plazo_min_meses,product.plazo_min_meses),product.plazo_max_meses)
        installment=calcular_cuota_inicial(amount,term,product.tasa_interes_anual,product.tipo_amortizacion)
        while amount>=product.monto_min and installment/evaluation.ingreso_mensual*100>product.relacion_cuota_ingreso_max:
            amount-=Decimal("100")
            installment=calcular_cuota_inicial(amount,term,product.tasa_interes_anual,product.tipo_amortizacion)
        if amount<product.monto_min: omitted+=1; continue
        active_savings=db.execute(select(func.coalesce(func.sum(CuentaAhorro.saldo_disponible),0)).where(CuentaAhorro.socio_id==socio.id,CuentaAhorro.moneda_id==credit.moneda_id,CuentaAhorro.estado=="ACTIVA")).scalar_one()
        arrears_count=db.execute(text("SELECT count(*) FROM pago_cuota p JOIN tabla_amortizacion t ON t.id=p.tabla_amortizacion_id WHERE p.credito_id=:cid AND p.dias_atraso > :grace"),{"cid":credit.id,"grace":product.dias_gracia_mora}).scalar_one()
        member_months=max(0,(date.today().year-socio.fecha_registro.year)*12+date.today().month-socio.fecha_registro.month)
        result=predecir_mora({"ratio_cuota_ingreso":installment/evaluation.ingreso_mensual*100,"asfi":evaluation.calificacion_asfi or "A","antiguedad_laboral_meses":evaluation.antiguedad_laboral_meses or 0,"endeudamiento":evaluation.cuota_deudas_mensual/evaluation.ingreso_mensual*100,"antiguedad_socio_meses":member_months,"ahorro_ratio":Decimal(str(active_savings))/amount*100 if amount else Decimal("0"),"atrasos_previos":int(arrears_count)})
        motivo=f"Crédito {credit.numero_credito} {('cancelado' if credit.estado=='CANCELADO' else 'vigente con al menos 70% del principal pagado')} sin atrasos"
        offer=OfertaRecredito(cooperativa_id=coop_id,socio_id=socio.id,credito_origen_id=credit.id,producto_credito_id=product.id,monto_sugerido=amount,plazo_meses=term,tasa_interes=product.tasa_interes_anual,cuota_estimada=installment,probabilidad_mora=result["probabilidad_mora"],nivel_riesgo=result["nivel_riesgo"],motivos=[motivo],fecha_vencimiento=date.today()+timedelta(days=30),usuario_id=usuario.id)
        db.add(offer); db.flush(); generated.append(_oferta_out(db,offer))
    registrar_accion(db,accion="GENERAR_RECREDITOS",modulo="CREDITOS",usuario_id=usuario.id,cooperativa_id=coop_id,descripcion=f"Generadas {len(generated)} ofertas de re-crédito",request=request); db.commit()
    return {"generadas":len(generated),"omitidas":omitted,"ofertas":generated}


@router.get("/recreditos", response_model=list[OfertaOut])
def listar_ofertas_recredito(estado: str|None=Query(None),usuario: Usuario=Depends(get_current_user),db: Session=Depends(get_db)):
    coop_id=_validar_lector(usuario)
    offers=db.execute(select(OfertaRecredito).where(OfertaRecredito.cooperativa_id==coop_id).order_by(OfertaRecredito.fecha_generacion.desc(),OfertaRecredito.id.desc())).scalars().all()
    result=[]
    for offer in offers:
        value=_oferta_out(db,offer)
        if estado is None or value["estado"]==estado: result.append(value)
    db.commit(); return result


@router.post("/recreditos/{oferta_id}/descartar", response_model=OfertaOut)
def descartar_oferta_recredito(oferta_id:int,body:DescartarRecreditoIn,request:Request,usuario:Usuario=Depends(get_current_user),db:Session=Depends(get_db)):
    coop_id=_validar_escritor_solicitud(usuario); reason=body.motivo
    if not isinstance(reason,str) or len(reason.strip())<5: raise HTTPException(status_code=422,detail="El motivo debe contener al menos 5 caracteres")
    offer=db.execute(select(OfertaRecredito).where(OfertaRecredito.id==oferta_id,OfertaRecredito.cooperativa_id==coop_id)).scalar_one_or_none()
    if offer is None: raise HTTPException(status_code=404,detail="Oferta no encontrada")
    if offer.estado!="VIGENTE": raise HTTPException(status_code=409,detail="La oferta no está vigente")
    offer.estado="DESCARTADA"; offer.motivo_descarte=reason.strip()
    registrar_accion(db,accion="DESCARTAR_RECREDITO",modulo="CREDITOS",usuario_id=usuario.id,cooperativa_id=coop_id,descripcion=f"Oferta {offer.id} descartada",request=request); db.commit(); db.refresh(offer); return _oferta_out(db,offer)


@router.post("/recreditos/{oferta_id}/aceptar",status_code=201,response_model=OfertaAceptadaOut)
def aceptar_oferta_recredito(oferta_id:int,request:Request,usuario:Usuario=Depends(get_current_user),db:Session=Depends(get_db)):
    coop_id=_validar_escritor_solicitud(usuario)
    offer=db.execute(select(OfertaRecredito).where(OfertaRecredito.id==oferta_id,OfertaRecredito.cooperativa_id==coop_id).with_for_update()).scalar_one_or_none()
    if offer is None: raise HTTPException(status_code=404,detail="Oferta no encontrada")
    if offer.estado=="VIGENTE" and offer.fecha_vencimiento<date.today(): offer.estado="EXPIRADA"
    if offer.estado!="VIGENTE": raise HTTPException(status_code=409,detail="La oferta no está vigente")
    if db.execute(select(SolicitudCredito.id).where(SolicitudCredito.socio_id==offer.socio_id,SolicitudCredito.estado.in_(("PENDIENTE","OBSERVADA","EN_EVALUACION","EN_COMITE")))).scalar_one_or_none(): raise HTTPException(status_code=409,detail="El socio ya tiene una solicitud en curso")
    socio=db.get(Socio,offer.socio_id); product=db.get(ProductoCredito,offer.producto_credito_id)
    evaluation=db.execute(select(EvaluacionCampo).where(EvaluacionCampo.socio_id==offer.socio_id).order_by(EvaluacionCampo.fecha.desc(),EvaluacionCampo.id.desc())).scalars().first()
    number=_siguiente_secuencia(db,coop_id,"SOLICITUD_CREDITO")
    solicitud=SolicitudCredito(monto=offer.monto_sugerido,plazo_meses=offer.plazo_meses,tasa_interes=offer.tasa_interes,calificacion_asfi=evaluation.calificacion_asfi if evaluation else None,tiene_deudas=bool(evaluation and evaluation.cuota_deudas_mensual>0),estado="PENDIENTE",socio_id=socio.id,usuario_id=usuario.id,evaluacion_campo_id=evaluation.id if evaluation else None,producto_credito_id=product.id,moneda_id=product.moneda_id,numero_solicitud=f"SOL-{number:06d}",destino="OTRO",destino_detalle=f"Re-crédito preaprobado (oferta #{offer.id})",cooperativa_id=coop_id)
    db.add(solicitud); db.flush(); offer.estado="ACEPTADA"; offer.solicitud_generada_id=solicitud.id
    registrar_accion(db,accion="ACEPTAR_RECREDITO",modulo="CREDITOS",usuario_id=usuario.id,cooperativa_id=coop_id,descripcion=f"Oferta de re-crédito {offer.id} aceptada; solicitud {solicitud.id}",request=request)
    db.commit(); db.refresh(offer); solicitud=_obtener_solicitud(db,coop_id,solicitud.id)
    return {"oferta":_oferta_out(db,offer),"solicitud":_solicitud_out(solicitud)}
