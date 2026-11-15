"""Manda muchos reportes de golpe a una zona (para la demo del incidente colectivo y de P9).
Solo usa la librería estándar de Python.

Uso:
  python scripts/pico.py --api https://xxxx.execute-api.us-east-1.amazonaws.com/prod --zona Z06 --n 15
  python scripts/pico.py --api ... --zona Z01 --n 300 --hilos 30      # prueba de pico
"""
import argparse
import json
import os
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RED = os.path.join(RAIZ, "contracts", "semilla", "red.json")
DESCRIPCIONES = [
    "no tengo señal desde hace una hora",
    "se cortan todas las llamadas",
    "sin internet en el celular, no carga nada",
    "desde la mañana no hay cobertura en la zona",
    "el internet está lentísimo y se cae",
]


def enviar(api, zona, i):
    # Formato de contracts/schemas/reporte.sqs.schema.json; clientes de contracts/semilla/clientes.json.
    cuerpo = {
        "dni": f"{70000000 + i:08d}",
        "numero": f"9{87000000 + i:08d}",
        "canales": ["email"],
        "lat": round(zona["lat"] + random.uniform(-0.0015, 0.0015), 5),
        "lon": round(zona["lon"] + random.uniform(-0.0015, 0.0015), 5),
        "fechaHoraUsuario": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tipo": random.choice(["SIN_SENAL", "SIN_INTERNET", "SIN_LLAMADAS"]),
        "descripcion": random.choice(DESCRIPCIONES),
    }
    req = urllib.request.Request(api.rstrip("/") + "/reportes", data=json.dumps(cuerpo).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()).get("codigo")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:120]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--zona", default="Z06")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--desde", type=int, default=11, help="primer cliente (DNI 70000000+desde), del 1 al 200")
    ap.add_argument("--hilos", type=int, default=15)
    a = ap.parse_args()

    zonas = {z["zonaId"]: z for z in json.load(open(RED, encoding="utf-8")) if z["tipoEntidad"] == "ZONA"}
    zona = zonas[a.zona]
    t0 = time.time()
    with ThreadPoolExecutor(a.hilos) as ex:
        res = list(ex.map(lambda i: enviar(a.api, zona, i), range(a.desde, a.desde + a.n)))
    ok = sum(1 for s, _ in res if s == 202)
    print(f"{ok}/{a.n} reportes aceptados (202) en {time.time() - t0:.1f} s hacia la zona {a.zona} ({zona['nombre']})")
    for s, c in res:
        if s != 202:
            print("  error:", s, c)


if __name__ == "__main__":
    main()
