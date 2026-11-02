"""Qué configuración tiene APLICADA cada trampa, según ella misma.

El servidor sabe qué versión de config quiere (`ConfigCamara.version`) y se la dice en cada
telemetría, pero hasta ahora no sabía cuál había aplicado la trampa. Una trampa que arranca sin
red y sin config guardada dispara con los valores de fábrica, sigue mandando telemetría y
parece sana: es el fallo F7 del experimento del artículo B, y el centinela no lo ve.

La trampa informa `{"v": version, "origen": "servidor" | "disco" | "fabrica"}`. Se guarda en
memoria, como `services/sensores.py`: tras reiniciar el servidor basta la siguiente telemetría
para rehacerlo, y no merece una columna en la base de datos.

Una discrepancia de versión es NORMAL durante un informe: el servidor sube la versión, la trampa
se entera en la respuesta y la descarga después. Por eso solo cuenta si se repite en
`REPETICIONES` informes seguidos y dura al menos `GRACIA_S`.
"""
from __future__ import annotations

import time

ORIGENES = ("servidor", "disco", "fabrica")
REPETICIONES = 2
GRACIA_S = 60.0
# Un dato más viejo que esto ya no describe a la trampa: el aviso de «sin fotos» cubre ese caso.
CADUCA_S = 24 * 3600.0

# trap_id -> {"v", "origen", "quiere", "t", "discrepa", "desde"}
_estado: dict[int, dict] = {}


def anotar(trap_id: int, dato, quiere: int, ahora: float | None = None) -> None:
    """Apunta lo que la trampa dice tener aplicado. Un dato malformado se ignora sin más:
    la telemetría no se rechaza por esto, igual que con el INA."""
    if not isinstance(dato, dict):
        return
    v, origen = dato.get("v"), dato.get("origen")
    if isinstance(v, bool) or not isinstance(v, int) or origen not in ORIGENES:
        return
    ahora = time.time() if ahora is None else ahora
    previo = _estado.get(trap_id, {})
    # «fábrica» también es una discrepancia, con el mismo margen: tras un arranque sin red el
    # primer informe dice fábrica y la config llega justo después (hallazgo de Codex).
    if v != quiere or origen == "fabrica":
        discrepa = previo.get("discrepa", 0) + 1
        desde = previo.get("desde") if previo.get("discrepa") else ahora
    else:
        discrepa, desde = 0, None
    _estado[trap_id] = {"v": v, "origen": origen, "quiere": quiere, "t": ahora,
                        "discrepa": discrepa, "desde": desde}


def problema(trap_id: int, ahora: float | None = None) -> str | None:
    """`"fabrica"`, `"desfase"` o `None` si no hay nada que decir (o no hay dato)."""
    e = _estado.get(trap_id)
    ahora = time.time() if ahora is None else ahora
    if not e or ahora - e["t"] > CADUCA_S:
        return None
    if e["discrepa"] >= REPETICIONES and ahora - (e["desde"] or ahora) >= GRACIA_S:
        return "fabrica" if e["origen"] == "fabrica" else "desfase"
    return None


def ver(trap_id: int) -> dict | None:
    return _estado.get(trap_id)


def olvidar() -> None:
    """Solo para las pruebas."""
    _estado.clear()
