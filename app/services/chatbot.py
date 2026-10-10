"""Servicio del asistente virtual (chatbot) de la cooperativa.

Construye un contexto con datos **reales** de la base de datos y responde
preguntas del usuario. Si hay un LLM configurado (ver `app.services.llm`) se
usa para redactar; en caso contrario se devuelve una respuesta determinista
basada en plantillas. El asistente nunca inventa cifras: sólo describe el
contexto calculado.
"""

import unicodedata
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import (
    Caja,
    ControlCaja,
    Credito,
    CuentaAhorro,
    DepositoPlazoFijo,
    ProductoCredito,
    Socio,
    TablaAmortizacion,
    Usuario,
)
from app.services import llm

MENSAJE_AYUDA = (
    "Soy el asistente de la cooperativa. Puedo ayudarte con:\n"
    "• Estado de socios y clientes\n"
    "• Cartera de créditos, colocaciones y tasas por producto\n"
    "• Morosidad (cuotas vencidas)\n"
    "• Captaciones de ahorro y depósitos a plazo fijo\n"
    "• Cierre / estado de caja\n"
    "• Preguntas de precalificación de crédito\n"
    "Pregúntame en lenguaje natural, por ejemplo: \"¿cómo está la mora?\"."
)

MENSAJE_AYUDA_SOCIO = (
    "Soy el asistente de la cooperativa. Puedo ayudarte con:\n"
    "• Saldos de tus cuentas de ahorro\n"
    "• Estado de tus créditos y cuotas vencidas\n"
    "• Tus depósitos a plazo fijo\n"
    "• Cómo se evalúa una solicitud de crédito (precalificación)\n"
    "Pregúntame en lenguaje natural, por ejemplo: \"¿cuál es mi saldo?\"."
)


def _norm(texto: str) -> str:
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
    return sin_acentos


def _f(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _money(value) -> str:
    return f"{_f(value):,.2f}".replace(",", " ")


def _es_superadmin(usuario: Usuario) -> bool:
    return bool(usuario.rol and usuario.rol.nombre == "SUPERADMIN")


def _es_socio(usuario: Usuario) -> bool:
    return bool(usuario.rol and usuario.rol.nombre == "SOCIO")


def _socio_de_usuario(db: Session, usuario: Usuario) -> Socio | None:
    if not _es_socio(usuario):
        return None
    return db.execute(select(Socio).where(Socio.usuario_id == usuario.id)).scalar_one_or_none()


def contexto_cooperativa(db: Session, usuario: Usuario) -> dict:
    """Métricas de la cooperativa del usuario (o globales para SUPERADMIN)."""
    coop_id = None if _es_superadmin(usuario) else usuario.cooperativa_id
    hoy = date.today()

    socios_q = select(
        func.count(Socio.id),
        func.count(Socio.id).filter(Socio.estado == "ACTIVO"),
    )
    if coop_id is not None:
        socios_q = socios_q.where(Socio.cooperativa_id == coop_id)
    total_socios, socios_activos = db.execute(socios_q).one()

    cuentas_q = select(
        func.count(CuentaAhorro.id),
        func.coalesce(func.sum(CuentaAhorro.saldo_disponible), 0),
    ).where(CuentaAhorro.estado == "ACTIVA")
    if coop_id is not None:
        cuentas_q = cuentas_q.join(Socio, Socio.id == CuentaAhorro.socio_id).where(
            Socio.cooperativa_id == coop_id
        )
    cuentas_activas, saldo_ahorro = db.execute(cuentas_q).one()

    creditos_q = select(
        func.count(Credito.id),
        func.coalesce(func.sum(Credito.saldo_pendiente), 0),
        func.coalesce(func.sum(Credito.monto_aprobado), 0),
    ).where(Credito.estado == "VIGENTE")
    if coop_id is not None:
        creditos_q = creditos_q.where(Credito.cooperativa_id == coop_id)
    creditos_vigentes, cartera, colocaciones = db.execute(creditos_q).one()

    mora_base = (
        select(
            func.count(TablaAmortizacion.id),
            func.coalesce(func.sum(TablaAmortizacion.monto_cuota_total - TablaAmortizacion.monto_pagado), 0),
        )
        .join(Credito, Credito.id == TablaAmortizacion.credito_id)
        .where(
            TablaAmortizacion.fecha_vencimiento < hoy,
            TablaAmortizacion.estado_pago != "PAGADO",
        )
    )
    if coop_id is not None:
        mora_base = mora_base.where(Credito.cooperativa_id == coop_id)
    cuotas_mora, monto_mora = db.execute(mora_base).one()

    mora_creditos_q = (
        select(func.count(func.distinct(Credito.id)))
        .join(TablaAmortizacion, TablaAmortizacion.credito_id == Credito.id)
        .where(
            TablaAmortizacion.fecha_vencimiento < hoy,
            TablaAmortizacion.estado_pago != "PAGADO",
        )
    )
    if coop_id is not None:
        mora_creditos_q = mora_creditos_q.where(Credito.cooperativa_id == coop_id)
    creditos_en_mora = db.execute(mora_creditos_q).scalar_one()

    dpf_q = select(
        func.count(DepositoPlazoFijo.id),
        func.coalesce(func.sum(DepositoPlazoFijo.monto), 0),
    ).where(DepositoPlazoFijo.estado == "VIGENTE")
    if coop_id is not None:
        dpf_q = dpf_q.where(DepositoPlazoFijo.cooperativa_id == coop_id)
    dpf_vigentes, monto_dpf = db.execute(dpf_q).one()

    caja_q = select(func.count(ControlCaja.id)).where(ControlCaja.estado == "ABIERTA")
    if coop_id is not None:
        caja_q = caja_q.join(Caja, Caja.id == ControlCaja.caja_id).where(Caja.cooperativa_id == coop_id)
    cajas_abiertas = db.execute(caja_q).scalar_one()

    usuarios_q = select(func.count(Usuario.id)).where(Usuario.estado == "ACTIVO")
    if coop_id is not None:
        usuarios_q = usuarios_q.where(Usuario.cooperativa_id == coop_id)
    usuarios_activos = db.execute(usuarios_q).scalar_one()

    productos_q = select(ProductoCredito).where(ProductoCredito.estado == "ACTIVO")
    if coop_id is not None:
        productos_q = productos_q.where(ProductoCredito.cooperativa_id == coop_id)
    productos = db.execute(productos_q.limit(20)).scalars().all()

    return {
        "alcance": "GLOBAL" if coop_id is None else "COOPERATIVA",
        "socios": {"total": total_socios, "activos": socios_activos},
        "ahorro": {"cuentas_activas": cuentas_activas, "saldo_total": _f(saldo_ahorro)},
        "creditos": {
            "vigentes": creditos_vigentes,
            "cartera_saldo_pendiente": _f(cartera),
            "colocaciones_monto_aprobado": _f(colocaciones),
        },
        "mora": {
            "cuotas_vencidas": cuotas_mora,
            "monto_vencido": _f(monto_mora),
            "creditos_en_mora": creditos_en_mora,
        },
        "dpf": {"vigentes": dpf_vigentes, "monto_total": _f(monto_dpf)},
        "caja": {"sesiones_abiertas": cajas_abiertas},
        "usuarios_activos": usuarios_activos,
        "productos": [
            {
                "codigo": p.codigo,
                "nombre": p.nombre,
                "tasa_interes_anual": _f(p.tasa_interes_anual),
                "tasa_mora_anual": _f(p.tasa_mora_anual),
                "monto_min": _f(p.monto_min),
                "monto_max": _f(p.monto_max),
                "plazo_min_meses": p.plazo_min_meses,
                "plazo_max_meses": p.plazo_max_meses,
            }
            for p in productos
        ],
    }


def contexto_socio(db: Session, usuario: Usuario) -> dict:
    """Métricas personales del socio autenticado."""
    hoy = date.today()
    socio = _socio_de_usuario(db, usuario)
    if socio is None:
        return {"es_socio": True, "socio": None, "nota": "No se encontró un perfil de socio asociado a tu usuario."}

    cuentas = db.execute(
        select(CuentaAhorro).where(CuentaAhorro.socio_id == socio.id)
    ).scalars().all()
    creditos = db.execute(
        select(Credito).where(Credito.socio_id == socio.id)
    ).scalars().all()
    dpf = db.execute(
        select(DepositoPlazoFijo).where(
            DepositoPlazoFijo.socio_id == socio.id,
            DepositoPlazoFijo.estado == "VIGENTE",
        )
    ).scalars().all()

    cuotas_mora = (
        select(
            func.count(TablaAmortizacion.id),
            func.coalesce(func.sum(TablaAmortizacion.monto_cuota_total - TablaAmortizacion.monto_pagado), 0),
        )
        .join(Credito, Credito.id == TablaAmortizacion.credito_id)
        .where(
            Credito.socio_id == socio.id,
            TablaAmortizacion.fecha_vencimiento < hoy,
            TablaAmortizacion.estado_pago != "PAGADO",
        )
    )
    n_mora, monto_mora = db.execute(cuotas_mora).one()

    proxima = db.execute(
        select(TablaAmortizacion)
        .join(Credito, Credito.id == TablaAmortizacion.credito_id)
        .where(
            Credito.socio_id == socio.id,
            TablaAmortizacion.estado_pago != "PAGADO",
            TablaAmortizacion.fecha_vencimiento >= hoy,
        )
        .order_by(TablaAmortizacion.fecha_vencimiento.asc())
        .limit(1)
    ).scalar_one_or_none()

    return {
        "es_socio": True,
        "socio": {
            "nombre": f"{socio.nombre} {socio.apellido}",
            "ci": socio.ci,
            "estado": socio.estado,
            "antiguedad_dias": (hoy - socio.fecha_registro).days if socio.fecha_registro else None,
        },
        "cuentas": [
            {
                "numero": c.numero,
                "tipo": c.tipo_producto,
                "saldo_disponible": _f(c.saldo_disponible),
                "estado": c.estado,
            }
            for c in cuentas
        ],
        "ahorro_total": _f(sum((c.saldo_disponible for c in cuentas), Decimal("0"))),
        "creditos": [
            {
                "numero": c.numero_credito,
                "estado": c.estado,
                "monto_aprobado": _f(c.monto_aprobado),
                "saldo_pendiente": _f(c.saldo_pendiente),
                "tasa_interes": _f(c.tasa_interes),
                "plazo_meses": c.plazo_meses,
            }
            for c in creditos
        ],
        "mora": {"cuotas_vencidas": n_mora, "monto_vencido": _f(monto_mora)},
        "proxima_cuota": (
            {
                "numero_cuota": proxima.numero_cuota,
                "fecha_vencimiento": proxima.fecha_vencimiento.isoformat(),
                "monto_cuota_total": _f(proxima.monto_cuota_total),
            }
            if proxima
            else None
        ),
        "dpf": [{"monto": _f(d.monto), "estado": d.estado, "fecha_vencimiento": d.fecha_vencimiento.isoformat()} for d in dpf],
    }


def _plantilla_cooperativa(intent: str, ctx: dict, mensaje: str) -> str:
    s = ctx["socios"]
    a = ctx["ahorro"]
    c = ctx["creditos"]
    m = ctx["mora"]
    d = ctx["dpf"]
    if intent == "socios":
        return f"Actualmente hay {s['total']} socios registrados, de los cuales {s['activos']} están activos."
    if intent == "ahorro":
        return f"Hay {a['cuentas_activas']} cuentas de ahorro activas con un saldo total de {_money(a['saldo_total'])}."
    if intent == "mora":
        return (
            f"Hay {m['cuotas_vencidas']} cuotas vencidas sin pagar que afectan a "
            f"{m['creditos_en_mora']} créditos, por un monto vencido de {_money(m['monto_vencido'])}."
        )
    if intent == "dpf":
        return f"Existen {d['vigentes']} depósitos a plazo fijo vigentes por un total de {_money(d['monto_total'])}."
    if intent == "caja":
        return f"Hay {ctx['caja']['sesiones_abiertas']} sesión(es) de caja abierta(s) en este momento."
    if intent == "usuarios":
        return f"Hay {ctx['usuarios_activos']} usuarios activos en el sistema."
    if intent == "productos":
        if not ctx["productos"]:
            return "No hay productos de crédito activos registrados."
        lineas = [
            f"• {p['nombre']} ({p['codigo']}): interés {p['tasa_interes_anual']:.2f}% anual, "
            f"mora {p['tasa_mora_anual']:.2f}% anual, montos {_money(p['monto_min'])}–{_money(p['monto_max'])}, "
            f"plazos {p['plazo_min_meses']}–{p['plazo_max_meses']} meses"
            for p in ctx["productos"]
        ]
        return "Productos de crédito activos:\n" + "\n".join(lineas)
    if intent == "precalificacion":
        return (
            "Una solicitud de crédito se evalúa con reglas (scoring) que consideran: capacidad de pago "
            "(ingresos − egresos − cuotas de deudas), calificación ASFI, antigüedad laboral, "
            "antigüedad como socio, historial de crédito y mora actual, y el saldo de ahorro como respaldo. "
            "Cada producto define su relación cuota/ingreso máxima. "
            f"En este momento la cooperativa tiene {c['vigentes']} créditos vigentes por una cartera de "
            f"{_money(c['cartera_saldo_pendiente'])} y {m['cuotas_vencidas']} cuotas vencidas."
        )
    if intent == "creditos":
        return (
            f"La cooperativa tiene {c['vigentes']} créditos vigentes. La cartera (saldo por cobrar) es de "
            f"{_money(c['cartera_saldo_pendiente'])} y el monto total aprobado (colocaciones) es de "
            f"{_money(c['colocaciones_monto_aprobado'])}. Hay {m['creditos_en_mora']} créditos con cuotas vencidas."
        )
    return MENSAJE_AYUDA


def _plantilla_socio(intent: str, ctx: dict, mensaje: str) -> str:
    socio = ctx.get("socio")
    if socio is None:
        return ctx.get("nota", MENSAJE_AYUDA_SOCIO)
    if intent == "ahorro":
        return (
            f"{socio['nombre']}, tus cuentas de ahorro suman {_money(ctx['ahorro_total'])} "
            f"({len(ctx['cuentas'])} cuenta(s))."
        )
    if intent == "mora":
        m = ctx["mora"]
        if m["cuotas_vencidas"] == 0:
            return f"{socio['nombre']}, no tienes cuotas vencidas. ¡Buen historial de pago!"
        return (
            f"{socio['nombre']}, tienes {m['cuotas_vencidas']} cuota(s) vencida(s) por "
            f"{_money(m['monto_vencido'])}. Te recomendamos regularizarlas."
        )
    if intent == "dpf":
        if not ctx["dpf"]:
            return f"{socio['nombre']}, no tienes depósitos a plazo fijo vigentes."
        return f"{socio['nombre']}, tienes {len(ctx['dpf'])} depósito(s) a plazo fijo vigente(s)."
    if intent == "precalificacion":
        m = ctx["mora"]
        return (
            f"{socio['nombre']}, para precalificar tu solicitud se evalúan tu capacidad de pago, tu historial "
            f"(actualmente {m['cuotas_vencidas']} cuota(s) vencida(s)), tu antigüedad como socio "
            f"({socio['antiguedad_dias']} días) y tu ahorro ({_money(ctx['ahorro_total'])}). "
            "Cuanto mayor sea tu ahorro y más limpio tu historial, mejor tu evaluación."
        )
    if intent == "creditos" or intent == "cuota":
        if not ctx["creditos"]:
            return f"{socio['nombre']}, no tienes créditos registrados."
        partes = [
            f"Crédito {c['numero'] or '-'}: estado {c['estado']}, saldo {_money(c['saldo_pendiente'])} "
            f"de {_money(c['monto_aprobado'])}"
            for c in ctx["creditos"]
        ]
        extra = ""
        if ctx["proxima_cuota"]:
            p = ctx["proxima_cuota"]
            extra = f" Tu próxima cuota (#{p['numero_cuota']}) vence el {p['fecha_vencimiento']} por {_money(p['monto_cuota_total'])}."
        return f"{socio['nombre']}:\n" + "\n".join(partes) + extra
    return (
        f"{socio['nombre']}, tu ahorro total es {_money(ctx['ahorro_total'])} y tienes "
        f"{len(ctx['creditos'])} crédito(s). ¿Quieres saber sobre saldos, cuotas o precalificación?"
    )


_INTENTS = (
    ("mora", ("mora", "moro", "vencid", "atras", "impag", "deuda atrasad")),
    ("precalificacion", ("precalific", "precalificar", "evaluacion de credito", "scoring", "puedo sacar", "me prestan", "riesgo crediticio")),
    ("mora_cuota_socio", ("mi cuota", "proxima cuota")),
    ("caja", ("caja", "arqueo", "cierre", "ventanilla")),
    ("dpf", ("plazo fijo", "dpf", "certificado de deposito", "deposito a plazo")),
    ("ahorro", ("ahorro", "saldo", "cuenta", "captacion", "ahorros")),
    ("productos", ("producto", "tasas", "tasa de interes", "interes", "planes de credito", "lineas de credito")),
    ("socios", ("socio", "socios", "cliente", "miembro", "afiliad", "asociad")),
    ("creditos", ("credito", "creditos", "cartera", "colocacion", "prestamo", "desembols", "prestamos")),
    ("usuarios", ("usuario", "usuarios", "accesos")),
    ("ayuda", ("ayuda", "help", "hola", "buenos dias", "buenas tardes", "buenas noches", "que puedes hacer", "opciones")),
)


def _detectar_intent(mensaje: str, es_socio: bool) -> str:
    texto = _norm(mensaje)
    for intent, claves in _INTENTS:
        for clave in claves:
            if clave in texto:
                if es_socio and intent == "creditos":
                    return "creditos"
                if es_socio and intent == "mora":
                    return "mora"
                if es_socio and intent == "mora_cuota_socio":
                    return "cuota"
                return intent
    return "ayuda" if len(texto.strip()) < 4 else ("creditos" if es_socio else "general")


def _contexto_texto(ctx: dict) -> str:
    if ctx.get("es_socio"):
        socio = ctx.get("socio")
        if socio is None:
            return ctx.get("nota", "")
        return (
            f"Socio: {socio['nombre']} (CI {socio['ci']}, estado {socio['estado']}, "
            f"{socio['antiguedad_dias']} días de antigüedad). "
            f"Ahorro total: {ctx['ahorro_total']:.2f}. "
            f"Créditos: {len(ctx['creditos'])}. "
            f"Cuotas vencidas: {ctx['mora']['cuotas_vencidas']} por {ctx['mora']['monto_vencido']:.2f}. "
            f"Próxima cuota: {ctx['proxima_cuota']}. "
            f"DPF: {len(ctx['dpf'])}."
        )
    return (
        f"Alcance: {ctx['alcance']}. Socios: {ctx['socios']['total']} ({ctx['socios']['activos']} activos). "
        f"Ahorro: {ctx['ahorro']['cuentas_activas']} cuentas activas, saldo {ctx['ahorro']['saldo_total']:.2f}. "
        f"Créditos vigentes: {ctx['creditos']['vigentes']}; cartera {ctx['creditos']['cartera_saldo_pendiente']:.2f}; "
        f"colocaciones {ctx['creditos']['colocaciones_monto_aprobado']:.2f}. "
        f"Mora: {ctx['mora']['cuotas_vencidas']} cuotas por {ctx['mora']['monto_vencido']:.2f} en "
        f"{ctx['mora']['creditos_en_mora']} créditos. "
        f"DPF: {ctx['dpf']['vigentes']} por {ctx['dpf']['monto_total']:.2f}. "
        f"Cajas abiertas: {ctx['caja']['sesiones_abiertas']}. Usuarios activos: {ctx['usuarios_activos']}. "
        f"Productos de crédito: {ctx['productos']}."
    )


def responder(db: Session, usuario: Usuario, mensaje: str) -> dict:
    es_socio = _es_socio(usuario)
    ctx = contexto_socio(db, usuario) if es_socio else contexto_cooperativa(db, usuario)
    intent = _detectar_intent(mensaje, es_socio)

    if intent in {"ayuda", "general"}:
        plantilla = MENSAJE_AYUDA_SOCIO if es_socio else MENSAJE_AYUDA
    else:
        plantilla = _plantilla_socio(intent, ctx, mensaje) if es_socio else _plantilla_cooperativa(intent, ctx, mensaje)

    fuente = "PLANTILLA"
    respuesta = plantilla
    if llm.llm_configurado():
        system = (
            "Eres el asistente virtual de una cooperativa de ahorro y crédito. "
            "Responde en español, de forma breve y profesional. Usa EXCLUSIVAMENTE los datos del contexto; "
            "si el dato no está en el contexto, dilo y no inventes cifras."
        )
        prompt = (
            f"Contexto financiero actual:\n{_contexto_texto(ctx)}\n\n"
            f"Pregunta del usuario: {mensaje}\n\n"
            f"Respuesta sugerida (puedes reformular, sin inventar datos): {plantilla}"
        )
        generado = llm.generar_texto(system, prompt)
        if generado:
            respuesta = generado
            fuente = "LLM"

    return {
        "respuesta": respuesta,
        "intent": intent,
        "fuente": fuente,
        "datos": ctx,
        "sugerencias": _sugerencias(es_socio),
    }


def _sugerencias(es_socio: bool) -> list[str]:
    if es_socio:
        return ["¿Cuál es mi saldo?", "¿Tengo cuotas vencidas?", "¿Cómo precalifico un crédito?"]
    return ["¿Cómo está la mora?", "¿Cuántos socios activos hay?", "Productos de crédito", "Estado de caja"]
