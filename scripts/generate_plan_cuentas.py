#!/usr/bin/env python3
"""Generate the official MCEF chart seed SQL from the versioned T02 text export."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

CODE_RE = re.compile(r"(?<![\w.])(?P<code>\d{3}\.\d{2}(?:\.[A-Za-z0-9]+)*)(?![\w.])")
CLASS_NAMES = {
    1: "ACTIVO", 2: "PASIVO", 3: "PATRIMONIO", 4: "GASTOS", 5: "INGRESOS",
    6: "CONTINGENTES DEUDORAS", 7: "CONTINGENTES ACREEDORAS",
    8: "ORDEN DEUDORAS", 9: "ORDEN ACREEDORAS",
}


def _level(code: str) -> int | None:
    if not re.fullmatch(r"\d{3}\.\d{2}", code):
        return None
    head, tail = code.split(".")
    if tail == "00":
        if head[1:] == "00":
            return 1
        if head[2] == "0":
            return 2
        return 3
    return 4


def _parent(code: str, level: int) -> str | None:
    head, _ = code.split(".")
    if level == 1:
        return None
    if level == 2:
        return f"{head[0]}00.00"
    if level == 3:
        return f"{head[:2]}0.00"
    return f"{head}.00"


def parse_mcef(content: str) -> tuple[list[dict], list[str]]:
    records: dict[str, dict] = {}
    omitted: list[str] = []
    current: dict | None = None
    for raw in content.splitlines():
        match = CODE_RE.search(raw)
        if match:
            code = match.group("code")
            level = _level(code)
            if level is None:
                omitted.append(code)
                current = None
                continue
            name = raw[match.end():].strip()
            if not name:
                current = None
                continue
            regularizing = name.startswith("(") and name.endswith(")")
            if regularizing:
                name = name[1:-1].strip()
            class_no = int(code[0])
            contra_nature = {1: "ACREEDORA", 2: "DEUDORA", 3: "DEUDORA", 4: "ACREEDORA", 5: "DEUDORA", 6: "ACREEDORA", 7: "DEUDORA", 8: "ACREEDORA", 9: "DEUDORA"}
            records[code] = {
                "codigo": code, "nombre": name, "nivel": level,
                "tipo": CLASS_NAMES[class_no],
                "naturaleza": contra_nature[class_no] if regularizing else ("DEUDORA" if class_no in {1, 4, 6, 8} else "ACREEDORA"),
                "es_regularizadora": regularizing,
                "padre_codigo": _parent(code, level),
                "acepta_movimientos": level == 4,
            }
            current = records[code]
        elif current is not None and raw.strip() and not raw.lstrip().startswith(("Título", "MANUAL")):
            continuation = raw.strip()
            # PDF page furniture is not account-name content.
            if not re.match(r"^(?:T[IÍ]TULO\s+\d|MANUAL DE CUENTAS|Página\s+\d)", continuation, re.I):
                current["nombre"] = (current["nombre"] + " " + continuation).strip()
                if current["nombre"].startswith("(") and current["nombre"].endswith(")"):
                    current["nombre"] = current["nombre"][1:-1].strip()
                    current["es_regularizadora"] = True
                    n = int(current["codigo"][0])
                    current["naturaleza"] = {1:"ACREEDORA",2:"DEUDORA",3:"DEUDORA",4:"ACREEDORA",5:"DEUDORA",6:"ACREEDORA",7:"DEUDORA",8:"ACREEDORA",9:"DEUDORA"}[n]
    rows = sorted(records.values(), key=lambda r: r["codigo"])
    return rows, omitted


def sql_seed(rows: list[dict]) -> str:
    out = []
    for r in rows:
        vals = [r["codigo"], r["nombre"], r["nivel"], r["tipo"], r["naturaleza"], r["es_regularizadora"], r["acepta_movimientos"], r["padre_codigo"]]
        def q(v):
            return "NULL" if v is None else ("TRUE" if v is True else "FALSE" if v is False else str(v) if isinstance(v, int) else "'" + str(v).replace("'", "''") + "'")
        code, name, level, tipo, nature, regular, accepts, parent = [q(v) for v in vals]
        parent_expr = "NULL" if r["padre_codigo"] is None else f"(SELECT id FROM plan_cuenta WHERE codigo={parent} AND cooperativa_id IS NULL)"
        out.append(f"INSERT INTO plan_cuenta (codigo,nombre,nivel,tipo,naturaleza,es_regularizadora,es_oficial,cooperativa_id,estado,acepta_movimientos,plan_cuenta_padre_id) VALUES ({code},{name},{level},{tipo},{nature},{regular},TRUE,NULL,'ACTIVA',{accepts},{parent_expr}) ON CONFLICT ((COALESCE(cooperativa_id, 0)), codigo) DO UPDATE SET nombre=EXCLUDED.nombre,nivel=EXCLUDED.nivel,tipo=EXCLUDED.tipo,naturaleza=EXCLUDED.naturaleza,es_regularizadora=EXCLUDED.es_regularizadora,es_oficial=TRUE,estado='ACTIVA',acepta_movimientos=EXCLUDED.acepta_movimientos,plan_cuenta_padre_id=EXCLUDED.plan_cuenta_padre_id;")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-omitted", type=Path)
    args = parser.parse_args()
    rows, omitted = parse_mcef(args.source.read_text(encoding="utf-8"))
    generated = sql_seed(rows)
    if args.output:
        args.output.write_text(generated, encoding="utf-8")
    else:
        print(generated, end="")
    if args.report_omitted:
        args.report_omitted.write_text("\n".join(sorted(set(omitted))) + "\n", encoding="utf-8")
    print(f"-- Parsed {len(rows)} supported accounts (levels 1-4); omitted {len(set(omitted))} distinct deeper/nonstandard codes.", file=__import__("sys").stderr)

if __name__ == "__main__":
    main()
