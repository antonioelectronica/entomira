"""Corriente y tensión de entrada de la trampa, medidas por su INA226 (ver `LecturaConsumo`).

Lo que llega por la telemetría es una LISTA de ventanas de 5 min, y aquí se decide qué se
guarda. La regla que manda sobre todas las demás: **una ventana mala no tumba la telemetría**.
Si el endpoint rechazara el informe entero por un campo raro, la trampa lo reenviaría igual
en el siguiente, y el siguiente, y dejaría de dar señales de vida por culpa de un número. Así
que las ventanas se validan una a una aquí dentro y la que no se entiende se descarta sola.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from ..models.models import LecturaConsumo

log = logging.getLogger(__name__)

# Tope de ventanas por informe. La trampa guarda 36 (3 h); esto solo protege de un cuerpo
# desmesurado, no recorta nada de lo que ella manda de verdad.
MAX_VENTANAS = 60
# Límites físicos, anchos a propósito: están para tirar basura, no para opinar. El raíl es de
# 5 V y el shunt de 0,1 ohm satura hacia los 819 mA; cualquier cosa fuera de esto es un fallo
# de lectura o de transmisión, no una medida.
_V_MAX = 30.0
_MA_MAX = 10_000.0
_CAMPOS = ("v_media", "v_min", "ma_media", "ma_min", "ma_max", "mwh")
# Ventanas con fechas imposibles. La cola de la trampa guarda 3 h, así que dos días de atrás es
# holgadísimo; y una ventana del FUTURO es un reloj desajustado: guardada, quedaría como la
# «última» lectura durante meses y taparía todas las buenas. Hallazgo de Codex, 2026-09-26.
_FUTURO_MAX = timedelta(minutes=10)
_PASADO_MAX = timedelta(days=2)
# Nombres cortos del cable (la telemetría viaja por la SIM) -> nombre de la columna.
_DEL_CABLE = {"v": "v_media", "v_min": "v_min", "ma": "ma_media", "ma_min": "ma_min",
              "ma_max": "ma_max", "mwh": "mwh"}


def _utc(d: datetime) -> datetime:
    """SQLite devuelve sin zona; lo guardado es siempre UTC."""
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _num(x, tope: float) -> float | None:
    """Un número medido, o `None`. Nunca convierte un hueco en cero."""
    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if not math.isfinite(x) or abs(x) > tope:
        return None
    return x


def _ventana(d) -> dict | None:
    """Una ventana del cable ya limpia, o `None` si no se puede creer."""
    if not isinstance(d, dict):
        return None
    try:
        fin = _utc(datetime.fromisoformat(str(d.get("fin"))))
    except (TypeError, ValueError):
        return None
    ahora = datetime.now(timezone.utc)
    if not ahora - _PASADO_MAX <= fin <= ahora + _FUTURO_MAX:
        return None
    n, sat = d.get("n"), d.get("sat", 0)
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return None
    if not isinstance(sat, int) or isinstance(sat, bool) or not 0 <= sat <= n:
        return None
    fila = {"fin": fin, "n": n, "saturadas": sat}
    for cable, col in _DEL_CABLE.items():
        fila[col] = _num(d.get(cable), _V_MAX if col.startswith("v") else _MA_MAX)
    # SIN TENSION PUEDE HABER VENTANA: si el pin VBUS del modulo no esta cableado, el chip
    # mide la corriente perfecta y la tension no (asi se monto el 2026-09-24). Lo que no se
    # cree es una ventana sin NADA: ni tension, ni corriente, ni muestras saturadas.
    if fila["v_media"] is None and fila["ma_media"] is None and not sat:
        return None
    return fila


def guardar(session: Session, trampa_id: int, ventanas) -> int:
    """Añade las ventanas nuevas. Devuelve cuántas eran nuevas. NO hace commit.

    El commit es del que llama, que ya tiene la sesión abierta para la telemetría: así la
    trampa y sus lecturas se guardan juntas o no se guarda nada.
    """
    if not isinstance(ventanas, list) or not ventanas:
        return 0
    limpias, malas = {}, 0
    for d in ventanas[:MAX_VENTANAS]:
        v = _ventana(d)
        if v is None:
            malas += 1
            continue
        limpias[v["fin"]] = v
    if malas:
        log.warning("trampa %s: %d ventana(s) del INA descartadas por ilegibles", trampa_id, malas)
    if not limpias:
        return 0
    # Las que ya estan: la misma ventana llega repetida si se perdio la respuesta anterior.
    # Se comparan en UTC normalizado porque SQLite las devuelve sin zona.
    ya = {_utc(f) for f in session.exec(
        select(LecturaConsumo.fin).where(
            LecturaConsumo.trampa_id == trampa_id,
            LecturaConsumo.fin >= min(limpias) - timedelta(seconds=1)))}
    filas = [{"trampa_id": trampa_id, **v} for fin, v in limpias.items() if fin not in ya]
    if not filas:
        return 0
    # ON CONFLICT DO NOTHING, y no comprobar-y-luego-insertar a secas: dos informes con la misma
    # ventana que se crucen pasarían los dos la comprobación de arriba, y el segundo reventaría
    # contra el índice único con un 500 que además deshace el contacto de la trampa. La
    # comprobación se queda como atajo; quien garantiza es el índice. Codex, 2026-09-26.
    res = session.execute(sqlite_insert(LecturaConsumo).values(filas)
                          .on_conflict_do_nothing(index_elements=["trampa_id", "fin"]))
    return max(0, res.rowcount or 0)


def _fila(r: LecturaConsumo) -> dict:
    return {"fin": _utc(r.fin), "n": r.n, "saturadas": r.saturadas,
            **{c: getattr(r, c) for c in _CAMPOS}}


def ultima(session: Session, trampa_id: int) -> dict | None:
    r = session.exec(select(LecturaConsumo).where(LecturaConsumo.trampa_id == trampa_id)
                     .order_by(LecturaConsumo.fin.desc()).limit(1)).first()
    return _fila(r) if r else None


def serie(session: Session, trampa_id: int, horas: int) -> list[dict]:
    desde = datetime.now(timezone.utc) - timedelta(hours=horas)
    filas = session.exec(select(LecturaConsumo).where(
        LecturaConsumo.trampa_id == trampa_id, LecturaConsumo.fin >= desde)
        .order_by(LecturaConsumo.fin)).all()
    return [_fila(r) for r in filas]
