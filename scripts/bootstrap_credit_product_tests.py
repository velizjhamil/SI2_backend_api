"""Initialize the isolated test database and its minimal credit-test references."""

import os
import sys
from pathlib import Path

from pytest_guard import UnsafeTestEnvironment
from verify_test_db import TestDatabaseVerificationError, verify_test_database

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


ROLE_NAMES = (
    "ADMINISTRADOR",
    "CAJERO",
    "OFICIAL_CREDITO",
    "CONTADOR",
    "SOCIO",
    "SUPERADMIN",
)


def main() -> int:
    try:
        database_url = verify_test_database()

        # Importing application modules is deliberately delayed until after the
        # read-only endpoint/cluster proof, and the engine receives that exact URL.
        os.environ["DATABASE_URL"] = database_url
        from app.db.session import SessionLocal, init_db
        from app.models.models import Moneda, Rol
        from sqlalchemy.dialects.postgresql import insert

        # Imports may construct an engine; re-prove the destination immediately
        # before the first schema-changing operation.
        if verify_test_database() != database_url:
            raise TestDatabaseVerificationError("The verified test endpoint changed before initialization.")
        init_db()

        # DDL/seed work in init_db can take time; prove the same target again
        # immediately before any reference-data DML.
        if verify_test_database() != database_url:
            raise TestDatabaseVerificationError("The verified test endpoint changed during initialization.")

        with SessionLocal.begin() as db:
            db.execute(
                insert(Rol).values([{"nombre": name} for name in ROLE_NAMES])
                .on_conflict_do_nothing(index_elements=[Rol.nombre])
            )
            db.execute(
                insert(Moneda).values(
                    codigo_iso="BOB",
                    nombre="Boliviano",
                    simbolo="Bs",
                    es_moneda_base=True,
                ).on_conflict_do_nothing(index_elements=[Moneda.codigo_iso])
            )
    except (TestDatabaseVerificationError, UnsafeTestEnvironment):
        print("Refusing: dedicated PostgreSQL test-database bootstrap was not verified.", file=sys.stderr)
        return 2
    except Exception:
        # Driver and application exceptions can carry the configured URL.
        print("Bootstrap failed without exposing database connection details.", file=sys.stderr)
        return 1

    print("Initialized the isolated test database and required credit-product references.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
