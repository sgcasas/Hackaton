"""Valida cada ejemplo de contracts/ejemplos contra su schema. Uso: python3 scripts/validar_contratos.py"""
import json, pathlib, sys
from jsonschema import Draft202012Validator, FormatChecker
raiz = pathlib.Path(__file__).resolve().parent.parent / "contracts"
errores = 0
for sch in sorted((raiz / "schemas").glob("*.schema.json")):
    nombre = sch.name.replace(".schema.json", "")
    ej = raiz / "ejemplos" / f"{nombre}.json"
    v = Draft202012Validator(json.loads(sch.read_text()), format_checker=FormatChecker())
    errs = list(v.iter_errors(json.loads(ej.read_text())))
    print(("OK   " if not errs else "FALLA"), nombre, *[e.message for e in errs])
    errores += len(errs)
sys.exit(1 if errores else 0)
