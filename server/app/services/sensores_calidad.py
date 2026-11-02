"""Sensores que dan un número que no es una medida: a cero o clavados.

Fallo F6 del experimento del artículo B: un sensor suelto que se manda como `0.0` en vez de
`null`. La telemetría lo acepta —está dentro de los límites físicos— y la web lo enseña como un
dato más. Nadie lo ve porque un cero parece una medida. Aquí se buscan las lecturas imposibles
con la trampa encendida, no las raras: un aviso que salta con datos buenos se aprende a ignorar.

Reglas, todas sobre trampas vistas en la última hora (con la trampa callada, sus sensores no
dicen nada nuevo y ya avisa «sin fotos»):

- **INA a cero**: las `N_CERO` últimas ventanas con tensión < 1 V, o con corriente máxima 0. Una
  trampa encendida no puede tener 0 mA en su entrada durante 5 minutos.
- **INA clavado**: `N_CLAVADO` ventanas seguidas con tensión y corrientes idénticas. El INA226 real
  tiene ruido de sobra para no repetir cuatro cifras media hora. SOLO con tensión medida: sin VBUS,
  las corrientes redondeadas de una trampa en reposo sí pueden repetirse (hallazgo de Codex).
- **Celda**: tensión < 1 V (el MAX17043 ya descarta en la trampa lo que sale de 2,5–4,5 V, así que
  un cero aquí viene de un sensor suelto), o 0 % con la celda por encima de 3,7 V.

Una tensión AUSENTE no es un cero: el pin VBUS del INA puede no estar cableado (así se montó el
2026-09-24) y entonces la ventana trae corriente sin tensión.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..models.models import LecturaConsumo, Trampa

N_CERO = 3
N_CLAVADO = 6
RECIENTE = timedelta(hours=1)
V_CERO = 1.0
CELDA_CERO_V = 1.0
CELDA_LLENA_V = 3.7


def _utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def ventanas_recientes(session: Session, ids: list[int],
                       ahora: datetime | None = None) -> dict[int, list[LecturaConsumo]]:
    """Las ventanas de la última hora de TODAS esas trampas en una consulta, más nuevas primero."""
    ahora = ahora or datetime.now(timezone.utc)
    por_trampa: dict[int, list[LecturaConsumo]] = defaultdict(list)
    if not ids:
        return por_trampa
    for f in session.exec(
            select(LecturaConsumo).where(LecturaConsumo.trampa_id.in_(ids),
                                         LecturaConsumo.fin >= ahora - RECIENTE)
            .order_by(LecturaConsumo.trampa_id, LecturaConsumo.fin.desc())).all():
        por_trampa[f.trampa_id].append(f)
    return por_trampa


def problemas(trap: Trampa, ventanas: list[LecturaConsumo],
              ahora: datetime | None = None) -> list[str]:
    """Frases cortas, una por problema. Vacía si no hay nada que decir o no hay dato.

    `ventanas`: las de la última hora de esta trampa, más nuevas primero (`ventanas_recientes`).
    """
    ahora = ahora or datetime.now(timezone.utc)
    visto = _utc(trap.ultimo_visto)
    if visto is None or ahora - visto > RECIENTE:
        return []
    out: list[str] = []

    filas = ventanas[:N_CLAVADO]
    ult = filas[:N_CERO]
    if len(ult) == N_CERO:
        if all(f.v_media is not None and f.v_media < V_CERO for f in ult):
            out.append(f"el medidor de consumo lee 0 V en sus {N_CERO} últimas ventanas")
        elif all(f.ma_max is not None and f.ma_max == 0 for f in ult):
            out.append(f"el medidor de consumo lee 0 mA en sus {N_CERO} últimas ventanas")
    if len(filas) == N_CLAVADO and not out:
        firma = {(f.v_media, f.ma_media, f.ma_min, f.ma_max) for f in filas}
        v, ma, _, _ = next(iter(firma))
        if len(firma) == 1 and v is not None and ma is not None:
            out.append(f"el medidor de consumo repite el mismo valor en {N_CLAVADO} ventanas seguidas")

    if trap.voltaje is not None and trap.voltaje < CELDA_CERO_V:
        out.append(f"la celda marca {trap.voltaje:g} V")
    elif trap.bateria == 0 and trap.voltaje is not None and trap.voltaje >= CELDA_LLENA_V:
        out.append(f"la batería marca 0 % con la celda a {trap.voltaje:.2f} V")
    return out
