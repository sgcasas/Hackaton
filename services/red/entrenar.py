"""λ redtelecorp-entrenar (imagen de contenedor). Lo llama la SF diaria.
Lee métricas de S3 (últimos N días), entrena el Isolation Forest y publica modelos/isoforest/<version>.joblib + latest.json."""
import json
from datetime import timedelta

import boto3

import comun
import modelo_ml


def leer_metricas(dias=7, max_filas=200000):
    s3, b, filas = boto3.client("s3"), comun.bucket(), []
    hoy = comun.ahora().date()
    for d in range(dias):
        pref = f"metricas/dt={(hoy - timedelta(days=d)).isoformat()}/"
        for pag in s3.get_paginator("list_objects_v2").paginate(Bucket=b, Prefix=pref):
            for o in pag.get("Contents", []):
                for linea in s3.get_object(Bucket=b, Key=o["Key"])["Body"].read().decode().splitlines():
                    if linea.strip():
                        x = json.loads(linea)
                        x["dt"] = comun.de_iso(x["ts"])
                        filas.append(x)
                if len(filas) >= max_filas:
                    return filas
    return filas


def handler(event, context):
    dias = int((event or {}).get("dias", 7))
    filas = leer_metricas(dias)
    ids = [a["antenaId"] for a in comun.antenas()]
    fin = comun.ahora()
    version = fin.strftime("v%Y%m%d%H%M%S")
    m = modelo_ml.entrenar(filas, ids, version, fin)
    s3 = boto3.client("s3")
    s3.put_object(Bucket=comun.bucket(), Key=f"modelos/isoforest/{version}.joblib", Body=modelo_ml.a_bytes(m))
    s3.put_object(Bucket=comun.bucket(), Key="modelos/isoforest/latest.json",
                  Body=json.dumps({"version": version, "entrenadoEn": comun.iso(fin), "filas": m["n"],
                                   "reales": len(filas)}).encode())
    return {"version": version, "filasEntrenamiento": m["n"], "filasReales": len(filas)}
