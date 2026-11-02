"""Fotos que la trampa ha perdido en su buffer local, según ella misma.

La trampa guarda en su tarjeta las fotos que no puede subir. Dos cosas las pierden: el buffer
lleno (descarta la más antigua) y el disco lleno (la nueva no se puede guardar). Antes solo
quedaba en el log de la Zero; el artículo F midió 30/30 pérdidas silenciosas con el disco
lleno. Ahora la trampa manda `{"descartadas": n, "sin_guardar": m}` desde su último arranque.

En memoria, como `config_aplicada`: tras reiniciar el servidor basta la siguiente telemetría.
"""
from __future__ import annotations

import time

CADUCA_S = 24 * 3600.0

# trap_id -> {"descartadas", "sin_guardar", "ina", "t"}
_estado: dict[int, dict] = {}


def anotar(trap_id: int, dato, ahora: float | None = None) -> None:
    """Un dato malformado se ignora: la telemetría no se rechaza por esto."""
    if not isinstance(dato, dict):
        return
    cuentas = {}
    # «ina»: ventanas de consumo descartadas con la cola llena (clientes antiguos no la mandan).
    for k in ("descartadas", "sin_guardar", "ina"):
        v = dato.get(k, 0)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return
        cuentas[k] = v
    cuentas["t"] = time.time() if ahora is None else ahora
    _estado[trap_id] = cuentas


def problema(trap_id: int, ahora: float | None = None) -> dict | None:
    """Las cuentas si la trampa ha perdido alguna foto y el dato es reciente; si no, None."""
    d = _estado.get(trap_id)
    if not d:
        return None
    ahora = time.time() if ahora is None else ahora
    if ahora - d["t"] > CADUCA_S or not (d["descartadas"] or d["sin_guardar"] or d["ina"]):
        return None
    return d


def olvidar(trap_id: int) -> None:
    _estado.pop(trap_id, None)
