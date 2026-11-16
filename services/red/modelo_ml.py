"""Isolation Forest por antena y hora: aprende el tráfico NORMAL y puntúa anomalías 0..1."""
import io
import math
import random
from datetime import timedelta

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

import trafico


def _vector(gb, mediana, dt):
    h = trafico.hora_lima(dt)
    return [gb / max(mediana, 1.0), math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24)]


def sinteticos(antena_ids, fin, dias=3, cada_min=30, rng=None):
    """Tráfico normal sintético para el primer entrenamiento (cuando aún no hay histórico)."""
    rng = rng or random.Random(7)
    out, pasos = [], int(dias * 24 * 60 / cada_min)
    for a in antena_ids:
        for k in range(pasos):
            dt = fin - timedelta(minutes=cada_min * k)
            out.append({"antenaId": a, "dt": dt, "gbMin": trafico.normal(a, dt, rng), "estado": "NORMAL"})
    return out


def entrenar(registros, antena_ids, version, fin, minimo=2000):
    """registros: [{antenaId, dt (datetime), gbMin, estado}]. Solo aprende de tráfico NORMAL."""
    normales = [r for r in registros if r.get("estado", "NORMAL") == "NORMAL"]
    # Con pocas horas del día reales, el modelo ve cualquier otra hora como anomalía:
    # se completa con sintéticos hasta tener las 24 horas cubiertas.
    if len(normales) < minimo or len({r["dt"].hour for r in normales}) < 24:
        normales += sinteticos(antena_ids, fin)
    por_antena = {}
    for r in normales:
        por_antena.setdefault(r["antenaId"], []).append(r["gbMin"])
    medianas = {a: float(np.median(v)) for a, v in por_antena.items()}
    X = np.array([_vector(r["gbMin"], medianas[r["antenaId"]], r["dt"]) for r in normales])
    modelo = IsolationForest(n_estimators=200, contamination="auto", random_state=42).fit(X)
    s = -modelo.score_samples(X)
    return {"modelo": modelo, "medianas": medianas, "p50": float(np.percentile(s, 50)),
            "p99": float(np.percentile(s, 99)), "version": version, "n": len(normales)}


def puntajes(m, items):
    """items: [(antenaId, gb, dt)] -> [score 0..1]. p99 del entrenamiento = 0.5."""
    if not items:
        return []
    X = np.array([_vector(gb, m["medianas"].get(a, 100.0), dt) for a, gb, dt in items])
    s = -m["modelo"].score_samples(X)
    escala = max(m["p99"] - m["p50"], 1e-6)
    return [round(float(min(1.0, max(0.0, 0.5 + 0.5 * (x - m["p99"]) / escala))), 3) for x in s]


def a_bytes(m):
    b = io.BytesIO()
    joblib.dump(m, b)
    return b.getvalue()


def de_bytes(data):
    return joblib.load(io.BytesIO(data))
