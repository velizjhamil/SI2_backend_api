from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,  # Verifica la conexión antes de ejecutar consultas
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependencia para obtener la sesión de BD en cada Request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()