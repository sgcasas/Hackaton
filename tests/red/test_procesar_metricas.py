import json
from datetime import timedelta

import boto3
import pytest

import comun
import entrenar
import modelo_ml
import procesar_metricas
import trafico


def _evento(metricas):
    return {"Records": [{"messageId": f"m{i}", "body": json.dumps(m)} for i, m in enumerate(metricas)]}


def _ejecuciones(arn):
    return [e["name"] for e in boto3.client("stepfunctions").list_executions(stateMachineArn=arn)["executions"]]


@pytest.fixture
def con_modelo(aws):
    r = entrenar.handler({"dias": 1}, None)  # sin histórico -> entrena con sintéticos
    assert r["filasReales"] == 0 and r["filasEntrenamiento"] > 2000
    procesar_metricas._modelo.update({"t": 0, "m": None, "version": None})
    return aws


def _metricas_normales(dt, excepto=()):
    return [{"antenaId": f"A{i:03d}", "ts": comun.iso(dt), "gbMin": trafico.normal(f"A{i:03d}", dt)}
            for i in range(1, 51) if f"A{i:03d}" not in excepto]


def test_normal_no_dispara_nada(con_modelo):
    dt = comun.ahora().replace(second=0)
    r = procesar_metricas.handler(_evento(_metricas_normales(dt)), None)
    assert r["batchItemFailures"] == [] and r["eventos"] == 0
    a = comun.de_ddb(comun.tabla("red").get_item(Key={"pk": "ANTENA#A001", "sk": "META"})["Item"])
    assert a["estadoActual"] == "NORMAL" and a["ultimoTs"] == comun.iso(dt)
    objs = boto3.client("s3").list_objects_v2(Bucket=comun.bucket(), Prefix="metricas/")["Contents"]
    lineas = boto3.client("s3").get_object(Bucket=comun.bucket(), Key=objs[0]["Key"])["Body"].read().decode().splitlines()
    assert len(lineas) == 50 and set(json.loads(lineas[0])) == {"antenaId", "ts", "gbMin", "estado", "anomaliaScore"}


def test_caida_dispara_una_vez_y_registra_falla(con_modelo):
    dt = comun.ahora().replace(second=0)
    procesar_metricas.handler(_evento(_metricas_normales(dt)), None)
    m = {"antenaId": "A011", "ts": comun.iso(dt + timedelta(minutes=1)), "gbMin": 30.0}
    r = procesar_metricas.handler(_evento([m]), None)
    assert r["eventos"] == 1
    ej = _ejecuciones(con_modelo["sf_arn"])
    assert len(ej) == 1 and ej[0].startswith("antena-A011-CAIDA-")
    # reentrega del mismo mensaje y siguiente minuto aún caída: no se duplica
    procesar_metricas.handler(_evento([m]), None)
    m2 = dict(m, ts=comun.iso(dt + timedelta(minutes=2)), gbMin=25.0)
    assert procesar_metricas.handler(_evento([m2]), None)["eventos"] == 0
    assert len(_ejecuciones(con_modelo["sf_arn"])) == 1
    fallas = comun.tabla("red").query(KeyConditionExpression="pk = :p AND begins_with(sk, :f)",
                                      ExpressionAttributeValues={":p": "ANTENA#A011", ":f": "FALLA#"})["Items"]
    assert len(fallas) == 1 and fallas[0]["estado"] == "CAIDA"
    sf = boto3.client("stepfunctions")
    entrada = json.loads(sf.describe_execution(executionArn=sf.list_executions(
        stateMachineArn=con_modelo["sf_arn"])["executions"][0]["executionArn"])["input"])
    assert entrada["origen"] == "ANTENA" and entrada["zonaId"] == "Z03" and entrada["evento"] == "CAIDA"


def test_anomalia_requiere_racha(con_modelo):
    dt = comun.ahora().replace(second=0)
    a = "A007"
    base = trafico.base(a) * trafico.factor_hora(dt)
    raro = round(max(61.5, base * 0.62), 2)
    # si el valor "raro" no es anómalo para esta hora el test no aplica (debería serlo)
    m = procesar_metricas._cargar_modelo()
    assert modelo_ml.puntajes(m, [(a, raro, dt)])[0] >= 0.6
    eventos = 0
    for k in range(4):
        r = procesar_metricas.handler(_evento([{"antenaId": a, "ts": comun.iso(dt + timedelta(minutes=k)), "gbMin": raro}]), None)
        eventos += r["eventos"]
    assert eventos == 1  # solo al completar la racha de 3
    assert any(n.startswith("antena-A007-ANOMALIA-") for n in _ejecuciones(con_modelo["sf_arn"]))


def test_mensaje_invalido_va_a_reintento(con_modelo):
    ev = _evento([{"antenaId": "A001", "ts": comun.iso(comun.ahora()), "gbMin": 90}])
    ev["Records"].append({"messageId": "malo", "body": "{no-json"})
    r = procesar_metricas.handler(ev, None)
    assert r["batchItemFailures"] == [{"itemIdentifier": "malo"}]


def test_sin_modelo_score_cero(aws):
    procesar_metricas._modelo.update({"t": 0, "m": None, "version": None})
    r = procesar_metricas.handler(_evento([{"antenaId": "A001", "ts": comun.iso(comun.ahora()), "gbMin": 90}]), None)
    assert r["batchItemFailures"] == []


def test_entrenar_usa_historico_real(con_modelo):
    dt = comun.ahora().replace(second=0)
    for k in range(3):
        procesar_metricas.handler(_evento(_metricas_normales(dt + timedelta(minutes=k))), None)
    r = entrenar.handler({"dias": 1}, None)
    assert r["filasReales"] == 150
    latest = json.loads(boto3.client("s3").get_object(Bucket=comun.bucket(), Key="modelos/isoforest/latest.json")["Body"].read())
    assert latest["version"] == r["version"]
