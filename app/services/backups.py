"""Database backup orchestration and external-system adapters."""

import hashlib
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import select

from app.core.bitacora import registrar_accion
from app.db.session import SessionLocal
from app.models.models import Backup, Rol, Usuario

logger = logging.getLogger(__name__)


def sanitize_backup_error(_error: str) -> str:
    """Never persist tool output, which may include connection secrets."""
    return "No se pudo completar el respaldo. Revise la configuración y los registros del servicio."


class PgDumper:
    def dump(self, destination: str, database_url: str) -> None:
        if not database_url:
            raise RuntimeError("Database backup source is not configured")
        safe_url, password = _pg_dump_connection(database_url)
        command = [
            "pg_dump", "--format=custom", "--no-owner", "--no-acl",
            "--file", destination, safe_url,
        ]
        environment = os.environ.copy()
        if password is not None:
            environment["PGPASSWORD"] = password
        try:
            subprocess.run(
                command, check=True, capture_output=True, text=True,
                env=environment, timeout=6 * 60 * 60,
            )
        except subprocess.CalledProcessError as error:
            stderr = _redact_pg_dump_stderr(error.stderr, database_url, password)
            logger.error("pg_dump failed (exit code %s): %s", error.returncode, stderr)
            raise RuntimeError(f"pg_dump failed with exit code {error.returncode}") from None


def _pg_dump_connection(database_url: str) -> tuple[str, str | None]:
    """Move URI credentials into libpq's environment and retain only a safe URI in argv."""
    parts = urlsplit(database_url)
    password = unquote(parts.password) if parts.password is not None else None
    username = unquote(parts.username) if parts.username is not None else None
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    if parts.port is not None:
        hostname = f"{hostname}:{parts.port}"
    authority = f"{quote(username, safe='')}@{hostname}" if username else hostname

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    safe_query = []
    for key, value in query_items:
        if key.lower() in {"password", "pass"}:
            if password is None:
                password = value
        else:
            safe_query.append((key, value))

    scheme = parts.scheme.replace("+psycopg2", "").replace("+asyncpg", "")
    safe_url = urlunsplit((scheme, authority, parts.path, urlencode(safe_query), ""))
    return safe_url, password


def _redact_pg_dump_stderr(stderr, database_url: str, password: str | None) -> str:
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    diagnostic = str(stderr or "").strip()
    diagnostic = diagnostic.replace(database_url, "[DATABASE_URL]")
    if password:
        diagnostic = diagnostic.replace(password, "[REDACTED]")
    diagnostic = re.sub(
        r"(?i)postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s\"']+",
        "[DATABASE_URL]",
        diagnostic,
    )
    diagnostic = re.sub(
        r"(?i)(password\s*[=:]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        diagnostic,
    )
    return diagnostic[:2000] or "No diagnostic details were returned."


class SupabaseStorage:
    def __init__(self, base_url: str, service_key: str, bucket: str):
        self.base_url, self.service_key, self.bucket = base_url.rstrip("/"), service_key, bucket

    def _request(self, method, path, **kwargs):
        headers = {"Authorization": f"Bearer {self.service_key}", "apikey": self.service_key}
        headers.update(kwargs.pop("headers", {}))
        response = httpx.request(
            method, f"{self.base_url}/storage/v1{path}",
            headers=headers,
            timeout=60, **kwargs,
        )
        response.raise_for_status()
        return response

    def upload(self, key: str, source: str) -> None:
        with open(source, "rb") as stream:
            self._request("POST", f"/object/{quote(self.bucket)}/{quote(key, safe='/')}",
                          content=stream, headers={"Content-Type": "application/octet-stream", "x-upsert": "false"})

    def signed_url(self, key: str, expires_in: int) -> str:
        response = self._request(
            "POST", f"/object/sign/{quote(self.bucket)}/{quote(key, safe='/')}",
            json={"expiresIn": expires_in},
        )
        signed = response.json().get("signedURL")
        if not signed:
            raise RuntimeError("Signed URL was not returned")
        return signed if signed.startswith("http") else f"{self.base_url}/storage/v1{signed}"

    def delete(self, key: str) -> None:
        self._request("DELETE", f"/object/{quote(self.bucket)}", json={"prefixes": [key]})


class BackupRepository:
    def __init__(self, db):
        self.db = db

    def stale(self, before):
        return self.db.execute(select(Backup).where(
            Backup.estado == "EN_PROCESO", Backup.iniciado_en < before
        )).scalars().all()

    def running(self):
        return self.db.execute(select(Backup.id).where(Backup.estado == "EN_PROCESO").limit(1)).first()

    def create(self, row):
        self.db.add(row)
        self.db.flush()
        return row

    def save(self, row):
        self.db.add(row)
        self.db.flush()

    def completed_automatic(self, limit):
        return self.db.execute(select(Backup).where(
            Backup.tipo == "AUTOMATICO", Backup.estado == "COMPLETADO"
        ).order_by(Backup.iniciado_en.desc(), Backup.id.desc())).scalars().all()[limit:]

    def delete(self, row):
        self.db.delete(row)

    def audit(self, actor_id, action, description):
        registrar_accion(self.db, accion=action, modulo="BACKUP", usuario_id=actor_id, descripcion=description)


class BackupService:
    def __init__(self, repository, dumper, storage, database_url, retention=7):
        self.repository, self.dumper, self.storage = repository, dumper, storage
        self.database_url, self.retention = database_url, retention

    def execute(self, backup_id, audit_actor_id):
        if audit_actor_id is None:
            raise RuntimeError("Backup execution requires a durable audit actor")
        self._execute_row(backup_id, audit_actor_id)

    def _execute_row(self, backup_id, audit_actor_id):
        with SessionLocal() as db:
            row = db.get(Backup, backup_id)
            if row is None or row.estado != "EN_PROCESO":
                return
            repository = BackupRepository(db)
            actor = audit_actor_id
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".dump") as stream:
                    tmp = stream.name
                self.dumper.dump(tmp, self.database_url)
                digest = hashlib.sha256()
                size = 0
                with open(tmp, "rb") as dump:
                    for chunk in iter(lambda: dump.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                started = row.iniciado_en or datetime.now(timezone.utc)
                key = f"{started:%Y/%m}/si2_{started:%Y%m%dT%H%M%SZ}_{row.tipo.lower()}_{row.id}.dump"
                self.storage.upload(key, tmp)
                row.archivo, row.tamano_bytes, row.checksum_sha256 = key, size, digest.hexdigest()
                row.estado, row.finalizado_en, row.error = "COMPLETADO", datetime.now(timezone.utc), None
                repository.save(row)
                repository.audit(actor, "BACKUP_COMPLETADO", f"Respaldo completado (id={row.id}).")
                db.commit()
                if row.tipo == "AUTOMATICO":
                    try:
                        self._apply_retention(db, repository, actor)
                    except Exception:
                        # The dump itself is complete; do not misreport it as failed
                        # merely because old-object cleanup needs operator attention.
                        db.rollback()
            except Exception as exc:
                db.rollback()
                row = db.get(Backup, backup_id)
                if row is not None:
                    row.estado, row.error, row.finalizado_en = "FALLIDO", sanitize_backup_error(str(exc)), datetime.now(timezone.utc)
                    repository.save(row)
                    repository.audit(actor, "BACKUP_FALLIDO", f"Respaldo fallido (id={row.id}).")
                    db.commit()
            finally:
                if tmp:
                    Path(tmp).unlink(missing_ok=True)

    def _apply_retention(self, db, repository, actor):
        for old in repository.completed_automatic(self.retention):
            if old.archivo:
                self.storage.delete(old.archivo)
            repository.delete(old)
            repository.audit(actor, "BACKUP_RETENCION_ELIMINADA", f"Respaldo automático eliminado por retención (id={old.id}).")
        db.commit()

def _system_actor(db):
    return db.execute(select(Usuario.id).join(Rol).where(Rol.nombre == "SUPERADMIN", Usuario.estado == "ACTIVO").order_by(Usuario.id).limit(1)).scalar_one_or_none()
