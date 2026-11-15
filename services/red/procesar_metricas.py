"""λ redtelecorp-procesar-metricas (imagen de contenedor, con ML).
SQS redtelecorp-metricas -> umbrales + score de anomalía -> estado de la antena en `red`,
FALLA#, métricas a S3 y StartExecution de redtelecorp-incidente (README §3, §5, §8)."""
import json
import time
import uuid
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError

import comun

_modelo = {"t": 0, "m": None, "version": None}


def _cargar_modelo():
    """Lee modelos/isoforest/latest.json y el .joblib (caché 10 min). Sin modelo -> score 0."""
    if time.time() - _modelo["t"] < 600:
        return _modelo["m"]
    _modelo["t"] = time.time()
    try:
        import modelo_ml
        s3 = boto3.client("s3")
        latest = json.loads(s3.get_object(Bucket=comun.bucket(), Key="modelos/isoforest/latest.json")["Body"].read())
        if latest["version"] != _modelo["version"]:
            data = s3.get_object(Bucket=comun.bucket(), Key=f"modelos/isoforest/{latest['version']}.joblib")["Body"].read()
            _modelo["m"], _modelo["version"] = modelo_ml.de_bytes(data), latest["version"]
    except Exception as e:
        print(f"Sin modelo de ML todavía: {e}")
    return _modelo["m"]


def _puntajes(metricas):
    m = _cargar_modelo()
    if m is None:
        return [0.0] * len(metricas)
    import modelo_ml
    return modelo_ml.puntajes(m, [(x["antenaId"], x["gbMin"], comun.de_iso(x["ts"])) for x in metricas])


def _escribir_s3(filas):
    grupos = defaultdict(list)
    for f in filas:
        grupos[(f["ts"][:10], f["ts"][11:13])].append(json.dumps(f, separators=(",", ":")))
    s3 = boto3.client("s3")
    for (dt, hora), lineas in grupos.items():
        s3.put_object(Bucket=comun.bucket(), Key=f"metricas/dt={dt}/hora={hora}/{uuid.uuid4()}.jsonl",
                      Body=("\n".join(lineas) + "\n").encode())


def procesar(metricas):
    """Procesa métricas ya validadas (ordenadas por ts). Devuelve lista de eventos disparados."""
    caida, saturada = comun.umbrales()
    umbral = float(comun.param("ml/umbral_anomalia", "0.75"))
    racha_min = int(comun.param("ml/racha", "3"))
    t = comun.tabla("red")
    scores = _puntajes(metricas)
    eventos, filas, cache = [], [], {}
    for x, score in zip(metricas, scores):
        a_id, ts, gb = x["antenaId"], x["ts"], float(x["gbMin"])
        estado = comun.clasificar(gb, caida, saturada)
        if a_id not in cache:
            it = t.get_item(Key={"pk": f"ANTENA#{a_id}", "sk": "META"}).get("Item")
            if not it:
                print(f"Antena desconocida {a_id}, se ignora")
                continue
            cache[a_id] = comun.de_ddb(it)
        ant = cache[a_id]
        if ts < ant.get("ultimoTs", ""):  # métrica vieja (reentrega fuera de orden)
            filas.append({"antenaId": a_id, "ts": ts, "gbMin": gb, "estado": estado, "anomaliaScore": score})
            continue
        previo = ant.get("estadoActual", "NORMAL")
        racha = ant.get("rachaAnomalia", 0) + 1 if (estado == "NORMAL" and score >= umbral) else 0
        cambios = {"estadoActual": estado, "ultimoGB": gb, "anomaliaScore": score, "ultimoTs": ts, "rachaAnomalia": racha}
        if estado != previo:
            cambios["desde"] = ts
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in cambios)
        t.update_item(Key={"pk": f"ANTENA#{a_id}", "sk": "META"}, UpdateExpression=expr,
                      ExpressionAttributeNames={f"#{k}": k for k in cambios},
                      ExpressionAttributeValues=comun.a_ddb({f":{k}": v for k, v in cambios.items()}))
        ant.update(cambios)
        evento = None
        if estado != previo and estado in comun.ESTADOS_FALLA:
            t.put_item(Item=comun.a_ddb({"pk": f"ANTENA#{a_id}", "sk": f"FALLA#{ts}", "estado": estado,
                                         "zonaId": ant["zonaId"], "ttl": comun.ttl_dias(90)}))
            evento = estado
        elif racha == racha_min:
            evento = "ANOMALIA"
        if evento:
            entrada = {"origen": "ANTENA", "evento": evento, "zonaId": ant["zonaId"], "antenaId": a_id,
                       "detectadoEn": ts, "anomaliaScore": score}
            nombre = f"antena-{a_id}-{evento}-{ts[:16].replace('-', '').replace('T', '').replace(':', '')}"
            comun.iniciar_incidente(nombre, entrada)
            eventos.append({"nombre": nombre, **entrada})
        filas.append({"antenaId": a_id, "ts": ts, "gbMin": gb, "estado": estado, "anomaliaScore": score})
    if filas:
        _escribir_s3(filas)
    return eventos


def handler(event, context):
    validas, fallidas = [], []
    for r in event.get("Records", []):
        try:
            m = json.loads(r["body"])
            assert isinstance(m["antenaId"], str) and isinstance(m["ts"], str) and float(m["gbMin"]) >= 0
            validas.append((m, r["messageId"]))
        except Exception as e:
            print(f"Mensaje inválido {r.get('messageId')}: {e}")
            fallidas.append(r["messageId"])  # tras 3 intentos va a la DLQ (alarma)
    validas.sort(key=lambda p: p[0]["ts"])
    try:
        eventos = procesar([m for m, _ in validas])
    except ClientError as e:
        print(f"Error AWS, se reintenta el lote: {e}")
        fallidas += [mid for _, mid in validas]
        eventos = []
    return {"batchItemFailures": [{"itemIdentifier": i} for i in fallidas], "eventos": len(eventos)}
