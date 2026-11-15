"""λ redtelecorp-simulador — EventBridge (1 min) -> SQS redtelecorp-metricas (README §4)."""
import json
import os
import random
import time

import boto3

import comun
import trafico

_antenas = {"t": 0, "lista": []}


def _lista_antenas():
    if time.time() - _antenas["t"] > 600 or not _antenas["lista"]:
        _antenas["lista"] = comun.antenas()
        _antenas["t"] = time.time()
    return _antenas["lista"]


def generar(antenas, escenario, dt, rng=random):
    ts = comun.iso(dt.replace(second=0))
    msgs = []
    for a in antenas:
        v = trafico.aplicar_escenario(escenario, a, trafico.normal(a["antenaId"], dt, rng), rng)
        msgs.append({"antenaId": a["antenaId"], "ts": ts, "gbMin": v})
    return msgs


def handler(event, context):
    if not comun.region_activa():
        return {"omitido": "region pasiva"}
    try:
        escenario = json.loads(comun.param("simulador/escenario", '{"tipo":"normal"}', ttl=0))
    except ValueError:
        escenario = {"tipo": "normal"}
    msgs = generar(_lista_antenas(), escenario, comun.ahora())
    sqs, url = boto3.client("sqs"), os.environ["COLA_METRICAS_URL"]
    for i in range(0, len(msgs), 10):
        lote = msgs[i:i + 10]
        r = sqs.send_message_batch(QueueUrl=url, Entries=[
            {"Id": str(j), "MessageBody": json.dumps(m)} for j, m in enumerate(lote)])
        if r.get("Failed"):
            raise RuntimeError(f"SQS rechazó {len(r['Failed'])} mensajes")
    return {"enviadas": len(msgs), "escenario": escenario}
