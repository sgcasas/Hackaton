"""Pruebas locales de la parte A con AWS simulado (moto), con las tablas de infra/template.yaml
y la semilla de contracts/semilla. Valida también los mensajes contra contracts/schemas.

Correr:  pip install -r tests/requirements.txt   y luego   python -m pytest -q tests/usuario
"""
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

import boto3
import pytest
from jsonschema import Draft202012Validator
from moto import mock_aws

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RAIZ, "services", "usuario"))

os.environ.update({
    "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
    "BUCKET": "redtelecorp-123456789012-us-east-1",
    "SF_INCIDENTE_ARN": "arn:aws:states:us-east-1:123456789012:stateMachine:redtelecorp-incidente",
})
SEMILLA = os.path.join(RAIZ, "contracts", "semilla")


def schema(nombre):
    return Draft202012Validator(json.load(open(os.path.join(RAIZ, "contracts", "schemas", f"{nombre}.schema.json"))))


def crear_tablas():
    """Mismas llaves y GSIs que infra/template.yaml (base de Casas)."""
    ddb = boto3.client("dynamodb")

    def tabla(nombre, attrs, gsis=(), solo_pk=False):
        ks = [{"AttributeName": "pk", "KeyType": "HASH"}]
        if not solo_pk:
            ks.append({"AttributeName": "sk", "KeyType": "RANGE"})
        kw = {}
        if gsis:
            kw["GlobalSecondaryIndexes"] = [{
                "IndexName": n, "Projection": {"ProjectionType": "ALL"},
                "KeySchema": [{"AttributeName": h, "KeyType": "HASH"}, {"AttributeName": r, "KeyType": "RANGE"}],
            } for n, h, r in gsis]
        ddb.create_table(TableName=f"redtelecorp-{nombre}", BillingMode="PAY_PER_REQUEST", KeySchema=ks,
                         AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs], **kw)

    tabla("incidencias", ["pk", "sk", "zonaId", "fechaRegistro", "estado", "prioridad", "dni"],
          [("por-zona", "zonaId", "fechaRegistro"), ("por-estado", "estado", "prioridad"),
           ("por-dni", "dni", "fechaRegistro")])
    tabla("red", ["pk", "sk", "tipoEntidad"], [("por-tipo", "tipoEntidad", "pk")])
    tabla("incidentes", ["pk", "sk", "zonaId", "estado"], [("por-zona-estado", "zonaId", "estado")])
    tabla("clientes", ["pk", "sk"])
    tabla("notificaciones", ["pk"], solo_pk=True)
    tabla("agregados", ["pk", "sk"])


def cargar_semilla():
    from decimal import Decimal
    ddb = boto3.resource("dynamodb")
    for nombre in ("red", "clientes"):
        with ddb.Table(f"redtelecorp-{nombre}").batch_writer() as w:
            for it in json.load(open(os.path.join(SEMILLA, f"{nombre}.json"), encoding="utf-8")):
                w.put_item(Item=json.loads(json.dumps(it), parse_float=Decimal))


@pytest.fixture
def entorno():
    with mock_aws():
        crear_tablas()
        cargar_semilla()
        boto3.client("s3").create_bucket(Bucket=os.environ["BUCKET"])
        boto3.client("ses").verify_email_identity(EmailAddress="noreply@rtc.test")
        ssm = boto3.client("ssm")
        for k, v in {"region_activa": "us-east-1", "regla/usuarios": "10", "regla/horas": "24",
                     "notif/remitente": "noreply@rtc.test", "gcp/url_principal": "pendiente"}.items():
            ssm.put_parameter(Name=f"/redtelecorp/{k}", Value=v, Type="String")
        boto3.client("stepfunctions").create_state_machine(
            name="redtelecorp-incidente", roleArn="arn:aws:iam::123456789012:role/LabRole",
            definition=json.dumps({"StartAt": "A", "States": {"A": {"Type": "Pass", "End": True}}}))
        for m in ("comun", "procesar_reporte", "api_operador"):
            sys.modules.pop(m, None)
        import api_operador
        import procesar_reporte
        yield procesar_reporte, api_operador


ZONAS = {i["zonaId"]: i for i in json.load(open(os.path.join(SEMILLA, "red.json"), encoding="utf-8"))
         if i["tipoEntidad"] == "ZONA"}


def cuerpo(i, zona="Z01", tipo="SIN_SENAL", desc="no tengo señal desde la mañana", dni=None, numero=None):
    """Lo que manda el portal (sin codigo ni fechaRegistro: esos los pone API Gateway)."""
    z = ZONAS[zona]
    return {"dni": dni or f"{70000000 + i:08d}", "numero": numero or f"9{87000000 + i:08d}",
            "canales": ["email", "sms"],
            "lat": round(float(z["lat"]) + random.uniform(-0.0015, 0.0015), 5),
            "lon": round(float(z["lon"]) + random.uniform(-0.0015, 0.0015), 5),
            "fechaHoraUsuario": "2026-11-15T04:30:00Z", "tipo": tipo, "descripcion": desc}


def evento(codigo, body, epoch_ms=None):
    """Mensaje SQS como lo deja la integración de API Gateway (requestTimeEpoch en ms)."""
    epoch_ms = epoch_ms or str(int(time.time() * 1000))
    return {"Records": [{"messageId": codigo, "body": json.dumps(body), "messageAttributes": {
        "codigo": {"stringValue": codigo, "dataType": "String"},
        "fechaRegistro": {"stringValue": epoch_ms, "dataType": "Number"}}}]}


def meta(codigo):
    return boto3.resource("dynamodb").Table("redtelecorp-incidencias").get_item(
        Key={"pk": f"INC#{codigo}", "sk": "META"}).get("Item")


def ejecuciones():
    return boto3.client("stepfunctions").list_executions(stateMachineArn=os.environ["SF_INCIDENTE_ARN"])["executions"]


# ---------------------------------------------------------------------------
def test_mensaje_cumple_el_schema_del_contrato(entorno):
    pr, _ = entorno
    rec = evento("c0ffee12-3456-4abc-8def-0123456789ab", cuerpo(1), epoch_ms="1763181312123")["Records"][0]
    msg = pr.leer_mensaje(rec)
    assert msg["fechaRegistro"] == "2025-11-15T04:35:12.123Z"
    errores = list(schema("reporte.sqs").iter_errors(msg))
    assert errores == [], [e.message for e in errores]


def test_reporte_valido_guarda_campos_del_contrato(entorno):
    pr, _ = entorno
    assert pr.handler(evento("c1", cuerpo(1, zona="Z06")), None) == {"batchItemFailures": []}
    m = meta("c1")
    for campo in ("zonaId", "fechaRegistro", "estado", "dni", "canales", "email", "celular", "antenaId", "prioridad"):
        assert campo in m, campo
    assert m["estado"] == "REGISTRADA" and m["zonaId"] == "Z06" and m["email"] == "cliente1@ejemplo.com"
    assert "tipoIA" not in m  # sin servicio-ia queda el tipo del usuario
    # S3 sin DNI ni contacto (README §8)
    s3 = boto3.client("s3")
    obj = s3.list_objects_v2(Bucket=os.environ["BUCKET"])["Contents"][0]
    assert obj["Key"].startswith(f"reportes/dt={datetime.now(timezone.utc):%Y-%m-%d}/")
    copia = json.loads(s3.get_object(Bucket=os.environ["BUCKET"], Key=obj["Key"])["Body"].read())
    assert "dni" not in copia and "email" not in copia and len(copia["dniHash"]) == 64
    # Acuse deduplicado (README §7)
    notif = boto3.resource("dynamodb").Table("redtelecorp-notificaciones").scan()["Items"]
    assert [n["pk"] for n in notif] == ["NOTIF#c1#REGISTRADA#70000001"]


def test_cliente_falso_se_rechaza(entorno):
    pr, _ = entorno
    pr.handler(evento("c2", cuerpo(1, dni="12345678")), None)
    assert meta("c2")["estado"] == "RECHAZADO"
    pr.handler(evento("c3", cuerpo(1, numero="987000002")), None)  # DNI real con el número de otro
    assert meta("c3")["motivoRechazo"] == "CLIENTE_NO_VALIDO"


def test_idempotencia(entorno):
    pr, _ = entorno
    pr.handler(evento("c4", cuerpo(4)), None)
    pr.handler(evento("c4", cuerpo(4)), None)
    items = boto3.resource("dynamodb").Table("redtelecorp-incidencias").scan()["Items"]
    assert len([i for i in items if i["pk"] == "INC#c4" and i["sk"].startswith("HIST#")]) == 1


def test_tope_de_reportes(entorno):
    pr, _ = entorno
    for k in range(6):
        pr.handler(evento(f"t{k}", cuerpo(5)), None)
    assert meta("t4")["estado"] == "REGISTRADA"
    assert meta("t5")["motivoRechazo"] == "TOPE_DE_REPORTES"


def test_regla_colectiva_y_input_de_la_sf(entorno):
    pr, _ = entorno
    for i in range(1, 10):
        pr.handler(evento(f"z{i}", cuerpo(i, zona="Z06")), None)
    pr.handler(evento("z-rep", cuerpo(1, zona="Z06")), None)  # el mismo usuario no suma
    assert ejecuciones() == []
    pr.handler(evento("z10", cuerpo(10, zona="Z06")), None)
    ejec = ejecuciones()
    assert [e["name"] for e in ejec] == [f"zona-Z06-{datetime.now(timezone.utc):%Y%m%d%H}"]
    entrada = json.loads(boto3.client("stepfunctions").describe_execution(executionArn=ejec[0]["executionArn"])["input"])
    errores = list(schema("sf-incidente.input").iter_errors(entrada))
    assert errores == [], [e.message for e in errores]
    pr.handler(evento("z11", cuerpo(11, zona="Z06")), None)  # misma hora: ExecutionAlreadyExists se ignora
    assert len(ejecuciones()) == 1


def test_region_pasiva_no_inicia_la_sf(entorno):
    pr, _ = entorno
    boto3.client("ssm").put_parameter(Name="/redtelecorp/region_activa", Value="us-west-2", Type="String", Overwrite=True)
    pr._cache_params = None
    import comun
    comun._cache_params.update(t=0, valores={})
    for i in range(1, 12):
        pr.handler(evento(f"p{i}", cuerpo(i, zona="Z06")), None)
    assert meta("p11")["estado"] == "REGISTRADA" and ejecuciones() == []


def test_seed_completo_cada_reporte_cae_en_su_zona(entorno):
    """Reportes como los de scripts/pico.py: cerca del centro de cada zona → esa zona."""
    pr, _ = entorno
    for zona in ZONAS:
        for i in range(3):
            n = int(zona[1:]) * 10 + i
            pr.handler(evento(f"s-{zona}-{i}", cuerpo(n, zona=zona)), None)
            assert meta(f"s-{zona}-{i}")["zonaId"] == zona


def test_api_consulta_y_cambio_de_estado(entorno):
    pr, api = entorno
    pr.handler(evento("a1", cuerpo(2)), None)
    r = api.handler({"httpMethod": "GET", "resource": "/reportes/{codigo}", "pathParameters": {"codigo": "a1"}}, None)
    c = json.loads(r["body"])
    assert r["statusCode"] == 200 and c["estado"] == "REGISTRADA" and c["dni"] == "****002"

    ev = {"httpMethod": "PATCH", "resource": "/operador/incidencias/{id}/estado", "pathParameters": {"id": "a1"},
          "body": json.dumps({"estado": "EN_ATENCION", "comentario": "técnico en camino"}),
          "requestContext": {"authorizer": {"claims": {"email": "op@rtc.test"}}}}
    assert api.handler(ev, None)["statusCode"] == 200
    ev["body"] = json.dumps({"estado": "PERDIDA"})
    assert api.handler(ev, None)["statusCode"] == 400
    ev["body"] = json.dumps({"estado": "EN_REVISION"})
    assert api.handler(ev, None)["statusCode"] == 409

    r = api.handler({"httpMethod": "GET", "resource": "/reportes/{codigo}", "pathParameters": {"codigo": "a1"}}, None)
    hist = json.loads(r["body"])["historial"]
    assert [(h["estadoNuevo"], h["actor"]) for h in hist] == [("REGISTRADA", "SISTEMA"), ("EN_ATENCION", "OPERADOR")]

    r = api.handler({"httpMethod": "GET", "resource": "/operador/incidencias",
                     "queryStringParameters": {"estado": "EN_ATENCION"}}, None)
    lista = json.loads(r["body"])
    assert len(lista) == 1 and "email" not in lista[0]


def test_api_antenas_analitica_y_agregados(entorno):
    pr, api = entorno
    r = api.handler({"httpMethod": "GET", "resource": "/operador/antenas"}, None)
    assert len(json.loads(r["body"])) == 50
    pr.handler(evento("b1", cuerpo(1, zona="Z01")), None)
    pr.handler(evento("b2", cuerpo(2, zona="Z06")), None)
    ev = {"httpMethod": "GET", "resource": "/operador/analitica/{tipo}", "pathParameters": {"tipo": "ZONA"}}
    c = json.loads(api.handler(ev, None)["body"])
    assert c["fuente"] == "en_vivo" and {d["clave"] for d in c["datos"]} == {"Z01", "Z06"}
    # Con el agregado de la SF diaria (README §3) se usa ese.
    boto3.resource("dynamodb").Table("redtelecorp-agregados").put_item(
        Item={"pk": "AGG#ZONA", "sk": "2026-11-15", "datos": {"Z06": 14, "Z01": 3}})
    c = json.loads(api.handler(ev, None)["body"])
    assert c["fuente"] == "agregados" and c["datos"][0] == {"clave": "Z06", "total": 14}


def test_resolver_incidente_esperando_operador(entorno):
    _, api = entorno
    t = boto3.resource("dynamodb").Table("redtelecorp-incidentes")
    t.put_item(Item={"pk": "ZONALOCK#Z06", "sk": "META", "incidenteId": "zona-Z06-2026111504"})  # lock de B: se ignora
    t.put_item(Item={"pk": "INCID#zona-Z06-2026111504", "sk": "META", "estado": "EN_ATENCION", "zonaId": "Z06"})
    ev = {"httpMethod": "POST", "resource": "/operador/incidentes/{id}/resolver",
          "pathParameters": {"id": "zona-Z06-2026111504"}, "body": json.dumps({"resolucion": "RESTABLECIDO"})}
    assert api.handler(ev, None)["statusCode"] == 409  # no espera al operador
    ev["body"] = json.dumps({"resolucion": "OTRA"})
    assert api.handler(ev, None)["statusCode"] == 400
    c = json.loads(api.handler({"httpMethod": "GET", "resource": "/operador/incidentes"}, None)["body"])
    assert len(c) == 1 and c[0]["esperandoOperador"] is False
