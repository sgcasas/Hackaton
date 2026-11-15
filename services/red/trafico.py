"""Modelo de tráfico sintético de las antenas (lo usan el simulador y el entrenamiento inicial del ML)."""
import math
import random


def base(antena_id):
    """GB/min promedio de la antena en su hora pico: 80..119, fijo por antena."""
    n = int(antena_id[1:])
    return 80 + (n * 37) % 40


def hora_lima(dt):
    return ((dt.hour - 5) % 24) + dt.minute / 60


def factor_hora(dt):
    """Ciclo diario: mínimo ~3 a.m., máximo ~3 p.m. (hora de Lima)."""
    return 0.88 + 0.12 * math.sin(2 * math.pi * (hora_lima(dt) - 9) / 24)


def normal(antena_id, dt, rng=random):
    v = base(antena_id) * factor_hora(dt) + rng.gauss(0, 2.5)
    return round(min(max(v, 61.0), 128.0), 2)


def aplicar_escenario(escenario, antena, valor, rng=random):
    """Escenarios para la demo (parámetro /redtelecorp/simulador/escenario)."""
    tipo, obj = escenario.get("tipo", "normal"), escenario.get("objetivo")
    if tipo == "corte_zona" and antena["zonaId"] == obj:
        return 0.0
    if tipo == "caida_antena" and antena["antenaId"] == obj:
        return round(rng.uniform(20, 50), 2)
    if tipo == "saturacion_zona" and antena["zonaId"] == obj:
        return round(rng.uniform(135, 160), 2)
    if tipo == "antena_inestable" and antena["antenaId"] == obj:
        # oscila fuera de su patrón normal pero sin cruzar los umbrales 60/129: solo el ML lo ve
        v = valor * 0.62 if rng.random() < 0.6 else valor * 1.22
        return round(min(max(v, 61.5), 127.5), 2)
    return valor
