"""Script para sembrar datos de prueba del Comité de Crédito (CU-W25).

Crea socios y solicitudes en estado EN_COMITE con sus evaluaciones crediticias de IA
y votos simulados para permitir probar todas las funcionalidades:
1. Solicitud nueva en comité (sin votos, quórum 0/3)
2. Solicitud con deliberación iniciada (1 voto registrado, quórum 1/3)
3. Solicitud al borde de resolución (2 votos registrados, quórum 2/3 - listo para completar quórum)
"""
import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone, timedelta, date

# Asegurar path de la app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.core.security import hash_password
from app.models.models import (
    Cooperativa,
    Usuario,
    Socio,
    ProductoCredito,
    Moneda,
    SolicitudCredito,
    EvaluacionCrediticia,
    VotoComite,
)


def get_or_create_socio(db, cooperativa_id, ci, nombre, apellido, correo):
    socio = db.query(Socio).filter(Socio.cooperativa_id == cooperativa_id, Socio.ci == ci).first()
    if not socio:
        socio = Socio(
            cooperativa_id=cooperativa_id,
            ci=ci,
            nombre=nombre,
            apellido=apellido,
            correo=correo,
            telefono="77654321",
            direccion="Av. Las Américas #450, Santa Cruz",
            estado="ACTIVO",
            fecha_registro=date(2023, 1, 15),
        )
        db.add(socio)
        db.flush()
    return socio


def seed_comite():
    with SessionLocal() as db:
        coop = db.query(Cooperativa).filter(Cooperativa.id == 1).first()
        if not coop:
            print("No se encontró la Cooperativa con id 1")
            return

        moneda = db.query(Moneda).filter(Moneda.codigo_iso == "BOB").first()
        moneda_id = moneda.id if moneda else 1

        # Asegurar contraseña de admin@test.com
        admin_user = db.query(Usuario).filter(Usuario.correo == "admin@test.com").first()
        if admin_user:
            admin_user.contrasena = hash_password("Password123")
            db.flush()

        # Usuarios del comité
        oficial_user = db.query(Usuario).filter(Usuario.correo == "oficial.credito@test.com").first()
        contador_user = db.query(Usuario).filter(Usuario.correo == "contador@test.com").first()
        registrador_user = db.query(Usuario).filter(Usuario.correo == "acredito@cooperativa.com").first()
        if not registrador_user:
            registrador_user = oficial_user

        # Productos
        prod_vivienda = db.query(ProductoCredito).filter(
            ProductoCredito.cooperativa_id == 1,
            ProductoCredito.nombre.ilike("%Vivienda%")
        ).first() or db.query(ProductoCredito).filter(ProductoCredito.cooperativa_id == 1).first()

        prod_productivo = db.query(ProductoCredito).filter(
            ProductoCredito.cooperativa_id == 1,
            ProductoCredito.nombre.ilike("%Productivo%")
        ).first() or prod_vivienda

        prod_consumo = db.query(ProductoCredito).filter(
            ProductoCredito.cooperativa_id == 1,
            ProductoCredito.nombre.ilike("%Consumo%")
        ).first() or prod_vivienda

        # Socios exclusivos para el comité demo (para evitar violar uq_solicitud_credito_un_socio_en_curso)
        socio1 = get_or_create_socio(db, coop.id, "COM-SOCIO-01", "Valeria", "Méndez Suárez", "valeria.mendez@test.bo")
        socio2 = get_or_create_socio(db, coop.id, "COM-SOCIO-02", "Carlos", "Gutiérrez Peña", "carlos.gutierrez@test.bo")
        socio3 = get_or_create_socio(db, coop.id, "COM-SOCIO-03", "Mariana", "Ríos Alarcón", "mariana.rios@test.bo")

        print(f"Cooperativa: {coop.nombre} (ID: {coop.id})")
        print(f"Socios demo listos: {socio1.nombre} ({socio1.id}), {socio2.nombre} ({socio2.id}), {socio3.nombre} ({socio3.id})")

        # Limpiar datos previos de comite de prueba si ya existían para idempotencia
        numeros_demo = ["COM-DEMO-001", "COM-DEMO-002", "COM-DEMO-003"]
        sols_previas = db.query(SolicitudCredito).filter(
            SolicitudCredito.cooperativa_id == 1,
            SolicitudCredito.numero_solicitud.in_(numeros_demo)
        ).all()
        for sp in sols_previas:
            db.query(VotoComite).filter(VotoComite.solicitud_credito_id == sp.id).delete()
            db.query(EvaluacionCrediticia).filter(EvaluacionCrediticia.solicitud_credito_id == sp.id).delete()
            db.delete(sp)
        db.commit()

        now = datetime.now(timezone.utc)

        # -------------------------------------------------------------
        # CASO 1: Solicitud Nueva en Comité (Sin votos, quórum 0/3)
        # -------------------------------------------------------------
        sol1 = SolicitudCredito(
            numero_solicitud="COM-DEMO-001",
            monto=Decimal("85000.00"),
            plazo_meses=48,
            tasa_interes=Decimal("9.50"),
            calificacion_asfi="A",
            tiene_deudas=False,
            estado="EN_COMITE",
            socio_id=socio1.id,
            usuario_id=registrador_user.id,
            producto_credito_id=prod_vivienda.id,
            moneda_id=moneda_id,
            cooperativa_id=coop.id,
            destino="VIVIENDA",
            destino_detalle="Ampliación y remodelación integral de vivienda familiar propia",
            ronda_comite=1,
            canal_origen="VENTANILLA",
            fecha_solicitud=now - timedelta(days=2),
            fecha_actualizacion=now - timedelta(days=2),
        )
        db.add(sol1)
        db.flush()

        eval1 = EvaluacionCrediticia(
            solicitud_credito_id=sol1.id,
            cooperativa_id=coop.id,
            version_modelo="v1.2-asfi",
            score=745,
            dictamen="REVISION_MANUAL",
            factores=[
                {
                    "codigo": "CAP_PAGO",
                    "descripcion": "Capacidad de Pago (RCI)",
                    "puntos": 280,
                    "maximo": 300,
                    "valor": "38.2%",
                    "motivo": "Relación cuota-ingreso holgada",
                },
                {
                    "codigo": "ESTAB_LAB",
                    "descripcion": "Estabilidad Laboral",
                    "puntos": 200,
                    "maximo": 200,
                    "valor": "4 años",
                    "motivo": "Antigüedad demostrada en empleo actual",
                },
                {
                    "codigo": "GARANTIA",
                    "descripcion": "Garantía Hipotecaria",
                    "puntos": 265,
                    "maximo": 300,
                    "valor": "Folio Real al día",
                    "motivo": "Inmueble propio sin gravamen previo",
                },
            ],
            knockouts=[],
            explicacion="El monto solicitado (Bs. 85,000) supera el límite de delegación directa de la agencia. Presenta buena solvencia y estabilidad patrimonial. Requiere deliberación colegiada del Comité.",
            cuota_estimada=Decimal("2135.50"),
            relacion_cuota_ingreso=Decimal("38.20"),
            usuario_id=registrador_user.id,
            probabilidad_mora=Decimal("0.12"),
            nivel_riesgo="MEDIO",
            version_modelo_mora="v1.0-logreg",
            fecha=now - timedelta(days=2),
        )
        db.add(eval1)
        print("-> Creada Solicitud COM-DEMO-001 (Valeria Méndez - Bs. 85,000 - 0/3 votos)")

        # -------------------------------------------------------------
        # CASO 2: Solicitud con Deliberación Iniciada (1 voto emitido, quórum 1/3)
        # -------------------------------------------------------------
        sol2 = SolicitudCredito(
            numero_solicitud="COM-DEMO-002",
            monto=Decimal("45000.00"),
            plazo_meses=24,
            tasa_interes=Decimal("14.00"),
            calificacion_asfi="A",
            tiene_deudas=False,
            estado="EN_COMITE",
            socio_id=socio2.id,
            usuario_id=registrador_user.id,
            producto_credito_id=prod_productivo.id,
            moneda_id=moneda_id,
            cooperativa_id=coop.id,
            destino="CAPITAL_TRABAJO",
            destino_detalle="Compra por mayor de insumos y mercadería para taller metalúrgico",
            ronda_comite=1,
            canal_origen="MOVIL",
            fecha_solicitud=now - timedelta(days=1),
            fecha_actualizacion=now - timedelta(days=1),
        )
        db.add(sol2)
        db.flush()

        eval2 = EvaluacionCrediticia(
            solicitud_credito_id=sol2.id,
            cooperativa_id=coop.id,
            version_modelo="v1.2-asfi",
            score=820,
            dictamen="REVISION_MANUAL",
            factores=[
                {
                    "codigo": "FLUJO_CAJA",
                    "descripcion": "Flujo de Caja Negocio",
                    "puntos": 350,
                    "maximo": 400,
                    "valor": "Ventas > Bs. 25,000",
                    "motivo": "Flujo positivo sostenido en últimos 6 meses",
                },
                {
                    "codigo": "MARGEN_OPERATIVO",
                    "descripcion": "Margen Operativo",
                    "puntos": 270,
                    "maximo": 300,
                    "valor": "32%",
                    "motivo": "Margen sólido para cubrir amortización",
                },
                {
                    "codigo": "CAP_PAGO",
                    "descripcion": "Capacidad de Pago",
                    "puntos": 200,
                    "maximo": 200,
                    "valor": "27.5%",
                    "motivo": "Relación cuota-ingreso muy baja",
                },
            ],
            knockouts=[],
            explicacion="Flujo de caja y capacidad de generación comprobados en la evaluación de campo. Supera el límite de aprobación directa del oficial.",
            cuota_estimada=Decimal("2160.00"),
            relacion_cuota_ingreso=Decimal("27.50"),
            usuario_id=registrador_user.id,
            probabilidad_mora=Decimal("0.07"),
            nivel_riesgo="BAJO",
            version_modelo_mora="v1.0-logreg",
            fecha=now - timedelta(days=1),
        )
        db.add(eval2)

        # 1er Voto: Contador
        voto_contador = VotoComite(
            solicitud_credito_id=sol2.id,
            cooperativa_id=coop.id,
            ronda=1,
            usuario_id=contador_user.id,
            voto="APROBAR",
            comentario="Revisados los estados financieros y flujo de caja del taller. La liquidez cubre holgadamente la cuota proyectada con cobertura 2.1x.",
            fecha=now - timedelta(hours=5),
        )
        db.add(voto_contador)
        print("-> Creada Solicitud COM-DEMO-002 (Carlos Gutiérrez - Bs. 45,000 - 1/3 votos, aprobado por Contador)")

        # -------------------------------------------------------------
        # CASO 3: Solicitud con 2 votos emitidos (Quórum 2/3 - Lista para definición)
        # -------------------------------------------------------------
        sol3 = SolicitudCredito(
            numero_solicitud="COM-DEMO-003",
            monto=Decimal("35000.00"),
            plazo_meses=18,
            tasa_interes=Decimal("16.50"),
            calificacion_asfi="B",
            tiene_deudas=True,
            estado="EN_COMITE",
            socio_id=socio3.id,
            usuario_id=registrador_user.id,
            producto_credito_id=prod_consumo.id,
            moneda_id=moneda_id,
            cooperativa_id=coop.id,
            destino="CONSUMO",
            destino_detalle="Consolidación de deudas comerciales y compra de equipamiento",
            ronda_comite=1,
            canal_origen="VENTANILLA",
            fecha_solicitud=now - timedelta(hours=18),
            fecha_actualizacion=now - timedelta(hours=18),
        )
        db.add(sol3)
        db.flush()

        eval3 = EvaluacionCrediticia(
            solicitud_credito_id=sol3.id,
            cooperativa_id=coop.id,
            version_modelo="v1.2-asfi",
            score=680,
            dictamen="REVISION_MANUAL",
            factores=[
                {
                    "codigo": "NIVEL_ENDEUDAMIENTO",
                    "descripcion": "Nivel de Endeudamiento",
                    "puntos": 210,
                    "maximo": 300,
                    "valor": "41.5% RCI",
                    "motivo": "Endeudamiento cercano al límite superior",
                },
                {
                    "codigo": "GARANTE_PERSONAL",
                    "descripcion": "Garantía Personal",
                    "puntos": 250,
                    "maximo": 300,
                    "valor": "Garante Dependiente",
                    "motivo": "Garante con respaldo salarial y estabilidad laboral",
                },
                {
                    "codigo": "CALIF_ASFI",
                    "descripcion": "Calificación ASFI",
                    "puntos": 220,
                    "maximo": 300,
                    "valor": "Calificación B",
                    "motivo": "Regularizaciones cumplidas puntualmente",
                },
            ],
            knockouts=[],
            explicacion="La socia presenta endeudamiento cercano al límite normativo. El garante cumple con los requisitos de patrimonio y estabilidad laboral.",
            cuota_estimada=Decimal("2208.75"),
            relacion_cuota_ingreso=Decimal("41.50"),
            usuario_id=registrador_user.id,
            probabilidad_mora=Decimal("0.18"),
            nivel_riesgo="MEDIO",
            version_modelo_mora="v1.0-logreg",
            fecha=now - timedelta(hours=18),
        )
        db.add(eval3)

        # 1er voto previo: Oficial de Crédito
        voto1 = VotoComite(
            solicitud_credito_id=sol3.id,
            cooperativa_id=coop.id,
            ronda=1,
            usuario_id=oficial_user.id,
            voto="APROBAR",
            comentario="Se verificó in situ la actividad económica del garante personal. Cuenta con respaldo de bienes suficientes para la operación.",
            fecha=now - timedelta(hours=4),
        )
        db.add(voto1)

        # 2do voto previo: Contador
        voto2 = VotoComite(
            solicitud_credito_id=sol3.id,
            cooperativa_id=coop.id,
            ronda=1,
            usuario_id=contador_user.id,
            voto="OBSERVAR",
            comentario="Recomiendo condicionar el desembolso a la presentación de boletas de pago actualizadas del garante y débito automático.",
            fecha=now - timedelta(hours=2),
        )
        db.add(voto2)
        print("-> Creada Solicitud COM-DEMO-003 (Mariana Ríos - Bs. 35,000 - 2/3 votos: 1 APROBAR, 1 OBSERVAR)")

        db.commit()
        print("\n¡Semillero del Comité de Crédito completado exitosamente!")


if __name__ == "__main__":
    seed_comite()
