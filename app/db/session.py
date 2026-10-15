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
    # 0) Asegurar constraints UNIQUE compuestos indispensables en tablas preexistentes
    #    para que create_all pueda vincular llaves foráneas compuestas (ej. voto_comite)
    with engine.begin() as conn:
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables WHERE table_name = 'solicitud_credito'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'solicitud_credito' AND constraint_name = 'uq_solicitud_credito_id_cooperativa'
                ) THEN
                    ALTER TABLE solicitud_credito ADD CONSTRAINT uq_solicitud_credito_id_cooperativa UNIQUE (id, cooperativa_id);
                END IF;

                IF EXISTS (
                    SELECT 1 FROM information_schema.tables WHERE table_name = 'usuario'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'usuario' AND constraint_name = 'uq_usuario_id_cooperativa'
                ) THEN
                    ALTER TABLE usuario ADD CONSTRAINT uq_usuario_id_cooperativa UNIQUE (id, cooperativa_id);
                END IF;
            END $$;
        """))

    # 1) Crear tablas que no existan (cooperativa, voto_comite, etc.)
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
            "ALTER TABLE socio ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id)"
        ))
        conn.execute(text(
            "ALTER TABLE socio ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id)"
        ))
        conn.execute(text(
            "ALTER TABLE socio ADD COLUMN IF NOT EXISTS uuid UUID NOT NULL DEFAULT gen_random_uuid()"
        ))
        conn.execute(text(
            "ALTER TABLE socio ADD COLUMN IF NOT EXISTS fecha_baja DATE"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_socio_cooperativa ON socio(cooperativa_id)"
        ))

        # --- bitacora: columnas del esquema ampliado ---
        conn.execute(text(
            "ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id)"
        ))
        conn.execute(text(
            "ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS user_agent TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE bitacora ALTER COLUMN accion TYPE VARCHAR(100)"
        ))

        # --- producto_credito: clasificación ASFI, garantías y límites ---
        conn.execute(text(
            "ALTER TABLE producto_credito ADD COLUMN IF NOT EXISTS tipo_credito_asfi VARCHAR(30)"
        ))
        conn.execute(text(
            "ALTER TABLE producto_credito ADD COLUMN IF NOT EXISTS sector_productivo BOOLEAN NOT NULL DEFAULT false"
        ))
        conn.execute(text(
            "ALTER TABLE producto_credito ADD COLUMN IF NOT EXISTS cobertura_minima_garantia NUMERIC(6,2) NOT NULL DEFAULT 100.00"
        ))
        conn.execute(text(
            "ALTER TABLE producto_credito ADD COLUMN IF NOT EXISTS monto_aprobacion_directa NUMERIC(14,2) NOT NULL DEFAULT 0.00"
        ))

        # --- solicitud_credito: comité y canal de origen móvil ---
        conn.execute(text(
            "ALTER TABLE solicitud_credito ADD COLUMN IF NOT EXISTS ronda_comite INT NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE solicitud_credito ADD COLUMN IF NOT EXISTS resultado_comite VARCHAR(12)"
        ))
        conn.execute(text(
            "ALTER TABLE solicitud_credito ADD COLUMN IF NOT EXISTS fecha_resolucion_comite TIMESTAMPTZ"
        ))
        conn.execute(text(
            "ALTER TABLE solicitud_credito ADD COLUMN IF NOT EXISTS canal_origen VARCHAR(12) NOT NULL DEFAULT 'VENTANILLA'"
        ))
        conn.execute(text(
            "ALTER TABLE solicitud_credito ADD COLUMN IF NOT EXISTS datos_declarados JSONB"
        ))

        # --- reporte: multi-tenant e informes ASFI ---
        conn.execute(text(
            "ALTER TABLE reporte ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id)"
        ))
        conn.execute(text(
            "ALTER TABLE reporte ADD COLUMN IF NOT EXISTS periodo DATE"
        ))
        conn.execute(text(
            "ALTER TABLE reporte ADD COLUMN IF NOT EXISTS estado VARCHAR(12) NOT NULL DEFAULT 'GENERADO'"
        ))
        conn.execute(text(
            "ALTER TABLE reporte ADD COLUMN IF NOT EXISTS contenido JSONB"
        ))
        conn.execute(text(
            "ALTER TABLE reporte ADD COLUMN IF NOT EXISTS resumen JSONB"
        ))
        conn.execute(text(
            "ALTER TABLE reporte ADD COLUMN IF NOT EXISTS hash_contenido VARCHAR(64)"
        ))

    # 3) Catálogo de planes y suscripción por defecto para cada cooperativa
    from app.services.suscripciones import seed_suscripciones

    db = SessionLocal()
    try:
        seed_suscripciones(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
