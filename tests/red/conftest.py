import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(RAIZ, "services", "red"))
sys.path.insert(0, os.path.join(RAIZ, "scripts"))

ENV = {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_DEFAULT_REGION": "us-east-1",
       "AWS_REGION": "us-east-1", "BUCKET": "redtelecorp-123-us-east-1", "PROYECTO": "redtelecorp",
       "PARAM_PREFIX": "/redtelecorp"}

TABLAS = {
    "incidencias": [("por-zona", "zonaId", "fechaRegistro"), ("por-estado", "estado", "prioridad"),
                    ("por-dni", "dni", "fechaRegistro")],
    "red": [("por-tipo", "tipoEntidad", "pk")],
    "incidentes": [("por-zona-estado", "zonaId", "estado")],
    "clientes": [], "agregados": [],
}


def _crear_tablas():
    ddb = boto3.client("dynamodb")
    for nombre, gsis in TABLAS.items():
        attrs = {"pk", "sk"} | {a for g in gsis for a in g[1:]}
        kw = dict(TableName=f"redtelecorp-{nombre}", BillingMode="PAY_PER_REQUEST",
                  AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)],
                  KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}])
        if gsis:
            kw["GlobalSecondaryIndexes"] = [{"IndexName": g[0], "KeySchema": [
                {"AttributeName": g[1], "KeyType": "HASH"}, {"AttributeName": g[2], "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"}} for g in gsis]
        ddb.create_table(**kw)
    ddb.create_table(TableName="redtelecorp-notificaciones", BillingMode="PAY_PER_REQUEST",
                     AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}])


PARAMS = {"umbrales/caida": "60", "umbrales/saturada": "129", "regla/usuarios": "10", "regla/horas": "24",
          "ml/umbral_anomalia": "0.6", "ml/racha": "3", "region_activa": "us-east-1",
          "gcp/url_principal": "pendiente", "gcp/url_secundaria": "pendiente",
          "notif/remitente": "noc@redtelecorp.pe", "sf/espera_seg": "300", "sf/max_intentos": "3",
          "simulador/escenario": '{"tipo":"normal"}', "recurrencia/min_fallas": "3"}


@pytest.fixture
def aws(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        import comun
        comun.limpiar_cache()
        _crear_tablas()
        boto3.client("s3").create_bucket(Bucket=ENV["BUCKET"])
        url = boto3.client("sqs").create_queue(QueueName="redtelecorp-metricas")["QueueUrl"]
        monkeypatch.setenv("COLA_METRICAS_URL", url)
        ssm = boto3.client("ssm")
        PARAMS["prioridad/pesos"] = _pesos()
        for k, v in PARAMS.items():
            ssm.put_parameter(Name=f"/redtelecorp/{k}", Value=v, Type="String", Overwrite=True)
        topic = boto3.client("sns").create_topic(Name="redtelecorp-avisos")["TopicArn"]
        ssm.put_parameter(Name="/redtelecorp/notif/topic_arn", Value=topic, Type="String", Overwrite=True)
        ses = boto3.client("ses")
        ses.verify_email_identity(EmailAddress="noc@redtelecorp.pe")
        sf = boto3.client("stepfunctions")
        arn = sf.create_state_machine(name="redtelecorp-incidente", definition='{"StartAt":"F","States":{"F":{"Type":"Succeed"}}}',
                                      roleArn="arn:aws:iam::123456789012:role/x")["stateMachineArn"]
        monkeypatch.setenv("SF_INCIDENTE_ARN", arn)
        import semilla
        datos = semilla.generar()
        for t, items in (("red", datos["red"]), ("clientes", datos["clientes"])):
            with boto3.resource("dynamodb").Table(f"redtelecorp-{t}").batch_writer() as w:
                for it in items:
                    w.put_item(Item=comun.a_ddb(it))
        yield {"sf_arn": arn, "cola": url}


def _pesos():
    import re
    tpl = open(os.path.join(RAIZ, "infra", "template.yaml")).read()
    return re.search(r"Value: '(\{\"criticidad\".*\})'", tpl).group(1)
