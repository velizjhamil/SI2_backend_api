"""Inicialización automática del esquema de BD al arrancar el backend.

Crea las tablas faltantes (create_all) y alinea las existentes con el modelo
SQLAlchemy mediante DDL idempotente, de modo que el backend funcione contra la
base de datos de Docker sin necesidad de ejecutar migraciones manuales.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
import app.models.models  # noqa: F401  -- registra los modelos en Base.metadata


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Crea tablas faltantes y alinea columnas de tablas existentes."""
    # 1) Crear tablas que no existan (cooperativa, etc.)
    Base.metadata.create_all(bind=engine)

    # 2) Alinear columnas faltantes en tablas ya existentes (DDL idempotente)
    with engine.begin() as conn:
        # --- usuario ---
        conn.execute(text(
            'CREATE EXTENSION IF NOT EXISTS "pgcrypto"'
        ))
        conn.execute(text(
            "ALTER TABLE usuario ADD COLUMN IF NOT EXISTS uuid UUID NOT NULL DEFAULT gen_random_uuid()"
        ))
        conn.execute(text(
            "ALTER TABLE usuario ADD COLUMN IF NOT EXISTS fecha_baja TIMESTAMPTZ"
        ))
        conn.execute(text(
            "ALTER TABLE usuario ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id)"
        ))
        # Renombrar contraseña (con Ñ) -> contrasena (sin Ñ), solo si aplica
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'usuario' AND column_name = 'contraseña'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'usuario' AND column_name = 'contrasena'
                ) THEN
                    ALTER TABLE usuario RENAME COLUMN "contraseña" TO contrasena;
                END IF;
            END $$;
        """))
        # Constraint UNIQUE sobre uuid (idempotente)
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'usuario' AND constraint_name = 'uq_usuario_uuid'
                ) THEN
                    ALTER TABLE usuario ADD CONSTRAINT uq_usuario_uuid UNIQUE (uuid);
                END IF;
            END $$;
        """))
        # Índice sobre cooperativa_id (idempotente)
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_usuario_cooperativa ON usuario(cooperativa_id)"
        ))

        # --- socio ---
        conn.execute(text(
            "ALTER TABLE socio ADD COLUMN IF NOT EXISTS uuid UUID NOT NULL DEFAULT gen_random_uuid()"
        ))
        conn.execute(text(
            "ALTER TABLE socio ADD COLUMN IF NOT EXISTS fecha_baja DATE"
        ))

        # --- bitacora: columnas del esquema ampliado ---
        conn.execute(text(
            "ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS user_agent TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE bitacora ALTER COLUMN accion TYPE VARCHAR(100)"
        ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
