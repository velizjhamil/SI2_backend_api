from fastapi import APIRouter

from app.api.v1.endpoints import admin, ahorros, auth, backups, caja, cooperativas, creditos, dpf, socios_kyc, uif, socio, plan_cuentas, comprobantes, libros, estados_financieros, reportes, informes_asfi, verificacion_comprobantes

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(cooperativas.router, prefix="/cooperativas", tags=["cooperativas"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(socios_kyc.router, prefix="/socios", tags=["socios-kyc"])
api_router.include_router(ahorros.router, prefix="/ahorros", tags=["ahorros"])
api_router.include_router(caja.router, prefix="/caja", tags=["caja"])
api_router.include_router(uif.router, prefix="/uif", tags=["uif"])
api_router.include_router(dpf.router, prefix="/dpf", tags=["dpf"])
api_router.include_router(creditos.router, prefix="/creditos", tags=["creditos"])
api_router.include_router(socio.router, prefix="/socio", tags=["socio-autoservicio"])
api_router.include_router(plan_cuentas.router, prefix="/contabilidad", tags=["contabilidad-plan-cuentas"])
api_router.include_router(comprobantes.router, prefix="/contabilidad", tags=["contabilidad-comprobantes"])
api_router.include_router(libros.router, prefix="/contabilidad", tags=["contabilidad-libros"])
api_router.include_router(estados_financieros.router, prefix="/contabilidad", tags=["contabilidad-estados-financieros"])
api_router.include_router(reportes.router, prefix="/reportes", tags=["reportes-gerenciales"])
api_router.include_router(informes_asfi.router, prefix="/informes-asfi", tags=["informes-asfi"])
api_router.include_router(backups.router, prefix="/backups", tags=["backups"])
api_router.include_router(verificacion_comprobantes.router, prefix="/verificacion", tags=["verificacion"])
