import analitica
import comun


def test_recurrencia_marca_estructural(aws, monkeypatch):
    sqls = []
    monkeypatch.setattr(analitica, "athena", lambda sql: sqls.append(sql) or [{"antena": "A011", "f30": "7", "f7": "4"}])
    r = analitica.handler({"accion": "recurrencia"}, None)
    assert r == {"estructurales": ["A011"]}
    a = comun.de_ddb(comun.tabla("red").get_item(Key={"pk": "ANTENA#A011", "sk": "META"})["Item"])
    assert a["estructural"] is True and a["fallas7d"] == 4 and a["fallas30d"] == 7
    assert "UNNEST(antenas)" in sqls[0] and ">= 3" in sqls[0]


def test_agregados_escribe_los_4_tipos(aws, monkeypatch):
    def falso(sql):
        if "zonaId AS clave" in sql:
            return [{"clave": "Z03", "incidentes": "5", "afectados": "1200", "duracionProm": "42.5"}]
        if "antena AS clave" in sql:
            return [{"clave": "A011", "incidentes": "4"}]
        if "tipo) AS clave" in sql:
            return [{"clave": "SIN_INTERNET", "reportes": "30"}]
        return [{"clave": "P1", "incidentes": "2", "duracionProm": "15.0", "detectadosPorSupervision": "1"}]
    monkeypatch.setattr(analitica, "athena", falso)
    r = analitica.handler({"accion": "agregados"}, None)
    assert r == {"ZONA": 1, "ANTENA": 1, "TIPO": 1, "DURACION": 1}
    hoy = comun.ahora().date().isoformat()
    z = comun.de_ddb(comun.tabla("agregados").get_item(Key={"pk": "AGG#ZONA", "sk": hoy})["Item"])
    assert z["datos"]["Z03"] == {"incidentes": 5, "afectados": 1200, "duracionProm": 42.5}


def test_region(aws, monkeypatch):
    assert analitica.handler({"accion": "region"}, None) == {"activa": True}
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    comun.limpiar_cache()
    assert analitica.handler({"accion": "region"}, None) == {"activa": False}
