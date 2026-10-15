import sys
from app.db.session import engine
from sqlalchemy import text

def main():
    tables = ['credito', 'solicitud_credito', 'tabla_amortizacion', 'producto_credito', 'moneda', 'socio']
    with engine.connect() as conn:
        for t in tables:
            cols = conn.execute(text(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}' ORDER BY ordinal_position")).fetchall()
            print(f"\n--- {t} ({len(cols)} columns) ---")
            for c in cols:
                print(f"  {c[0]} ({c[1]})")

if __name__ == "__main__":
    main()
