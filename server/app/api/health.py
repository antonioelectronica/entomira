"""Salud del servidor y del worker."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from ..core.db import get_session
from ..models.models import Imagen, Invernadero, Trampa
from ..services import estado as estado_svc
from ..services import traps as traps_svc

router = APIRouter(prefix="/api/v1", tags=["salud"])


@router.get("/cambios")
def cambios():
    """Contador que sube cuando cambia algo (foto, inferencia, config, bateria).

    Lo sondea la web cada pocos segundos para repintarse sola sin que el usuario
    recargue. Ver services/cambios.py.
    """
    from ..services import cambios as cambios_svc
    from ..services import en_linea as en_linea_svc
    # Una trampa que se calla no produce ningún cambio: sin esto, la web la seguía pintando
    # «En línea» hasta recargar. Se revisa como mucho cada 30 s. Ver services/en_linea.py.
    en_linea_svc.revisar()
    return cambios_svc.estado()

# marca en memoria del último poll del worker
_worker_last_seen: dict[str, datetime] = {}


def mark_worker_seen() -> None:
    _worker_last_seen["ts"] = datetime.now(timezone.utc)
    # Y se le dice tambien al servicio de estado, que es quien construye los avisos que ve el
    # usuario. Antes este dato se quedaba aqui: existia `/health/worker`, pero nadie lo
    # miraba, asi que un worker parado no generaba ni un aviso. Ver `estado.avisos()`.
    estado_svc.marcar_worker_visto()


@router.get("/health")
def health(session: Session = Depends(get_session)):
    # COUNT(*), no traer las filas: este endpoint lo llama CADA pestaña abierta cada 15 s,
    # y materializar todas las imagenes pendientes como objetos ORM se nota con la galeria
    # llena. Medido en la auditoria de agosto.
    n_traps = session.exec(select(func.count()).select_from(Trampa).where(
        Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)).one()
    n_gh = session.exec(select(func.count()).select_from(Invernadero)).one()
    n_pend = session.exec(select(func.count()).select_from(Imagen)
                          .where(Imagen.estado == "pendiente")).one()
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(),
            "trampas": n_traps, "invernaderos": n_gh, "pendientes": n_pend}


@router.get("/avisos")
def avisos(session: Session = Depends(get_session)):
    """Problemas activos que merecen la atencion del usuario.

    Existe por la leccion del 31/07: el disco se lleno, la ingesta dejo de aceptar fotos y
    nadie se entero en TRES DIAS porque el servicio seguia `active` y la web funcionaba.
    Lista vacia = todo en orden.
    """
    from ..services import estado as estado_svc
    lista = estado_svc.avisos(session)
    return {"avisos": lista,
            "criticos": sum(1 for a in lista if a["nivel"] == "critico"),
            "total": len(lista)}


@router.get("/health/worker")
def worker_health():
    ts = _worker_last_seen.get("ts")
    online = bool(ts and (datetime.now(timezone.utc) - ts).total_seconds() < 60)
    return {"online": online, "last_seen": ts.isoformat() if ts else None}
