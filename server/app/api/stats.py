"""Estadísticas globales (KPIs de la galería, balance de plagas)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session

from ..core.db import get_session
from ..core.deps import current_user, is_farmer, visible_trap_ids
from ..services import stats as stats_svc
from ..services import tendencia as tendencia_svc

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats")
def stats(request: Request, session: Session = Depends(get_session)):
    user = current_user(request)
    trap_ids = visible_trap_ids(session, user) if is_farmer(user) else None
    return stats_svc.global_stats(session, trap_ids)


def _trampas_tendencia(session: Session, user, trampa_id, invernadero_id) -> list[int]:
    from sqlmodel import select

    from ..models.models import Trampa
    from ..services import traps as traps_svc
    from ..services.demo import DEMO_DEVICE
    q = select(Trampa.id).where(Trampa.device_id.not_in((traps_svc.MANUAL_UPLOAD_DEVICE,
                                                          DEMO_DEVICE)))
    if trampa_id is not None:
        q = q.where(Trampa.id == trampa_id)
    if invernadero_id is not None:
        q = q.where(Trampa.invernadero_id == invernadero_id)
    ids = list(session.exec(q.order_by(Trampa.id)).all())
    if is_farmer(user):
        vis = set(visible_trap_ids(session, user))
        ids = [i for i in ids if i in vis]
    return ids


@router.get("/stats/tendencia")
def tendencia(request: Request, trampa_id: int | None = None, invernadero_id: int | None = None,
              desde: date | None = None, hasta: date | None = None, formato: str = "json",
              session: Session = Depends(get_session)):
    """Media diaria de mosca blanca y trips por trampa (ver `services/tendencia.py`).

    `formato=csv` la devuelve como CSV para la memoria o un artículo. Se recalcula en cada
    petición: si un modelo mejor reprocesa el pasado, la serie cambia sola.
    """
    ids = _trampas_tendencia(session, current_user(request), trampa_id, invernadero_id)
    series = tendencia_svc.serie(session, ids, desde, hasta)
    if formato == "csv":
        return Response(tendencia_svc.csv_texto(series), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 'attachment; filename="tendencia_entomira.csv"'})
    return {"series": series}
