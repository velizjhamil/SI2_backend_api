from fastapi import APIRouter

from app.api.v1.endpoints import admin, auth, cooperativas, socios_kyc

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(cooperativas.router, prefix="/cooperativas", tags=["cooperativas"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(socios_kyc.router, prefix="/socios", tags=["socios-kyc"])

