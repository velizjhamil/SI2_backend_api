"""Chart-of-accounts endpoints for the MCEF catalog and analytic accounts."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_db
from app.core.bitacora import registrar_accion
from app.models.models import PlanCuenta, Usuario
from app.schemas.schemas import PlanCuentaAnaliticaCreate, PlanCuentaAnaliticaUpdate, PlanCuentaEstadoUpdate

router = APIRouter()
READ_ROLES = {"CONTADOR", "ADMINISTRADOR"}
CLASS_NAMES = {
    1: "ACTIVO", 2: "PASIVO", 3: "PATRIMONIO", 4: "GASTOS", 5: "INGRESOS",
    6: "CONTINGENTES DEUDORAS", 7: "CONTINGENTES ACREEDORAS",
    8: "ORDEN DEUDORAS", 9: "ORDEN ACREEDORAS",
}


def _tenant(user: Usuario) -> int:
    role = user.rol.nombre if user.rol else None
    if role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Operación reservada a CONTADOR o ADMINISTRADOR")
    if user.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _account_query():
    return text("""
        SELECT pc.id, pc.codigo, pc.nombre, pc.nivel, pc.naturaleza,
               pc.es_regularizadora, pc.es_oficial, pc.estado, pc.acepta_movimientos,
               pc.plan_cuenta_padre_id AS padre_id, parent.codigo AS padre_codigo,
               EXISTS (
                   SELECT 1 FROM plan_cuenta analytic
                   WHERE analytic.plan_cuenta_padre_id = pc.id
                     AND analytic.nivel = 5 AND analytic.cooperativa_id = :cooperativa_id
               ) AS tiene_analiticas
        FROM plan_cuenta pc
        LEFT JOIN plan_cuenta parent ON parent.id = pc.plan_cuenta_padre_id
        WHERE (pc.cooperativa_id IS NULL OR pc.cooperativa_id = :cooperativa_id)
    """)


def _visible_row(db: Session, account_id: int, cooperative_id: int):
    return db.execute(
        text("""
            SELECT pc.id, pc.codigo, pc.nombre, pc.nivel, pc.naturaleza,
                   pc.es_regularizadora, pc.es_oficial, pc.estado, pc.acepta_movimientos,
                   pc.plan_cuenta_padre_id AS padre_id, parent.codigo AS padre_codigo,
                   EXISTS (
                       SELECT 1 FROM plan_cuenta analytic
                       WHERE analytic.plan_cuenta_padre_id = pc.id
                         AND analytic.nivel = 5 AND analytic.cooperativa_id = :cooperativa_id
                   ) AS tiene_analiticas,
                   pc.cooperativa_id
            FROM plan_cuenta pc
            LEFT JOIN plan_cuenta parent ON parent.id = pc.plan_cuenta_padre_id
            WHERE pc.id = :account_id
              AND (pc.cooperativa_id IS NULL OR pc.cooperativa_id = :cooperativa_id)
        """),
        {"account_id": account_id, "cooperativa_id": cooperative_id},
    ).mappings().one_or_none()


def _public(row):
    return {
        "id": row["id"], "codigo": row["codigo"], "nombre": row["nombre"],
        "nivel": row["nivel"], "naturaleza": row["naturaleza"],
        "es_regularizadora": row["es_regularizadora"], "es_oficial": row["es_oficial"],
        "estado": row["estado"], "acepta_movimientos": row["acepta_movimientos"],
        "padre_id": row["padre_id"], "padre_codigo": row["padre_codigo"],
        "tiene_analiticas": row["tiene_analiticas"],
    }


@router.get("/plan-cuentas")
def listar_plan_cuentas(
    clase: int | None = Query(None, ge=1, le=9),
    nivel: int | None = Query(None, ge=1, le=5),
    q: str | None = Query(None, max_length=200),
    estado: str | None = Query(None, pattern="^(ACTIVA|INACTIVA)$"),
    solo_movimiento: bool = Query(False),
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    query = _account_query().text
    conditions = []
    params = {"cooperativa_id": cooperative_id}
    if clase is not None:
        conditions.append("pc.codigo LIKE :class_prefix")
        params["class_prefix"] = f"{clase}%"
    if nivel is not None:
        conditions.append("pc.nivel = :nivel")
        params["nivel"] = nivel
    if q:
        conditions.append("(pc.codigo LIKE :code_prefix OR pc.nombre ILIKE :name_query)")
        params["code_prefix"] = f"{q}%"
        params["name_query"] = f"%{q}%"
    if estado:
        conditions.append("pc.estado = :estado")
        params["estado"] = estado
    if solo_movimiento:
        conditions.append("pc.acepta_movimientos IS TRUE")
    if conditions:
        query += " AND " + " AND ".join(conditions)
    query += " ORDER BY pc.codigo"
    return [_public(row) for row in db.execute(text(query), params).mappings().all()]


@router.get("/plan-cuentas/arbol")
def arbol_plan_cuentas(
    clase: int = Query(..., ge=1, le=9),
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    rows = db.execute(
        text(_account_query().text + " AND pc.codigo LIKE :class_prefix ORDER BY pc.codigo"),
        {"cooperativa_id": cooperative_id, "class_prefix": f"{clase}%"},
    ).mappings().all()
    nodes = {row["id"]: {**_public(row), "hijos": []} for row in rows}
    roots = []
    for node in nodes.values():
        parent_id = node["padre_id"]
        if parent_id in nodes:
            nodes[parent_id]["hijos"].append(node)
        elif node["codigo"] == f"{clase}00.00":
            roots.append(node)
    return roots


@router.get("/plan-cuentas/resumen")
def resumen_plan_cuentas(
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    rows = db.execute(
        text("""
            SELECT left(codigo, 1)::int AS clase, nivel, count(*) AS cantidad
            FROM plan_cuenta
            WHERE (cooperativa_id IS NULL OR cooperativa_id = :cooperativa_id)
            GROUP BY left(codigo, 1)::int, nivel
        """),
        {"cooperativa_id": cooperative_id},
    ).mappings().all()
    classes = {
        str(number): {"nombre": name, "total": 0, "niveles": {str(level): 0 for level in range(1, 6)}}
        for number, name in CLASS_NAMES.items()
    }
    for row in rows:
        group = classes[str(row["clase"])]
        group["niveles"][str(row["nivel"])] = row["cantidad"]
        group["total"] += row["cantidad"]
    return {"total": sum(group["total"] for group in classes.values()), "clases": classes}


@router.get("/plan-cuentas/{account_id}")
def detalle_plan_cuenta(
    account_id: int,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    row = _visible_row(db, account_id, cooperative_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    path = []
    current = row
    while current is not None:
        path.append({"codigo": current["codigo"], "nombre": current["nombre"]})
        current = _visible_row(db, current["padre_id"], cooperative_id) if current["padre_id"] else None
    path.reverse()
    movements = db.execute(
        text("""
            SELECT count(*)
            FROM detalle_asiento d
            JOIN comprobante_contable cc ON cc.id = d.comprobante_contable_id
            JOIN transaccion t ON t.id = cc.transaccion_id
            JOIN control_caja ctrl ON ctrl.id = t.control_caja_id
            JOIN caja c ON c.id = ctrl.caja_id
            WHERE d.plan_cuenta_id = :account_id AND c.cooperativa_id = :cooperativa_id
        """),
        {"account_id": account_id, "cooperativa_id": cooperative_id},
    ).scalar_one()
    return {**_public(row), "ruta": path, "movimientos": movements}




OFFICIAL_IMMUTABLE = "Las cuentas oficiales ASFI no se pueden modificar"


def _valid_parent(parent: PlanCuenta) -> None:
    if not parent.es_oficial or parent.nivel != 4 or parent.estado != "ACTIVA":
        raise HTTPException(status_code=422, detail="El padre debe ser una subcuenta oficial ASFI activa")


def _write_account(db: Session, account: PlanCuenta, user: Usuario, action: str, description: str, request: Request):
    db.add(account)
    registrar_accion(
        db, accion=action, modulo="CONTABILIDAD", usuario_id=user.id,
        cooperativa_id=user.cooperativa_id, descripcion=description, request=request,
    )
    db.commit()
    db.refresh(account)


def _get_analytic_for_write(db: Session, account_id: int, cooperative_id: int) -> PlanCuenta:
    account = db.get(PlanCuenta, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    if account.es_oficial:
        raise HTTPException(status_code=403, detail=OFFICIAL_IMMUTABLE)
    if account.cooperativa_id != cooperative_id:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return account


def _movement_query():
    return text("""
        FROM detalle_asiento d
        JOIN comprobante_contable cc ON cc.id = d.comprobante_contable_id
        JOIN transaccion t ON t.id = cc.transaccion_id
        JOIN control_caja ctrl ON ctrl.id = t.control_caja_id
        JOIN caja c ON c.id = ctrl.caja_id
        WHERE d.plan_cuenta_id = :account_id AND c.cooperativa_id = :cooperativa_id
    """)


@router.post("/plan-cuentas", status_code=status.HTTP_201_CREATED)
def crear_cuenta_analitica(
    body: PlanCuentaAnaliticaCreate,
    request: Request,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    parent = db.get(PlanCuenta, body.padre_id)
    if parent is None:
        raise HTTPException(status_code=422, detail="Cuenta padre no válida")
    _valid_parent(parent)
    name = body.nombre.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")
    duplicate = db.execute(
        select(PlanCuenta.id).where(
            PlanCuenta.cooperativa_id == cooperative_id,
            PlanCuenta.plan_cuenta_padre_id == parent.id,
            func.lower(PlanCuenta.nombre) == name.lower(),
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese nombre bajo el padre seleccionado")
    siblings = db.execute(
        select(PlanCuenta.codigo).where(
            PlanCuenta.cooperativa_id == cooperative_id,
            PlanCuenta.plan_cuenta_padre_id == parent.id,
        )
    ).scalars().all()
    suffixes = [int(match.group(1)) for code in siblings if (match := re.fullmatch(re.escape(parent.codigo) + r"\.(\d{2})", code))]
    next_number = max(suffixes, default=0) + 1
    if next_number > 99:
        raise HTTPException(status_code=409, detail="No quedan correlativos analíticos disponibles para esta subcuenta")
    account = PlanCuenta(
        codigo=f"{parent.codigo}.{next_number:02d}", nombre=name, nivel=5, tipo=parent.tipo,
        naturaleza=parent.naturaleza, es_regularizadora=parent.es_regularizadora,
        es_oficial=False, cooperativa_id=cooperative_id, estado="ACTIVA",
        acepta_movimientos=True, descripcion=body.descripcion,
        plan_cuenta_padre_id=parent.id,
    )
    try:
        _write_account(db, account, user, "PLAN_CUENTAS_CREAR_ANALITICA", f"Creó {account.codigo}: {name}", request)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código analítico ya fue asignado; vuelva a intentar")
    return {**_public(_visible_row(db, account.id, cooperative_id)), "cooperativa_id": cooperative_id, "descripcion": account.descripcion}


@router.patch("/plan-cuentas/{account_id}")
def actualizar_cuenta_analitica(
    account_id: int,
    body: PlanCuentaAnaliticaUpdate,
    request: Request,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    account = _get_analytic_for_write(db, account_id, cooperative_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Debe especificar al menos un campo para actualizar")
    if "nombre" in changes:
        if changes["nombre"] is None or not changes["nombre"].strip():
            raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")
        name = changes["nombre"].strip()
        duplicate = db.execute(
            select(PlanCuenta.id).where(
                PlanCuenta.cooperativa_id == cooperative_id,
                PlanCuenta.plan_cuenta_padre_id == account.plan_cuenta_padre_id,
                PlanCuenta.id != account.id,
                func.lower(PlanCuenta.nombre) == name.lower(),
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese nombre bajo el padre seleccionado")
        account.nombre = name
    if "descripcion" in changes:
        account.descripcion = changes["descripcion"]
    _write_account(db, account, user, "PLAN_CUENTAS_MODIFICAR_ANALITICA", f"Modificó {account.codigo}", request)
    return {**_public(_visible_row(db, account.id, cooperative_id)), "cooperativa_id": cooperative_id, "descripcion": account.descripcion}


@router.post("/plan-cuentas/{account_id}/estado")
def cambiar_estado_cuenta_analitica(
    account_id: int,
    body: PlanCuentaEstadoUpdate,
    request: Request,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    account = _get_analytic_for_write(db, account_id, cooperative_id)
    if body.estado == "INACTIVA":
        balance = db.execute(
            text("SELECT COALESCE(SUM(d.debe - d.haber), 0) " + _movement_query().text),
            {"account_id": account.id, "cooperativa_id": cooperative_id},
        ).scalar_one()
        if balance != 0:
            raise HTTPException(status_code=409, detail="No se puede desactivar una cuenta con saldo distinto de cero")
    account.estado = body.estado
    _write_account(db, account, user, "PLAN_CUENTAS_CAMBIAR_ESTADO", f"Estado {account.codigo}: {body.estado}. Motivo: {body.motivo.strip()}", request)
    return {**_public(_visible_row(db, account.id, cooperative_id)), "cooperativa_id": cooperative_id, "descripcion": account.descripcion}


@router.delete("/plan-cuentas/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_cuenta_analitica(
    account_id: int,
    request: Request,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    account = _get_analytic_for_write(db, account_id, cooperative_id)
    movements = db.execute(
        text("SELECT count(*) " + _movement_query().text),
        {"account_id": account.id, "cooperativa_id": cooperative_id},
    ).scalar_one()
    if movements:
        raise HTTPException(status_code=409, detail="La cuenta tiene movimientos; desactívela en lugar de eliminarla")
    code = account.codigo
    registrar_accion(
        db, accion="PLAN_CUENTAS_ELIMINAR_ANALITICA", modulo="CONTABILIDAD",
        usuario_id=user.id, cooperativa_id=cooperative_id,
        descripcion=f"Eliminó {code}", request=request,
    )
    db.delete(account)
    db.commit()
    return None
