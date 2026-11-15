"""Datos semilla (zonas, antenas, clientes) del contrato.
  python3 scripts/semilla.py            -> genera contracts/semilla/*.json
  python3 scripts/semilla.py --cargar   -> además los carga en DynamoDB (necesita boto3 y credenciales)
"""
import json, random, sys, pathlib
random.seed(42)
P = "redtelecorp"
OUT = pathlib.Path(__file__).resolve().parent.parent / "contracts" / "semilla"
ZONAS = [  # zonaId, nombre, tipoZona, lat, lon
    ("Z01", "Miraflores", "RESIDENCIAL", -12.1211, -77.0297),
    ("Z02", "San Isidro financiero", "COMERCIAL", -12.0971, -77.0365),
    ("Z03", "Barranco", "RESIDENCIAL", -12.1491, -77.0217),
    ("Z04", "Santiago de Surco", "RESIDENCIAL", -12.1366, -76.9916),
    ("Z05", "Campus UTEC", "EDUCATIVA", -12.1355, -77.0222),
    ("Z06", "Hospital Rebagliati", "MEDICA", -12.0789, -77.0400),
    ("Z07", "Aeropuerto Callao", "CRITICA", -12.0219, -77.1143),
    ("Z08", "Ate industrial", "INDUSTRIAL", -12.0450, -76.9300),
    ("Z09", "La Molina", "RESIDENCIAL", -12.0790, -76.9420),
    ("Z10", "Cercado de Lima", "CRITICA", -12.0464, -77.0428),
]
CRIT = {"RESIDENCIAL": 1, "COMERCIAL": 1.5, "EDUCATIVA": 2, "INDUSTRIAL": 2, "MEDICA": 3, "CRITICA": 3}

def generar():
    zonas = [{"pk": f"ZONA#{z}", "sk": "META", "tipoEntidad": "ZONA", "zonaId": z, "nombre": n,
              "tipoZona": t, "criticidad": CRIT[t], "lat": la, "lon": lo} for z, n, t, la, lo in ZONAS]
    antenas, k = [], 1
    for z, _, _, la, lo in ZONAS:
        for _ in range(5):
            antenas.append({"pk": f"ANTENA#A{k:03d}", "sk": "META", "tipoEntidad": "ANTENA", "antenaId": f"A{k:03d}",
                            "zonaId": z, "lat": round(la + random.uniform(-.008, .008), 5),
                            "lon": round(lo + random.uniform(-.008, .008), 5),
                            "usuariosEstimados": random.choice([300, 800, 1500, 3000]),
                            "estadoActual": "NORMAL", "ultimoGB": 95.0, "desde": "2026-11-15T00:00:00Z",
                            "anomaliaScore": 0.0, "estructural": False})
            k += 1
    clientes = [{"pk": f"DNI#{70000000 + i:08d}", "sk": f"NUM#9{87000000 + i:08d}", "nombre": f"Cliente {i}",
                 "email": f"cliente{i}@ejemplo.com", "celular": f"+519{87000000 + i:08d}"} for i in range(1, 201)]
    return {"red": zonas + antenas, "clientes": clientes}

def cargar(datos):
    import boto3
    from decimal import Decimal
    ddb = boto3.resource("dynamodb")
    for tabla, items in (("red", datos["red"]), ("clientes", datos["clientes"])):
        with ddb.Table(f"{P}-{tabla}").batch_writer() as w:
            for it in items:
                w.put_item(Item=json.loads(json.dumps(it), parse_float=Decimal))
        print(f"{P}-{tabla}: {len(items)} items")

if __name__ == "__main__":
    d = generar()
    OUT.mkdir(parents=True, exist_ok=True)
    for k, v in d.items():
        (OUT / f"{k}.json").write_text(json.dumps(v, indent=1, ensure_ascii=False))
    print(f"red: {len(d['red'])} items · clientes: {len(d['clientes'])} items -> {OUT}")
    if "--cargar" in sys.argv:
        cargar(d)
