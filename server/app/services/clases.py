"""Qué clases de insecto gestiona ENTOMIRA.

Desde el 2026-10-23 (orden de Antonio) el sistema solo gestiona **mosca blanca (WF) y trips (TR)**.
El modelo bichosi de producción sigue detectando además Macrolophus (MR) y Nesidiocoris (NC):
esas detecciones **se guardan** en la BD (no se pierde nada, y un modelo futuro podría
necesitarlas), pero no cuentan en `num_insectos`, ni en las estadísticas, ni se muestran.
`insecto` es la clase genérica de un modelo sin especies: sí cuenta.
"""
from __future__ import annotations

GESTIONADAS = ("WF", "TR", "insecto")


def gestionada(clase: str) -> bool:
    return clase in GESTIONADAS
