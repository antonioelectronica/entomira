"""Mapa de calor: insectos por sector (y por sector×línea).

⚠️ Cada celda lleva **dos** cifras, y significan cosas distintas:

- **`insectos`** = lo pegado AHORA en la tarjeta (la última foto de cada trampa). Es un
  **acumulado**: una zona con la tarjeta puesta hace dos semanas sale peor que otra con el
  cartón recién cambiado aunque tengan la misma plaga.
- **`nuevas`** = capturas nuevas en la ventana, es decir la **presión de plaga**, comparable
  entre sectores porque no depende de cuánto lleve puesta cada tarjeta.

Antes se sumaba `num_insectos` de todas las fotos de la ventana, lo que multiplicaba cada
insecto por el número de capturas. Ver `services/actividad.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from ..core import config
from ..models.models import Imagen, Invernadero, Trampa
from . import actividad as act_svc

# Ventana de la presion de plaga en el mapa. Una semana es el ritmo con el que se revisa
# un invernadero, y suaviza el ruido del detector entre capturas sueltas.
DIAS_PRESION = 7


def _ultimo_conteo(session: Session, trap: Trampa) -> int | None:
    # última imagen procesada vía SQL (no cargar todas las imágenes en memoria)
    row = session.exec(
        select(Imagen.num_insectos)
        .where(Imagen.trampa_id == trap.id, Imagen.estado == "procesada")
        .order_by(Imagen.capturada.desc()).limit(1)
    ).first()
    # None = la IA aún no ha contado ninguna foto de esta trampa. NO es un cero.
    return row


def map_grid(session: Session, greenhouse_id: int) -> dict:
    """Rejilla sector×línea: cada celda con nº de insectos (último análisis) y nº de trampas."""
    gh = session.get(Invernadero, greenhouse_id)
    if not gh:
        return {}
    traps = session.exec(
        select(Trampa).where(Trampa.invernadero_id == greenhouse_id)).all()

    # Fotos de ESTE invernadero que aun no ha visto la IA. Sin este numero, la web no puede
    # distinguir «he contado y hay cero» de «todavia no he contado», y las dos cosas se
    # pintaban igual: sin presion de plaga.
    ids = [t.id for t in traps]
    pendientes = 0
    if ids:
        pendientes = session.exec(
            select(func.count()).select_from(Imagen)
            .where(Imagen.trampa_id.in_(ids), Imagen.estado == "pendiente")).one() or 0

    n_lineas = max([gh.num_lineos] + [t.linea or 0 for t in traps] + [1])
    sectores = config.SECTORES  # A, B, pasillo, C, D
    grid = {}
    total = 0
    total_nuevas = 0
    sin_dato = 0
    lineas_usadas = set()
    desde = datetime.now(timezone.utc) - timedelta(days=DIAS_PRESION)
    for t in traps:
        if t.sector not in sectores or not t.linea:
            continue
        key = f"{t.sector}|{t.linea}"
        cell = grid.setdefault(key, {"insectos": 0, "nuevas": 0, "trampas": 0,
                                     "con_dato": 0, "sin_dato": 0})
        c = _ultimo_conteo(session, t)
        # Presion de plaga: capturas nuevas, no acumulado. Es lo comparable entre celdas.
        # `nuevas` devuelve None si no hay datos: esa trampa NO suma un cero, se cuenta aparte
        # para que la web pinte «sin dato» y no «sin plaga».
        n = act_svc.nuevas(session, t.id, desde)
        cell["trampas"] += 1
        if c is not None:
            cell["insectos"] += c
            total += c
        if n is None:
            cell["sin_dato"] += 1
            sin_dato += 1
        else:
            cell["con_dato"] += 1
            cell["nuevas"] += max(0, n)
            total_nuevas += max(0, n)
        lineas_usadas.add(t.linea)

    return {
        "greenhouse": {
            "id": gh.id, "nombre": gh.nombre, "ubicacion": gh.ubicacion,
            "cultivo": gh.cultivo, "tipo": gh.tipo, "responsable": gh.responsable,
            "contacto": gh.contacto, "num_lineos": gh.num_lineos,
        },
        "sectores": sectores,
        "n_lineas": n_lineas,
        "cells": grid,
        "total": total,
        # Acumulado vs presion: la web los distingue porque llevan a decisiones distintas.
        "total_nuevas": total_nuevas,
        "dias_presion": DIAS_PRESION,
        # Los ceros de arriba solo son medidas si esto vale 0.
        "pendientes": int(pendientes),
        # Trampas colocadas sin ninguna medida de presion: sus celdas no son ceros.
        "trampas_sin_dato": sin_dato,
        # solo trampas fisicas: los moviles de los agricultores (`owner_id`) son
        # dispositivos de captura, no trampas. Ver serializers.greenhouse_out.
        "num_trampas": sum(1 for t in traps if t.owner_id is None),
        "lineas_con_datos": sorted(lineas_usadas),
    }


def sector_counts(session: Session, greenhouse_id: int, days: int = 7) -> dict:
    """Capturas NUEVAS por sector en la ventana: la presion de plaga, no el acumulado.

    Antes sumaba `num_insectos` de todas las fotos de la ventana. Con 108 capturas al dia eso
    multiplicaba cada insecto por 108, y el numero subia o bajaba al tocar el intervalo de
    captura, que no tiene ninguna relacion con la plaga.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts = {s: 0 for s in config.SECTORES}
    per_trap: dict[int, int] = {}

    traps = session.exec(
        select(Trampa).where(Trampa.invernadero_id == greenhouse_id)
    ).all()
    for trap in traps:
        n = act_svc.nuevas(session, trap.id, since)
        if n is None:
            continue
        n = max(0, n)
        per_trap[trap.id] = n
        if trap.sector in counts:
            counts[trap.sector] += n

    return {
        "greenhouse_id": greenhouse_id,
        "window_days": days,
        "sectors": counts,
        "total": sum(counts.values()),
        "per_trap": per_trap,
        "magnitud": "capturas_nuevas",   # el cliente sabe que NO es un acumulado
    }
