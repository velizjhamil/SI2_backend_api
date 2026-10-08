"""Isolated backup service and API contract tests; external systems are faked."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import hashlib


def test_backup_settings_have_safe_defaults():
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.BACKUP_BUCKET == "backups"
    assert settings.BACKUP_RETENTION_AUTOMATIC == 7
    assert settings.BACKUP_STALE_MINUTES == 60
    assert settings.BACKUP_SIGNED_URL_SECONDS == 300


def test_sanitize_error_does_not_expose_database_credentials():
    from app.services.backups import sanitize_backup_error

    result = sanitize_backup_error(
        "failed postgresql://admin:secret@db.example:5432/prod?password=also-secret"
    )
    assert "secret" not in result
    assert "db.example" not in result


def _background_fakes(monkeypatch, row, *, candidates=None, storage_error=None):
    from app.services import backups

    candidates = candidates if candidates is not None else [row]
    state = {
        "audits": [], "uploaded": {}, "deleted": [], "deleted_objects": [],
        "paths": [], "commits": 0, "rollbacks": 0,
    }

    class FakeDb:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, _model, _id): return row
        def commit(self): state["commits"] += 1
        def rollback(self): state["rollbacks"] += 1

    class FakeRepository:
        def __init__(self, _db): pass
        def save(self, _row): pass
        def audit(self, actor, action, _description): state["audits"].append((actor, action))
        def completed_automatic(self, limit):
            completed = [item for item in candidates if item.tipo == "AUTOMATICO" and item.estado == "COMPLETADO"]
            completed.sort(key=lambda item: (item.iniciado_en, item.id), reverse=True)
            return completed[limit:]
        def delete(self, item): state["deleted"].append(item)
        def delete(self, item): state["deleted"].append(item)

    class FakeDumper:
        def dump(self, destination, _database_url):
            state["paths"].append(destination)
            Path(destination).write_bytes(b"backup payload")

    class FakeStorage:
        def upload(self, key, path):
            if storage_error:
                raise RuntimeError(storage_error)
            state["uploaded"][key] = Path(path).read_bytes()
        def delete(self, key): state["deleted_objects"].append(key)

    monkeypatch.setattr(backups, "SessionLocal", FakeDb)
    monkeypatch.setattr(backups, "BackupRepository", FakeRepository)
    monkeypatch.setattr(backups, "_system_actor", lambda _db: None)
    return state, FakeDumper(), FakeStorage()


def _backup_row(identifier, *, tipo="MANUAL", estado="EN_PROCESO", user_id=12, day=7):
    from types import SimpleNamespace
    return SimpleNamespace(
        id=identifier, tipo=tipo, estado=estado, usuario_id=user_id,
        iniciado_en=datetime(2026, 10, day, tzinfo=timezone.utc),
        archivo=None, tamano_bytes=None, checksum_sha256=None,
        finalizado_en=None, error=None,
    )


def test_background_completion_records_checksum_size_and_removes_temp_file(monkeypatch):
    from app.services.backups import BackupService
    import hashlib

    row = _backup_row(20)
    state, dumper, storage = _background_fakes(monkeypatch, row)
    BackupService(None, dumper, storage, "not-used").execute(row.id, audit_actor_id=12)

    assert row.estado == "COMPLETADO"
    assert row.tamano_bytes == len(b"backup payload")
    assert row.checksum_sha256 == hashlib.sha256(b"backup payload").hexdigest()
    assert list(state["uploaded"].values()) == [b"backup payload"]
    assert (12, "BACKUP_COMPLETADO") in state["audits"]
    assert state["paths"] and not Path(state["paths"][0]).exists()


def test_backup_router_is_registered():
    from app.api.v1.endpoints.backups import router

    assert {route.path for route in router.routes} >= {"", "/automatico", "/{backup_id}", "/{backup_id}/descarga"}


@pytest.mark.parametrize("token", [None, "wrong"])
def test_automatic_trigger_rejects_missing_or_wrong_token(token):
    from fastapi import BackgroundTasks, HTTPException
    from app.api.v1.endpoints.backups import create_automatic
    from app.core.config import settings

    settings.BACKUP_CRON_TOKEN = "expected"
    with pytest.raises(HTTPException) as error:
        create_automatic(BackgroundTasks(), token=token, db=object())
    assert error.value.status_code == 401


def test_automatic_trigger_fails_closed_when_token_is_unconfigured(monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from app.api.v1.endpoints.backups import create_automatic
    from app.core.config import settings

    monkeypatch.setattr(settings, "BACKUP_CRON_TOKEN", "")
    with pytest.raises(HTTPException) as error:
        create_automatic(BackgroundTasks(), token="anything", db=object())
    assert error.value.status_code == 503


def test_start_marks_stale_backup_failed_before_checking_active_backup(monkeypatch):
    from app.api.v1.endpoints import backups as endpoint
    from fastapi import HTTPException

    stale = _backup_row(3)
    saved, audited = [], []
    class Repo:
        def __init__(self, _db): pass
        def stale(self, _before): return [stale]
        def save(self, row): saved.append(row)
        def audit(self, actor, action, _description): audited.append((actor, action))
        def running(self): return True
        def create(self, row): row.id = 4; return row
    class Db:
        def execute(self, _statement): return None
        def commit(self): pass
        def refresh(self, _row): pass
    monkeypatch.setattr(endpoint, "BackupRepository", Repo)
    with pytest.raises(HTTPException) as error:
        endpoint._start(Db(), "MANUAL", 1)
    assert error.value.status_code == 409
    assert stale.estado == "FALLIDO"
    assert saved == [stale]
    assert audited == [(1, "BACKUP_FALLIDO")]


def test_dump_failure_is_sanitized_and_temp_file_is_removed(monkeypatch):
    from app.services.backups import BackupService

    row = _backup_row(4, user_id=2)
    state, dumper, storage = _background_fakes(
        monkeypatch, row, storage_error="secret postgresql://user:password@secret.host/db"
    )
    BackupService(None, dumper, storage, "unused").execute(row.id, audit_actor_id=2)
    assert row.estado == "FALLIDO"
    assert "password" not in row.error
    assert "secret.host" not in row.error
    assert (2, "BACKUP_FALLIDO") in state["audits"]
    assert state["paths"] and not Path(state["paths"][0]).exists()


def test_automatic_start_fails_closed_without_audit_actor(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.endpoints import backups as endpoint

    class Repo:
        def __init__(self, _db): pass
        def stale(self, _before): return []
        def running(self): return False
        def create(self, row): row.id = 10; return row

    class Db:
        def execute(self, _statement): return None
        def commit(self): pass
        def refresh(self, _row): pass

    monkeypatch.setattr(endpoint, "BackupRepository", Repo)
    monkeypatch.setattr(endpoint, "_system_actor", lambda _db: None)
    with pytest.raises(HTTPException) as error:
        endpoint._start(Db(), "AUTOMATICO", None)
    assert error.value.status_code == 503


def test_automatic_trigger_carries_audit_actor_into_background_task(monkeypatch):
    from fastapi import BackgroundTasks
    from app.api.v1.endpoints import backups as endpoint
    from app.core.config import settings

    class Repo:
        def __init__(self, _db): pass
        def stale(self, _before): return []
        def running(self): return False
        def create(self, row): row.id = 11; return row
        def audit(self, *_args): pass

    class Db:
        def execute(self, _statement): return None
        def commit(self): pass
        def refresh(self, _row): pass

    class FakeService:
        def execute(self, backup_id, audit_actor_id=None): pass

    monkeypatch.setattr(settings, "BACKUP_CRON_TOKEN", "expected")
    monkeypatch.setattr(endpoint, "BackupRepository", Repo)
    monkeypatch.setattr(endpoint, "_system_actor", lambda _db: 77)
    monkeypatch.setattr(endpoint, "_service", lambda: FakeService())
    tasks = BackgroundTasks()
    endpoint.create_automatic(tasks, token="expected", db=Db())
    assert tasks.tasks[0].args == (11, 77)


def test_background_completion_uses_injected_fakes_and_records_required_audit(monkeypatch):
    from types import SimpleNamespace
    from app.services import backups

    row = SimpleNamespace(
        id=20, estado="EN_PROCESO", tipo="AUTOMATICO", usuario_id=None,
        iniciado_en=datetime(2026, 10, 7, tzinfo=timezone.utc),
        archivo=None, tamano_bytes=None, checksum_sha256=None,
        finalizado_en=None, error=None,
    )
    class FakeDb:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, _model, _id): return row
        def commit(self): pass
        def rollback(self): pass
    class Repo:
        audits = []
        def __init__(self, _db): pass
        def save(self, _row): pass
        def audit(self, actor, action, _description): self.audits.append((actor, action))
        def completed_automatic(self, _limit): return []
    class Dumper:
        paths = []
        def dump(self, destination, _url):
            self.paths.append(destination)
            Path(destination).write_bytes(b"background dump")
    class Storage:
        uploaded = {}
        def upload(self, key, path): self.uploaded[key] = Path(path).read_bytes()

    dumper, storage = Dumper(), Storage()
    monkeypatch.setattr(backups, "SessionLocal", FakeDb)
    monkeypatch.setattr(backups, "BackupRepository", Repo)
    # The actor may become inactive after start; execution must reuse the
    # captured FK identity rather than requiring a currently-active actor.
    monkeypatch.setattr(backups, "_system_actor", lambda _db: None)
    service = backups.BackupService(None, dumper, storage, "not-used")
    service.execute(20, audit_actor_id=77)
    assert row.estado == "COMPLETADO"
    assert row.tamano_bytes == len(b"background dump")
    assert row.checksum_sha256 == hashlib.sha256(b"background dump").hexdigest()
    assert list(storage.uploaded.values()) == [b"background dump"]
    assert (77, "BACKUP_COMPLETADO") in Repo.audits
    assert dumper.paths and not Path(dumper.paths[0]).exists()


def test_background_storage_failure_is_audited_and_cleans_temp_file(monkeypatch):
    from types import SimpleNamespace
    from app.services import backups

    row = SimpleNamespace(
        id=21, estado="EN_PROCESO", tipo="MANUAL", usuario_id=4,
        iniciado_en=datetime(2026, 10, 7, tzinfo=timezone.utc),
        archivo=None, tamano_bytes=None, checksum_sha256=None,
        finalizado_en=None, error=None,
    )
    class FakeDb:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, _model, _id): return row
        def commit(self): pass
        def rollback(self): pass
    class Repo:
        audits = []
        def __init__(self, _db): pass
        def save(self, _row): pass
        def audit(self, actor, action, _description): self.audits.append((actor, action))
    class Dumper:
        paths = []
        def dump(self, destination, _url):
            self.paths.append(destination)
            Path(destination).write_bytes(b"dump")
    class Storage:
        def upload(self, _key, _path):
            raise RuntimeError("secret postgresql://user:password@private.invalid/db")

    dumper = Dumper()
    monkeypatch.setattr(backups, "SessionLocal", FakeDb)
    monkeypatch.setattr(backups, "BackupRepository", Repo)
    service = backups.BackupService(None, dumper, Storage(), "not-used")
    service.execute(21, audit_actor_id=4)
    assert row.estado == "FALLIDO"
    assert "password" not in row.error and "private.invalid" not in row.error
    assert (4, "BACKUP_FALLIDO") in Repo.audits
    assert dumper.paths and not Path(dumper.paths[0]).exists()


def test_automatic_retention_deletes_only_old_automatic_rows(monkeypatch):
    from app.services.backups import BackupService

    new = _backup_row(33, tipo="AUTOMATICO", user_id=None, day=7)
    newest_old = _backup_row(32, tipo="AUTOMATICO", estado="COMPLETADO", user_id=None, day=5)
    oldest_old = _backup_row(31, tipo="AUTOMATICO", estado="COMPLETADO", user_id=None, day=1)
    oldest_old.archivo = "old/automatic.dump"
    manual = _backup_row(30, tipo="MANUAL", estado="COMPLETADO", day=3)
    manual.archivo = "manual/kept.dump"
    candidates = [new, newest_old, oldest_old, manual]
    state, dumper, storage = _background_fakes(monkeypatch, new, candidates=candidates)
    BackupService(None, dumper, storage, "unused", retention=2).execute(new.id, audit_actor_id=77)
    assert new.estado == "COMPLETADO"
    assert state["deleted"] == [oldest_old]
    assert state["deleted_objects"] == ["old/automatic.dump"]
    assert newest_old not in state["deleted"]
    assert manual not in state["deleted"]
    assert (77, "BACKUP_RETENCION_ELIMINADA") in state["audits"]


def test_retention_failure_does_not_fail_completed_backup(monkeypatch):
    from app.services.backups import BackupService

    new = _backup_row(40, tipo="AUTOMATICO", user_id=None)
    old = _backup_row(39, tipo="AUTOMATICO", estado="COMPLETADO", user_id=None, day=1)
    old.archivo = "old.dump"
    state, dumper, storage = _background_fakes(monkeypatch, new, candidates=[new, old])
    def fail_delete(_key):
        raise RuntimeError("storage unavailable")
    storage.delete = fail_delete
    BackupService(None, dumper, storage, "unused", retention=1).execute(new.id, audit_actor_id=77)
    assert new.estado == "COMPLETADO"
    assert new.error is None
    assert (77, "BACKUP_COMPLETADO") in state["audits"]
    assert (77, "BACKUP_FALLIDO") not in state["audits"]


def test_signed_url_requires_completed_backup_and_uses_object_path(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.endpoints import backups as endpoint
    from app.core.config import settings

    class Db:
        row = type("Row", (), {"estado": "EN_PROCESO", "archivo": "2026/10/si2.dump"})()
        def get(self, _model, _id): return self.row
    class Storage:
        calls = []
        def signed_url(self, key, expires): self.calls.append((key, expires)); return "signed-url"
    storage = Storage()
    monkeypatch.setattr(endpoint, "_service", lambda: type("Service", (), {"storage": storage})())
    monkeypatch.setattr(settings, "BACKUP_SIGNED_URL_SECONDS", 90)
    with pytest.raises(HTTPException) as error:
        endpoint.download_backup(1, user=object(), db=Db())
    assert error.value.status_code == 409
    Db.row.estado = "COMPLETADO"
    result = endpoint.download_backup(1, user=object(), db=Db())
    assert result["url"] == "signed-url"
    assert storage.calls == [("2026/10/si2.dump", 90)]


def test_route_denies_non_superadmin_without_database(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.api.v1.deps import get_current_user
    from app.api.v1.endpoints.backups import router

    application = FastAPI()
    application.include_router(router, prefix="/backups")
    user = SimpleNamespace(id=1, rol=SimpleNamespace(nombre="ADMINISTRADOR"))
    application.dependency_overrides[get_current_user] = lambda: user
    with TestClient(application) as client:
        response = client.get("/backups")
    assert response.status_code == 403


def test_pg_dump_uses_password_free_argument_and_password_environment(monkeypatch, tmp_path):
    from app.services import backups

    captured = {}
    def fake_run(argv, **kwargs):
        captured["argv"], captured["kwargs"] = argv, kwargs
    monkeypatch.setattr(backups.subprocess, "run", fake_run)
    database_url = "postgresql://backup_user:very-secret@db.example:5432/si2?sslmode=require"
    backups.PgDumper().dump(str(tmp_path / "out.dump"), database_url)
    assert all("very-secret" not in arg for arg in captured["argv"])
    assert captured["kwargs"]["env"]["PGPASSWORD"] == "very-secret"


def test_pg_dump_failure_logs_redacted_diagnostics(monkeypatch, tmp_path, caplog):
    import logging
    from subprocess import CalledProcessError
    from app.services import backups

    database_url = "postgresql://backup_user:very-secret@db.example:5432/si2"
    def fake_run(_argv, **_kwargs):
        raise CalledProcessError(2, "pg_dump", stderr=f"could not connect to {database_url}; FATAL: password=very-secret")
    monkeypatch.setattr(backups.subprocess, "run", fake_run)
    with caplog.at_level(logging.ERROR, logger="app.services.backups"):
        with pytest.raises(RuntimeError):
            backups.PgDumper().dump(str(tmp_path / "out.dump"), database_url)
    message = caplog.text
    assert "exit code 2" in message
    assert "could not connect" in message
    assert "FATAL" in message
    assert "very-secret" not in message
    assert database_url not in message


def test_docker_image_uses_python_313_slim():
    from pathlib import Path

    assert "FROM python:3.13-slim" in Path("Dockerfile").read_text()
