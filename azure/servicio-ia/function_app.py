"""servicio-ia (Azure Functions, Python v2).

POST /clasificar  {descripcion, tipoUsuario}       -> {tipo, urgencia}          (contracts/schemas/ia-clasificar.*)
POST /resumir     {incidente, antenas, reportes}   -> {resumen, causaProbable}  (contracts/schemas/ia-resumir.*)
GET  /salud                                        -> {ok, region, modelo}

Misma lógica que gcp/servicio-ia/main.py, pero llama a Gemini con una API key de Google AI Studio
(app setting GEMINI_API_KEY) en vez de Vertex AI. Si Gemini falla, responde con reglas simples.
El origen va en el header X-Fuente-IA (gemini | reglas), porque el cuerpo tiene que cumplir el schema exacto.
Autenticación: Authorization: Bearer <TOKEN> (app setting TOKEN; /redtelecorp/gcp/token en Parameter Store).
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import azure.functions as func

TOKEN = os.environ.get("TOKEN", "")
REGION = os.environ.get("REGION_NAME", "azure")
# gemini-2.5-flash ya no está disponible para keys nuevas de AI Studio; los flash 3.7/3.8 tardan más
# de lo que espera AWS (8 s). flash-lite responde en ~2 s.
MODELO = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

TIPOS = ["SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "SIN_SENAL", "OTRO"]
URGENCIAS = ["BAJA", "MEDIA", "ALTA"]

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
_cliente = None


def cliente():
    global _cliente
    if _cliente is None:
        from google import genai
        from google.genai import types
        # La API no acepta deadlines < 10 s; el límite real (ESPERA_SEG) lo pone gemini_json.
        _cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(
            timeout=10000, retry_options=types.HttpRetryOptions(attempts=1)))
    return _cliente


# AWS espera 8 s por URL (llamar_ia): si Gemini no contesta en 6 s, responden las reglas.
ESPERA_SEG = 6
_hilos = ThreadPoolExecutor(max_workers=4)


def gemini_json(prompt, esquema):
    return _hilos.submit(_gemini_json, prompt, esquema).result(timeout=ESPERA_SEG)


def _gemini_json(prompt, esquema):
    from google.genai import types
    r = cliente().models.generate_content(
        model=MODELO,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=esquema,
            max_output_tokens=2048,
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
        print(f"Gemini falló en clasificar: {type(e).__name__}: {e}")
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
        print(f"Gemini falló en resumir: {type(e).__name__}: {e}")
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
def _json(obj, status=200, fuente=None):
    headers = {"X-Region": REGION}
    if fuente:
        headers["X-Fuente-IA"] = fuente
    return func.HttpResponse(json.dumps(obj, ensure_ascii=False), status_code=status,
                             mimetype="application/json", charset="utf-8", headers=headers)


def _atender(req, logica):
    if TOKEN and req.headers.get("Authorization", "") != f"Bearer {TOKEN}":
        return _json({"error": "no autorizado"}, 401)
    try:
        datos = req.get_json()
    except ValueError:
        datos = {}
    cuerpo, fuente = logica(datos if isinstance(datos, dict) else {})
    return _json(cuerpo, fuente=fuente)


@app.route(route="salud", methods=["GET"])
def salud(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"ok": True, "region": REGION, "modelo": MODELO})


@app.route(route="clasificar", methods=["POST"])
def clasificar_http(req: func.HttpRequest) -> func.HttpResponse:
    return _atender(req, clasificar)


@app.route(route="resumir", methods=["POST"])
def resumir_http(req: func.HttpRequest) -> func.HttpResponse:
    return _atender(req, resumir)
