"""servicio-ia (Cloud Run function, GCP).

POST /clasificar  {descripcion, tipoUsuario}       -> {tipo, urgencia}          (contracts/schemas/ia-clasificar.*)
POST /resumir     {incidente, antenas, reportes}   -> {resumen, causaProbable}  (contracts/schemas/ia-resumir.*)
GET  /salud                                        -> {ok, region, modelo}

Se despliega igual en us-east4 (principal) y us-central1 (secundaria).
Usa Vertex AI Gemini en su propia región. Si Gemini falla, responde con reglas
simples para que AWS nunca se quede sin respuesta. El origen va en el header X-Fuente-IA
(gemini | reglas), porque el cuerpo tiene que cumplir el schema exacto (additionalProperties: false).
Autenticación: Authorization: Bearer <token> (/redtelecorp/gcp/token en Parameter Store).
"""
import json
import os

import functions_framework

TOKEN = os.environ.get("TOKEN", "")
PROYECTO = os.environ.get("PROYECTO", "")
REGION = os.environ.get("GEMINI_LOCATION", os.environ.get("REGION", "us-east4"))
MODELO = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

TIPOS = ["SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "SIN_SENAL", "OTRO"]
URGENCIAS = ["BAJA", "MEDIA", "ALTA"]

_cliente = None


def cliente():
    global _cliente
    if _cliente is None:
        from google import genai
        _cliente = genai.Client(vertexai=True, project=PROYECTO or None, location=REGION)
    return _cliente


def gemini_json(prompt, esquema):
    from google.genai import types
    r = cliente().models.generate_content(
        model=MODELO,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=esquema,
            max_output_tokens=512,
        ),
    )
    return json.loads(r.text)


# ---------------------------------------------------------------------------
def clasificar(datos):
    descripcion = str(datos.get("descripcion", ""))[:1000]
    tipo_usuario = datos.get("tipoUsuario", "OTRO")
    prompt = (
        "Eres el clasificador de reportes de una empresa de telecomunicaciones en Perú.\n"
        f"Tipos posibles: {', '.join(TIPOS)}.\n"
        "Urgencia: ALTA si afecta a un hospital, clínica, emergencia, colegio en clases o a muchas personas; "
        "MEDIA si el usuario se quedó sin servicio; BAJA si es lentitud o algo intermitente.\n"
        f"El usuario eligió el tipo {tipo_usuario}; corrígelo si la descripción dice otra cosa.\n"
        f"Descripción del usuario: \"{descripcion}\""
    )
    esquema = {
        "type": "OBJECT",
        "properties": {"tipo": {"type": "STRING", "enum": TIPOS}, "urgencia": {"type": "STRING", "enum": URGENCIAS}},
        "required": ["tipo", "urgencia"],
    }
    try:
        r = gemini_json(prompt, esquema)
        if r.get("tipo") in TIPOS and r.get("urgencia") in URGENCIAS:
            return {"tipo": r["tipo"], "urgencia": r["urgencia"]}, "gemini"
    except Exception as e:  # noqa: BLE001
        print(f"Gemini falló en clasificar: {e}")
    return clasificar_reglas(descripcion, tipo_usuario), "reglas"


def clasificar_reglas(descripcion, tipo_usuario):
    d = descripcion.lower()
    reglas = [
        ("SIN_LLAMADAS", ["llamada", "se corta", "colgar"]),
        ("SIN_SMS", ["sms", "mensaje de texto", "mensajes"]),
        ("LENTITUD", ["lento", "lenta", "carga mucho", "demora"]),
        ("SIN_INTERNET", ["sin internet", "no tengo internet", "datos", "no navega", "no carga"]),
        ("SIN_SENAL", ["señal", "senal", "sin servicio", "cobertura"]),
    ]
    tipo = next((t for t, palabras in reglas if any(p in d for p in palabras)),
                tipo_usuario if tipo_usuario in TIPOS else "OTRO")
    urgente = any(p in d for p in ["hospital", "clínica", "clinica", "emergencia", "colegio", "ambulancia"])
    urgencia = "ALTA" if urgente else ("BAJA" if tipo == "LENTITUD" else "MEDIA")
    return {"tipo": tipo, "urgencia": urgencia}


def resumir(datos):
    contexto = json.dumps({k: datos.get(k) for k in ("incidente", "antenas", "reportes")},
                          ensure_ascii=False, default=str)[:6000]
    prompt = (
        "Eres un ingeniero de operaciones de una red móvil. Con estos datos de un incidente "
        "(reportes de usuarios y antenas; tráfico en GB/min: 0 = inhabilitada, <60 = caída, >129 = saturada), "
        "escribe en español: resumen (máximo 2 frases, para el panel del operador, con cifras concretas) "
        "y causaProbable (una frase).\n"
        f"Datos: {contexto}"
    )
    esquema = {
        "type": "OBJECT",
        "properties": {"resumen": {"type": "STRING"}, "causaProbable": {"type": "STRING"}},
        "required": ["resumen", "causaProbable"],
    }
    try:
        r = gemini_json(prompt, esquema)
        return {"resumen": str(r["resumen"]), "causaProbable": str(r["causaProbable"])}, "gemini"
    except Exception as e:  # noqa: BLE001
        print(f"Gemini falló en resumir: {e}")
    return resumir_reglas(datos), "reglas"


def resumir_reglas(datos):
    inc = datos.get("incidente") or {}
    antenas = datos.get("antenas") or []
    reportes = datos.get("reportes") or []
    peores = [a for a in antenas if a.get("estadoActual") not in (None, "NORMAL")]
    resumen = f"{len(reportes)} reportes en la zona {inc.get('zonaId', '-')}"
    if peores:
        a = peores[0]
        resumen += f"; la antena {a.get('antenaId')} está {a.get('estadoActual')} ({a.get('ultimoGB', '-')} GB/min)."
        causa = f"Falla en la antena {a.get('antenaId')}."
    else:
        resumen += "; las antenas de la zona se ven normales."
        causa = "Sin falla visible en la red: revisar en campo."
    return {"resumen": resumen, "causaProbable": causa}


# ---------------------------------------------------------------------------
@functions_framework.http
def servicio_ia(request):
    ruta = request.path.rstrip("/")
    if ruta.endswith("/salud"):
        return _json({"ok": True, "region": REGION, "modelo": MODELO})
    if TOKEN and request.headers.get("Authorization", "") != f"Bearer {TOKEN}":
        return _json({"error": "no autorizado"}, 401)
    if request.method != "POST":
        return _json({"error": "usa POST"}, 405)
    datos = request.get_json(silent=True) or {}
    if ruta.endswith("/clasificar"):
        return _json(*clasificar(datos))
    if ruta.endswith("/resumir"):
        return _json(*resumir(datos))
    return _json({"error": f"ruta desconocida: {ruta}"}, 404)


def _json(obj, fuente=None, status=200):
    if isinstance(fuente, int):
        fuente, status = None, fuente
    headers = {"Content-Type": "application/json", "X-Region": REGION}
    if fuente:
        headers["X-Fuente-IA"] = fuente
    return (json.dumps(obj, ensure_ascii=False), status, headers)
