"""Utilidades compartidas por las Lambdas del lado usuario (parte A).
Nombres, llaves y enums según README.md (contrato A/B)."""
import json
import logging
import math
import os
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
P = os.environ.get("PROYECTO", "redtelecorp")

T_INCIDENCIAS = os.environ.get("TABLA_INCIDENCIAS", f"{P}-incidencias")
T_RED = os.environ.get("TABLA_RED", f"{P}-red")
T_INCIDENTES = os.environ.get("TABLA_INCIDENTES", f"{P}-incidentes")
T_CLIENTES = os.environ.get("TABLA_CLIENTES", f"{P}-clientes")
T_NOTIFICACIONES = os.environ.get("TABLA_NOTIFICACIONES", f"{P}-notificaciones")
T_AGREGADOS = os.environ.get("TABLA_AGREGADOS", f"{P}-agregados")
BUCKET = os.environ.get("BUCKET", "")
PARAM_PREFIX = os.environ.get("PARAM_PREFIX", f"/{P}").rstrip("/")

ESTADOS = ["REGISTRADA", "EN_REVISION", "EN_ATENCION", "SOLUCIONADA", "CERRADA"]
TIPOS = ["SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "SIN_SENAL", "OTRO"]
URGENCIAS = ["BAJA", "MEDIA", "ALTA"]

_clientes_aws = {}
_recursos = {}


def aws(servicio):
    """Cliente boto3 reutilizado entre invocaciones."""
    if servicio not in _clientes_aws:
        _clientes_aws[servicio] = boto3.client(servicio, region_name=REGION)
    return _clientes_aws[servicio]


def tabla(nombre):
    if nombre not in _recursos:
        _recursos[nombre] = boto3.resource("dynamodb", region_name=REGION).Table(nombre)
    return _recursos[nombre]


# ---------------- Parameter Store (/redtelecorp/..., caché de 60 s) ----------------
_cache_params = {"t": 0, "valores": {}}

DEFAULTS = {
    "region_activa": "us-east-1",
    "regla/usuarios": "10",
    "regla/horas": "24",
    "regla/tope_dni": "5",
    "gcp/url_principal": "pendiente",
    "gcp/url_secundaria": "pendiente",
    "gcp/token": "",
    "notif/remitente": "",
    "notif/topic_arn": "",
}


def params():
    """Lee todo /redtelecorp/ de Parameter Store. Si falla, usa los valores por defecto."""
    ahora = time.time()
    if ahora - _cache_params["t"] < 60 and _cache_params["valores"]:
        return _cache_params["valores"]
    valores = dict(DEFAULTS)
    try:
        pag = aws("ssm").get_paginator("get_parameters_by_path")
        for pagina in pag.paginate(Path=PARAM_PREFIX + "/", Recursive=True, WithDecryption=True):
            for p in pagina["Parameters"]:
                valores[p["Name"][len(PARAM_PREFIX) + 1:]] = p["Value"]
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo leer Parameter Store, uso defaults: %s", e)
    _cache_params.update(t=ahora, valores=valores)
    return valores


# ---------------- Utilidades ----------------
def iso(dt=None):
    """Fecha ISO 8601 en UTC con Z y milisegundos (README §2)."""
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_us(dt=None):
    """Para la llave HIST#: con microsegundos, para que ordene bien (README §3)."""
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def a_decimal(obj):
    """DynamoDB no acepta float: los convierte a Decimal."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def a_json(obj):
    def conv(o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        if isinstance(o, set):
            return sorted(o)
        raise TypeError(type(o))
    # Una sola línea y compacto (regla de Athena, README §8).
    return json.dumps(obj, default=conv, ensure_ascii=False, separators=(",", ":"))


def distancia_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def llamar_ia(ruta, cuerpo, timeout=8):
    """Llama a servicio-ia (README §6): primero url_principal (us-east4), luego url_secundaria
    (us-central1). Devuelve la respuesta o None si las dos fallan (se sigue sin IA)."""
    p = params()
    for clave in ("gcp/url_principal", "gcp/url_secundaria"):
        url = p.get(clave, "")
        if not url.startswith("http"):
            continue
        try:
            req = urllib.request.Request(
                url.rstrip("/") + ruta,
                data=json.dumps(cuerpo).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {p.get('gcp/token', '')}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            log.warning("servicio-ia falló en %s: %s", url, e)
    return None


def respuesta(status, cuerpo):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
        },
        "body": a_json(cuerpo),
    }
