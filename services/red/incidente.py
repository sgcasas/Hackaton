"""λ redtelecorp-incidente — pasos de la SF redtelecorp-incidente (README §5 y §7).
Cada estado de la SF invoca esta función con {"accion": ..., "incidenteId": ..., ...}."""
import json
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import comun

ABIERTOS = ("ABIERTO", "EN_ATENCION", "ESPERANDO_OPERADOR", "RESTABLECIDO")
ACTIVAS = ("REGISTRADA", "EN_REVISION", "EN_ATENCION")
ESTADO_INCIDENCIA_SEGUN_INCIDENTE = {"ABIERTO": "EN_REVISION", "ESPERANDO_OPERADOR": "EN_REVISION",
                                     "EN_ATENCION": "EN_ATENCION"}
MENSAJES = {  # README §7 (español peruano)
    "EN_REVISION": ("Tu reporte está en evaluación", "Tu inconveniente está siendo evaluado."),
    "EN_ATENCION": ("Incidencia identificada en tu zona",
                    "Identificamos una incidencia en tu zona y ya la estamos atendiendo."),
    "SOLUCIONADA": ("Servicio restablecido", "El servicio en tu zona fue restablecido. Gracias por tu paciencia."),
}


def _cond_fallida(e):
    return isinstance(e, ClientError) and e.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _inc(iid):
    it = comun.tabla("incidentes").get_item(Key={"pk": f"INCID#{iid}", "sk": "META"}).get("Item")
    if not it:
        raise ValueError(f"Incidente {iid} no existe")
    return comun.de_ddb(it)


def _set_inc(iid, **campos):
    comun.tabla("incidentes").update_item(
        Key={"pk": f"INCID#{iid}", "sk": "META"},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in campos),
        ExpressionAttributeNames={f"#{k}": k for k in campos},
        ExpressionAttributeValues=comun.a_ddb({f":{k}": v for k, v in campos.items()}))


def _incidencias_zona(zona_id, desde_iso):
    t, items = comun.tabla("incidencias"), []
    kw = {"IndexName": "por-zona",
          "KeyConditionExpression": Key("zonaId").eq(zona_id) & Key("fechaRegistro").gte(desde_iso)}
    while True:
        r = t.query(**kw)
        items += [comun.de_ddb(i) for i in r["Items"] if i.get("sk") == "META"]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def _vinculadas(inc):
    horas = int(comun.param("regla/horas", "24"))
    desde = comun.iso(comun.de_iso(inc["inicio"]) - timedelta(hours=horas))
    return [i for i in _incidencias_zona(inc["zonaId"], desde) if i.get("incidenteId") == inc["id"]]


def _cambiar_estado_incidencia(codigo, anterior, nuevo, comentario, extra=None):
    t = comun.tabla("incidencias")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # con microsegundos: ordena bien
    campos = {"estado": nuevo, **(extra or {})}
    t.update_item(Key={"pk": f"INC#{codigo}", "sk": "META"},
                  UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in campos),
                  ExpressionAttributeNames={f"#{k}": k for k in campos},
                  ExpressionAttributeValues=comun.a_ddb({f":{k}": v for k, v in campos.items()}))
    if anterior != nuevo:
        t.put_item(Item={"pk": f"INC#{codigo}", "sk": f"HIST#{ts}", "estadoAnterior": anterior,
                         "estadoNuevo": nuevo, "actor": "SF", "comentario": comentario})


# ---------------- acciones ----------------
def abrir(ev):
    """Crea el incidente o, si la zona ya tiene uno abierto, se une a él (lock por zona)."""
    e, iid = ev["entrada"], ev["ejecucion"]
    t, zona_id = comun.tabla("incidentes"), e["zonaId"]
    espera = int(comun.param("sf/espera_seg", "300"))
    lock = {"pk": f"ZONALOCK#{zona_id}", "sk": "META", "incidenteId": iid}
    try:
        t.put_item(Item=lock, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as err:
        if not _cond_fallida(err):
            raise
        actual = t.get_item(Key={"pk": f"ZONALOCK#{zona_id}", "sk": "META"})["Item"]["incidenteId"]
        try:
            existente = _inc(actual)
        except ValueError:
            existente = {"estado": "CERRADO"}
        if existente["estado"] in ABIERTOS:
            if e.get("antenaId") and e["antenaId"] not in existente.get("antenas", []):
                _set_inc(actual, antenas=existente.get("antenas", []) + [e["antenaId"]])
            vincular({"incidenteId": actual})
            notificar({"incidenteId": actual,
                       "estado": ESTADO_INCIDENCIA_SEGUN_INCIDENTE.get(existente["estado"], "EN_REVISION")})
            return {"existente": True, "incidenteId": actual, "esperaSeg": espera}
        t.put_item(Item=lock, ConditionExpression="incidenteId = :v", ExpressionAttributeValues={":v": actual})
    inc = {"pk": f"INCID#{iid}", "sk": "META", "id": iid, "origen": e["origen"], "evento": e["evento"],
           "zonaId": zona_id, "antenas": [e["antenaId"]] if e.get("antenaId") else [], "reportes": 0,
           "usuarios": 0, "afectados": 0, "prioridad": "P4", "estado": "ABIERTO", "inicio": e["detectadoEn"],
           "detectadoPor": "SUPERVISION" if e["origen"] == "ANTENA" else "USUARIOS", "falla": "NINGUNA",
           "executionArn": ev.get("executionArn", ""), "intentos": 0, "anomaliaScore": e.get("anomaliaScore")}
    t.put_item(Item=comun.a_ddb({k: v for k, v in inc.items() if v is not None}))
    return {"existente": False, "incidenteId": iid, "esperaSeg": espera}


def vincular(ev):
    inc = _inc(ev["incidenteId"])
    horas = int(comun.param("regla/horas", "24"))
    desde = comun.iso(comun.de_iso(inc["inicio"]) - timedelta(hours=horas))
    destino = ESTADO_INCIDENCIA_SEGUN_INCIDENTE.get(inc["estado"], "EN_REVISION")
    nuevas, todas = 0, []
    for i in _incidencias_zona(inc["zonaId"], desde):
        if i.get("estado") not in ACTIVAS:
            continue
        if i.get("incidenteId") not in (None, inc["id"]):
            continue
        if i.get("incidenteId") is None:
            _cambiar_estado_incidencia(i["codigo"], i["estado"], destino if i["estado"] == "REGISTRADA" else i["estado"],
                                       f"Vinculada al incidente {inc['id']}",
                                       {"incidenteId": inc["id"], "prioridad": inc.get("prioridad", "P4")})
            nuevas += 1
        todas.append(i)
    usuarios = len({i["dni"] for i in todas})
    campos = {"reportes": len(todas), "usuarios": usuarios}
    if todas:
        campos["primerReporte"] = min(i["fechaRegistro"] for i in todas)
    _set_inc(inc["id"], **campos)
    return {"vinculadas": nuevas, "reportes": len(todas), "usuarios": usuarios}


def notificar(ev):
    """Deduplicado por NOTIF#<incidenteId>#<estado>#<dni> (README §3/§7)."""
    iid, estado = ev["incidenteId"], ev["estado"]
    asunto, texto = MENSAJES[estado]
    inc = _inc(iid)
    por_dni = {}
    for i in sorted(_vinculadas(inc), key=lambda x: x["fechaRegistro"]):
        por_dni[i["dni"]] = i
    t = comun.tabla("notificaciones")
    remitente = comun.param("notif/remitente", "pendiente@correo.com")
    enviadas = omitidas = fallidas = 0
    for dni, i in por_dni.items():
        pk = f"NOTIF#{iid}#{estado}#{dni}"
        try:
            t.put_item(Item={"pk": pk, "canal": ",".join(i.get("canales", [])), "enviadoEn": comun.iso(comun.ahora()),
                             "ttl": comun.ttl_dias(30)}, ConditionExpression="attribute_not_exists(pk)")
        except ClientError as err:
            if _cond_fallida(err):
                omitidas += 1
                continue
            raise
        try:
            if "email" in i.get("canales", []) and i.get("email"):
                boto3.client("ses").send_email(
                    Source=remitente, Destination={"ToAddresses": [i["email"]]},
                    Message={"Subject": {"Data": f"Red TeleCorp: {asunto}"},
                             "Body": {"Text": {"Data": f"{texto}\n\nCódigo de tu reporte: {i['codigo']}"}}})
            if "sms" in i.get("canales", []) and i.get("celular"):
                boto3.client("sns").publish(PhoneNumber=i["celular"], Message=f"Red TeleCorp: {texto}")
            enviadas += 1
        except Exception as err:  # se borra la marca para que un reintento pueda volver a enviar
            print(f"Fallo al notificar {pk}: {err}")
            t.delete_item(Key={"pk": pk})
            fallidas += 1
    return {"enviadas": enviadas, "omitidas": omitidas, "fallidas": fallidas}


def _falla_antena(a, umbral, racha):
    if a.get("estadoActual") in comun.ESTADOS_FALLA:
        return a["estadoActual"]
    if a.get("anomaliaScore", 0) >= umbral and a.get("rachaAnomalia", 0) >= racha:
        return "ANOMALIA"
    return None


def verificar(ev):
    """Correlación reportes <-> antenas de la zona (P8)."""
    inc = _inc(ev["incidenteId"])
    umbral, racha = float(comun.param("ml/umbral_anomalia", "0.6")), int(comun.param("ml/racha", "3"))
    fallas = []
    for a in comun.antenas(inc["zonaId"]):
        f = _falla_antena(a, umbral, racha)
        if f is None and a["antenaId"] in inc.get("antenas", []) and inc["origen"] == "ANTENA":
            f = inc["evento"] if inc["evento"] in comun.GRAVEDAD else None  # la que disparó ya pudo recuperarse
        if f:
            fallas.append({"antenaId": a["antenaId"], "falla": f, "desde": a.get("desde", inc["inicio"]),
                           "usuariosEstimados": a.get("usuariosEstimados", 0)})
    falla = max((f["falla"] for f in fallas), key=lambda x: comun.GRAVEDAD[x], default="NINGUNA")
    detectado = inc.get("detectadoPor", "USUARIOS")
    if inc["origen"] == "ZONA" and fallas and inc.get("primerReporte"):
        if min(f["desde"] for f in fallas) < inc["primerReporte"]:
            detectado = "SUPERVISION"
    _set_inc(inc["id"], antenas=[f["antenaId"] for f in fallas], falla=falla, detectadoPor=detectado,
             afectadosAntenas=sum(f["usuariosEstimados"] for f in fallas))
    return {"falla": falla, "antenasFalla": [f["antenaId"] for f in fallas], "detectadoPor": detectado}


def calcular_prioridad(tipo_zona, afectados, falla, pesos):
    crit = pesos["criticidad"].get(tipo_zona, 1)
    fa = next(f for lim, f in pesos["afectados"] if afectados < lim)
    ff = pesos["falla"].get(falla, 1)
    puntaje = crit * fa * ff
    banda = next(b for thr, b in pesos["bandas"] if puntaje >= thr)
    return banda, puntaje


def prioridad(ev):
    inc = _inc(ev["incidenteId"])
    z = comun.zona(inc["zonaId"]) or {"tipoZona": "RESIDENCIAL"}
    pesos = json.loads(comun.param("prioridad/pesos", "{}"))
    afectados = int(inc.get("usuarios", 0)) + int(inc.get("afectadosAntenas", 0))
    banda, puntaje = calcular_prioridad(z["tipoZona"], afectados, inc.get("falla", "NINGUNA"), pesos)
    _set_inc(inc["id"], prioridad=banda, afectados=afectados, puntajePrioridad=puntaje)
    t = comun.tabla("incidencias")
    for i in _vinculadas(inc):
        t.update_item(Key={"pk": f"INC#{i['codigo']}", "sk": "META"}, UpdateExpression="SET prioridad = :p",
                      ExpressionAttributeValues={":p": banda})
    return {"prioridad": banda, "afectados": afectados, "puntaje": puntaje}


def resumir(ev):
    inc = _inc(ev["incidenteId"])
    ants = [a for a in comun.antenas(inc["zonaId"]) if a["antenaId"] in inc.get("antenas", [])]
    reps = [{"tipo": i.get("tipo"), "tipoIA": i.get("tipoIA"), "descripcion": i.get("descripcion", "")[:300]}
            for i in _vinculadas(inc)[:20]]
    cuerpo = {"incidente": {k: inc.get(k) for k in ("id", "origen", "evento", "zonaId", "falla", "afectados",
                                                      "prioridad", "inicio", "detectadoPor")},
              "antenas": [{k: a.get(k) for k in ("antenaId", "estadoActual", "ultimoGB", "anomaliaScore", "desde")}
                          for a in ants], "reportes": reps}
    r = comun.llamar_ia("/resumir", cuerpo)
    if r and r.get("resumen"):
        _set_inc(inc["id"], resumenIA=r["resumen"], causaProbable=r.get("causaProbable", ""))
        return {"ia": True}
    return {"ia": False}


def estado(ev):
    inc = _inc(ev["incidenteId"])
    _set_inc(inc["id"], estado=ev["estadoIncidente"])
    n = 0
    for i in _vinculadas(inc):
        if i["estado"] in ("CERRADA", "RECHAZADO") or i["estado"] == ev["estadoIncidencias"]:
            continue
        _cambiar_estado_incidencia(i["codigo"], i["estado"], ev["estadoIncidencias"],
                                   ev.get("comentario", f"Incidente {inc['id']}: {ev['estadoIncidente']}"))
        n += 1
    return {"actualizadas": n}


def revisar(ev):
    inc = _inc(ev["incidenteId"])
    umbral, racha = float(comun.param("ml/umbral_anomalia", "0.6")), int(comun.param("ml/racha", "3"))
    pendientes = [a["antenaId"] for a in comun.antenas(inc["zonaId"])
                  if a["antenaId"] in inc.get("antenas", []) and _falla_antena(a, umbral, racha)]
    if not pendientes:
        return {"restablecido": True, "agotado": False, "pendientes": []}
    intentos = int(inc.get("intentos", 0)) + 1
    _set_inc(inc["id"], intentos=intentos)
    return {"restablecido": False, "agotado": intentos >= int(comun.param("sf/max_intentos", "36")),
            "pendientes": pendientes}


def cerrar(ev):
    inc = _inc(ev["incidenteId"])
    resolucion = ev.get("resolucion") or inc.get("resolucion") or "RESTABLECIDO"
    fin = comun.ahora()
    dur = round((fin - comun.de_iso(inc["inicio"])).total_seconds() / 60, 1)
    _set_inc(inc["id"], estado="CERRADO", fin=comun.iso(fin), duracionMin=dur,
             resolucion=resolucion)
    for i in _vinculadas(inc):
        if i["estado"] not in ("CERRADA", "RECHAZADO"):
            _cambiar_estado_incidencia(i["codigo"], i["estado"], "CERRADA", f"Incidente {inc['id']} cerrado")
    reg = {k: inc.get(k) for k in ("id", "origen", "evento", "zonaId", "antenas", "afectados", "usuarios",
                                   "reportes", "prioridad", "inicio", "detectadoPor", "falla")}
    reg.update({"fin": comun.iso(fin), "duracionMin": dur, "resolucion": resolucion})
    boto3.client("s3").put_object(Bucket=comun.bucket(), Key=f"incidentes/dt={comun.iso(fin)[:10]}/{inc['id']}.json",
                                  Body=json.dumps(reg, separators=(",", ":"), default=str).encode())
    _soltar_lock(inc)
    return {"duracionMin": dur}


def _soltar_lock(inc):
    try:
        comun.tabla("incidentes").delete_item(Key={"pk": f"ZONALOCK#{inc['zonaId']}", "sk": "META"},
                                              ConditionExpression="incidenteId = :v",
                                              ExpressionAttributeValues={":v": inc["id"]})
    except ClientError as err:
        if not _cond_fallida(err):
            raise


def error(ev):
    """Catch de la SF: cierra el incidente con error y libera la zona."""
    print(f"Error en la SF: {json.dumps(ev, default=str)[:2000]}")
    iid = ev.get("incidenteId")
    if iid:
        try:
            inc = _inc(iid)
            _set_inc(iid, estado="CERRADO", fin=comun.iso(comun.ahora()), resolucion="ERROR",
                     error=str(ev.get("causa"))[:1000])
            _soltar_lock(inc)
        except Exception as e:
            print(f"No se pudo marcar el error: {e}")
    return {"registrado": True}


ACCIONES = {"abrir": abrir, "vincular": vincular, "notificar": notificar, "verificar": verificar,
            "prioridad": prioridad, "resumir": resumir, "estado": estado, "revisar": revisar,
            "cerrar": cerrar, "error": error}


def handler(event, context):
    return ACCIONES[event["accion"]](event)
