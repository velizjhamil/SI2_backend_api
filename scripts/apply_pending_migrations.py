import os
import sys
from pathlib import Path
from sqlalchemy import text
from app.db.session import engine

def main():
    migrations_dir = Path("migrations")
    sql_files = sorted(migrations_dir.glob("*.sql"))
    print(f"Found {len(sql_files)} migration files.")
    
    with engine.connect() as conn:
        for f in sql_files:
            # Skip big seed data or plan cuentas if not needed, or run them safely
            # Let's inspect which files:
            print(f"Applying {f.name}...")
            content = f.read_text(encoding="utf-8")
            # If the SQL file contains multiple statements or transactions,
            # in psycopg2 we can execute via raw connection or run per statement.
            try:
                # Raw connection allows executing multi-statement SQL scripts directly
                raw_conn = conn.connection
                with raw_conn.cursor() as cursor:
                    cursor.execute(content)
                raw_conn.commit()
                print(f"  -> SUCCESS: {f.name}")
            except Exception as e:
                raw_conn.rollback()
                print(f"  -> ERROR applying {f.name}: {e}")

if __name__ == "__main__":
    main()
