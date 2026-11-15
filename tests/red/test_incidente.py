import json
from datetime import timedelta

import boto3
import pytest

import comun
import incidente


def _incidencia(i, zona="Z03", hace_min=30, estado="REGISTRADA"):
    f = comun.iso(comun.ahora() - timedelta(minutes=hace_min))
    it = {"pk": f"INC#cod-{zona}-{i}", "sk": "META", "codigo": f"cod-{zona}-{i}", "dni": f"{70000000 + i:08d}",
          "numero": f"9{87000000 + i:08d}", "email": f"cliente{i}@ejemplo.com", "celular": f"+519{87000000 + i:08d}",
          "canales": ["email", "sms"], "lat": -12.14, "lon": -77.02, "antenaId": "A011", "zonaId": zona,
          "fechaHoraUsuario": f, "fechaRegistro": f, "tipo": "SIN_INTERNET", "descripcion": "sin internet",
          "estado": estado, "prioridad": "P4"}
    comun.tabla("incidencias").put_item(Item=comun.a_ddb(it))


def _antena(a, **campos):
    comun.tabla("red").update_item(Key={"pk": f"ANTENA#{a}", "sk": "META"},
                                   UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in campos),
                                   ExpressionAttributeNames={f"#{k}": k for k in campos},
                                   ExpressionAttributeValues=comun.a_ddb({f":{k}": v for k, v in campos.items()}))


def _inc(iid):
    return comun.de_ddb(comun.tabla("incidentes").get_item(Key={"pk": f"INCID#{iid}", "sk": "META"})["Item"])


@pytest.fixture
def zona_con_reportes(aws):
    for i in range(1, 13):
        _incidencia(i)
    _incidencia(99, zona="Z05")  # otra zona: no se vincula
    _incidencia(98, hace_min=60 * 30)  # fuera de la ventana de 24 h
    return aws


def test_flujo_completo_colectivo(zona_con_reportes):
    ent = {"origen": "ZONA", "evento": "COLECTIVO", "zonaId": "Z03", "antenaId": None,
           "detectadoEn": comun.iso(comun.ahora())}
    r = incidente.handler({"accion": "abrir", "entrada": ent, "ejecucion": "zona-Z03-2026111504"}, None)
    assert r == {"existente": False, "incidenteId": "zona-Z03-2026111504", "esperaSeg": 300}
    iid = r["incidenteId"]

    v = incidente.handler({"accion": "vincular", "incidenteId": iid}, None)
    assert v == {"vinculadas": 12, "reportes": 12, "usuarios": 12}

    n = incidente.handler({"accion": "notificar", "incidenteId": iid, "estado": "EN_REVISION"}, None)
    assert n["enviadas"] == 12 and n["fallidas"] == 0
    n2 = incidente.handler({"accion": "notificar", "incidenteId": iid, "estado": "EN_REVISION"}, None)
    assert n2["enviadas"] == 0 and n2["omitidas"] == 12  # deduplicado

    # la antena A011 (Z03) cayó ANTES del primer reporte -> la supervisión lo vio primero
    _antena("A011", estadoActual="CAIDA", desde=comun.iso(comun.ahora() - timedelta(hours=2)))
    ver = incidente.handler({"accion": "verificar", "incidenteId": iid}, None)
    assert ver["falla"] == "CAIDA" and ver["antenasFalla"] == ["A011"] and ver["detectadoPor"] == "SUPERVISION"

    p = incidente.handler({"accion": "prioridad", "incidenteId": iid}, None)
    assert p["prioridad"] in ("P1", "P2", "P3", "P4") and p["afectados"] >= 12

    assert incidente.handler({"accion": "resumir", "incidenteId": iid}, None) == {"ia": False}  # sin GCP: sigue

    e = incidente.handler({"accion": "estado", "incidenteId": iid, "estadoIncidente": "EN_ATENCION",
                           "estadoIncidencias": "EN_ATENCION"}, None)
    assert e["actualizadas"] == 12

    rv = incidente.handler({"accion": "revisar", "incidenteId": iid}, None)
    assert rv["restablecido"] is False and rv["pendientes"] == ["A011"]

    _antena("A011", estadoActual="NORMAL", rachaAnomalia=0, anomaliaScore=0.1)
    assert incidente.handler({"accion": "revisar", "incidenteId": iid}, None)["restablecido"] is True

    incidente.handler({"accion": "estado", "incidenteId": iid, "estadoIncidente": "RESTABLECIDO",
                       "estadoIncidencias": "SOLUCIONADA"}, None)
    incidente.handler({"accion": "notificar", "incidenteId": iid, "estado": "SOLUCIONADA"}, None)
    c = incidente.handler({"accion": "cerrar", "incidenteId": iid}, None)
    assert c["duracionMin"] >= 0

    inc = _inc(iid)
    assert inc["estado"] == "CERRADO" and inc["reportes"] == 12
    i1 = comun.de_ddb(comun.tabla("incidencias").get_item(Key={"pk": "INC#cod-Z03-1", "sk": "META"})["Item"])
    assert i1["estado"] == "CERRADA" and i1["incidenteId"] == iid
    hist = comun.tabla("incidencias").query(KeyConditionExpression="pk = :p AND begins_with(sk, :h)",
                                            ExpressionAttributeValues={":p": "INC#cod-Z03-1", ":h": "HIST#"})["Items"]
    assert [h["estadoNuevo"] for h in sorted(hist, key=lambda h: h["sk"])] == \
           ["EN_REVISION", "EN_ATENCION", "SOLUCIONADA", "CERRADA"]
    otra = comun.de_ddb(comun.tabla("incidencias").get_item(Key={"pk": "INC#cod-Z05-99", "sk": "META"})["Item"])
    assert "incidenteId" not in otra
    s3 = boto3.client("s3")
    key = f"incidentes/dt={inc['fin'][:10]}/{iid}.json"
    cuerpo = s3.get_object(Bucket=comun.bucket(), Key=key)["Body"].read().decode()
    assert "\n" not in cuerpo.strip() and json.loads(cuerpo)["detectadoPor"] == "SUPERVISION"
    assert "Item" not in comun.tabla("incidentes").get_item(Key={"pk": "ZONALOCK#Z03", "sk": "META"})
    # cerrada la zona, un nuevo evento abre un incidente nuevo
    r2 = incidente.handler({"accion": "abrir", "entrada": ent, "ejecucion": "zona-Z03-2026111505"}, None)
    assert r2["existente"] is False


def test_segundo_evento_se_une_al_incidente_abierto(zona_con_reportes):
    ent = {"origen": "ZONA", "evento": "COLECTIVO", "zonaId": "Z03", "antenaId": None, "detectadoEn": comun.iso(comun.ahora())}
    iid = incidente.handler({"accion": "abrir", "entrada": ent, "ejecucion": "zona-Z03-1"}, None)["incidenteId"]
    ent2 = {"origen": "ANTENA", "evento": "CAIDA", "zonaId": "Z03", "antenaId": "A012", "detectadoEn": comun.iso(comun.ahora())}
    r = incidente.handler({"accion": "abrir", "entrada": ent2, "ejecucion": "antena-A012-CAIDA-1"}, None)
    assert r["existente"] is True and r["incidenteId"] == iid
    assert "A012" in _inc(iid)["antenas"]
    assert _inc(iid)["reportes"] == 12  # el segundo evento vinculó los reportes


def test_error_libera_la_zona(zona_con_reportes):
    ent = {"origen": "ZONA", "evento": "COLECTIVO", "zonaId": "Z03", "antenaId": None, "detectadoEn": comun.iso(comun.ahora())}
    iid = incidente.handler({"accion": "abrir", "entrada": ent, "ejecucion": "zona-Z03-err"}, None)["incidenteId"]
    incidente.handler({"accion": "error", "incidenteId": iid, "causa": {"Error": "X"}}, None)
    assert _inc(iid)["estado"] == "CERRADO"
    assert "Item" not in comun.tabla("incidentes").get_item(Key={"pk": "ZONALOCK#Z03", "sk": "META"})


def test_revisar_se_agota(zona_con_reportes):
    ent = {"origen": "ANTENA", "evento": "CAIDA", "zonaId": "Z03", "antenaId": "A011", "detectadoEn": comun.iso(comun.ahora())}
    iid = incidente.handler({"accion": "abrir", "entrada": ent, "ejecucion": "antena-A011-CAIDA-x"}, None)["incidenteId"]
    _antena("A011", estadoActual="CAIDA")
    res = [incidente.handler({"accion": "revisar", "incidenteId": iid}, None)["agotado"] for _ in range(3)]
    assert res == [False, False, True]  # sf/max_intentos = 3 en los tests


def test_prioridad_segun_zona_y_falla(aws):
    pesos = json.loads(comun.param("prioridad/pesos"))
    assert incidente.calcular_prioridad("MEDICA", 1500, "CAIDA", pesos)[0] == "P1"
    assert incidente.calcular_prioridad("RESIDENCIAL", 5, "NINGUNA", pesos)[0] == "P4"
    assert incidente.calcular_prioridad("RESIDENCIAL", 300, "CAIDA", pesos)[0] == "P2"
    assert incidente.calcular_prioridad("EDUCATIVA", 50, "SATURADA", pesos)[0] == "P2"


def test_vinculada_tarde_hereda_prioridad(zona_con_reportes):
    ent = {"origen": "ZONA", "evento": "COLECTIVO", "zonaId": "Z03", "antenaId": None, "detectadoEn": comun.iso(comun.ahora())}
    iid = incidente.handler({"accion": "abrir", "entrada": ent, "ejecucion": "zona-Z03-p"}, None)["incidenteId"]
    incidente.handler({"accion": "vincular", "incidenteId": iid}, None)
    _antena("A011", estadoActual="CAIDA")
    incidente.handler({"accion": "verificar", "incidenteId": iid}, None)
    p = incidente.handler({"accion": "prioridad", "incidenteId": iid}, None)["prioridad"]
    _incidencia(50, hace_min=1)  # llega después de calcular la prioridad
    incidente.handler({"accion": "vincular", "incidenteId": iid}, None)
    nueva = comun.de_ddb(comun.tabla("incidencias").get_item(Key={"pk": "INC#cod-Z03-50", "sk": "META"})["Item"])
    assert nueva["incidenteId"] == iid and nueva["prioridad"] == p
