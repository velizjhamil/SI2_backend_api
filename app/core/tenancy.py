"""Utilidades de aislamiento multi-tenant.

Todo endpoint de negocio que consulte entidades con `cooperativa_id` debe pasar
su query por `scope_cooperativa` y todo acceso a un recurso individual por
`puede_acceder_recurso`. Los usuarios con `cooperativa_id = NULL` (nivel SaaS,
p. ej. SUPERADMIN) no quedan limitados a ningún tenant.
"""

from sqlalchemy import Select

from app.models.models import Usuario


def scope_cooperativa(query: Select, model, usuario: Usuario) -> Select:
    """Filtra una consulta por la cooperativa del usuario autenticado."""
    if usuario.cooperativa_id is None:
        return query
    return query.where(model.cooperativa_id == usuario.cooperativa_id)


def puede_acceder_recurso(recurso_cooperativa_id: int | None, usuario: Usuario) -> bool:
    """Indica si el usuario puede operar sobre un recurso individual."""
    if usuario.cooperativa_id is None:
        return True
    return recurso_cooperativa_id == usuario.cooperativa_id
