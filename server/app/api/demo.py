"""Router de Demo: siembra una imagen y se consulta hasta que el Worker la procesa."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.db import get_session
from ..core.deps import require_admin
from ..models.models import ConfigCamara, Imagen, Trampa
from ..services import demo as demo_svc
from ..services import traps as traps_svc
from .schemas import ImageOut
from .serializers import image_out

# La Demo es una función del panel de administración.
router = APIRouter(prefix="/api/v1", tags=["demo"], dependencies=[Depends(require_admin)])


@router.post("/demo/run", response_model=ImageOut, status_code=201)
def demo_run(trap_id: int | None = None, session: Session = Depends(get_session)):
    """
    Lanza una demo. Si se indica una trampa con cámara en línea, toma una **foto real**
    de esa cámara y la analiza. Si no, usa el fallback (imagen del dataset).
    Devuelve la imagen sembrada (estado 'pendiente'); el frontend hace el escáner
    mientras el Worker la procesa y luego consulta GET /demo/{id}.
    """
    if trap_id is not None:
        trap = session.get(Trampa, trap_id)
        if not trap:
            raise HTTPException(404, "Trampa no encontrada")
        estado = traps_svc.computed_estado(trap, session.get(ConfigCamara, trap.id))
        # Apagada desde la web: su IP guardada no sirve de nada, no hay a quién pedirle la foto.
        if estado == "apagada" or (estado != "online" and not trap.ip):
            raise HTTPException(409, "La trampa no está en línea.")
        try:
            img = demo_svc.capture_from_trap(session, trap)
        except (FileNotFoundError, ConnectionError) as e:
            raise HTTPException(502, str(e))
        return image_out(session, img)

    # fallback: imagen del dataset
    try:
        img = demo_svc.seed_demo_image(session)
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    return image_out(session, img)


@router.get("/demo/{image_id}", response_model=ImageOut)
def demo_status(image_id: int, session: Session = Depends(get_session)):
    img = session.get(Imagen, image_id)
    if not img:
        raise HTTPException(404, "Demo no encontrada")
    return image_out(session, img)


@router.get("/demo/samples/count")
def demo_samples(session: Session = Depends(get_session)):
    return {"samples": len(demo_svc.list_samples())}
