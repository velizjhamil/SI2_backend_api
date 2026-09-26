"""Cooperative credit-product catalog (CU-W20)."""

import re
from decimal import Decimal, ROUND_HALF_UP, localcontext
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Date, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_user, get_db, require_admin
from app.api.v1.endpoints.ahorros import _siguiente_secuencia
from app.core.bitacora import registrar_accion
from app.services.scoring import score_application
from app.models.models import (
    CuentaAhorro,
    EvaluacionCampo,
    EvaluacionCrediticia,
    Moneda,
    ProductoCredito,
    Socio,
    SolicitudCredito,
    Usuario,
)
from app.schemas.schemas import (
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
)

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
    "APROBADO",
    "RECHAZADO",
    "DESEMBOLSADO",
    "ANULADA",
}
ESTADOS_EDITABLES_SOLICITUD = {"PENDIENTE", "OBSERVADA"}


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
    with localcontext() as context:
        context.prec = 32
        tasa_mensual = tasa_anual / Decimal("1200")
        if amortizacion == "ALEMAN":
            cuota = monto / Decimal(plazo_meses) + monto * tasa_mensual
        elif tasa_mensual == 0:
            cuota = monto / Decimal(plazo_meses)
        else:
            cuota = monto * tasa_mensual / (Decimal(1) - (Decimal(1) + tasa_mensual) ** (-plazo_meses))
        return _a_centavos(cuota)


def _intereses_estimados(
    monto: Decimal,
    plazo_meses: int,
    tasa_anual: Decimal,
    amortizacion: str,
    cuota: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = 32
        tasa_mensual = tasa_anual / Decimal("1200")
        saldo = monto
        capital_aleman = _a_centavos(monto / Decimal(plazo_meses))
        total_intereses = Decimal("0.00")
        for numero_cuota in range(1, plazo_meses + 1):
            interes = _a_centavos(saldo * tasa_mensual)
            total_intereses += interes
            if numero_cuota == plazo_meses:
                capital = saldo
            elif amortizacion == "ALEMAN":
                capital = capital_aleman
            else:
                capital = cuota - interes
            saldo -= capital
        return _a_centavos(total_intereses)


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
        body.monto, body.plazo_meses, producto.tasa_interes_anual, producto.tipo_amortizacion, cuota
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
            SolicitudCredito.estado.in_(("PENDIENTE", "OBSERVADA", "EN_EVALUACION")),
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
                ) AS tiene_mora_vigente
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
    )
    solicitud.estado = (
        "EN_EVALUACION" if resultado["dictamen"] == "REVISION_MANUAL" else resultado["dictamen"]
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
