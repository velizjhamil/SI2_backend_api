import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "CoopSmart API"
    API_V1_STR: str = "/api/v1"

    SECRET_KEY: str = "super_secreta_llave_para_jwt_cooperativa_si2_uagrm"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    # Expiración del token de recuperación de contraseña (minutos)
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/cooperativa_db"

    # ── Envío de correo (Resend, API HTTPS) ──
    # SMTP directo no funciona desde Render: el hosting bloquea el tráfico
    # saliente hacia puertos SMTP (465/587) a nivel de red ("Network is
    # unreachable"), sin importar el proveedor. Resend se consume por HTTPS
    # (puerto 443), que sí está permitido.
    RESEND_API_KEY: str = ""       # API key de https://resend.com
    RESEND_FROM: str = "CoopIA <onboarding@resend.dev>"  # remitente verificado en Resend

    # URL base del frontend para construir enlaces de recuperación
    APP_FRONTEND_URL: str = "https://si2frontendweb.vercel.app"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()