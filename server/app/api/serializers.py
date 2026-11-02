"""Conversión de modelos ORM a esquemas de salida.

Nota de escalabilidad: con decenas de cámaras y miles de imágenes, NUNCA cargar
`trap.imagenes` completo en memoria; usar COUNT / ORDER BY ... LIMIT 1 en SQL.
"""
from __future__ import annotations

from ..services.clases import gestionada

from sqlmodel import Session, func, select

from ..models.models import ConfigCamara, Imagen, Invernadero, Trampa
from ..services import bateria as bat_svc, ina as ina_svc, live as live_svc
from ..services import bateria_estimada as bat_est_svc
from ..services import consumo as consumo_svc
from ..services import condiciones as cond_svc
from ..services import traps as traps_svc
from .schemas import (ConfigOut, DetectionOut, GreenhouseOut, ImageOut, TrapOut,
                      UltimaImagenOut)


def config_out(cfg: ConfigCamara | None, trap: Trampa | None = None,
               para_dispositivo: bool = False) -> ConfigOut | None:
    """La config tal y como la ve quien pregunta.

    Con `trap`, se calcula el intervalo EFECTIVO --la politica de datos puede recortarlo-- y
    se explica por que. Al DISPOSITIVO se le sirve ya recortado en `intervalo_seg`: asi obedece
    aunque su firmware no conozca los campos nuevos, que es el fallo seguro. Al panel se le
    sirve lo configurado, y aparte lo efectivo y el motivo, para que pueda enseñar las dos
    cosas sin que parezca que la trampa esta averiada.
    """
    if not cfg:
        return None
    efectivo, motivo = (traps_svc.intervalo_efectivo(trap, cfg) if trap is not None
                        else (cfg.intervalo_seg, None))
    return ConfigOut(version=cfg.version,
                     intervalo_seg=efectivo if para_dispositivo else cfg.intervalo_seg,
                     intervalo_config_seg=cfg.intervalo_seg,
                     intervalo_efectivo_seg=efectivo, intervalo_motivo=motivo,
                     ancho=cfg.ancho, alto=cfg.alto, calidad_jpeg=cfg.calidad_jpeg,
                     brillo=cfg.brillo, contraste=cfg.contraste,
                     saturacion=cfg.saturacion, ev=cfg.ev, balance=cfg.balance,
                     enfoque=cfg.enfoque, lente_dioptrias=cfg.lente_dioptrias,
                     deep_sleep=cfg.deep_sleep,
                     hora_despertar=cfg.hora_despertar,
                     horas_despertar=cfg.horas_despertar or [cfg.hora_despertar],
                     ventana_despierto_seg=cfg.ventana_despierto_seg)


def _senal_pct(rssi: int | None, red: str | None) -> int | None:
    # Por USB no hay radio: la placa manda `WiFi.RSSI()` = 0 con el WiFi apagado, y la escala
    # del WiFi lo convertia en «100 %» de cobertura, que es exactamente lo contrario de la
    # verdad. Un hueco honesto vale mas que un numero inventado.
    if rssi is None or red == "usb" or rssi == 0:
        return None
    if red == "lte":
        return max(0, min(100, round((rssi + 113) / 62 * 100)))   # -113..-51 dBm
    return max(0, min(100, 2 * (rssi + 100)))                     # -100..-50 dBm


def _num_imagenes(session: Session, trap_id: int) -> int:
    return session.exec(
        select(func.count()).select_from(Imagen).where(Imagen.trampa_id == trap_id)
    ).one()


def ultimo_conteo(session: Session, trap_id: int) -> int | None:
    row = session.exec(
        select(Imagen.num_insectos)
        .where(Imagen.trampa_id == trap_id, Imagen.estado == "procesada")
        .order_by(Imagen.capturada.desc()).limit(1)
    ).first()
    return row


def ultima_imagen(session: Session, trap_id: int) -> UltimaImagenOut | None:
    """La ultima foto de la trampa, procesada o no.

    Va DENTRO de la trampa a proposito: la vista Trampas la pedia con un
    `/images?trampa=N&limit=1` por cada una, y con 20 trampas eso son 20 peticiones a la vez
    contra un pool de 15 conexiones -- el fallo que ya dejo la API sin servicio en el ensayo de
    20 trampas. Una consulta mas aqui sale mucho mas barata que una peticion HTTP por trampa.

    `num_insectos` viaja en None si la foto no esta procesada. Es la regla de la casa: un 0 en
    una foto que nadie ha mirado no es «no hay insectos», es «no se sabe».
    """
    img = session.exec(
        select(Imagen).where(Imagen.trampa_id == trap_id)
        .order_by(Imagen.capturada.desc()).limit(1)).first()
    if not img:
        return None
    return UltimaImagenOut(
        id=img.id, capturada=img.capturada, estado=img.estado,
        num_insectos=img.num_insectos if img.estado == "procesada" else None,
        procesada=img.procesada)


def trap_out(session: Session, trap: Trampa) -> TrapOut:
    # Veredicto rapido de carga: la tension reacciona al instante, el crate tarda
    # 5-15 min en asentarse. Ver services/bateria.py.
    _carga = bat_svc.estado(trap.id, trap.crate)
    # Estado vivo del 4G: vive en memoria (`services/live.py`) y caduca en 3 min. Si no hay
    # dato reciente sale `None`, que NO es lo mismo que «no hay 4G»: es «no lo sé».
    _lte = live_svc.lte_listo(trap.id)
    cfg = session.get(ConfigCamara, trap.id)
    gh = session.get(Invernadero, trap.invernadero_id) if trap.invernadero_id else None
    return TrapOut(
        id=trap.id, device_id=trap.device_id, alias=trap.alias or trap.device_id,
        invernadero_id=trap.invernadero_id,
        invernadero_nombre=gh.nombre if gh else None,
        sector=trap.sector, linea=trap.linea, estado=traps_svc.computed_estado(trap, cfg),
          reposo_hasta=live_svc.reposo_hasta(trap.id),
          reposo_modo=live_svc.reposo_modo(trap.id),
          reposo_error=live_svc.reposo_error(trap.id),
        despertar_confirmado=trap.despertar_confirmado,   # UTC sin zona, como ultimo_visto
        despertar_origen=trap.despertar_origen,
        despertar_informado=trap.despertar_informado,
        en_linea=traps_svc.en_linea(trap, cfg),
        es_movil=trap.owner_id is not None,
        ip=trap.ip, bateria=trap.bateria,
        bateria_estimada=(bat_est_svc.estimar_por_tension(trap.voltaje)
                          if trap.bateria is None and trap.owner_id is None else None),
        voltaje=trap.voltaje,
        crate=trap.crate, rssi=trap.rssi, fw_version=trap.fw_version,
        buffer_pend=trap.buffer_pend,
        datos_mes_b=(consumo_svc.estimado_mes(session, trap.id)
                     if (trap.red or "") == "lte" else None),
        consumo_ina=ina_svc.ultima(session, trap.id),
        carga_estado=_carga['estado'], carga_fuente=_carga['fuente'],
        carga_confianza=_carga.get('confianza'),
        tendencia_mv_h=_carga['tendencia_mv_h'],
        red=trap.red, operador=trap.operador,
        # Lo que se USA y lo que ESTA DISPONIBLE son dos cosas: con el servidor en la misma
        # red, la trampa sube por WiFi aunque el 4G funcione. Decirlo por separado evita que
        # el panel parezca mentir cuando dice «WiFi» con la SIM enganchada.
        lte_lista=_lte[0], ifaz=_lte[1],
        # El % de señal se escala segun la red: en WiFi -100..-50 dBm cubre de nulo a
        # excelente; en movil el rango util es otro (-113..-51 dBm, que es el del CSQ), y
        # usar la escala del WiFi pintaba «100 %» con una cobertura mediocre.
        wifi_pct=_senal_pct(trap.rssi, trap.red),
        ultimo_visto=trap.ultimo_visto,
        num_imagenes=_num_imagenes(session, trap.id),
        ultimo_conteo=ultimo_conteo(session, trap.id),
        ultima_imagen=ultima_imagen(session, trap.id),
        px_mm=trap.px_mm, distancia_cm=trap.distancia_cm,
        config=config_out(cfg),
    )


def greenhouse_out(session: Session, gh: Invernadero) -> GreenhouseOut:
    # Los insectos se suman de TODOS los dispositivos del invernadero: la foto que un
    # agricultor hace con el movil a una trampa es una observacion valida.
    trap_ids = session.exec(
        select(Trampa.id).where(Trampa.invernadero_id == gh.id)).all()
    total = sum((ultimo_conteo(session, tid) or 0) for tid in trap_ids)
    # Pero el CONTADOR de trampas cuenta solo trampas de verdad. El movil de cada agricultor
    # se registra como dispositivo (`owner_id`) para poder subir fotos, y colarlo aqui inflaba
    # la cuenta: «2 trampas» cuando solo hay una camara. Mismo criterio que la vista Trampas,
    # que ya los oculta con `es_movil`.
    n_fisicas = len(session.exec(
        select(Trampa.id).where(Trampa.invernadero_id == gh.id,
                                Trampa.owner_id.is_(None),
                                Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)).all())
    return GreenhouseOut(
        id=gh.id, nombre=gh.nombre, ubicacion=gh.ubicacion,
        num_lineos=gh.num_lineos, responsable=gh.responsable, contacto=gh.contacto,
        email=gh.email, tipo=gh.tipo, cultivo=gh.cultivo, notas=gh.notas,
        client_token=gh.client_token, num_trampas=n_fisicas, num_insectos=total,
    )


def image_out(session: Session, img: Imagen) -> ImageOut:
    trap = session.get(Trampa, img.trampa_id)
    gh = session.get(Invernadero, trap.invernadero_id) if trap and trap.invernadero_id else None
    dets = [DetectionOut(clase=d.clase, confianza=d.confianza, x=d.x, y=d.y, w=d.w, h=d.h)
            for d in img.detecciones if gestionada(d.clase)]
    return ImageOut(
        id=img.id, trampa_id=img.trampa_id,
        device_id=trap.device_id if trap else "",
        alias=(trap.alias or trap.device_id) if trap else "",
        invernadero_id=trap.invernadero_id if trap else None,
        invernadero_nombre=gh.nombre if gh else None,
        sector=trap.sector if trap else None,
        estado=img.estado, num_insectos=img.num_insectos,
        modelo_version=img.modelo_version, es_demo=img.es_demo,
        ancho=img.ancho, alto=img.alto, capturada=img.capturada,
        condiciones=cond_svc.condiciones_de(session, img),
        detecciones=dets,
        recortada=bool(img.ruta_recorte),
    )
