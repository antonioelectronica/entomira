"""Vista de cliente (solo lectura, con token) — sin autenticación de admin.

El enlace del invernadero enseña lo mismo que ve su agricultor al entrar con su perfil (petición
de Antonio, 2026-10-16): el plano, la población que hay ahora en las tarjetas y las fotos
RECORTADAS de las trampas del invernadero, con sus detecciones para pintar los recuadros. Nada de
control, ni el original sin recortar, ni datos técnicos de la trampa o de la captura.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from ..core.db import get_session
from ..core.deps import greenhouse_trap_ids
from ..models.models import Imagen, Invernadero, Trampa
from ..services import heatmap as heatmap_svc
from ..services import stats as stats_svc
from .schemas import ImageOut
from .serializers import image_out

router = APIRouter(prefix="/api/v1", tags=["cliente"])


def _invernadero(session: Session, token: str) -> Invernadero:
    gh = session.exec(
        select(Invernadero).where(Invernadero.client_token == token)).first() if token else None
    if not gh:
        raise HTTPException(404, "Enlace no válido")
    return gh


def _imagen(session: Session, token: str, image_id: int) -> Imagen:
    gh = _invernadero(session, token)
    img = session.get(Imagen, image_id)
    if not img or img.es_demo or img.trampa_id not in greenhouse_trap_ids(session, gh.id):
        raise HTTPException(404, "Imagen no encontrada")
    return img


@router.get("/client/{token}")
def client_map(token: str, session: Session = Depends(get_session)):
    gh = _invernadero(session, token)
    return heatmap_svc.map_grid(session, gh.id)


@router.get("/client/{token}/stats")
def client_stats(token: str, session: Session = Depends(get_session)):
    """La población en las tarjetas, igual que /stats para el agricultor de ese invernadero."""
    gh = _invernadero(session, token)
    s = stats_svc.global_stats(session, greenhouse_trap_ids(session, gh.id) or [-1])
    return {k: s[k] for k in ("insectos", "por_especie", "mosca_blanca", "trips", "plagas",
                              "pendientes", "analizadas", "fotos", "actividad_1d",
                              "trampas_activas")}


@router.get("/client/{token}/images", response_model=list[ImageOut])
def client_images(token: str, limit: int = Query(60, le=200), offset: int = 0,
                  session: Session = Depends(get_session)):
    gh = _invernadero(session, token)
    ids = greenhouse_trap_ids(session, gh.id) or [-1]
    imgs = session.exec(select(Imagen).where(Imagen.trampa_id.in_(ids), Imagen.es_demo == False)  # noqa: E712
                        .order_by(Imagen.capturada.desc()).offset(offset).limit(limit)).all()
    # Sin el identificador del dispositivo ni las condiciones de captura: datos de mantenimiento.
    return [image_out(session, im).model_copy(update={"device_id": "", "condiciones": None})
            for im in imgs]


@router.get("/client/{token}/images/{image_id}/file")
def client_image_file(token: str, image_id: int, session: Session = Depends(get_session)):
    """Solo la foto recortada (la de trabajo, donde caen los recuadros)."""
    img = _imagen(session, token, image_id)
    if img.ruta_recorte and Path(img.ruta_recorte).is_file():
        return FileResponse(img.ruta_recorte, media_type="image/jpeg")
    # Sin recorte: si la trampa está calibrada, la foto de campo NO sale sin recortar (hallazgo
    # de Codex). Las de una trampa sin calibrar (el móvil del agricultor) no tienen otra versión:
    # salen tal cual, igual que en su perfil.
    trampa = session.get(Trampa, img.trampa_id)
    if trampa is None or trampa.recorte_tarjeta:
        raise HTTPException(404, "Foto todavía sin recortar")
    return FileResponse(img.ruta_fichero, media_type="image/jpeg")


@router.get("/client/{token}/images/{image_id}/thumb")
def client_image_thumb(token: str, image_id: int, session: Session = Depends(get_session)):
    img = _imagen(session, token, image_id)
    # La miniatura sale del recorte cuando lo hay; nunca se cae al original (hallazgo de Codex).
    if not img.ruta_thumbnail or not Path(img.ruta_thumbnail).is_file():
        raise HTTPException(404, "Sin miniatura")
    return FileResponse(img.ruta_thumbnail, media_type="image/jpeg")
