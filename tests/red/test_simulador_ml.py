import json
import random
from datetime import datetime, timedelta, timezone

import boto3

import comun
import modelo_ml
import simulador
import trafico

FIN = datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc)
IDS = [f"A{i:03d}" for i in range(1, 51)]


def test_umbrales_del_enunciado():
    assert comun.clasificar(0) == "INHABILITADA"
    assert comun.clasificar(59.99) == "CAIDA"
    assert comun.clasificar(60) == "NORMAL"
    assert comun.clasificar(129) == "NORMAL"
    assert comun.clasificar(129.01) == "SATURADA"


def test_trafico_normal_siempre_en_rango():
    rng = random.Random(3)
    for a in IDS:
        for k in range(0, 1440, 20):
            assert 60 <= trafico.normal(a, FIN + timedelta(minutes=k), rng) <= 129


def test_escenarios():
    rng = random.Random(1)
    a = {"antenaId": "A011", "zonaId": "Z03"}
    assert trafico.aplicar_escenario({"tipo": "corte_zona", "objetivo": "Z03"}, a, 90, rng) == 0
    assert trafico.aplicar_escenario({"tipo": "caida_antena", "objetivo": "A011"}, a, 90, rng) < 60
    assert trafico.aplicar_escenario({"tipo": "saturacion_zona", "objetivo": "Z03"}, a, 90, rng) > 129
    v = trafico.aplicar_escenario({"tipo": "antena_inestable", "objetivo": "A011"}, a, 90, rng)
    assert 60 <= v <= 129  # no cruza umbrales: solo el ML lo detecta
    assert trafico.aplicar_escenario({"tipo": "corte_zona", "objetivo": "Z09"}, a, 90, rng) == 90


def test_ml_calibrado():
    m = modelo_ml.entrenar([], IDS, "v", FIN)
    rng = random.Random(11)
    normal = [(a, trafico.normal(a, FIN + timedelta(minutes=7 * k), rng), FIN + timedelta(minutes=7 * k))
              for a in IDS for k in range(0, 200, 10)]
    fp = sum(p >= 0.6 for p in modelo_ml.puntajes(m, normal)) / len(normal)
    assert fp < 0.01, fp
    ant = {"antenaId": "A007", "zonaId": "Z02"}
    ins = []
    for k in range(0, 1440, 15):
        dt = FIN + timedelta(minutes=k)
        ins.append(("A007", trafico.aplicar_escenario({"tipo": "antena_inestable", "objetivo": "A007"}, ant,
                                                      trafico.normal("A007", dt, rng), rng), dt))
    det = sum(p >= 0.6 for p in modelo_ml.puntajes(m, ins)) / len(ins)
    assert det > 0.6, det
    m2 = modelo_ml.de_bytes(modelo_ml.a_bytes(m))
    assert modelo_ml.puntajes(m2, ins[:5]) == modelo_ml.puntajes(m, ins[:5])


def test_simulador_envia_50_y_respeta_escenario(aws):
    boto3.client("ssm").put_parameter(Name="/redtelecorp/simulador/escenario", Type="String", Overwrite=True,
                                      Value='{"tipo":"corte_zona","objetivo":"Z03"}')
    simulador._antenas["t"] = 0
    r = simulador.handler({}, None)
    assert r["enviadas"] == 50
    sqs, msgs = boto3.client("sqs"), []
    while True:
        lote = sqs.receive_message(QueueUrl=aws["cola"], MaxNumberOfMessages=10).get("Messages", [])
        if not lote:
            break
        msgs += [json.loads(m["Body"]) for m in lote]
    assert len(msgs) == 50
    z03 = {a["antenaId"] for a in comun.antenas("Z03")}
    assert all(m["gbMin"] == 0 for m in msgs if m["antenaId"] in z03)
    assert all(m["gbMin"] >= 60 for m in msgs if m["antenaId"] not in z03)


def test_simulador_no_corre_en_region_pasiva(aws, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    comun.limpiar_cache()
    assert simulador.handler({}, None) == {"omitido": "region pasiva"}
