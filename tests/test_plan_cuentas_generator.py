import importlib.util
import os
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_plan_cuentas.py"
spec = importlib.util.spec_from_file_location("generate_plan_cuentas", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def test_parser_joins_unaccounted_continuation_lines_and_classifies_mcef():
    rows, omitted = generator.parse_mcef("""100.00 ACTIVO\n110.00 DISPONIBILIDADES\n111.00 CAJA\n111.01 Caja de\n           ahorro y moneda\n139.00 (PREVISIÓN PARA\n           INCOBRABILIDAD DE CARTERA)\n400.00 GASTOS\n500.00 INGRESOS\n127.01.M.01 Detalle fuera del contrato\n""")
    by_code = {r["codigo"]: r for r in rows}
    assert by_code["111.01"]["nombre"] == "Caja de ahorro y moneda"
    assert by_code["139.00"]["nombre"] == "PREVISIÓN PARA INCOBRABILIDAD DE CARTERA"
    assert by_code["139.00"]["es_regularizadora"] is True
    assert by_code["139.00"]["naturaleza"] == "ACREEDORA"
    assert by_code["400.00"]["tipo"] == "GASTOS"
    assert by_code["500.00"]["tipo"] == "INGRESOS"
    assert "127.01.M.01" in omitted
    assert "111.01" in by_code and by_code["111.01"]["nivel"] == 4


def test_committed_migration_seeds_the_full_official_catalog():
    migration = Path(__file__).parents[1] / "migrations" / "024_sprint11_plan_cuentas.sql"
    codes = re.findall(r"VALUES \('(\d{3}\.\d{2})'", migration.read_text(encoding="utf-8"))
    assert len(codes) == len(set(codes)) == 1997
    assert {"400.00", "410.00", "500.00", "510.00", "139.01"} <= set(codes)


def test_catalog_source_has_only_supported_levels_and_reports_deeper_codes():
    # Text export of ASFI MCEF Titulo II (pdftotext -layout); not versioned, set SI2_MCEF_SOURCE to run.
    source = Path(os.environ.get("SI2_MCEF_SOURCE", "data/asfi/T02.txt"))
    if not source.is_file():
        pytest.skip("MCEF source text not available; set SI2_MCEF_SOURCE")
    rows, omitted = generator.parse_mcef(source.read_text(encoding="utf-8"))
    assert rows
    assert {r["nivel"] for r in rows} == {1, 2, 3, 4}
    assert len(rows) == 1997
    assert len(set(omitted)) == 941
    assert sum(".M" in code for code in set(omitted)) == 875
    assert sum(".M" not in code for code in set(omitted)) == 66
    assert any(".M." in code for code in omitted)
    assert all(r["tipo"] != "SIN" for r in rows)


def test_plan_cuenta_orm_and_input_schemas_expose_new_catalog_contract():
    from app.models.models import PlanCuenta
    from app.schemas.schemas import PlanCuentaAnaliticaCreate, PlanCuentaAnaliticaUpdate

    columns = PlanCuenta.__table__.columns
    assert {"naturaleza", "es_regularizadora", "es_oficial", "cooperativa_id", "estado", "acepta_movimientos", "descripcion", "fecha_creacion"} <= set(columns.keys())
    assert "codigo" in PlanCuenta.__table__.c
    assert PlanCuentaAnaliticaCreate.model_validate({"padre_id": 3, "nombre": "Caja principal"}).nombre == "Caja principal"
    assert PlanCuentaAnaliticaUpdate.model_validate({"nombre": "Caja"}).nombre == "Caja"


def test_parser_does_not_append_deeper_mcef_children_to_supported_parent_name():
    rows, omitted = generator.parse_mcef("159.02 Previsión\n   159.02.1.01 (Previsión por menor valor)\n")
    assert rows[0]["nombre"] == "Previsión"
    assert "159.02.1.01" in omitted
