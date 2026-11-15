"""Utilidades compartidas del lado red (B). Nombres y formatos según README.md."""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

PROYECTO = os.environ.get("PROYECTO", "redtelecorp")
PARAM_PREFIX = os.environ.get("PARAM_PREFIX", "/redtelecorp")

ESTADOS_FALLA = ("INHABILITADA", "CAIDA", "SATURADA")
GRAVEDAD = {"INHABILITADA": 4, "CAIDA": 3, "SATURADA": 2, "ANOMALIA": 1, "NINGUNA": 0}

_cache_param = {}


# ---------- entorno ----------
def region():
    return os.environ.get("AWS_REGION", "us-east-1")


def nombre_tabla(nombre):
    return os.environ.get(f"TABLA_{nombre.upper()}", f"{PROYECTO}-{nombre}")


def tabla(nombre):
    return boto3.resource("dynamodb").Table(nombre_tabla(nombre))


def bucket():
    return os.environ["BUCKET"]


# ---------- Parameter Store ----------
def param(nombre, defecto=None, ttl=60, cifrado=False):
    """Lee /redtelecorp/<nombre> con caché de `ttl` segundos. Si no existe devuelve `defecto`."""
    ahora = time.time()
    c = _cache_param.get(nombre)
    if c and ahora - c[1] < ttl:
        return c[0]
    try:
        v = boto3.client("ssm").get_parameter(Name=f"{PARAM_PREFIX}/{nombre}", WithDecryption=cifrado)["Parameter"]["Value"]
    except Exception:
        v = defecto
    _cache_param[nombre] = (v, ahora)
    return v


def limpiar_cache():
    _cache_param.clear()


def region_activa():
    """Regla del README §1: solo la región activa inicia cosas."""
    return param("region_activa", "us-east-1", ttl=30) == region()


def umbrales():
    return float(param("umbrales/caida", "60")), float(param("umbrales/saturada", "129"))


def clasificar(gb, caida=60.0, saturada=129.0):
    if gb <= 0:
        return "INHABILITADA"
    if gb < caida:
        return "CAIDA"
    if gb > saturada:
        return "SATURADA"
    return "NORMAL"


# ---------- tiempo ----------
def ahora():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def de_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def ttl_dias(d):
    return int((ahora() + timedelta(days=d)).timestamp())


# ---------- JSON / Decimal ----------
def a_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def de_ddb(obj):
    if isinstance(obj, list):
        return [de_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: de_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


# ---------- Red ----------
def antenas(zona_id=None):
    t = tabla("red")
    items, kw = [], {"IndexName": "por-tipo", "KeyConditionExpression": Key("tipoEntidad").eq("ANTENA")}
    while True:
        r = t.query(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    items = [de_ddb(i) for i in items]
    return [a for a in items if zona_id is None or a["zonaId"] == zona_id]


def zona(zona_id):
    it = tabla("red").get_item(Key={"pk": f"ZONA#{zona_id}", "sk": "META"}).get("Item")
    return de_ddb(it) if it else None


# ---------- Step Functions ----------
def arn_sf_incidente():
    return os.environ.get("SF_INCIDENTE_ARN")


def iniciar_incidente(nombre, entrada):
    """StartExecution idempotente (README §5). Devuelve True si se inició."""
    if not region_activa():
        return False
    try:
        boto3.client("stepfunctions").start_execution(
            stateMachineArn=arn_sf_incidente(), name=nombre[:80], input=json.dumps(entrada))
        return True
    except Exception as e:  # ExecutionAlreadyExists u otro duplicado
        if "ExecutionAlreadyExists" in type(e).__name__ or "ExecutionAlreadyExists" in str(e):
            return False
        raise


# ---------- IA (GCP) con failover desde quien llama ----------
def llamar_ia(ruta, cuerpo, timeout=8):
    """POST a servicio-ia: principal -> secundaria -> None (se sigue sin IA)."""
    token = param("gcp/token", "", cifrado=True) or ""
    for clave in ("gcp/url_principal", "gcp/url_secundaria"):
        url = param(clave, "pendiente")
        if not url or url == "pendiente":
            continue
        try:
            req = urllib.request.Request(
                url.rstrip("/") + ruta, data=json.dumps(cuerpo, default=str).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"IA {clave} falló: {e}")
    return None
