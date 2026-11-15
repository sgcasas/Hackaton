"""λ redtelecorp-procesar-reporte: consume la cola redtelecorp-reportes en lotes (README §3, §4, §5, §7, §8).

Por cada reporte:
  1. Idempotencia por `codigo`: si ya existe, no se procesa de nuevo.
  2. Anti-spoofing: el par DNI + número tiene que existir en redtelecorp-clientes.
  3. Tope de reportes por DNI en la ventana (regla propia de A).
  4. Ubicación → zona: antena más cercana (Query por-tipo, tipoEntidad=ANTENA).
  5. /clasificar en servicio-ia (us-east4 → us-central1 → sin IA).
  6. Guarda META + HIST y la copia anonimizada en S3 reportes/.
  7. Acuse (NOTIF#<codigo>#REGISTRADA#<dni>) por los canales que eligió el usuario.
  8. Regla de 10/24 h → StartExecution de redtelecorp-incidente (solo en la región activa).
"""
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from comun import (
    BUCKET, REGION, T_CLIENTES, T_INCIDENCIAS, T_NOTIFICACIONES, T_RED, TIPOS, URGENCIAS,
    a_decimal, a_json, aws, distancia_km, iso, iso_us, llamar_ia, log, params, tabla,
)

SF_INCIDENTE_ARN = os.environ.get("SF_INCIDENTE_ARN", "")
PRIORIDAD_INICIAL = "P4"  # la SF de B la recalcula; hace falta para que el reporte aparezca en el GSI por-estado

_cache_antenas = {"t": 0, "lista": []}


def handler(event, _context):
    fallidos = []
    for rec in event.get("Records", []):
        try:
            procesar(leer_mensaje(rec))
        except Exception:  # noqa: BLE001
            log.exception("Error procesando %s", rec.get("messageId"))
            fallidos.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": fallidos}  # solo se reintentan los que fallaron


def leer_mensaje(rec):
    """El cuerpo es lo que mandó el portal. API Gateway agrega `codigo` (requestId) y
    `fechaRegistro` (requestTimeEpoch, en ms) como atributos del mensaje; aquí se juntan."""
    msg = json.loads(rec["body"])
    attrs = rec.get("messageAttributes") or {}
    msg.setdefault("codigo", (attrs.get("codigo") or {}).get("stringValue") or rec["messageId"])
    fecha = msg.get("fechaRegistro") or (attrs.get("fechaRegistro") or {}).get("stringValue")
    if not fecha:
        fecha = iso()
    elif str(fecha).isdigit():
        fecha = iso(datetime.fromtimestamp(int(fecha) / 1000, tz=timezone.utc))
    msg["fechaRegistro"] = fecha
    return msg


def procesar(msg):
    codigo = msg["codigo"]
    t_inc = tabla(T_INCIDENCIAS)
    if "Item" in t_inc.get_item(Key={"pk": f"INC#{codigo}", "sk": "META"}, ProjectionExpression="pk"):
        log.info("Reporte %s ya procesado, se ignora", codigo)
        return

    p = params()
    horas = int(p.get("regla/horas", 24))
    desde = iso(datetime.now(timezone.utc) - timedelta(hours=horas))
    dni, numero = str(msg.get("dni", "")), str(msg.get("numero", ""))
    base = {
        "pk": f"INC#{codigo}", "sk": "META", "codigo": codigo,
        "dni": dni, "numero": numero,
        "canales": msg.get("canales") or ["email"],
        "lat": msg.get("lat"), "lon": msg.get("lon"),
        "fechaHoraUsuario": msg.get("fechaHoraUsuario", ""),
        "fechaRegistro": msg["fechaRegistro"],
        "tipo": msg.get("tipo") if msg.get("tipo") in TIPOS else "OTRO",
        "descripcion": str(msg.get("descripcion", ""))[:1000],
        "prioridad": PRIORIDAD_INICIAL,
    }

    # 2. Anti-spoofing: ese par DNI + número tiene que existir.
    cliente = tabla(T_CLIENTES).get_item(Key={"pk": f"DNI#{dni}", "sk": f"NUM#{numero}"}).get("Item")
    if not cliente:
        guardar_rechazado(base, "CLIENTE_NO_VALIDO")
        return

    # 3. Tope de reportes por DNI dentro de la ventana.
    tope = int(p.get("regla/tope_dni", 5))
    previos = t_inc.query(
        IndexName="por-dni",
        KeyConditionExpression=Key("dni").eq(dni) & Key("fechaRegistro").gte(desde),
        Select="COUNT",
    )["Count"]
    if previos >= tope:
        guardar_rechazado(base, "TOPE_DE_REPORTES")
        return

    # 4. Zona = la de la antena más cercana.
    antena = antena_mas_cercana(float(msg["lat"]), float(msg["lon"]))

    # 5. Clasificación con IA (si falla, queda el tipo del usuario).
    ia = llamar_ia("/clasificar", {"descripcion": base["descripcion"], "tipoUsuario": base["tipo"]})

    item = {
        **base,
        "estado": "REGISTRADA",
        "email": msg.get("email") or cliente.get("email"),
        "celular": msg.get("celular") or cliente.get("celular"),
        "nombre": cliente.get("nombre"),
        "zonaId": antena["zonaId"] if antena else None,
        "antenaId": antena["antenaId"] if antena else None,
    }
    if ia and ia.get("tipo") in TIPOS:
        item["tipoIA"] = ia["tipo"]
        item["urgenciaIA"] = ia.get("urgencia") if ia.get("urgencia") in URGENCIAS else "MEDIA"
    item = {k: v for k, v in item.items() if v is not None}

    # 6. META + HIST + copia en S3.
    try:
        t_inc.put_item(Item=a_decimal(item), ConditionExpression="attribute_not_exists(pk)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return  # otro worker ya lo guardó
        raise
    guardar_historial(codigo, None, "REGISTRADA", "SISTEMA",
                      f"Reporte recibido (zona {item.get('zonaId', '-')}, tipo {item.get('tipoIA', item['tipo'])})")
    copiar_a_s3(item)

    # 7. Acuse.
    notificar(item, "REGISTRADA", "Recibimos tu reporte",
              f"Recibimos tu reporte (código {codigo}). Te avisaremos cualquier novedad.")

    # 8. Regla colectiva.
    if item.get("zonaId"):
        evaluar_regla_colectiva(item, int(p.get("regla/usuarios", 10)), desde, p.get("region_activa", "us-east-1"))


# ---------------------------------------------------------------------------
def guardar_rechazado(base, motivo):
    log.info("Reporte %s RECHAZADO: %s", base["codigo"], motivo)
    try:
        tabla(T_INCIDENCIAS).put_item(
            Item=a_decimal({**base, "estado": "RECHAZADO", "motivoRechazo": motivo}),
            ConditionExpression="attribute_not_exists(pk)",
        )
        guardar_historial(base["codigo"], None, "RECHAZADO", "SISTEMA", motivo)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def guardar_historial(codigo, anterior, nuevo, actor, comentario):
    ts = iso_us()
    tabla(T_INCIDENCIAS).put_item(Item={
        "pk": f"INC#{codigo}", "sk": f"HIST#{ts}",
        "estadoAnterior": anterior or "-", "estadoNuevo": nuevo, "actor": actor, "comentario": comentario,
    })


def antena_mas_cercana(lat, lon):
    # Caché de 5 min: son 50 antenas y no se mueven.
    if time.time() - _cache_antenas["t"] > 300 or not _cache_antenas["lista"]:
        antenas, kwargs = [], {"IndexName": "por-tipo", "KeyConditionExpression": Key("tipoEntidad").eq("ANTENA")}
        while True:
            r = tabla(T_RED).query(**kwargs)
            antenas += r["Items"]
            if "LastEvaluatedKey" not in r:
                break
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        _cache_antenas.update(t=time.time(), lista=antenas)
    mejor = None
    for a in _cache_antenas["lista"]:
        d = distancia_km(lat, lon, float(a["lat"]), float(a["lon"]))
        if mejor is None or d < mejor["distanciaKm"]:
            mejor = {"antenaId": a["antenaId"], "zonaId": a["zonaId"], "distanciaKm": d}
    return mejor


def copiar_a_s3(item):
    """README §8: sin DNI ni datos de contacto."""
    if not BUCKET:
        return
    anonimo = {
        "codigo": item["codigo"],
        "dniHash": hashlib.sha256(item["dni"].encode()).hexdigest(),
        "zonaId": item.get("zonaId"), "antenaId": item.get("antenaId"),
        "tipo": item["tipo"], "tipoIA": item.get("tipoIA"),
        "fechaRegistro": item["fechaRegistro"], "estado": item["estado"],
    }
    try:
        aws("s3").put_object(
            Bucket=BUCKET, Key=f"reportes/dt={item['fechaRegistro'][:10]}/{item['codigo']}.json",
            Body=a_json(anonimo).encode(), ContentType="application/json",
        )
    except Exception as e:  # noqa: BLE001 — el histórico no debe frenar el flujo
        log.warning("No se pudo copiar a S3: %s", e)


def notificar(item, estado, asunto, mensaje):
    """README §7: PutItem condicional en notificaciones y, solo si pasa, se envía."""
    canales = item.get("canales") or []
    try:
        tabla(T_NOTIFICACIONES).put_item(
            Item={"pk": f"NOTIF#{item['codigo']}#{estado}#{item['dni']}", "canal": ",".join(canales),
                  "enviadoEn": iso(), "ttl": int(time.time()) + 30 * 86400},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return
        raise
    remitente = params().get("notif/remitente", "")
    if "email" in canales and item.get("email") and "@" in remitente and "pendiente" not in remitente:
        try:
            aws("ses").send_email(
                Source=remitente,
                Destination={"ToAddresses": [item["email"]]},
                Message={"Subject": {"Data": f"Red TeleCorp: {asunto}"}, "Body": {"Text": {"Data": mensaje}}},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("SES falló: %s", e)
    if "sms" in canales and item.get("celular"):
        try:
            aws("sns").publish(PhoneNumber=item["celular"], Message=f"Red TeleCorp: {mensaje}"[:160])
        except Exception as e:  # noqa: BLE001
            log.warning("SNS SMS falló: %s", e)


def evaluar_regla_colectiva(item, umbral, desde, region_activa):
    """README §3 y §5: dni distintos en por-zona, últimas H horas, sin RECHAZADO."""
    zona_id = item["zonaId"]
    dnis, kwargs = set(), {
        "IndexName": "por-zona",
        "KeyConditionExpression": Key("zonaId").eq(zona_id) & Key("fechaRegistro").gte(desde),
        "ProjectionExpression": "dni, estado",
    }
    while True:
        r = tabla(T_INCIDENCIAS).query(**kwargs)
        dnis |= {i["dni"] for i in r["Items"] if i.get("estado") != "RECHAZADO"}
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    log.info("Zona %s: %d usuarios distintos (umbral %d)", zona_id, len(dnis), umbral)
    if len(dnis) < umbral:
        return

    # Regla de región activa (README §1): solo la región activa inicia la SF.
    if region_activa != REGION:
        log.info("Región %s no es la activa (%s): no se inicia la SF", REGION, region_activa)
        return

    f = item["fechaRegistro"]  # 2026-11-15T04:35:12.123Z → yyyyMMddHH
    nombre = f"zona-{zona_id}-{f[0:4]}{f[5:7]}{f[8:10]}{f[11:13]}"
    entrada = {"origen": "ZONA", "evento": "COLECTIVO", "zonaId": zona_id, "antenaId": None,
               "detectadoEn": f[:19] + "Z"}
    try:
        aws("stepfunctions").start_execution(stateMachineArn=SF_INCIDENTE_ARN, name=nombre, input=json.dumps(entrada))
        log.info("SF incidente iniciada: %s", nombre)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ExecutionAlreadyExists":
            return  # ya se está atendiendo
        # Si la SF aún no existe, no se reintenta el mensaje: el siguiente reporte de la zona lo vuelve a intentar.
        log.error("No se pudo iniciar %s (%s): %s", nombre, SF_INCIDENTE_ARN, e)
