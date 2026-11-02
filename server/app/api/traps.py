"""Router de trampas (web) + config de cámara + config para la PCB."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, func, select

from ..core import config as cfg
from ..core.db import get_session
from ..core.deps import current_user, is_farmer, require_admin, visible_trap_ids
from ..core.security import require_device
from ..services import cambios as cambios_svc
from ..services import condiciones as cond_svc
from ..models.models import ConfigCamara, Imagen, Trampa, LecturaHardware
from ..services import consumo as consumo_svc
from ..services import ina as ina_svc
from ..services import live as live_svc
from ..services import traps as traps_svc
from .schemas import ConfigOut, ConfigUpdate, TrapCreate, TrapOut, TrapUpdate
from .serializers import config_out, trap_out

router = APIRouter(prefix="/api/v1", tags=["trampas"])


@router.get('/traps/{trap_id}/hardware')
def hardware(trap_id: int, request: Request, horas: int = 24,
             session: Session = Depends(get_session)):
    trap = session.get(Trampa, trap_id)
    if not trap or (is_farmer(current_user(request)) and trap.owner_id != current_user(request)['id']):
        raise HTTPException(404, 'Trampa no encontrada')
    horas = max(1, min(horas, 168))
    from datetime import timedelta
    desde = datetime.now(timezone.utc) - timedelta(hours=horas)
    filas = session.exec(select(LecturaHardware).where(
        LecturaHardware.trampa_id == trap_id, LecturaHardware.instante >= desde)
        .order_by(LecturaHardware.instante.desc()).limit(10080)).all()
    return {'lecturas': [{'instante': f.instante, 'tension_v': f.tension_v,
                         'corriente_ma': f.corriente_ma, 'rtc_hora': f.rtc_hora}
                        for f in reversed(filas)]}


# Lo que un agricultor NO ve de una trampa de su invernadero (acordado con Codex, 2026-10-16):
# red, firmware, consumo, reposo y configuración son datos de mantenimiento, no de su cultivo.
_SOLO_ADMIN = {"ip": None, "fw_version": None, "operador": None, "red": None, "ifaz": None,
               "lte_lista": None, "rssi": None, "wifi_pct": None, "datos_mes_b": None,
               "consumo_ina": None, "reposo_hasta": None, "reposo_modo": None,
               "reposo_error": None, "despertar_origen": None, "despertar_informado": None,
               "carga_fuente": None, "carga_confianza": None, "tendencia_mv_h": None,
               "crate": None, "config": None, "px_mm": None, "distancia_cm": None}


def _para(user: dict | None, t: TrapOut) -> TrapOut:
    """La ficha completa para el admin y para el dueño; recortada para el agricultor que solo la
    ve por ser de su invernadero."""
    if not is_farmer(user) or t.es_movil:
        return t
    return t.model_copy(update=_SOLO_ADMIN)


@router.get("/traps", response_model=list[TrapOut])
def list_traps(request: Request, invernadero: int | None = None, sector: str | None = None,
               estado: str | None = None, incluir_demo: bool = False,
               session: Session = Depends(get_session)):
    from ..services.demo import DEMO_DEVICE
    q = select(Trampa)
    # El PC puede subir imágenes, pero no es una trampa que se deba controlar o vigilar.
    q = q.where(Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)
    # `demo-fallback` no es una trampa: es el dispositivo ficticio que usa la vista Demo
    # cuando no hay hardware alcanzable. Aparecia en el listado como si fuera una trampa mas.
    if not incluir_demo:
        q = q.where(Trampa.device_id != DEMO_DEVICE)
    user = current_user(request)
    if is_farmer(user):
        q = q.where(Trampa.id.in_(visible_trap_ids(session, user) or [-1]))
    if invernadero is not None:
        q = q.where(Trampa.invernadero_id == invernadero)
    if sector:
        q = q.where(Trampa.sector == sector)
    traps = session.exec(q.order_by(Trampa.id)).all()
    out = [_para(user, trap_out(session, t)) for t in traps]
    if estado:
        out = [t for t in out if t.estado == estado]
    return out


@router.post("/traps", response_model=TrapOut, status_code=201,
             dependencies=[Depends(require_admin)])
def create_trap(body: TrapCreate, session: Session = Depends(get_session)):
    if session.exec(select(Trampa).where(Trampa.device_id == body.device_id)).first():
        raise HTTPException(409, "Ya existe una trampa con ese device_id")
    if body.sector and body.sector not in cfg.SECTORES:
        raise HTTPException(422, f"Sector inválido; usa {cfg.SECTORES}")
    trap = Trampa(device_id=body.device_id, alias=body.alias or body.device_id,
                  invernadero_id=body.invernadero_id, sector=body.sector,
                  estado="sin_asignar" if body.invernadero_id is None else "offline")
    session.add(trap)
    session.commit()
    session.refresh(trap)
    session.add(ConfigCamara(trampa_id=trap.id))
    session.commit()
    session.refresh(trap)
    return trap_out(session, trap)


@router.get("/traps/{trap_id}", response_model=TrapOut)
def get_trap(trap_id: int, request: Request, session: Session = Depends(get_session)):
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    user = current_user(request)
    if is_farmer(user) and trap.id not in visible_trap_ids(session, user):
        raise HTTPException(404, "Trampa no encontrada")
    return _para(user, trap_out(session, trap))


@router.patch("/traps/{trap_id}", response_model=TrapOut,
              dependencies=[Depends(require_admin)])
def update_trap(trap_id: int, body: TrapUpdate, session: Session = Depends(get_session)):
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    if body.sector is not None and body.sector and body.sector not in cfg.SECTORES:
        raise HTTPException(422, f"Sector inválido; usa {cfg.SECTORES}")
    if body.alias is not None:
        trap.alias = body.alias
    if body.invernadero_id is not None:
        trap.invernadero_id = body.invernadero_id or None
    if body.sector is not None:
        trap.sector = body.sector or None
    if body.linea is not None:
        trap.linea = body.linea or None
    if body.px_mm is not None:
        trap.px_mm = body.px_mm
    if body.distancia_cm is not None:
        trap.distancia_cm = body.distancia_cm
    if trap.invernadero_id is None:
        trap.estado = "sin_asignar"
    session.add(trap)
    session.commit()
    session.refresh(trap)
    return trap_out(session, trap)


@router.delete("/traps/{trap_id}", status_code=204,
               dependencies=[Depends(require_admin)])
def delete_trap(trap_id: int, session: Session = Depends(get_session)):
    """Da de baja una trampa y todo lo que colgaba de ella.

    Las tablas que apuntan a `trampa` deben vaciarse antes de borrar la fila, o la clave
    ajena (`PRAGMA foreign_keys=ON`) impide el borrado: `imagen` —y sus ficheros y detecciones—,
    `tarjeta`, `confighistorial`, `consumo`, `lecturahardware` y `config_camara`.

    Hasta el 2026-09-12 solo se limpiaban `imagen` y `config_camara`, así que **borrar una
    trampa que hubiera reportado algo devolvía un 500**: en cuanto habla una vez por 4G tiene
    fila en `consumo`, y en cuanto sube una foto tiene `tarjeta`. O sea que funcionaba solo con
    trampas recién creadas que no habían hecho nada — justo las únicas con las que se probó.
    Encontrado al limpiar las 20 trampas del ensayo, que no se dejaban borrar.
    """
    from ..models.models import ConfigHistorial, Consumo, Tarjeta

    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")

    # 1) imágenes: fichero, miniatura y fila (la fila arrastra sus detecciones)
    imgs = session.exec(select(Imagen).where(Imagen.trampa_id == trap_id)).all()
    for im in imgs:
        for ruta in (im.ruta_fichero, im.ruta_thumbnail, im.ruta_recorte):
            try:
                if ruta:
                    Path(ruta).unlink(missing_ok=True)
            except OSError:
                pass
        session.delete(im)

    # 2) todo lo demás que referencia a la trampa
    for modelo in (Tarjeta, ConfigHistorial, Consumo, LecturaHardware):
        for fila in session.exec(select(modelo).where(modelo.trampa_id == trap_id)).all():
            session.delete(fila)

    cfgm = session.get(ConfigCamara, trap.id)
    if cfgm:
        session.delete(cfgm)
    session.delete(trap)
    session.commit()


@router.put("/traps/{trap_id}/config", response_model=ConfigOut,
            dependencies=[Depends(require_admin)])
def update_config(trap_id: int, body: ConfigUpdate, session: Session = Depends(get_session)):
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    cfgm = traps_svc.ensure_config(session, trap)
    changed = False
    for field, val in body.model_dump(exclude_none=True).items():
        if getattr(cfgm, field) != val:
            setattr(cfgm, field, val)
            changed = True
    if changed:
        cfgm.version += 1
        cfgm.actualizado = datetime.now(timezone.utc)
        session.add(cfgm)
        session.commit()
        session.refresh(cfgm)
        # Congela la version nueva en el historico: las fotos solo guardan el numero de
        # version, asi que sin esta fila no hay forma de saber despues que significaba.
        cond_svc.instantanea(session, cfgm)
        cambios_svc.marcar("config")
    if cfgm.deep_sleep:
        # La política de campo prevalece incluso si queda un panel antiguo abierto.
        live_svc.parar_vivo(trap_id)
        traps_svc.parar_keep_awake(trap_id)
    return config_out(cfgm)


@router.get("/traps/{trap_id}/consumo", dependencies=[Depends(require_admin)])
def trap_consumo(trap_id: int, dias: int = 30, session: Session = Depends(get_session)):
    """Datos móviles gastados por esta trampa: total, desglose por concepto y por día.

    Devuelve por separado lo **medido** (`bytes`, la carga útil) y lo **contado**
    (`conexiones`), además del `estimado_b` que las suma con el coste del saludo TLS. Un total
    sin desglose no deja decidir qué recortar, que es justo para lo que sirve este número.
    """
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    dias = max(1, min(dias, 365))
    return consumo_svc.resumen(session, trap_id, dias)


@router.get("/traps/{trap_id}/consumo-ina", dependencies=[Depends(require_admin)])
def trap_consumo_ina(trap_id: int, horas: int = 24, session: Session = Depends(get_session)):
    """Corriente y tension de entrada medidas por el INA226, una fila por ventana de 5 min.

    No confundir con `/consumo`, que son megas de la SIM. Lista vacia = no hay lecturas en ese
    plazo; nunca se rellena con ceros.
    """
    if not session.get(Trampa, trap_id):
        raise HTTPException(404, "Trampa no encontrada")
    horas = max(1, min(horas, 24 * 7))
    return {"horas": horas, "ventanas": ina_svc.serie(session, trap_id, horas)}


@router.get("/traps/{trap_id}/config/historial", dependencies=[Depends(require_admin)])
def config_historial(trap_id: int, limit: int = 50, session: Session = Depends(get_session)):
    """Configuraciones por las que ha pasado esta trampa, y cuántas fotos hay de cada una.

    Es la vista que permite responder «¿con qué condiciones se tomó este lote?» sin abrir la
    base de datos, y ver de un vistazo si un cambio de exposición o de resolución movió los
    conteos. Cada foto guarda solo el número de versión; aquí está lo que significaba.
    """
    from ..models.models import ConfigHistorial

    filas = session.exec(
        select(ConfigHistorial).where(ConfigHistorial.trampa_id == trap_id)
        .order_by(ConfigHistorial.version.desc()).limit(limit)).all()
    out = []
    for h in filas:
        n = session.exec(select(func.count()).select_from(Imagen).where(
            Imagen.trampa_id == trap_id, Imagen.config_version == h.version)).one()
        d = {c: getattr(h, c) for c in cond_svc.CAMPOS}
        d.update({"version": h.version, "creado": h.creado, "num_imagenes": n})
        out.append(d)
    return {"trampa_id": trap_id, "historial": out}


@router.post("/traps/{trap_id}/keep-awake", dependencies=[Depends(require_admin)])
def keep_awake(trap_id: int, session: Session = Depends(get_session)):
    """En banco no hace falta; en campo no puede impedir el corte."""
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    cfgm = traps_svc.ensure_config(session, trap)
    return {"ok": True, "segundos": 0,
            "duerme": bool(cfgm.deep_sleep),
            "intervalo_seg": cfgm.intervalo_seg}


@router.get("/traps/{device_id}/config", response_model=ConfigOut,
            dependencies=[Depends(require_device)])
def device_config(device_id: str, session: Session = Depends(get_session)):
    """La PCB consulta su config vigente (por device_id)."""
    trap = session.exec(select(Trampa).where(Trampa.device_id == device_id)).first()
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    consumo_svc.anotar(session, trap, "config", bytes_=400)
    # AL DISPOSITIVO SE LE SIRVE LA POLITICA YA APLICADA, no la configuracion cruda: con datos
    # moviles, una foto al dia. Regla de Antonio, 2026-09-20.
    return config_out(traps_svc.ensure_config(session, trap), trap, para_dispositivo=True)


@router.get("/traps/{trap_id}/recorte", dependencies=[Depends(require_admin)])
def ver_recorte(trap_id: int, session: Session = Depends(get_session)):
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    return {"recorte": trap.recorte_tarjeta}


@router.put("/traps/{trap_id}/recorte", dependencies=[Depends(require_admin)])
def fijar_recorte(trap_id: int, body: dict, session: Session = Depends(get_session)):
    """Calibración fija del recorte de la tarjeta; solo afecta a las fotos que lleguen después.

    `{"res": [4608, 2592], "quad": [[x, y] ×4]}` (sup-izq, sup-der, inf-der, inf-izq) en px del
    original, o `{"recorte": null}` para quitarlo.
    """
    from ..services import recorte as recorte_svc
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    if "recorte" in body and body["recorte"] is None:
        trap.recorte_tarjeta = None
    else:
        try:
            trap.recorte_tarjeta = recorte_svc.validar(body)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    session.add(trap)
    session.commit()
    return {"recorte": trap.recorte_tarjeta,
            "version": recorte_svc.version(trap.recorte_tarjeta) if trap.recorte_tarjeta else None}
