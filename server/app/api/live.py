"""Endpoints de la vista en vivo «al revés»: la trampa empuja, el servidor reparte.

Los usa la PCB (autenticación por `X-API-Key`), no el navegador. El porqué del diseño está
en `services/live.py`. La web y la app siguen usando `/traps/{id}/snapshot` y `/stream` de
`stream.py`, que ahora sirven estos fotogramas cuando la trampa no es alcanzable.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from ..core.db import get_session, sesion_corta
from ..core.deps import current_user, is_farmer, require_admin
from ..core.security import require_device
from ..models.models import ConfigCamara, Trampa, LecturaHardware
from ..services import consumo as consumo_svc
from ..services import bateria as bat_svc
from ..services import live as live_svc
from ..services import sensores as sensores_svc
from ..services import traps as traps_svc

router = APIRouter(prefix="/api/v1", tags=["vista en vivo"])

# Cada cuanto se refresca en la BASE el «visto por ultima vez» de una trampa que esta
# emitiendo video. Cinco segundos: la trampa se da por offline a los minutos, asi que esto
# sobra de largo, y evita ~7 escrituras por segundo que agotaban el pool de conexiones.
VISTO_CADA_S = 5.0
# Un `device_id` no cambia de trampa: se cachea para no consultar la base en cada fotograma.
# Se revalida en cada refresco, asi que una trampa borrada deja de colarse en 5 s.
_ID_DE_DEVICE: dict[str, int] = {}
_VISTO_EN_BD: dict[int, float] = {}
# Bytes y conexiones que todavia no se han volcado. Escribir menos veces no puede significar
# contar de menos: se acumulan aqui y se vuelcan enteros en el siguiente refresco.
_PENDIENTE_CONSUMO: dict[str, list[int]] = {}


def _trap_mia_o_404(session: Session, request: Request, trap_id: int) -> Trampa:
    """La trampa, comprobando ADEMAS que quien pregunta puede tocarla.

    `live-ping` y `foto` comprobaban que la trampa EXISTE, no de quien es. Con eso, un
    agricultor podia mandar a cualquier trampa del sistema --tambien las de otro-- que se
    pusiera a transmitir video o que disparara una foto a maxima resolucion: bateria, datos
    moviles y almacenamiento ajenos, gastados por quien no tiene por que ver esa trampa.
    Hallazgo de Codex en la mentoria del 2026-09-19.

    Se responde 404 y no 403 a proposito, igual que en `stream.py`: a quien no puede verla no
    se le confirma siquiera que existe.
    """
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    user = current_user(request)
    if is_farmer(user) and trap.owner_id != user["id"]:
        raise HTTPException(404, "Trampa no encontrada")
    return trap


def _trap_por_device(session: Session, device_id: str) -> Trampa:
    trap = session.exec(select(Trampa).where(Trampa.device_id == device_id)).first()
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    return trap


def _politica_por_device(device_id: str) -> tuple[int, bool]:
    """Solo id y modo, para el que va a esperar con la sesion cerrada.

    Existe para que `esperar_vivo()` **no tenga un objeto `Trampa` en su ambito**: retiene hasta
    60 s despues de cerrar la sesion, y una fila que sobreviva a ese cierre revienta en cuanto un
    commit previo haya caducado sus atributos. Es el fallo G15, que costo 111 respuestas 500 en
    un dia en `telemetry.py`.
    """
    with sesion_corta() as session:
        trap = _trap_por_device(session, device_id)
        cfgm = session.get(ConfigCamara, trap.id)
        return trap.id, bool(cfgm and cfgm.deep_sleep)


@router.get("/traps/{device_id}/live", dependencies=[Depends(require_device)])
async def esperar_vivo(device_id: str, espera: float = 20.0):
    """Long polling: la trampa pregunta si tiene que mandar vídeo.

    El servidor **retiene** la respuesta hasta `espera` segundos y contesta en cuanto alguien
    abre la vista en vivo. Así la trampa no sondea cada segundo (cada sondeo por HTTPS cuesta
    un handshake TLS) y aun así el vídeo arranca casi al momento.
    """
    # El id, y nada mas. `_id_por_device` deja el objeto `Trampa` dentro de si misma a
    # proposito: aqui abajo se cierra la sesion y se espera un minuto, y cualquier fila que
    # sobreviva a ese cierre es un 500 esperando (ver G15 en `09_Troubleshooting`).
    trap_id, campo = _politica_por_device(device_id)
    if campo:
        return {"en_vivo": False, "trap_id": trap_id}

    # Hasta 60 s. La respuesta de un long polling no llega antes por preguntar mas veces
    # —el servidor la retiene y contesta EN CUANTO alguien mira—, asi que alargar la espera
    # baja las conexiones a la mitad sin tocar la latencia. Y por 4G cada conexion es un
    # saludo TLS de ~4 KB.
    espera = max(0.0, min(espera, 60.0))

    # NO HAY SESION QUE SOLTAR: `_id_por_device` abrio la suya y la cerro al volver.
    #
    # Este endpoint es el TERCER long polling del servidor, y se quedo sin el arreglo dos
    # dias despues de que lo recibieran `telemetry.py` y `stream.py`: retenia hasta 60 s
    # con una conexion del pool agarrada, y el pool son 15.
    #
    # Hoy lo usa solo el puente serie (155 peticiones/hora medidas), asi que gastaba una
    # sola conexion y no se notaba. Pero «no se nota» es exactamente lo que se decia de la
    # telemetria con una trampa: el ensayo de 20 enseño que a partir de la nº 16 no
    # responde NADA, ni `/health` ni la web, porque todo sale del mismo pool.

    en_vivo = await live_svc.esperar_peticion(trap_id, espera)
    return {"en_vivo": en_vivo, "trap_id": trap_id}


@router.post("/traps/{trap_id}/sms-prueba", dependencies=[Depends(require_admin)])
def sms_prueba(trap_id: int, session: Session = Depends(get_session)):
    """Pide a la trampa que mande un SMS de aviso de prueba al numero configurado.

    Un canal de avisos que solo se ejercita el dia de la urgencia no es un canal: es una
    suposicion. Esto permite comprobarlo cuando uno quiera.
    """
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    live_svc.pedir_sms(trap_id)
    return {"ok": True, "detalle": "la trampa lo enviara en su proxima telemetria"}


@router.post("/traps/{trap_id}/live-ping")
def live_ping(trap_id: int, request: Request, session: Session = Depends(get_session)):
    """Latido del que ESTA MIRANDO (web o app), cada pocos segundos mientras tenga la vista
    abierta.

    Es lo que sostiene el video. Antes lo renovaba el propio servidor desde el generador del
    MJPEG, y una conexion huerfana lo mantenia vivo para siempre: la trampa transmitia sin
    nadie delante y se comia la bateria. Con el latido en el cliente, si el navegador se
    cierra deja de llegar y la trampa para sola en `LIVE_S` segundos.
    """
    trap = _trap_mia_o_404(session, request, trap_id)
    cfgm = traps_svc.ensure_config(session, trap)
    if cfgm.deep_sleep:
        live_svc.parar_vivo(trap.id)
        raise HTTPException(409, "Modo invernadero: vídeo desactivado para permitir el corte")
    live_svc.pedir_vivo(trap.id)
    # `edad_frame` viaja en el latido para que el que mira pueda detectar que su <img> se ha
    # quedado CONGELADO. Un MJPEG que termina no dispara `error` en el navegador: la imagen
    # se queda con el ultimo fotograma pintado y ahi se queda para siempre, con pinta de
    # estar en vivo. Con este dato, el navegador sabe distinguir «no llega nada porque la
    # trampa no emite» de «la trampa emite y yo no lo estoy recibiendo», que es cuando hay
    # que reconectar. Es `None` si no ha llegado ningun fotograma todavia.
    # `via` dice por donde va el video del stream abierto: 'empuje' (el servidor guarda cada
    # fotograma, y entonces `edad_frame` mide la frescura de verdad) o 'directo' (relay por IP
    # en la red local, donde los bytes no pasan por el servidor y `edad_frame` no mide nada).
    # Sin esto el navegador no puede distinguirlas --un <img> no lee cabeceras-- y el visor
    # se equivocaba en las dos direcciones. `None` si no hay stream reciente.
    return {"ok": True, "segundos": live_svc.LIVE_S,
            "edad_frame": live_svc.edad_frame(trap.id),
            "via": live_svc.via_actual(trap.id)}


@router.post("/traps/{trap_id}/sensores")
def sensores_ahora(trap_id: int, request: Request, session: Session = Depends(get_session)):
    """Tension y corriente de AHORA, y lo que se estima de la celda. Es tambien el latido.

    El panel lo pregunta cada pocos segundos mientras esta abierto, y cada pregunta renueva el
    latido de sensores: la trampa informa a su suelo sin encender la camara. Por eso funciona
    igual sin camara, que es cuando el panel de video se rinde y dejaba de refrescar.

    También por 4G desde el 15/10/2026: Antonio comprobó que la trampa no llega al límite del
    plan y pidió reducir la latencia de tensión y corriente. Antes, por 4G se esperaba al
    informe normal de la trampa (cada 2 min en banco) y el panel enseñaba datos viejos.
    """
    trap = _trap_mia_o_404(session, request, trap_id)
    rapido = True
    sensores_svc.pedir(trap.id)
    carga = bat_svc.estado(trap.id, trap.crate)
    # El veredicto entero, no un «¿carga?»: la autonomia solo sale con descarga comprobada.
    d = sensores_svc.ahora(trap.id, carga.get("estado"))
    muestra = session.exec(select(LecturaHardware).where(LecturaHardware.trampa_id == trap.id)
                          .order_by(LecturaHardware.id.desc()).limit(1)).first()
    if muestra:
        edad_bd = max(0, time.time() - muestra.instante.replace(
            tzinfo=timezone.utc).timestamp())
        d.update(rtc_hora=muestra.rtc_hora, edad_rtc_s=edad_bd)
        # Tras reiniciar la API, el histórico sirve de respaldo hasta la siguiente
        # telemetría. Nunca tapa una lectura nueva del INA219.
        if d["edad_hw_s"] is None or d["edad_hw_s"] > sensores_svc.FRESCO_S:
            d.update(ina219_v=muestra.tension_v, ina219_ma=muestra.corriente_ma,
                     edad_hw_s=edad_bd)
    d.update({"ok": True, "rapido": rapido, "segundos": sensores_svc.SENSORES_S,
              "carga": carga})
    return d


@router.post("/traps/{trap_id}/foto")
def pedir_foto(trap_id: int, request: Request, session: Session = Depends(get_session)):
    """Pide una captura a MAXIMA resolucion ahora mismo, desde la vista en vivo.

    El video va a SVGA para que fluya; esto pide la foto buena, con la resolucion y calidad
    configuradas, y acaba en la galeria como cualquier otra captura. La trampa lo recoge en
    su siguiente telemetria (~10 s).
    """
    trap = _trap_mia_o_404(session, request, trap_id)
    live_svc.pedir_foto(trap.id)
    return {"ok": True, "segundos": int(live_svc.FOTO_VALIDA_S)}


@router.post("/traps/{trap_id}/reiniciar", dependencies=[Depends(require_admin)])
def pedir_reinicio(trap_id: int, session: Session = Depends(get_session)):
    """Reinicia la placa **a distancia**, sin ir con un cable.

    Hasta ahora reiniciar una trampa exigia tenerla delante. Con la trampa colgada en un
    invernadero eso es un viaje, y una placa atascada deja de dar datos hasta que alguien va.

    La orden viaja en la respuesta de la telemetria —el mismo camino que «tomar foto»— y la
    placa la obedece en cuanto la lee, asi que tarda como mucho una ventana de retencion
    (~2 min por 4G). **Se consume al leerla** y caduca en 5 minutos: una orden olvidada no
    puede convertirse en un bucle de reinicios.
    """
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    live_svc.pedir_reinicio(trap.id)
    return {"ok": True, "segundos": int(live_svc.REINICIO_VALIDO_S),
            "aviso": "la placa reiniciara en cuanto lea su proxima telemetria"}


@router.post("/traps/{trap_id}/reiniciar/cancelar", dependencies=[Depends(require_admin)])
def cancelar_reinicio(trap_id: int):
    """Retira una orden de reinicio que aun no ha recogido la trampa."""
    live_svc.cancelar_reinicio(trap_id)
    return {"ok": True}


@router.post("/traps/{trap_id}/apagar", dependencies=[Depends(require_admin)])
def pedir_apagado(trap_id: int, session: Session = Depends(get_session)):
    """Apaga la placa **a distancia**, de forma ordenada (`poweroff`).

    Esta orden no programa el RTC. El overlay gpio-poweroff lleva GPIO12 alto al final
    del cierre y Pololu corta VOUT. Con VIN conectado, A-GND la vuelve a encender.
    Mismo camino y mismas garantias que `reiniciar`: se
    consume al leerla, caduca en 5 minutos y anula un reinicio pendiente.
    """
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    live_svc.pedir_apagado(trap.id)
    return {"ok": True, "segundos": int(live_svc.APAGADO_VALIDO_S),
            "aviso": "Linux se cerrara ordenadamente y el Pololu cortara VOUT; "
                     "para encender de nuevo, conecta VIN y pulsa A-GND"}


@router.post("/traps/{trap_id}/apagar/cancelar", dependencies=[Depends(require_admin)])
def cancelar_apagado(trap_id: int):
    """Retira una orden de apagado que aun no ha recogido la trampa."""
    live_svc.cancelar_apagado(trap_id)
    return {"ok": True}


@router.post("/traps/{trap_id}/reposo", dependencies=[Depends(require_admin)])
def pedir_reposo(trap_id: int, minutos: int, session: Session = Depends(get_session)):
    """Pone la Zero en pausa durante 2 minutos..24 horas, con vuelta automatica."""
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    if isinstance(minutos, bool) or not 2 <= minutos <= 1440:
        raise HTTPException(422, "minutos debe estar entre 2 y 1440")
    ident = live_svc.pedir_reposo(trap.id, minutos * 60)
    return {"ok": True, "reposo": {"id": ident, "seg": minutos * 60},
            "aviso": "se marcara en reposo cuando la trampa acuse la orden"}


@router.post("/traps/{trap_id}/apagar/despertar", dependencies=[Depends(require_admin)])
def pedir_apagado_con_despertar(trap_id: int, minutos: int,
                               session: Session = Depends(get_session)):
    """Pide un corte real y alarma RTC; la Zero rechaza la orden si no está validada."""
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    if not trap.device_id.startswith("zero-"):
        raise HTTPException(409, "El encendido por RTC solo está disponible en la Zero")
    if not 3 <= minutos <= 1440:
        raise HTTPException(422, "minutos debe estar entre 3 y 1440")
    ident = live_svc.pedir_reposo(trap.id, minutos * 60, exigir_apagado=True)
    return {"ok": True, "reposo": {"id": ident, "seg": minutos * 60,
                                    "exigir_apagado": True},
            "aviso": "la trampa confirmara o rechazara el corte real en su siguiente telemetria"}


@router.post("/traps/{trap_id}/apagar/despertar-en", dependencies=[Depends(require_admin)])
def pedir_apagado_con_fecha(trap_id: int, cuando: datetime,
                           session: Session = Depends(get_session)):
    """Corta alimentación y programa un encendido en una fecha y hora absolutas."""
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    if not trap.device_id.startswith("zero-"):
        raise HTTPException(409, "El encendido por RTC solo está disponible en la Zero")
    if cuando.tzinfo is None or cuando.utcoffset() is None:
        raise HTTPException(422, "La fecha debe incluir zona horaria")
    target = int(cuando.timestamp())
    seg = target - int(time.time())
    if not 180 <= seg < live_svc.REPOSO_ABS_MAX_S:
        raise HTTPException(422, "El encendido debe estar entre 3 minutos y 27 días")
    ident = live_svc.pedir_reposo(trap.id, seg, exigir_apagado=True, target=target)
    return {"ok": True, "reposo": {"id": ident, "target": target,
                                      "exigir_apagado": True}}


@router.post("/traps/{device_id}/frame", dependencies=[Depends(require_device)])
async def subir_frame(device_id: str, request: Request):
    """La trampa sube un fotograma JPEG en crudo (body = bytes, sin multipart).

    No se guarda en disco ni en la base de datos: es vídeo, vive en memoria y se pisa con el
    siguiente. La respuesta dice si hay que seguir mandando.

    AQUÍ NO HAY `Depends(get_session)`, Y ESO ES EL ARREGLO. Con la sesión inyectada, la
    conexión se quedaba agarrada **toda la petición**, y esta petición ESPERA: consultaba la
    trampa y luego hacía `await request.body()`. Por 4G, o con una subida que se corta a
    medias, esa espera dura lo que dure la red. Son 15 conexiones en el pool: bastan quince
    subidas lentas a la vez para dejar sin servicio la API entera --la web, la telemetría que
    le dice a la trampa que emita, y el propio vídeo--. Es el mismo fallo que mató la API en el
    ensayo de 20 trampas (gotcha G15) y que ya se corrigió en el relay MJPEG y en el long
    polling; este camino se quedó sin mirar. Hallazgo de Codex en la mentoría del 2026-09-19.

    El cuerpo se lee ANTES de tocar la base, y la base se toca dentro de un `sesion_corta()`
    que se cierra solo. Del bloque no sale ninguna fila: solo enteros.
    """
    data = await request.body()
    if not data:
        raise HTTPException(422, "Fotograma vacío")
    # Un fotograma es contacto. Sin esto, una trampa que lleva dos minutos emitiendo vídeo
    # aparecía como **OFFLINE** en su propio panel, al lado de la imagen en directo: durante la
    # tanda de vídeo no se manda telemetría, y `ultimo_visto` se quedaba viejo.
    # La placa usa keep-alive dentro de una tanda, así que la conexión se cuenta UNA vez por
    # tanda y el resto de fotogramas solo suman carga útil. Se mira ANTES de guardar el
    # fotograma: después, la edad siempre es 0 y la conexión no se contaba nunca.
    # NI UNA ESCRITURA POR FOTOGRAMA. ESTO ES LO QUE TUMBO LA API.
    #
    # Aqui se llamaba a `touch_seen` en CADA fotograma, y `touch_seen` hace `commit`. A los
    # 6,6 fps que da la Zero son ~7 transacciones de escritura por segundo contra una SQLite
    # sobre tarjeta SD. Se encolan, cada una retiene su conexion mientras espera, y el pool
    # --5 + 10 de desbordamiento-- se agota. Entonces **la API entera deja de responder**: la
    # web, la telemetria y el propio video. Paso el 2026-09-19 a las 19:19, con Antonio
    # mirando el directo y pidiendo una foto:
    #
    #     sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
    #     connection timed out, timeout 30.00
    #
    # El servicio no llego a caerse --nunca se reinicio-- pero desde fuera es identico a una
    # caida, que es lo peor de este fallo: no deja ni rastro de proceso muerto.
    #
    # Ahora el camino caliente NO TOCA LA BASE. El identificador se cachea (un `device_id` no
    # cambia de trampa) y solo cada `VISTO_CADA_S` se abre una sesion para refrescar
    # `ultimo_visto` y volcar lo consumido. Ese refresco sigue siendo de sobra: la trampa se da
    # por offline a los minutos, no a los segundos.
    ahora = time.time()
    trap_id = _ID_DE_DEVICE.get(device_id)
    # Lo consumido se acumula en memoria para no perder ni un byte por escribir menos veces.
    pend = _PENDIENTE_CONSUMO.setdefault(device_id, [0, 0])
    pend[0] += len(data) + 200

    if trap_id is None or ahora - _VISTO_EN_BD.get(trap_id, 0.0) >= VISTO_CADA_S:
        with sesion_corta() as session:
            trap = _trap_por_device(session, device_id)      # 404 si ya no existe
            trap_id = trap.id
            _ID_DE_DEVICE[device_id] = trap_id
            edad = live_svc.edad_frame(trap_id)
            if edad is None or edad > 10:
                pend[1] += 1                                  # tanda nueva: una conexion mas
            traps_svc.touch_seen(session, trap)
            consumo_svc.anotar(session, trap, "vivo", bytes_=pend[0], conexiones=pend[1])
        _PENDIENTE_CONSUMO[device_id] = [0, 0]
        _VISTO_EN_BD[trap_id] = ahora

    live_svc.guardar_frame(trap_id, data)
    return {"en_vivo": live_svc.quiere_vivo(trap_id), "bytes": len(data)}
