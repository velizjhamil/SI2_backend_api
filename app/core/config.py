import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "CoopSmart API"
    API_V1_STR: str = "/api/v1"
    
    SECRET_KEY: str = "super_secreta_llave_para_jwt_cooperativa_si2_uagrm"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/cooperativa_db"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()