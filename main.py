import logging
import sys

# Render (y otros hosts que capturan stdout via pipe) usan buffering por
# bloques en vez de por línea: los logs pueden quedar atrapados en memoria y
# nunca aparecer en el visor de logs si el proceso no escribe lo suficiente
# para llenar el buffer. Forzamos line-buffering para que cada línea salga
# de inmediato, igual que en una terminal local.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import init_db

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)


@app.on_event("startup")
def on_startup() -> None:
    """Alinea el esquema de la BD con el modelo al arrancar (sin migraciones manuales)."""
    init_db()

# Permitir peticiones desde tu Frontend (Vite dev server y builds locales)
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "https://si2frontendweb.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/")
def root():
    return {
        "sistema": settings.PROJECT_NAME,
        "mensaje": "Core Financiero Transaccional Operativo 🚀"
    }

@app.get("/health")
def health_check():
    return {"status": "ok"}