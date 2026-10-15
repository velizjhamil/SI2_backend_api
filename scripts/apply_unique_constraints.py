import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import engine
from app.db.base import Base
import app.models.models  # noqa: F401

def main():
    with engine.begin() as conn:
        print("--- Checking columns of solicitud_credito ---")
        cols = conn.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'solicitud_credito' AND column_name IN ('id', 'cooperativa_id');
        """)).fetchall()
        for c in cols:
            print(f"  solicitud_credito.{c[0]}: {c[1]}")

        print("\n--- Checking columns of usuario ---")
        cols_u = conn.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'usuario' AND column_name IN ('id', 'cooperativa_id');
        """)).fetchall()
        for c in cols_u:
            print(f"  usuario.{c[0]}: {c[1]}")

        print("\n--- Checking existing constraints ---")
        constraints = conn.execute(text("""
            SELECT tc.table_name, tc.constraint_name, tc.constraint_type
            FROM information_schema.table_constraints tc
            WHERE tc.table_name IN ('solicitud_credito', 'usuario')
            ORDER BY tc.table_name, tc.constraint_name;
        """)).fetchall()
        for r in constraints:
            print(f"  {r[0]} -> {r[1]} ({r[2]})")

        # 1. Apply unique constraint on solicitud_credito (id, cooperativa_id)
        print("\n--- Applying uq_solicitud_credito_id_cooperativa ---")
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'solicitud_credito' 
                    AND constraint_name = 'uq_solicitud_credito_id_cooperativa'
                ) THEN
                    ALTER TABLE solicitud_credito 
                    ADD CONSTRAINT uq_solicitud_credito_id_cooperativa UNIQUE (id, cooperativa_id);
                    RAISE NOTICE 'Constraint uq_solicitud_credito_id_cooperativa created successfully.';
                ELSE
                    RAISE NOTICE 'Constraint uq_solicitud_credito_id_cooperativa already exists.';
                END IF;
            END $$;
        """))

        # 2. Apply unique constraint on usuario (id, cooperativa_id)
        print("\n--- Applying uq_usuario_id_cooperativa ---")
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'usuario' 
                    AND constraint_name = 'uq_usuario_id_cooperativa'
                ) THEN
                    ALTER TABLE usuario 
                    ADD CONSTRAINT uq_usuario_id_cooperativa UNIQUE (id, cooperativa_id);
                    RAISE NOTICE 'Constraint uq_usuario_id_cooperativa created successfully.';
                ELSE
                    RAISE NOTICE 'Constraint uq_usuario_id_cooperativa already exists.';
                END IF;
            END $$;
        """))

    print("\n--- Attempting Base.metadata.create_all() ---")
    Base.metadata.create_all(bind=engine)
    print("Base.metadata.create_all() executed successfully!")

    # Verify if voto_comite exists now
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'voto_comite'
            );
        """)).scalar()
        print(f"\nDoes table 'voto_comite' exist? -> {exists}")

if __name__ == "__main__":
    main()
