"""Avisa a la web cuando una trampa ENTRA o SALE de línea, aunque nadie haya hecho nada.

EL FALLO QUE ARREGLA (Antonio, 2026-09-24: «en el servidor siempre aparece la trampa online»).
La web solo se repinta cuando `services/cambios.py` sube el contador, y el contador solo sube
cuando PASA algo: una foto, una telemetría, un cambio de config. Una trampa que se calla no
hace pasar nada —ese es justo el síntoma—, así que la tarjeta se quedaba «En línea» con el
dato de cuando se pintó hasta que alguien recargaba la página. `en_linea` estaba bien
calculado en el servidor; lo que no había era quién le dijera a la web que lo volviera a pedir.

Se revisa desde `/cambios`, que la web ya sondea cada 4 s, y como mucho cada 30 s: son dos
columnas de una tabla de pocas filas, con una sesión corta que se suelta al momento (el pool
de SQLite es de 15 conexiones y aquí no se puede tener una retenida).
"""
from __future__ import annotations

import time
from threading import Lock

from sqlmodel import select

from ..core.db import sesion_corta
from ..models.models import ConfigCamara, Trampa
from . import cambios as cambios_svc
from . import traps as traps_svc

REVISAR_CADA_S = 30.0

_lock = Lock()
_ultima_revision = 0.0
_estado: dict[int, bool] | None = None      # None = aún no se ha mirado nunca


def revisar() -> bool:
    """Mira quién está en línea y anuncia un cambio si alguna trampa ha cruzado. Devuelve si lo hizo.

    La primera vez solo toma nota: al arrancar el servidor no ha «cambiado» nada, y anunciar
    un cambio ahí repintaría todas las pestañas abiertas sin motivo.
    """
    global _ultima_revision, _estado
    ahora = time.monotonic()
    with _lock:
        if ahora - _ultima_revision < REVISAR_CADA_S:
            return False
        _ultima_revision = ahora
    try:
        with sesion_corta() as s:
            trampas = s.exec(select(Trampa)).all()
            cfgs = {c.trampa_id: c for c in s.exec(select(ConfigCamara)).all()}
            nuevo = {t.id: traps_svc.en_linea(t, cfgs.get(t.id)) for t in trampas}
    except Exception:                          # noqa: BLE001 - esto no puede tumbar /cambios
        return False
    with _lock:
        antes, _estado = _estado, nuevo
    if antes is None or antes == nuevo:
        return False
    # «bateria» es el tipo que ya escuchan Trampas y Control (TIPOS_POR_VISTA en app.js): así
    # se repintan en caliente, sin cortar el vídeo, igual que con cualquier telemetría.
    cambios_svc.marcar("bateria")
    return True


def _reiniciar_para_pruebas() -> None:
    global _ultima_revision, _estado
    with _lock:
        _ultima_revision, _estado = 0.0, None
