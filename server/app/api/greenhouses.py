"""Router de invernaderos (CRUD) + mapa de calor por sector + enlace de cliente."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from ..core.db import get_session
from ..core.deps import current_user, is_farmer, require_admin
from ..models.models import Invernadero, Trampa
from ..services import heatmap as heatmap_svc
from .schemas import GreenhouseIn, GreenhouseOut
from .serializers import greenhouse_out

router = APIRouter(prefix="/api/v1", tags=["invernaderos"])


def _check_farmer_access(request: Request, gh_id: int):
    """El agricultor solo puede acceder a su invernadero asignado."""
    user = current_user(request)
    if is_farmer(user) and user.get("invernadero_id") != gh_id:
        raise HTTPException(403, "No tienes acceso a este invernadero.")


@router.get("/greenhouses", response_model=list[GreenhouseOut])
def list_greenhouses(request: Request, session: Session = Depends(get_session)):
    user = current_user(request)
    q = select(Invernadero).order_by(Invernadero.id)
    if is_farmer(user):
        q = q.where(Invernadero.id == (user.get("invernadero_id") or -1))
    ghs = session.exec(q).all()
    return [greenhouse_out(session, g) for g in ghs]


@router.post("/greenhouses", response_model=GreenhouseOut, status_code=201,
             dependencies=[Depends(require_admin)])
def create_greenhouse(body: GreenhouseIn, session: Session = Depends(get_session)):
    gh = Invernadero(**body.model_dump())
    session.add(gh)
    session.commit()
    session.refresh(gh)
    return greenhouse_out(session, gh)


@router.get("/greenhouses/{gh_id}", response_model=GreenhouseOut)
def get_greenhouse(gh_id: int, request: Request, session: Session = Depends(get_session)):
    _check_farmer_access(request, gh_id)
    gh = session.get(Invernadero, gh_id)
    if not gh:
        raise HTTPException(404, "Invernadero no encontrado")
    return greenhouse_out(session, gh)


@router.patch("/greenhouses/{gh_id}", response_model=GreenhouseOut,
              dependencies=[Depends(require_admin)])
def update_greenhouse(gh_id: int, body: GreenhouseIn, session: Session = Depends(get_session)):
    gh = session.get(Invernadero, gh_id)
    if not gh:
        raise HTTPException(404, "Invernadero no encontrado")
    for k, v in body.model_dump().items():
        setattr(gh, k, v)
    session.add(gh)
    session.commit()
    session.refresh(gh)
    return greenhouse_out(session, gh)


@router.delete("/greenhouses/{gh_id}", status_code=204,
               dependencies=[Depends(require_admin)])
def delete_greenhouse(gh_id: int, session: Session = Depends(get_session)):
    gh = session.get(Invernadero, gh_id)
    if not gh:
        raise HTTPException(404, "Invernadero no encontrado")
    # desasignar sus trampas (no se borran las trampas)
    for t in session.exec(select(Trampa).where(Trampa.invernadero_id == gh_id)).all():
        t.invernadero_id = None
        t.sector = None
        t.estado = "sin_asignar"
        session.add(t)
    session.delete(gh)
    session.commit()


@router.get("/greenhouses/{gh_id}/heatmap")
def greenhouse_heatmap(gh_id: int, request: Request, days: int = 7,
                       session: Session = Depends(get_session)):
    _check_farmer_access(request, gh_id)
    gh = session.get(Invernadero, gh_id)
    if not gh:
        raise HTTPException(404, "Invernadero no encontrado")
    return heatmap_svc.sector_counts(session, gh_id, days=days)


@router.get("/greenhouses/{gh_id}/map")
def greenhouse_map(gh_id: int, request: Request, session: Session = Depends(get_session)):
    _check_farmer_access(request, gh_id)
    gh = session.get(Invernadero, gh_id)
    if not gh:
        raise HTTPException(404, "Invernadero no encontrado")
    return heatmap_svc.map_grid(session, gh_id)


@router.post("/greenhouses/{gh_id}/client-link", dependencies=[Depends(require_admin)])
def greenhouse_client_link(gh_id: int, session: Session = Depends(get_session)):
    """Genera (o devuelve) el token del enlace de cliente de solo lectura."""
    gh = session.get(Invernadero, gh_id)
    if not gh:
        raise HTTPException(404, "Invernadero no encontrado")
    if not gh.client_token:
        gh.client_token = secrets.token_urlsafe(9)
        session.add(gh)
        session.commit()
        session.refresh(gh)
    return {"token": gh.client_token, "path": f"/cliente/{gh.client_token}"}
