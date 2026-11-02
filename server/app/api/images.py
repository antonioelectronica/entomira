"""Router de imágenes / galería."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete as sa_delete, func
from sqlmodel import Session, select

from ..core.db import get_session
from ..core.deps import current_user, is_farmer, require_admin, visible_trap_ids
from ..models.models import Deteccion, Imagen, Trampa
from .schemas import ImageOut
from .serializers import image_out

router = APIRouter(prefix="/api/v1", tags=["imagenes"])


def _farmer_restrict(session: Session, request: Request):
    """Si es agricultor, las trampas que puede ver (suyas y de su invernadero); si no, None."""
    user = current_user(request)
    if is_farmer(user):
        return visible_trap_ids(session, user) or [-1]
    return None


def _filtered_query(session, invernadero, sector, trampa, clase, estado,
                    incluir_demo, desde, hasta, restrict_traps=None, resolucion=None):
    """Construye el SELECT de Imagen con todos los filtros (compartido por listado y conteo)."""
    q = select(Imagen)
    if restrict_traps is not None:
        q = q.where(Imagen.trampa_id.in_(restrict_traps))
    if trampa is not None:
        q = q.where(Imagen.trampa_id == trampa)
    if estado:
        q = q.where(Imagen.estado == estado)
    # Por condiciones de captura: es el eje del analisis del proyecto (el tamano aparente del
    # objetivo fija el techo de precision), asi que se puede aislar el corpus de una resolucion.
    if resolucion:
        q = q.where(Imagen.resolucion == resolucion)
    if not incluir_demo:
        q = q.where(Imagen.es_demo == False)  # noqa: E712
    if desde:
        try:
            q = q.where(Imagen.capturada >= datetime.fromisoformat(desde))
        except ValueError:
            pass
    if hasta:
        try:
            q = q.where(Imagen.capturada <= datetime.fromisoformat(hasta))
        except ValueError:
            pass
    # filtros por invernadero/sector via join a trampa
    if invernadero is not None or sector:
        trap_ids = _trap_ids(session, invernadero, sector)
        q = q.where(Imagen.trampa_id.in_(trap_ids or [-1]))
    # filtro por especie en SQL (imágenes con al menos una detección de esa clase)
    if clase:
        q = q.where(Imagen.id.in_(
            select(Deteccion.imagen_id).where(Deteccion.clase == clase)))
    return q


@router.get("/images", response_model=list[ImageOut])
def list_images(
    request: Request,
    invernadero: int | None = None,
    sector: str | None = None,
    trampa: int | None = None,
    clase: str | None = None,
    estado: str | None = None,
    # Por defecto FUERA: las de la vista Demo salen de un dataset, no de una trampa,
    # y mezcladas con las capturas reales ensucian la galeria y falsean los conteos.
    # Con ?incluir_demo=true se pueden volver a ver.
    incluir_demo: bool = False,
    resolucion: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    limit: int = Query(120, le=500),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    restrict = _farmer_restrict(session, request)
    q = _filtered_query(session, invernadero, sector, trampa, clase, estado,
                        incluir_demo, desde, hasta, restrict, resolucion)
    imgs = session.exec(
        q.order_by(Imagen.capturada.desc()).offset(offset).limit(limit)
    ).all()
    return [image_out(session, im) for im in imgs]


@router.get("/images/count")
def count_images(
    request: Request,
    invernadero: int | None = None,
    sector: str | None = None,
    trampa: int | None = None,
    clase: str | None = None,
    estado: str | None = None,
    # Por defecto FUERA: las de la vista Demo salen de un dataset, no de una trampa,
    # y mezcladas con las capturas reales ensucian la galeria y falsean los conteos.
    # Con ?incluir_demo=true se pueden volver a ver.
    incluir_demo: bool = False,
    resolucion: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    session: Session = Depends(get_session),
):
    """Total de imágenes que cumplen los filtros (para la paginación de la galería)."""
    restrict = _farmer_restrict(session, request)
    q = _filtered_query(session, invernadero, sector, trampa, clase, estado,
                        incluir_demo, desde, hasta, restrict, resolucion)
    total = session.exec(select(func.count()).select_from(q.subquery())).one()
    return {"total": total}


def _borrar_imagenes(session: Session, imgs) -> tuple[int, int, int]:
    """Borra ficheros y filas de esas imágenes. Devuelve (borradas, ficheros, bytes)."""
    # 1) ficheros primero (libera disco aunque la BD falle después)
    liberado = ficheros = 0
    for im in imgs:
        for ruta in (im.ruta_fichero, im.ruta_thumbnail, im.ruta_recorte):
            if not ruta:
                continue
            p = Path(ruta)
            try:
                liberado += p.stat().st_size
                p.unlink()
                ficheros += 1
            except OSError:
                pass

    # 2) filas por lotes (detecciones antes que imágenes: FK)
    ids = [im.id for im in imgs]
    session.expunge_all()
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        session.execute(sa_delete(Deteccion).where(Deteccion.imagen_id.in_(lote)))
        session.execute(sa_delete(Imagen).where(Imagen.id.in_(lote)))
        session.commit()
    return len(ids), ficheros, liberado


@router.delete("/images", dependencies=[Depends(require_admin)])
def empty_gallery(incluir_demo: bool = False, ids: str | None = None,
                  session: Session = Depends(get_session)):
    """Borra imágenes del servidor (ficheros + registros). Solo admin.

    - Con **`?ids=1,2,3`**: borra EXACTAMENTE esas y nada más. Es lo que usa el botón de
      borrar la selección de la galería.
    - Sin `ids`: vacía la galería entera, **conservando las de demostración** (`es_demo=1`)
      salvo que se pase `?incluir_demo=true`.

    Las detecciones asociadas caen con la imagen. Es irreversible: el frontend confirma.
    """
    # `ids is not None`, NO `if ids`: con `?ids=` vacio la cadena es falsa y se caia al
    # camino de VACIAR LA GALERIA ENTERA. Un cliente que mande la lista vacia por error no
    # puede acabar borrandolo todo.
    if ids is not None:
        try:
            lista = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(422, "El parámetro ids debe ser una lista de números.")
        if not lista:
            raise HTTPException(422, "No se ha indicado ninguna imagen.")
        # Al pedirlas por id se borran tal cual, demo incluida: el usuario ha seleccionado
        # esas y no otras, y no tiene por qué saberse la regla de las de demostración.
        imgs = session.exec(select(Imagen).where(Imagen.id.in_(lista))).all()
        borradas, ficheros, liberado = _borrar_imagenes(session, imgs)
        return {"borradas": borradas, "ficheros": ficheros,
                "liberado_mb": round(liberado / 1e6, 1), "pedidas": len(lista),
                "incluia_demo": True, "conservadas_demo": 0}

    q = select(Imagen)
    if not incluir_demo:
        q = q.where(Imagen.es_demo == False)  # noqa: E712
    imgs = session.exec(q).all()
    borradas, ficheros, liberado = _borrar_imagenes(session, imgs)
    ids_borradas = [None] * borradas   # solo para el conteo de abajo

    # Cuantas de demostracion quedan. Sin este dato el usuario ve la galeria con fotos
    # despues de «vaciar» y concluye que el boton no funciona: le pasó exactamente eso.
    quedan_demo = 0
    if not incluir_demo:
        quedan_demo = len(session.exec(
            select(Imagen.id).where(Imagen.es_demo == True)).all())  # noqa: E712

    return {"borradas": len(ids_borradas), "ficheros": ficheros,
            "liberado_mb": round(liberado / 1e6, 1), "incluia_demo": incluir_demo,
            "conservadas_demo": quedan_demo}


def _trap_ids(session: Session, invernadero: int | None, sector: str | None):
    tq = select(Trampa.id)
    if invernadero is not None:
        tq = tq.where(Trampa.invernadero_id == invernadero)
    if sector:
        tq = tq.where(Trampa.sector == sector)
    return list(session.exec(tq).all())


def _get_owned_image(session: Session, request: Request, image_id: int) -> Imagen:
    img = session.get(Imagen, image_id)
    if not img:
        raise HTTPException(404, "Imagen no encontrada")
    restrict = _farmer_restrict(session, request)
    if restrict is not None and img.trampa_id not in restrict:
        raise HTTPException(404, "Imagen no encontrada")
    return img


@router.get("/images/zip")
def images_zip(request: Request, ids: str = "", session: Session = Depends(get_session)):
    """Descarga un ZIP con las imágenes indicadas (?ids=1,2,3). Respeta el scoping del agricultor."""
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:300]
    restrict = _farmer_restrict(session, request)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for iid in id_list:
            img = session.get(Imagen, iid)
            if not img:
                continue
            if restrict is not None and img.trampa_id not in restrict:
                continue
            # El agricultor solo recibe la foto recortada (decisión de Antonio, 16/10).
            ruta = img.ruta_fichero
            if restrict is not None and img.ruta_recorte and Path(img.ruta_recorte).is_file():
                ruta = img.ruta_recorte
            try:
                z.write(ruta, arcname=f"entomira_{iid}.jpg")
            except OSError:
                pass
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=entomira_imagenes.zip"})


@router.get("/images/{image_id}", response_model=ImageOut)
def get_image(image_id: int, request: Request, session: Session = Depends(get_session)):
    img = _get_owned_image(session, request, image_id)
    return image_out(session, img)


@router.get("/images/{image_id}/file")
def image_file(image_id: int, request: Request, session: Session = Depends(get_session)):
    """La foto de TRABAJO: el recorte de la tarjeta si existe. Es la que descarga el worker
    de IA (decisión de Antonio, 15/10: el modelo ve solo el recorte) y la que pinta la galería,
    así que las cajas de detección caen en sus coordenadas."""
    img = _get_owned_image(session, request, image_id)
    ruta = img.ruta_recorte if img.ruta_recorte and Path(img.ruta_recorte).is_file() else img.ruta_fichero
    return FileResponse(ruta, media_type="image/jpeg")


@router.get("/images/{image_id}/original")
def image_original(image_id: int, request: Request, session: Session = Depends(get_session)):
    """El JPEG tal como lo subió la trampa, sin recortar ni recomprimir. Solo para el admin:
    el agricultor ve la foto recortada (decisión de Antonio, 2026-10-16)."""
    img = _get_owned_image(session, request, image_id)
    if is_farmer(current_user(request)):
        raise HTTPException(403, "La foto sin recortar solo la ve el administrador.")
    return FileResponse(img.ruta_fichero, media_type="image/jpeg")


@router.get("/images/{image_id}/thumb")
def image_thumb(image_id: int, request: Request, session: Session = Depends(get_session)):
    img = _get_owned_image(session, request, image_id)
    path = img.ruta_thumbnail or img.ruta_fichero
    return FileResponse(path, media_type="image/jpeg")


@router.post("/images/reprocesar", dependencies=[Depends(require_admin)])
def reprocesar(trampa: int | None = None, desde: datetime | None = None,
               hasta: datetime | None = None, session: Session = Depends(get_session)):
    """Pide al worker otra pasada de la IA sobre fotos YA procesadas (p. ej. con un modelo mejor).

    Las fotos no vuelven a `pendiente`: hasta que el worker las recuente, sigue valiendo el
    conteo anterior, y el aviso de Telegram no las anuncia como nuevas. La tendencia diaria se
    actualiza sola al llegar los conteos nuevos.
    """
    from datetime import timezone

    from sqlalchemy import update

    def utc_naive(dt: datetime) -> datetime:      # la BD guarda UTC sin zona
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    q = update(Imagen).where(Imagen.estado == "procesada", Imagen.es_demo == False)  # noqa: E712
    if trampa is not None:
        q = q.where(Imagen.trampa_id == trampa)
    if desde is not None:
        q = q.where(Imagen.capturada >= utc_naive(desde))
    if hasta is not None:
        q = q.where(Imagen.capturada <= utc_naive(hasta))
    n = session.execute(q.values(reprocesar=True)).rowcount or 0
    session.commit()
    return {"ok": True, "marcadas": n}
