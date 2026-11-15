"""λ redtelecorp-api-operador: consulta del usuario y rutas del panel (ARQUITECTURA §8, README §3 y §5).

GET   /reportes/{codigo}                    público: el usuario consulta su reporte
GET   /operador/incidencias?estado=&zona=   Cognito
PATCH /operador/incidencias/{id}/estado     Cognito
GET   /operador/incidentes                  Cognito
POST  /operador/incidentes/{id}/resolver    Cognito → SendTaskSuccess {resolucion, comentario}
GET   /operador/analitica/{tipo}            Cognito; tipo = ZONA | ANTENA | TIPO | DURACION
GET   /operador/antenas                     Cognito
GET   /operador/zonas                       Cognito
"""
import json
from collections import Counter

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from comun import (
    ESTADOS, T_AGREGADOS, T_INCIDENCIAS, T_INCIDENTES, T_RED,
    aws, iso, iso_us, log, respuesta, tabla,
)

# Transiciones que puede hacer el operador a mano.
TRANSICIONES = {
    "REGISTRADA": {"EN_REVISION", "EN_ATENCION", "SOLUCIONADA", "CERRADA"},
    "EN_REVISION": {"EN_ATENCION", "SOLUCIONADA", "CERRADA"},
    "EN_ATENCION": {"SOLUCIONADA", "CERRADA"},
    "SOLUCIONADA": {"CERRADA", "EN_ATENCION"},  # reabrir si no quedó bien
    "CERRADA": set(),
    "RECHAZADO": set(),
}


def handler(event, _context):
    metodo = event.get("httpMethod", "GET")
    recurso = event.get("resource", "")
    pp = event.get("pathParameters") or {}
    qs = event.get("queryStringParameters") or {}
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return respuesta(400, {"error": "JSON inválido"})
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    operador = claims.get("email", "operador")

    try:
        if recurso == "/reportes/{codigo}" and metodo == "GET":
            return consultar_reporte(pp["codigo"])
        if recurso == "/operador/incidencias" and metodo == "GET":
            return listar_incidencias(qs.get("estado"), qs.get("zona"))
        if recurso == "/operador/incidencias/{id}/estado" and metodo == "PATCH":
            return cambiar_estado(pp["id"], body.get("estado"), body.get("comentario", ""), operador)
        if recurso == "/operador/incidentes" and metodo == "GET":
            return listar_incidentes()
        if recurso == "/operador/incidentes/{id}/resolver" and metodo == "POST":
            return resolver_incidente(pp["id"], body, operador)
        if recurso == "/operador/analitica/{tipo}" and metodo == "GET":
            return analitica(pp["tipo"].upper())
        if recurso == "/operador/antenas" and metodo == "GET":
            return respuesta(200, por_tipo("ANTENA"))
        if recurso == "/operador/zonas" and metodo == "GET":
            return respuesta(200, por_tipo("ZONA"))
        return respuesta(404, {"error": f"Ruta no encontrada: {metodo} {recurso}"})
    except ClientError as e:
        log.exception("Error de AWS")
        return respuesta(500, {"error": e.response["Error"]["Message"]})


# ---------------------------------------------------------------------------
def consultar_reporte(codigo):
    items = tabla(T_INCIDENCIAS).query(KeyConditionExpression=Key("pk").eq(f"INC#{codigo}"))["Items"]
    meta = next((i for i in items if i["sk"] == "META"), None)
    if not meta:
        # El 202 llega antes de que la Lambda procese el mensaje.
        return respuesta(404, {"error": "Aún no encontramos tu reporte. Si lo acabas de enviar, espera unos segundos."})
    historial = sorted((i for i in items if i["sk"].startswith("HIST#")), key=lambda i: i["sk"])
    publico = {k: meta.get(k) for k in (
        "codigo", "estado", "zonaId", "tipo", "tipoIA", "fechaRegistro", "fechaHoraUsuario",
        "descripcion", "incidenteId", "motivoRechazo")}
    publico["dni"] = "****" + str(meta.get("dni", ""))[-3:]
    publico["historial"] = [{
        "fecha": h["sk"][5:], "estadoAnterior": h.get("estadoAnterior"), "estadoNuevo": h.get("estadoNuevo"),
        "actor": h.get("actor"), "comentario": h.get("comentario"),
    } for h in historial]
    return respuesta(200, publico)


def listar_incidencias(estado, zona):
    t = tabla(T_INCIDENCIAS)
    if estado:  # GSI por-estado: estado + prioridad (P1 primero)
        items = t.query(IndexName="por-estado", KeyConditionExpression=Key("estado").eq(estado), Limit=300)["Items"]
    elif zona:
        items = t.query(IndexName="por-zona", KeyConditionExpression=Key("zonaId").eq(zona),
                        ScanIndexForward=False, Limit=300)["Items"]
    else:
        items = escanear(T_INCIDENCIAS, Attr("sk").eq("META"))
        items.sort(key=lambda i: i.get("fechaRegistro", ""), reverse=True)
        items = items[:300]
    for i in items:  # el panel no necesita los datos de contacto
        for k in ("email", "celular"):
            i.pop(k, None)
    return respuesta(200, items)


def cambiar_estado(codigo, nuevo, comentario, operador):
    if nuevo not in ESTADOS:
        return respuesta(400, {"error": f"Estado inválido. Usa uno de {ESTADOS}"})
    t = tabla(T_INCIDENCIAS)
    meta = t.get_item(Key={"pk": f"INC#{codigo}", "sk": "META"}).get("Item")
    if not meta:
        return respuesta(404, {"error": "Incidencia no encontrada"})
    actual = meta["estado"]
    if nuevo not in TRANSICIONES.get(actual, set()):
        return respuesta(409, {"error": f"No se puede pasar de {actual} a {nuevo}"})
    try:
        t.update_item(
            Key={"pk": f"INC#{codigo}", "sk": "META"},
            UpdateExpression="SET estado = :n",
            ConditionExpression="estado = :a",  # nadie la cambió mientras tanto
            ExpressionAttributeValues={":n": nuevo, ":a": actual},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respuesta(409, {"error": "La incidencia cambió mientras tanto, recarga"})
        raise
    t.put_item(Item={
        "pk": f"INC#{codigo}", "sk": f"HIST#{iso_us()}",
        "estadoAnterior": actual, "estadoNuevo": nuevo, "actor": "OPERADOR",
        "comentario": f"{comentario} ({operador})".strip(),
    })
    return respuesta(200, {"codigo": codigo, "estadoAnterior": actual, "estado": nuevo})


def listar_incidentes():
    items = escanear(T_INCIDENTES, Attr("pk").begins_with("INCID#"))  # ZONALOCK# es interno de B
    for i in items:
        i["esperandoOperador"] = i.get("estado") == "ESPERANDO_OPERADOR" and bool(i.get("taskToken"))
        i.pop("taskToken", None)
        i.pop("executionArn", None)
    items.sort(key=lambda i: str(i.get("inicio", "")), reverse=True)
    return respuesta(200, items)


def resolver_incidente(incidente_id, body, operador):
    """README §5: la SF guarda taskToken y pasa a ESPERANDO_OPERADOR; aquí se responde."""
    resolucion = body.get("resolucion")
    if resolucion not in ("RESTABLECIDO", "SIN_FALLA"):
        return respuesta(400, {"error": "resolucion debe ser RESTABLECIDO o SIN_FALLA"})
    t = tabla(T_INCIDENTES)
    item = t.get_item(Key={"pk": f"INCID#{incidente_id}", "sk": "META"}).get("Item")
    if not item:
        return respuesta(404, {"error": "Incidente no encontrado"})
    if not item.get("taskToken"):
        return respuesta(409, {"error": "Este incidente no está esperando al operador"})
    comentario = f"{body.get('comentario', '')} ({operador})".strip()
    try:
        aws("stepfunctions").send_task_success(
            taskToken=item["taskToken"], output=json.dumps({"resolucion": resolucion, "comentario": comentario}))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            return respuesta(409, {"error": "La Step Function ya no espera este token"})
        raise
    t.update_item(Key={"pk": f"INCID#{incidente_id}", "sk": "META"}, UpdateExpression="REMOVE taskToken")
    return respuesta(200, {"incidenteId": incidente_id, "resolucion": resolucion})


def analitica(tipo):
    if tipo not in ("ZONA", "ANTENA", "TIPO", "DURACION"):
        return respuesta(400, {"error": "tipo debe ser ZONA, ANTENA, TIPO o DURACION"})
    # 1) Lo que precalcula la SF diaria de B (README §3, redtelecorp-agregados).
    r = tabla(T_AGREGADOS).query(KeyConditionExpression=Key("pk").eq(f"AGG#{tipo}"), ScanIndexForward=False, Limit=1)
    if r["Items"]:
        it = r["Items"][0]
        datos = sorted(({"clave": k, "total": v} for k, v in (it.get("datos") or {}).items()),
                       key=lambda d: d["total"], reverse=True)
        return respuesta(200, {"fuente": "agregados", "fecha": it["sk"], "datos": datos})
    # 2) Si la SF diaria aún no corrió, se calcula en vivo (útil en la demo).
    cont = Counter()
    if tipo in ("ANTENA", "DURACION"):
        for inc in escanear(T_INCIDENTES, Attr("pk").begins_with("INCID#")):
            if tipo == "ANTENA":
                for a in inc.get("antenas") or []:
                    cont[a] += 1
            elif inc.get("duracionMin") is not None:
                cont[inc.get("zonaId", "?")] += int(inc["duracionMin"])  # minutos acumulados por zona
    else:
        campo = "zonaId" if tipo == "ZONA" else "tipoIA"
        for i in escanear(T_INCIDENCIAS, Attr("sk").eq("META")):
            if i.get("estado") != "RECHAZADO":
                cont[i.get(campo) or i.get("tipo") or "?"] += 1
    datos = [{"clave": k, "total": v} for k, v in cont.most_common(20)]
    return respuesta(200, {"fuente": "en_vivo", "fecha": iso()[:10], "datos": datos})


def por_tipo(tipo_entidad):
    items, kwargs = [], {"IndexName": "por-tipo", "KeyConditionExpression": Key("tipoEntidad").eq(tipo_entidad)}
    while True:
        r = tabla(T_RED).query(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def escanear(nombre_tabla, filtro):
    items, kwargs = [], {"FilterExpression": filtro}
    while True:
        r = tabla(nombre_tabla).scan(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r or len(items) > 3000:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
