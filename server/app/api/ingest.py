"""Ingesta de capturas desde las PCB (auto-registro incluido)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException, Request,
                     Response, UploadFile)
from sqlmodel import Session, select

from ..core import config
from ..core.db import get_session
from ..core.red import ip_valida
from ..core.deps import current_user, is_farmer
from ..models.models import Imagen
import shutil

from ..core import config
from ..services import bateria as bat_svc
from ..services import estado as estado_svc
from ..services import cambios as cambios_svc
from ..services import consumo as consumo_svc
from ..services import condiciones as cond_svc
from ..services import tarjeta as tarjeta_svc
from ..services import images as images_svc
from ..services import traps as traps_svc
from .schemas import IngestResponse
from .serializers import config_out

router = APIRouter(prefix="/api/v1", tags=["ingesta"])

# Tope de una subida. Una captura QSXGA de la trampa son ~343 KB; una foto de movil, varios MB.
# Se corta lo absurdo, no lo grande.
#
# En MB decimales (1e6), no en MiB: el mensaje de error dice «el tope son 20 MB» y tiene que
# ser 20, no 21. Un limite que se anuncia distinto del que se aplica es el mismo problema que
# un contador que no cuadra — pequeño, pero mina la confianza en lo que dice el sistema.
MAX_SUBIDA_B = 20_000_000

# Margen por debajo del cual se rechaza la ingesta. Una captura QSXGA ronda 1 MB;
# 300 MB dejan sitio de sobra para que la purga diaria actue antes del apuro.
MIN_LIBRE_MB = 300.0


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(
    request: Request,
    response: Response,
    image: UploadFile = File(...),
    device_id: str = Form(...),
    captured_at: str | None = Form(default=None),
    config_version: int | None = Form(default=None),
    ip: str | None = Form(default=None),
    bateria: int | None = Form(default=None),
    voltaje: float | None = Form(default=None),
    crate: float | None = Form(default=None),
    fw: str | None = Form(default=None),
    rssi: int | None = Form(default=None),
    # Por donde llego la foto. El puente serie manda 'usb' porque una imagen que entra por el
    # marco ENTOMIRA_IMG vino por cable, por definicion.
    red: str | None = Form(default=None),
    operador: str | None = Form(default=None),
    x_api_key: str = Header(default=""),
    session: Session = Depends(get_session),
):
    user = current_user(request)
    # Autorización: clave de dispositivo (ESP32) o usuario autenticado (app).
    if x_api_key != config.DEVICE_API_KEY and not user:
        raise HTTPException(401, "Se requiere clave de dispositivo o usuario.")

    # EL TAMAÑO SE MIRA ANTES DE DAR DE ALTA NADA.
    #
    # La cabecera `Content-Length` viene antes que el cuerpo, asi que se puede rechazar sin leer
    # 20 MB por la red. Y sobre todo: si se comprueba DESPUES de `get_or_register`, una subida
    # rechazada deja igualmente la trampa creada — se vio el 2026-09-14, con `esp32-TOPE01`
    # apareciendo en el panel sin haber subido jamas una foto. Una trampa en la lista es algo
    # que alguien va a ir a buscar al invernadero.
    # SE LEE Y SE COMPRUEBA EL TAMAÑO ANTES DE DAR DE ALTA NADA.
    #
    # El primer intento comprobaba despues y borraba la trampa si acababa de crearse. Mal por
    # dos razones: **cinco tablas** referencian `trampa` (es el mismo fallo que dejaba
    # `delete_trap` devolviendo 500 hasta ayer), y sobre todo porque deshacer algo es siempre
    # peor que no hacerlo.
    #
    # Comprobar antes hace que no haya nada que deshacer: si la foto no vale, la trampa no
    # llega a existir. Y eso importa porque **una trampa en la lista es algo que alguien va a ir
    # a buscar al invernadero** — se vio con `esp32-TOPE01`, que aparecia en el panel sin haber
    # subido jamas una foto.
    data = await image.read()
    if len(data) > MAX_SUBIDA_B:
        raise HTTPException(
            413, f"imagen demasiado grande: {len(data)/1e6:.1f} MB (el tope son "
                 f"{MAX_SUBIDA_B/1e6:.0f} MB). Una captura normal son ~0,3 MB.")

    trap, registered = traps_svc.get_or_register(session, device_id)
    cfg = traps_svc.ensure_config(session, trap)
    # Asegura que la version vigente esta en el historico. Idempotente y de una consulta:
    # sin esto, una trampa a la que nadie ha tocado la config (version 1) tendria fotos con
    # un numero de version que no significa nada.
    cond_svc.instantanea(session, cfg)
    changed = False
    # Si sube un agricultor, la trampa (su móvil) le pertenece y va a su invernadero.
    if is_farmer(user):
        if trap.owner_id != user["id"]:
            trap.owner_id = user["id"]
            changed = True
        if user.get("invernadero_id") and trap.invernadero_id != user["invernadero_id"]:
            trap.invernadero_id = user["invernadero_id"]
            changed = True
        if not trap.alias or trap.alias == trap.device_id:
            trap.alias = f"Móvil de {user.get('nombre') or user['username']}"
            changed = True
    ip = ip_valida(ip)
    if ip and trap.ip != ip:
        trap.ip = ip
        changed = True
    # ¿ES UNA FOTO ATRASADA? Las del buffer traen la bateria y la cobertura de CUANDO SE
    # TOMARON, que puede ser de hace horas. Escribirlas en la fila de la trampa deja el panel
    # mostrando el pasado con cara de presente — y la bateria es justo lo que se mira para
    # decidir si hay que ir al invernadero.
    #
    # La foto se guarda igual, con su hora: lo que no se toca es el estado VIVO, que es cosa de
    # la telemetria.
    diferida = False
    if captured_at:
        try:
            cap = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
            if cap.tzinfo is None:
                cap = cap.replace(tzinfo=timezone.utc)
            diferida = (datetime.now(timezone.utc) - cap).total_seconds() > 300
        except ValueError:
            diferida = False

    if bateria is not None and not diferida:
        trap.bateria = bateria
        changed = True
    if voltaje is not None and not diferida:
        trap.voltaje = voltaje
        changed = True
        bat_svc.anotar(trap.id, voltaje)
    if crate is not None and not diferida:
        trap.crate = crate
        changed = True
    if fw and trap.fw_version != fw:
        trap.fw_version = fw
        changed = True
    if rssi is not None and not diferida:
        trap.rssi = rssi
        changed = True
    if red and trap.red != red:
        trap.red = red
        changed = True
    if operador and trap.operador != operador:
        trap.operador = operador
        changed = True
    if changed:
        session.add(trap)
        session.commit()

    # ESPACIO EN DISCO: el 31/07 el disco llego al 100 %, SQLite empezo a dar
    # "database or disk is full" y la ingesta estuvo 3 dias caida EN SILENCIO (422 errores
    # 500 en 24 h, con el servicio `active` y la web funcionando). Fallar con un 507 claro
    # y dejar constancia es infinitamente mejor que un 500 que nadie mira.
    libre_mb = shutil.disk_usage(str(config.DATA_DIR)).free / 1e6
    if libre_mb < MIN_LIBRE_MB:
        estado_svc.marcar_disco_lleno(libre_mb)
        raise HTTPException(
            507, f"Sin espacio en el servidor: quedan {libre_mb:.0f} MB (minimo "
                 f"{MIN_LIBRE_MB:.0f}). No se acepta la imagen para no corromper la base "
                 f"de datos. Libera espacio o revisa la purga automatica.")
    estado_svc.marcar_disco_ok()

    # CUANDO se tomo. Por defecto, ahora: acierta mientras la foto suba al instante. La
    # trampa manda `captured_at` desde el firmware 2.4-buf, que es lo que hace honesta a una
    # foto que estuvo esperando en su tarjeta durante un corte de cobertura.
    cap = datetime.now(timezone.utc)
    cap_explicita = False
    if captured_at:
        try:
            cap = datetime.fromisoformat(captured_at)
            # Todo se guarda en UTC. SQLite no almacena el huso, asi que un `+02:00` que
            # entrara tal cual quedaria dos horas desplazado respecto a las demas capturas.
            cap = (cap.astimezone(timezone.utc) if cap.tzinfo
                   else cap.replace(tzinfo=timezone.utc))
            cap_explicita = True
        except ValueError:
            pass

    # DUPLICADOS: una captura, una fila.
    #
    # El acuse de recibo por USB es indirecto —la respuesta del puente de la Pi—, asi que si el
    # puente sube la foto pero su respuesta se pierde, la trampa la da por NO subida, la guarda
    # en su buffer y acaba mandandola otra vez. Dos filas para una sola captura inflarian el
    # conteo acumulado de la tarjeta, que es justo el numero con el que se decide si tratar.
    #
    # Dos capturas distintas de la misma trampa no pueden compartir instante, asi que
    # (trampa, captured_at) identifica la foto. Solo aplica con hora EXPLICITA: sin ella `cap`
    # es la hora de llegada, y ahi dos fotos legitimas tampoco coincidirian nunca.
    #
    # Se comprueba ANTES de leer y guardar el fichero: un duplicado no debe costar ni una
    # escritura en disco ni una miniatura.
    if cap_explicita:
        ya = session.exec(select(Imagen).where(Imagen.trampa_id == trap.id,
                                               Imagen.capturada == cap)).first()
        if ya:
            # Se responde OK a proposito. La foto ESTA en el servidor, asi que lo correcto es
            # que la trampa la borre de su tarjeta; un error la dejaria reintentando la misma
            # captura para siempre y gastando datos moviles en cada vuelta.
            traps_svc.touch_seen(session, trap)
            response.status_code = 200
            return IngestResponse(image_id=ya.id, trap_id=trap.id, registered=registered,
                                  config=config_out(cfg), duplicada=True,
                                  keep_awake=not cfg.deep_sleep and traps_svc.keep_awake_activo(trap.id))

    # TOPE DE TAMAÑO. No habia ninguno: se acepto una imagen de 8,2 MB sin rechistar, cuando una
    # captura real de la trampa son ~343 KB. Una placa averiada —o cualquiera con la clave de
    # dispositivo— podia llenar el disco a ritmo libre, y el disco lleno tumba la ingesta EN
    # SILENCIO (ver `09_Troubleshooting` G4).
    #
    # El tope es generoso a proposito (20 MB): la app movil sube fotos de telefono, que hoy
    # pesan varios MB, y lo que se quiere cortar es lo absurdo, no lo grande.
    # La corrección de color del OV5640 solo para capturas del ESP32 (clave de dispositivo),
    # NO para las fotos del móvil (su cámara no tiene el tinte magenta).
    from_device = (x_api_key == config.DEVICE_API_KEY)
    try:
        saved = images_svc.save_capture(device_id, data, apply_wb=from_device,
                                        recorte=trap.recorte_tarjeta)
    except ValueError as e:
        # Subida cortada o fichero que no es una imagen. Se contesta 422 —«esto no vale»— y no
        # 500. La diferencia no es de estilo: ante un 500 la placa entiende «el servidor esta
        # mal» y REINTENTA, gastando otros 300 KB del plan en reenviar los mismos bytes rotos.
        raise HTTPException(422, str(e)) from e

    img = Imagen(trampa_id=trap.id, estado="pendiente", capturada=cap, **saved)
    # CONDICIONES DE CAPTURA. Sin esto, cada foto perdia para siempre el contexto que explica
    # su resultado (resolucion, exposicion, escala), que es justamente el eje del analisis del
    # proyecto. Se anota aqui, en la ingesta, porque es el unico momento en que se sabe.
    cond_svc.anotar_imagen(session, img, cfg, trap, config_version)
    session.add(img)
    # Los ficheros ya están escritos, pero en provisional: si el commit falla se tiran, para
    # que no quede en disco una imagen que nadie va a mirar nunca (ver `images.save_capture`).
    try:
        session.commit()
    except Exception:
        session.rollback()
        images_svc.descartar(saved)
        raise
    images_svc.confirmar(saved)
    session.refresh(img)

    # A que tarjeta pertenece. El conteo de una foto es el ACUMULADO de su tarjeta, asi que
    # sin este enlace no se puede expresar por dia de exposicion ni distinguir un cambio de
    # carton de una plaga que se desvanece.
    #
    # Se resuelve DESPUES de guardar la imagen a proposito: al crear la primera tarjeta de una
    # trampa, `actual()` la fecha en su captura mas antigua, y esta foto tiene que contar para
    # esa fecha. Si se resolviera antes, la primera tarjeta quedaria fechada "ahora" aunque la
    # captura viniera con `captured_at` anterior (fotos del movil, importaciones).
    tarj = tarjeta_svc.actual(session, trap.id)   # crea y adopta las sueltas si no habia
    session.refresh(img)
    if img.tarjeta_id != tarj.id:
        img.tarjeta_id = tarj.id
        session.add(img)
        session.commit()
        session.refresh(img)

    traps_svc.touch_seen(session, trap)

    # Lo que ha costado esta foto en datos moviles. El JPEG es casi todo; se suma un poco por
    # la cabecera multipart y la respuesta, que tambien viajan.
    consumo_svc.anotar(session, trap, "ingesta", bytes_=len(data) + 900)

    cambios_svc.marcar("imagen")   # la web se entera sola, sin recargar
    return IngestResponse(image_id=img.id, trap_id=trap.id,
                          registered=registered, config=config_out(cfg),
                          keep_awake=not cfg.deep_sleep and traps_svc.keep_awake_activo(trap.id))
