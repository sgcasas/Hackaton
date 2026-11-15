"""λ redtelecorp-analitica — pasos de la SF redtelecorp-diaria.
acciones: region (¿soy la región activa?), recurrencia (P7), agregados (P6/P10). Consultas en Athena."""
import os
import time
from datetime import timedelta

import boto3

import comun

DB = os.environ.get("GLUE_DB", "redtelecorp")
WG = os.environ.get("ATHENA_WORKGROUP", "redtelecorp")


def athena(sql, espera_max=240):
    cli = boto3.client("athena")
    qid = cli.start_query_execution(QueryString=sql, QueryExecutionContext={"Database": DB},
                                    WorkGroup=WG)["QueryExecutionId"]
    inicio = time.time()
    while True:
        st = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() - inicio > espera_max:
            raise TimeoutError(f"Athena {qid} no terminó")
        time.sleep(1.5)
    if st["State"] != "SUCCEEDED":
        raise RuntimeError(f"Athena {qid}: {st.get('StateChangeReason')}")
    filas, kw = [], {"QueryExecutionId": qid}
    while True:
        r = cli.get_query_results(**kw)
        filas += [[c.get("VarCharValue") for c in f["Data"]] for f in r["ResultSet"]["Rows"]]
        if "NextToken" not in r:
            break
        kw["NextToken"] = r["NextToken"]
    if not filas:
        return []
    cab = filas[0]
    return [dict(zip(cab, f)) for f in filas[1:]]


def _desde(dias):
    return (comun.ahora().date() - timedelta(days=dias)).isoformat()


def sql_recurrencia(min_fallas):
    return f"""
SELECT antena, count(*) AS f30, count_if(dt >= '{_desde(7)}') AS f7
FROM incidentes CROSS JOIN UNNEST(antenas) AS t(antena)
WHERE dt >= '{_desde(30)}'
GROUP BY antena
HAVING count_if(dt >= '{_desde(7)}') >= {int(min_fallas)}
ORDER BY f7 DESC"""


SQL_AGREGADOS = {
    "ZONA": lambda: f"""
SELECT zonaId AS clave, count(*) AS incidentes, sum(afectados) AS afectados, round(avg(duracionMin),1) AS duracionProm
FROM incidentes WHERE dt >= '{_desde(30)}' GROUP BY zonaId ORDER BY incidentes DESC""",
    "ANTENA": lambda: f"""
SELECT antena AS clave, count(*) AS incidentes
FROM incidentes CROSS JOIN UNNEST(antenas) AS t(antena)
WHERE dt >= '{_desde(30)}' GROUP BY antena ORDER BY incidentes DESC LIMIT 20""",
    "TIPO": lambda: f"""
SELECT coalesce(tipoIA, tipo) AS clave, count(*) AS reportes
FROM reportes WHERE dt >= '{_desde(30)}' GROUP BY coalesce(tipoIA, tipo) ORDER BY reportes DESC""",
    "DURACION": lambda: f"""
SELECT prioridad AS clave, count(*) AS incidentes, round(avg(duracionMin),1) AS duracionProm,
       sum(CASE WHEN detectadoPor = 'SUPERVISION' THEN 1 ELSE 0 END) AS detectadosPorSupervision
FROM incidentes WHERE dt >= '{_desde(30)}' GROUP BY prioridad ORDER BY prioridad""",
}


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def recurrencia(ev):
    min_fallas = int(comun.param("recurrencia/min_fallas", "3"))
    filas = athena(sql_recurrencia(min_fallas))
    t, marcadas = comun.tabla("red"), []
    for f in filas:
        a = f["antena"]
        cuerpo = {"incidente": {"tipo": "RECURRENCIA", "antenaId": a, "fallas7d": _num(f["f7"]),
                                "fallas30d": _num(f["f30"])}, "antenas": [], "reportes": []}
        ia = comun.llamar_ia("/resumir", cuerpo) or {}
        t.update_item(Key={"pk": f"ANTENA#{a}", "sk": "META"},
                      UpdateExpression="SET estructural = :e, fallas7d = :f7, fallas30d = :f30, resumenEstructural = :r",
                      ExpressionAttributeValues=comun.a_ddb({":e": True, ":f7": _num(f["f7"]), ":f30": _num(f["f30"]),
                                                             ":r": ia.get("resumen", "")}))
        marcadas.append(a)
    return {"estructurales": marcadas}


def agregados(ev):
    t, hoy, out = comun.tabla("agregados"), comun.ahora().date().isoformat(), {}
    for tipo, sql in SQL_AGREGADOS.items():
        filas = athena(sql())
        datos = {f["clave"] or "SIN_DATO": {k: _num(v) for k, v in f.items() if k != "clave"} for f in filas}
        t.put_item(Item=comun.a_ddb({"pk": f"AGG#{tipo}", "sk": hoy, "datos": datos, "generadoEn": comun.iso(comun.ahora())}))
        out[tipo] = len(datos)
    return out


def region(ev):
    return {"activa": comun.region_activa()}


ACCIONES = {"region": region, "recurrencia": recurrencia, "agregados": agregados}


def handler(event, context):
    return ACCIONES[event["accion"]](event)
