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

    # ── Envío de correo (SMTP Gmail con contraseña de aplicación) ──
    # El usuario debe generar una "contraseña de aplicación" en su cuenta de
    # Google y ponerla en SMTP_PASSWORD (ver .env).
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""            # correo remitente, ej. coopia@gmail.com
    SMTP_PASSWORD: str = ""        # contraseña de aplicación de Google
    SMTP_FROM: str = ""            # nombre/dirección visible del remitente
    SMTP_USE_TLS: bool = True

    # URL base del frontend para construir enlaces de recuperación
    APP_FRONTEND_URL: str = "http://localhost:5173"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()