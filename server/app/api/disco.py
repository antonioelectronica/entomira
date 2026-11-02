"""Router del disco externo del servidor y del apagado seguro (2026-10-23).

Todo es de administrador y lo que es irreversible o deja el servidor sin servicio pide una
confirmación explícita en el cuerpo, además del diálogo de la web. Ver `services/disco.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import config
from ..core.deps import require_admin
from ..services import disco as disco_svc

router = APIRouter(prefix="/api/v1", tags=["disco"], dependencies=[Depends(require_admin)])


class Confirmacion(BaseModel):
    confirmar: str = ""


class Periodo(BaseModel):
    desde: datetime | None = None
    hasta: datetime | None = None


def _utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(sep=" ")


def _llamar(f, *args):
    try:
        return f(*args)
    except disco_svc.DiscoError as e:
        raise HTTPException(409, str(e))


@router.get("/disco")
def estado():
    return disco_svc.estado()


@router.post("/disco/montar")
def montar():
    return {"ok": True, "mensaje": _llamar(disco_svc.montar), **disco_svc.estado()}


@router.post("/disco/expulsar")
def expulsar(body: Confirmacion):
    if body.confirmar != "EXPULSAR":
        raise HTTPException(400, "Falta la confirmación («EXPULSAR»).")
    return {"ok": True, "mensaje": _llamar(disco_svc.expulsar)}


@router.post("/sistema/apagar")
def apagar(body: Confirmacion):
    """Apaga el servidor (p. ej. para moverlo). Desmonta antes el disco; para encenderlo hay que
    volver a darle corriente. Mientras esté apagado no entra ninguna foto: las trampas las
    guardan en su tarjeta y las suben al volver."""
    if body.confirmar != "APAGAR":
        raise HTTPException(400, "Falta la confirmación («APAGAR»).")
    return {"ok": True, "mensaje": _llamar(disco_svc.apagar_servidor)}


@router.post("/disco/backup/invernadero/{gh_id}")
def backup_invernadero(gh_id: int, body: Periodo | None = None):
    """Pack de campaña de un invernadero (todas sus trampas juntas) en el disco. En segundo plano:
    el progreso y el resultado salen en GET /disco → backup."""
    body = body or Periodo()
    _llamar(disco_svc.lanzar_pack, config.DB_PATH, gh_id, _utc(body.desde), _utc(body.hasta))
    return {"ok": True, "backup": disco_svc.estado()["backup"]}
