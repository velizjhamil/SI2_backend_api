"""UIF configuration and declaration audit endpoints."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_db, require_operaciones, ROLES_OPERACIONES
from app.models.models import Usuario
from app.services.uif import DESTINOS, ORIGENES, UMBRAL_BOB, UMBRAL_USD

router = APIRouter()


def _usuario_cooperativa(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    return usuario.cooperativa_id


@router.get("/configuracion")
def obtener_configuracion_uif(usuario: Usuario = Depends(require_operaciones), db: Session = Depends(get_db)):
    _usuario_cooperativa(usuario)
    return {
        "umbral_bob": f"{UMBRAL_BOB:.2f}",
        "umbral_usd": f"{UMBRAL_USD:.2f}",
        "origenes": [{"codigo": codigo, "descripcion": descripcion} for codigo, descripcion in ORIGENES.items()],
        "destinos": [{"codigo": codigo, "descripcion": descripcion} for codigo, descripcion in DESTINOS.items()],
    }


@router.get("/declaraciones")
def listar_declaraciones_uif(
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    socio_ci: str | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if usuario.rol.nombre not in {"ADMINISTRADOR", "CONTADOR"}:
        raise HTTPException(status_code=403, detail="Operación reservada a cumplimiento")
    cooperativa_id = _usuario_cooperativa(usuario)
    rows = db.execute(
        text("""
            SELECT u.id, u.fecha, u.tipo_operacion, u.monto,
                   m.id AS moneda_id, m.codigo_iso, m.nombre AS moneda_nombre, m.simbolo,
                   s.id AS socio_id, concat_ws(' ', s.nombre, s.apellido) AS nombre_completo,
                   s.ci, u.realizado_por, u.fraccionada,
                   usr.id AS usuario_id, usr.nombre AS usuario_nombre
            FROM declaracion_jurada_uif u
            JOIN moneda m ON m.id = u.moneda_id
            JOIN socio s ON s.id = u.socio_id
            LEFT JOIN usuario usr ON usr.id = u.usuario_id
            WHERE u.cooperativa_id = :cooperativa_id
              AND (:desde IS NULL OR u.fecha::date >= :desde)
              AND (:hasta IS NULL OR u.fecha::date <= :hasta)
              AND (:socio_ci IS NULL OR s.ci = :socio_ci)
            ORDER BY u.fecha DESC, u.id DESC
        """),
        {"cooperativa_id": cooperativa_id, "desde": desde, "hasta": hasta, "socio_ci": socio_ci},
    ).mappings().all()
    return [
        {
            "id": row["id"], "fecha": row["fecha"], "tipo_operacion": row["tipo_operacion"],
            "monto": f"{row['monto']:.2f}",
            "moneda": {"id": row["moneda_id"], "codigo_iso": row["codigo_iso"], "nombre": row["moneda_nombre"], "simbolo": row["simbolo"]},
            "socio": {"id": row["socio_id"], "nombre_completo": row["nombre_completo"], "ci": row["ci"]},
            "realizado_por": row["realizado_por"], "fraccionada": row["fraccionada"],
            "usuario": {"id": row["usuario_id"], "nombre": row["usuario_nombre"]} if row["usuario_id"] else None,
        }
        for row in rows
    ]


@router.get("/declaraciones/{declaracion_id}")
def obtener_declaracion_uif(
    declaracion_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if usuario.rol.nombre not in {"ADMINISTRADOR", "CONTADOR", *ROLES_OPERACIONES}:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    cooperativa_id = _usuario_cooperativa(usuario)
    row = db.execute(
        text("""
            SELECT u.*, m.codigo_iso, m.nombre AS moneda_nombre, m.simbolo,
                   s.id AS socio_pk, concat_ws(' ', s.nombre, s.apellido) AS nombre_completo, s.ci,
                   usr.id AS actor_id, usr.nombre AS actor_nombre,
                   (SELECT t.id FROM transaccion t WHERE t.declaracion_jurada_uif_id = u.id ORDER BY t.id DESC LIMIT 1) AS transaccion_id,
                   (SELECT d.id FROM deposito_plazo_fijo d WHERE d.declaracion_jurada_uif_id = u.id ORDER BY d.id DESC LIMIT 1) AS dpf_id
            FROM declaracion_jurada_uif u
            LEFT JOIN moneda m ON m.id = u.moneda_id
            LEFT JOIN socio s ON s.id = u.socio_id
            LEFT JOIN usuario usr ON usr.id = u.usuario_id
            WHERE u.id = :id AND u.cooperativa_id = :cooperativa_id
        """),
        {"id": declaracion_id, "cooperativa_id": cooperativa_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Declaración UIF no encontrada")
    return {
        "id": row["id"], "fecha": row["fecha"], "tipo_operacion": row["tipo_operacion"],
        "monto": f"{row['monto']:.2f}",
        "moneda": {"id": row["moneda_id"], "codigo_iso": row["codigo_iso"], "nombre": row["moneda_nombre"], "simbolo": row["simbolo"]},
        "socio": {"id": row["socio_pk"], "nombre_completo": row["nombre_completo"], "ci": row["ci"]},
        "origen": {"codigo": row["origen"], "descripcion": ORIGENES.get(row["origen"], row["origen"])},
        "origen_detalle": row["origen_detalle"],
        "destino": {"codigo": row["destino"], "descripcion": DESTINOS.get(row["destino"], row["destino"])},
        "destino_detalle": row["destino_detalle"], "actividad_economica": row["actividad_economica"],
        "realizado_por": row["realizado_por"], "tercero_nombre": row["tercero_nombre"],
        "tercero_ci": row["tercero_ci"], "tercero_parentesco": row["tercero_parentesco"],
        "declara_bajo_juramento": row["declara_bajo_juramento"], "fraccionada": row["fraccionada"],
        "usuario": {"id": row["actor_id"], "nombre": row["actor_nombre"]} if row["actor_id"] else None,
        "transaccion_id": row["transaccion_id"], "dpf_id": row["dpf_id"],
    }
