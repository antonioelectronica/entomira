"""
Router de tarjetas adhesivas: el periodo de exposición de cada conteo.

Ver `services/tarjeta.py` para el porqué. En resumen: sin saber cuándo se puso el cartón, un
conteo no significa nada comparable, y cambiarlo parece una plaga que se desvanece.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..core.db import get_session
from ..core.deps import require_admin
from ..models.models import Trampa
from ..services import cambios as cambios_svc
from ..services import tarjeta as tarjeta_svc

router = APIRouter(prefix="/api/v1", tags=["tarjetas"])


class TarjetaNueva(BaseModel):
    nota: str = ""


class TarjetaFecha(BaseModel):
    colocada: datetime          # con zona; sin ella se toma como UTC


def _trampa(session: Session, trap_id: int) -> Trampa:
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    return trap


@router.get("/traps/{trap_id}/tarjeta")
def tarjeta_actual(trap_id: int, session: Session = Depends(get_session)):
    """La tarjeta que está puesta ahora: días expuesta, conteo y capturas/día."""
    _trampa(session, trap_id)
    return tarjeta_svc.resumen(session, tarjeta_svc.actual(session, trap_id))


@router.get("/traps/{trap_id}/tarjetas")
def lista_tarjetas(trap_id: int, limit: int = 30, session: Session = Depends(get_session)):
    """Histórico de tarjetas de esa trampa, de la más reciente a la más antigua.

    Es la serie que hace falta para hablar de dinámica de plaga en vez de de fotos: cada fila
    es un periodo cerrado con su conteo y su ritmo de captura.
    """
    _trampa(session, trap_id)
    return {"trampa_id": trap_id, "tarjetas": tarjeta_svc.historial(session, trap_id, limit)}


@router.post("/traps/{trap_id}/tarjeta", dependencies=[Depends(require_admin)])
def tarjeta_nueva(trap_id: int, body: TarjetaNueva | None = None,
                  session: Session = Depends(get_session)):
    """Botón «tarjeta nueva»: retira la que estaba y empieza a contar un periodo limpio.

    A partir de aquí el conteo vuelve a cero **y el sistema lo sabe**, que era justo el punto:
    la caída deja de ser una anomalía y pasa a ser un evento con fecha.
    """
    _trampa(session, trap_id)
    anterior = tarjeta_svc.actual(session, trap_id)      # cierra bien aunque no hubiera ninguna
    nueva = tarjeta_svc.nueva(session, trap_id, (body.nota if body else "") or "")
    session.refresh(anterior)                            # ya cerrada: con retirada y conteo
    cambios_svc.marcar("tarjeta")
    return {"anterior": tarjeta_svc.resumen(session, anterior),
            "actual": tarjeta_svc.resumen(session, nueva)}


@router.patch("/traps/{trap_id}/tarjeta", dependencies=[Depends(require_admin)])
def corregir_tarjeta(trap_id: int, body: TarjetaFecha, session: Session = Depends(get_session)):
    """Corrige la fecha de colocación de la tarjeta puesta (no crea una nueva).

    Para cuando el registro empezó tarde o la tarjeta se cambió sin pulsar «tarjeta nueva»: el
    ritmo de capturas por día depende de esa fecha.
    """
    _trampa(session, trap_id)
    try:
        t = tarjeta_svc.corregir_colocada(session, trap_id, body.colocada)
    except tarjeta_svc.FechaNoValida as e:
        raise HTTPException(409, str(e))
    cambios_svc.marcar("tarjeta")
    return tarjeta_svc.resumen(session, t)
