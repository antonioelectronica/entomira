"""Router de inferencia: el Worker consume pendientes y sube resultados."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlmodel import Session, select

from ..core.db import get_session
from ..services import cambios as cambios_svc
from ..core.security import require_worker
from ..models.models import Deteccion, Imagen
from ..services.clases import gestionada
from .schemas import InferenceResult

router = APIRouter(prefix="/api/v1", tags=["inferencia"],
                   dependencies=[Depends(require_worker)])


@router.get("/inference/pending")
def pending(limit: int = 20, session: Session = Depends(get_session)):
    from .health import mark_worker_seen
    mark_worker_seen()
    imgs = session.exec(
        select(Imagen).where(Imagen.estado == "pendiente")
        .order_by(Imagen.capturada).limit(limit)
    ).all()
    # Los reprocesos van DETRÁS de las fotos nuevas: una foto sin contar vale más que recontar.
    if len(imgs) < limit:
        imgs += session.exec(
            select(Imagen).where(Imagen.reprocesar == True, Imagen.estado != "pendiente")  # noqa: E712
            .order_by(Imagen.capturada).limit(limit - len(imgs))
        ).all()
    return [{"image_id": im.id, "trampa_id": im.trampa_id, "es_demo": im.es_demo,
             "capturada": im.capturada.isoformat()} for im in imgs]


@router.get("/inference/{image_id}/detections")
def detections(image_id: int, session: Session = Depends(get_session)):
    """Lo que vio el modelo de producción en una foto. Lo consulta el etiquetador (Modelo 1).

    Devuelve TODAS las clases guardadas; quien las pinta decide cuáles usa. `estado` distinto de
    «procesada» significa «sin dato», no «cero insectos».
    """
    img = session.get(Imagen, image_id)
    if not img:
        raise HTTPException(404, "Imagen no encontrada")
    return {"image_id": img.id, "estado": img.estado, "modelo_version": img.modelo_version,
            "ancho": img.ancho, "alto": img.alto, "recortada": bool(img.ruta_recorte),
            "detections": [{"clase": d.clase, "confianza": d.confianza,
                            "x": d.x, "y": d.y, "w": d.w, "h": d.h} for d in img.detecciones]}


@router.put("/inference/{image_id}/ilegible")
def ilegible(image_id: int, session: Session = Depends(get_session)):
    """El worker no ha podido leer esta imagen. Se marca `error` y SALE DE LA COLA.

    Sin esto, la cola se vuelve a atascar entera por otro camino. El arreglo de anoche puso el
    `try` por imagen, asi que una ilegible ya no corta el lote; pero la fallida **seguia
    `pendiente` en el servidor** y el worker solo la saltaba en su memoria. Como esta vista
    sirve las 20 primeras POR FECHA, con 20 ilegibles a la cabeza el lote entero se salta y la
    21 no llega nunca. Reproducido en `scripts/_repro_cola_worker.py`: 21 pendientes, 20
    fallidas, **0 procesables**, sin un solo error y con el worker dando senales de vida.

    Marcar `error` no pierde nada: la imagen ya era ilegible, sigue en disco, y la purga se la
    lleva a los 7 dias como cualquier otra sin procesar. Lo que se recupera es la cola.
    """
    img = session.get(Imagen, image_id)
    if not img:
        raise HTTPException(404, "Imagen no encontrada")

    # EN UNA SOLA OPERACION, Y NO «MIRAR Y LUEGO ESCRIBIR». Una que ya se proceso no se
    # degrada: si llega tarde el aviso de un intento anterior, no puede tirar un resultado
    # bueno. Comprobarlo en Python y escribir despues deja un hueco entre las dos cosas, y por
    # ese hueco cabe justo el caso que se quiere evitar: `/ilegible` lee «pendiente»,
    # `/inference/{id}` confirma el recuento, y `/ilegible` lo pisa con «error». La condicion
    # viaja en el UPDATE, asi que la decide la base. Hallazgo de Codex.
    res = session.execute(
        update(Imagen).where(Imagen.id == image_id, Imagen.estado != "procesada")
        .values(estado="error"))
    # Un reproceso ilegible sale de la cola y CONSERVA su conteo anterior: era bueno.
    session.execute(update(Imagen).where(Imagen.id == image_id).values(reprocesar=False))
    session.commit()
    cambiado = bool(res.rowcount)
    if cambiado:
        cambios_svc.marcar("inferencia")
    session.refresh(img)
    return {"ok": True, "image_id": image_id, "estado": img.estado, "cambiado": cambiado}


@router.put("/inference/{image_id}")
def submit(image_id: int, result: InferenceResult, session: Session = Depends(get_session)):
    img = session.get(Imagen, image_id)
    if not img:
        raise HTTPException(404, "Imagen no encontrada")

    # limpia detecciones previas (reproceso idempotente)
    for d in list(img.detecciones):
        session.delete(d)
    for d in result.detections:
        session.add(Deteccion(imagen_id=img.id, clase=d.clase, confianza=d.confianza,
                              x=d.x, y=d.y, w=d.w, h=d.h))

    # Solo cuentan las clases que se gestionan (WF, TR): MR/NC se guardan pero no suman.
    img.num_insectos = (sum(1 for d in result.detections if gestionada(d.clase))
                        if result.detections else (result.num_insectos or 0))
    img.modelo_version = result.model_version
    # Un reproceso no cambia `procesada`: es el cursor del aviso de Telegram, que si no la
    # tomaría por una foto nueva y la volvería a anunciar.
    if not (img.reprocesar and img.estado == "procesada" and img.procesada):
        img.procesada = datetime.now(timezone.utc)
    img.estado = "procesada"
    img.reprocesar = False
    session.add(img)
    session.commit()
    cambios_svc.marcar("inferencia")
    return {"ok": True, "image_id": image_id, "num_insectos": img.num_insectos}
