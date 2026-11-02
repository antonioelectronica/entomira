"""Ajuste de color de la cámara (gains R/G/B del OV5640) con previsualización en vivo.

Solo admin. La previsualización aplica los gains a una muestra RAW (sin corregir) de la
última captura del ESP32 → el usuario ve el efecto en tiempo real antes de guardar.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from PIL import Image as PILImage
from sqlmodel import Session, select

from ..core.db import get_session
from ..core.deps import require_admin
from ..models.models import Imagen, Trampa
from ..services import images as images_svc

router = APIRouter(prefix="/api/v1", tags=["color"],
                   dependencies=[Depends(require_admin)])


class ColorParams(BaseModel):
    r_gain: float | None = None
    g_gain: float | None = None
    b_gain: float | None = None
    hires_min_w: int | None = None


@router.get("/image-params")
def get_params():
    return images_svc.load_img_params()


@router.put("/image-params")
def set_params(p: ColorParams):
    return images_svc.save_img_params(p.model_dump(exclude_none=True))


def _sample_raw(session: Session) -> PILImage.Image | None:
    """Muestra RAW para la previsualización: la última captura del ESP32 sin corregir."""
    if images_svc.CALIB_SAMPLE.exists():
        try:
            return PILImage.open(images_svc.CALIB_SAMPLE).convert("RGB")
        except Exception:
            pass
    # fallback: última imagen de una trampa física (ya está corregida, pero sirve de referencia)
    trap_ids = list(session.exec(select(Trampa.id).where(Trampa.owner_id.is_(None))).all())
    if trap_ids:
        img = session.exec(
            select(Imagen).where(Imagen.trampa_id.in_(trap_ids))
            .order_by(Imagen.capturada.desc()).limit(1)).first()
        if img:
            try:
                return PILImage.open(img.ruta_fichero).convert("RGB")
            except Exception:
                pass
    return None


@router.get("/image-params/preview")
def preview(r: float = Query(0.90), g: float = Query(1.25), b: float = Query(0.90),
            hires_min_w: int = Query(1500), raw: bool = Query(False),
            session: Session = Depends(get_session)):
    """Devuelve un JPEG de la muestra con los gains dados (o RAW si raw=true)."""
    sample = _sample_raw(session)
    if sample is None:
        # imagen gris con aviso si aún no hay muestra
        sample = PILImage.new("RGB", (640, 480), (200, 198, 194))
    # limita el tamaño para que la preview sea rápida
    sample.thumbnail((900, 900))
    out_img = sample if raw else images_svc.apply_gains(sample, r, g, b, 1)  # forzar aplicación
    buf = io.BytesIO()
    out_img.save(buf, "JPEG", quality=88)
    return Response(content=buf.getvalue(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
