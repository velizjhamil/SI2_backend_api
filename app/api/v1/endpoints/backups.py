"""SUPERADMIN backup operations and authenticated scheduled trigger."""

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, require_superadmin
from app.core.config import settings
from app.models.models import Backup, Usuario
from app.services.backups import BackupRepository, BackupService, PgDumper, SupabaseStorage, _system_actor

router = APIRouter()


def _service():
    return BackupService(
        None, PgDumper(),
        SupabaseStorage(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY, settings.BACKUP_BUCKET),
        settings.BACKUP_DATABASE_URL or settings.DATABASE_URL,
        settings.BACKUP_RETENTION_AUTOMATIC,
    )


def _body(row):
    return {
        "id": row.id, "tipo": row.tipo, "estado": row.estado,
        "archivo": row.archivo, "tamano_bytes": row.tamano_bytes,
        "checksum_sha256": row.checksum_sha256, "error": row.error,
        "usuario_id": row.usuario_id, "usuario_nombre": row.usuario.nombre if row.usuario else None,
        "iniciado_en": row.iniciado_en, "finalizado_en": row.finalizado_en,
    }


def _start(db, tipo, user_id):
    # Serialize starts so the concurrency check cannot race across workers.
    db.execute(text("SELECT pg_advisory_xact_lock(608197241)"))
    now = datetime.now(timezone.utc)
    repository = BackupRepository(db)
    audit_actor_id = user_id or _system_actor(db)
    if audit_actor_id is None:
        raise HTTPException(status_code=503, detail="No hay un actor disponible para auditar respaldos")
    for stale in repository.stale(now - timedelta(minutes=settings.BACKUP_STALE_MINUTES)):
        stale.estado, stale.error, stale.finalizado_en = "FALLIDO", "Backup anterior abandonado por timeout.", now
        repository.save(stale)
        repository.audit(audit_actor_id, "BACKUP_FALLIDO", f"Respaldo abandonado por timeout (id={stale.id}).")
    if repository.running():
        db.commit()
        raise HTTPException(status_code=409, detail="Ya existe un respaldo en proceso")
    row = repository.create(Backup(tipo=tipo, estado="EN_PROCESO", usuario_id=user_id))
    repository.audit(audit_actor_id, "BACKUP_INICIADO", f"Respaldo {tipo} iniciado (id={row.id}).")
    db.commit()
    db.refresh(row)
    return row, audit_actor_id


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_manual(background: BackgroundTasks, user: Usuario = Depends(require_superadmin), db: Session = Depends(get_db)):
    row, audit_actor_id = _start(db, "MANUAL", user.id)
    background.add_task(_service().execute, row.id, audit_actor_id)
    return _body(row)


@router.post("/automatico", status_code=status.HTTP_202_ACCEPTED)
def create_automatic(
    background: BackgroundTasks,
    token: str | None = Header(default=None, alias="X-Backup-Token"),
    db: Session = Depends(get_db),
):
    if not settings.BACKUP_CRON_TOKEN:
        raise HTTPException(status_code=503, detail="Backup cron token is not configured")
    if not token or not hmac.compare_digest(token, settings.BACKUP_CRON_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid backup token")
    row, audit_actor_id = _start(db, "AUTOMATICO", None)
    background.add_task(_service().execute, row.id, audit_actor_id)
    return _body(row)


@router.get("")
def list_backups(
    tipo: str | None = None,
    estado: str | None = None,
    user: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    query = select(Backup).order_by(Backup.iniciado_en.desc(), Backup.id.desc())
    if tipo:
        query = query.where(Backup.tipo == tipo)
    if estado:
        query = query.where(Backup.estado == estado)
    return [_body(row) for row in db.execute(query).scalars().all()]


@router.get("/{backup_id}")
def get_backup(backup_id: int, user: Usuario = Depends(require_superadmin), db: Session = Depends(get_db)):
    row = db.get(Backup, backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    return _body(row)


@router.get("/{backup_id}/descarga")
def download_backup(backup_id: int, user: Usuario = Depends(require_superadmin), db: Session = Depends(get_db)):
    row = db.get(Backup, backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    if row.estado != "COMPLETADO" or not row.archivo:
        raise HTTPException(status_code=409, detail="Backup is not completed")
    try:
        url = _service().storage.signed_url(row.archivo, settings.BACKUP_SIGNED_URL_SECONDS)
    except Exception:
        raise HTTPException(status_code=503, detail="Backup download is unavailable")
    return {"url": url, "expira_en": datetime.now(timezone.utc) + timedelta(seconds=settings.BACKUP_SIGNED_URL_SECONDS)}
