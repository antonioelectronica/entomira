"""Vista en vivo que funciona AUNQUE la trampa esté en otra red.

El diseño original era **de tiro**: el servidor abría una conexión HACIA la trampa
(`http://<ip>:81/snapshot`). Eso solo vale dentro de la misma red local; en cuanto la trampa
se une a otra (hotspot del móvil, otro invernadero, 4G) queda detrás de un NAT y es
inalcanzable desde fuera. No es un fallo de configuración: no hay a dónde llamar.

Aquí se **invierte el sentido**: la TRAMPA empuja los fotogramas al servidor y el servidor
los reparte a la web y a la app. Como todas las conexiones las abre la trampa, funciona
desde cualquier red con salida a internet (por el funnel de Tailscale).

Cómo se coordinan sin gastar batería ni radio:

- Cuando alguien abre la vista en vivo, el servidor anota «esta trampa en vivo N segundos».
- La trampa pregunta con **long polling** (`GET /traps/<id>/live`): el servidor **retiene** la
  petición hasta 20 s y contesta en cuanto alguien pide vídeo. Así el arranque es casi
  inmediato sin sondear cada segundo (cada sondeo por HTTPS cuesta un handshake TLS).
- Mientras dura, la trampa manda fotogramas y cada respuesta le dice si seguir.

Todo vive en memoria a propósito: si el servidor reinicia, nadie se queda «en vivo» para
siempre gastando batería.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
import itertools
import logging
import threading
import time

log = logging.getLogger(__name__)

# Cuánto se mantiene «en vivo» una trampa desde la última petición de un cliente.
LIVE_S = 25
# Edad máxima de un fotograma para darlo por bueno como «foto en directo».
FRESCO_S = 15

_frames: dict[int, tuple[bytes, float]] = {}
_pedido: dict[int, float] = {}


def pedir_vivo(trap_id: int, segundos: int = LIVE_S) -> None:
    """Alguien está mirando: renueva la ventana de vídeo de esa trampa."""
    _pedido[trap_id] = time.time() + segundos


def quiere_vivo(trap_id: int) -> bool:
    hasta = _pedido.get(trap_id, 0)
    if hasta and hasta < time.time():
        _pedido.pop(trap_id, None)
        return False
    return bool(hasta)


def parar_vivo(trap_id: int) -> None:
    _pedido.pop(trap_id, None)


def guardar_frame(trap_id: int, jpeg: bytes) -> None:
    _frames[trap_id] = (jpeg, time.time())


def ultimo_frame(trap_id: int, max_edad: float = FRESCO_S) -> bytes | None:
    dato = _frames.get(trap_id)
    if not dato:
        return None
    jpeg, ts = dato
    return jpeg if (time.time() - ts) <= max_edad else None


def edad_frame(trap_id: int) -> float | None:
    dato = _frames.get(trap_id)
    return (time.time() - dato[1]) if dato else None


# ------------------------------------------------------------------ por que via va el video
# El video llega por DOS caminos y desde el navegador no se distinguen, porque un <img> no lee
# cabeceras. Y la diferencia importa: por la via de EMPUJE el servidor guarda cada fotograma,
# asi que `edad_frame` mide la frescura de verdad; por el relay DIRECTO por IP los bytes van de
# la trampa al navegador sin pasar por `_frames`, y `edad_frame` se queda viejo o en None
# aunque el video vaya perfecto.
#
# Sin este dato el visor se equivoca en las dos direcciones, y las dos se midieron el
# 2026-09-19: decia «Sin senal reciente · hace 2 min» con la imagen cambiando delante. El
# navegador no puede adivinarlo; el servidor lo SABE, porque es el quien elige la via. Asi que
# lo dice. Idea de Codex al revisar la medicion.
VIA_VALIDA_S = 120.0
# «Tiene 4G listo» es ESTADO VIVO, no historia: por eso vive aqui en memoria y no como una
# columna mas de `trampa`. Una columna obligaria a migrar la base --checkpoint humano-- para
# guardar un dato que caduca en minutos y que no se consulta hacia atras jamas. Si el servidor
# se reinicia se pierde, y no pasa nada: la siguiente telemetria lo vuelve a traer.
# 15 MINUTOS, Y NO 3. Con 3 el dato caducaba ENTRE UNA TELEMETRIA Y LA SIGUIENTE --la trampa
# reporta cada 5-10 min-- y el panel enseñaba «no lo se» casi siempre, con el 4G enganchado y
# funcionando. Medido el 2026-09-20. La caducidad tiene que ir con el ritmo de quien manda el
# dato, no con lo que a uno le parezca «reciente»; y «tiene 4G enganchado» no cambia cada tres
# minutos. Si la trampa lleva mas de 15 min callada, `en_linea` ya lo dice por su cuenta.
LTE_VALIDO_S = 900.0
REFRESCO_VIA_S = 5.0
_via: dict[int, tuple[str, float]] = {}


_lte_listo: dict[int, tuple[float, bool, str | None]] = {}


def marcar_lte(trap_id: int, listo: bool | None, ifaz: str | None) -> None:
    """Anota si la trampa tiene el 4G enganchado, aunque ahora mismo no lo use."""
    if listo is None:
        return
    _lte_listo[trap_id] = (time.time(), bool(listo), ifaz)


def lte_listo(trap_id: int) -> tuple[bool | None, str | None]:
    """(hay 4G listo, interfaz por la que sale el trafico). None si no hay dato reciente."""
    d = _lte_listo.get(trap_id)
    if not d or time.time() - d[0] > LTE_VALIDO_S:
        return None, None
    return d[1], d[2]


def marcar_via(trap_id: int, via: str) -> None:
    """La via del stream vivo: 'directo' o 'empuje'.

    Se llama al abrir Y mientras corren los bytes, porque marcarla solo al abrir la hacia
    caducar a los 120 s con el video yendo perfectamente. Como el relay la llama por cada
    trozo, se escribe como mucho una vez cada `REFRESCO_VIA_S`: no hay motivo para tocar el
    reloj veinte veces por fotograma.
    """
    dato = _via.get(trap_id)
    ahora = time.time()
    if dato and dato[0] == via and ahora - dato[1] < REFRESCO_VIA_S:
        return
    _via[trap_id] = (via, ahora)


def via_actual(trap_id: int) -> str | None:
    """La via del ultimo stream, si es reciente. Caduca: una via de hace media hora no dice
    nada del video de ahora, y contestar con ella seria peor que contestar `None`."""
    dato = _via.get(trap_id)
    if not dato:
        return None
    via, ts = dato
    if time.time() - ts > VIA_VALIDA_S:
        _via.pop(trap_id, None)
        return None
    return via


async def esperar_peticion(trap_id: int, espera_s: float) -> bool:
    """Long polling del lado de la trampa: espera a que alguien pida vídeo.

    Devuelve en cuanto hay petición (arranque casi inmediato) o al agotar la espera. Se
    sondea cada 250 ms sobre un diccionario en memoria: no toca la base de datos.

    **También corta la espera si hay una orden de reinicio.** Sin esto, una orden dada mientras
    la petición está retenida no se entregaba hasta que la retención se agotaba: por 4G son
    **120 s** de espera antes de que la placa se entere, más los ~40 s de arranque. Casi tres
    minutos en los que el usuario ha pulsado un botón y no pasa nada visible — y concluye, con
    razón, que no funciona. Lo que se entrega en esa respuesta lo decide `telemetry`; aquí solo
    se deja de esperar.
    """
    # EL PANEL DE SENSORES TAMBIEN CORTA LA ESPERA, pero sin pedir video: con alguien mirando
    # la bateria y el consumo, la trampa informa a su suelo (2 s) y la camara ni se entera. El
    # valor devuelto sigue siendo solo `quiere_vivo`, que es lo que enciende el video.
    from . import sensores as sensores_svc
    fin = time.time() + espera_s
    while time.time() < fin:
        # Un log pedido desde el panel también suelta la espera: en banco sale en segundos.
        if (quiere_vivo(trap_id) or sensores_svc.quiere(trap_id) or hay_log_pedido(trap_id)
                or hay_reinicio_pedido(trap_id) or hay_apagado_pedido(trap_id)
                or orden_reposo(trap_id) is not None):
            return quiere_vivo(trap_id)
        await asyncio.sleep(0.25)
    return quiere_vivo(trap_id)


async def esperar_frame(trap_id: int, desde_ts: float, espera_s: float = 5.0):
    """Espera a que llegue un fotograma más nuevo que `desde_ts` (para el MJPEG)."""
    fin = time.time() + espera_s
    while time.time() < fin:
        dato = _frames.get(trap_id)
        if dato and dato[1] > desde_ts:
            return dato
        await asyncio.sleep(0.1)
    return None


# ---------------------------------------------------------------- foto bajo demanda
# El vídeo va a SVGA para que fluya. Cuando alguien ve algo que quiere guardar bien, pide
# una captura a resolución completa: se anota aquí y la trampa la recoge en su siguiente
# telemetría (cada 10 s), así que la foto sale casi al momento en vez de esperar al
# siguiente ciclo de captura, que puede ser de 10 minutos.
_foto_pedida: dict[int, float] = {}
FOTO_VALIDA_S = 120.0


def pedir_foto(trap_id: int) -> None:
    _foto_pedida[trap_id] = time.time()


# ------------------------------------------------------------- reinicio de la placa a distancia
# Mismo mecanismo que la foto: se anota, viaja en la respuesta de la telemetria y **se consume
# al leerlo**. Que se consuma es lo importante: si la orden se quedara puesta, la trampa
# reiniciaria, volveria a leerla al arrancar y entraria en un bucle de reinicios del que solo
# se sale con un cable — justo lo contrario de para lo que existe esto.
#
# Y caduca: una orden dada hace media hora a una trampa sin cobertura no debe dispararse cuando
# vuelva la red, porque para entonces ya nadie la esta esperando.
_reinicio_pedido: dict[int, float] = {}
REINICIO_VALIDO_S = 300.0

# Secuencia comun a las tres ordenes excluyentes. Los ids solo se exponen para reposo, pero
# asignarlos tambien a apagar/reiniciar permite decidir sin ambiguedad cual fue la ultima.
_ids_orden = itertools.count(time.time_ns())
_ultima_orden: dict[int, tuple[int, str]] = {}
_orden_lock = threading.RLock()


def _serializar_orden(fn):
    @wraps(fn)
    def dentro(*args, **kwargs):
        with _orden_lock:
            return fn(*args, **kwargs)
    return dentro


def _nueva_orden(trap_id: int, tipo: str) -> int:
    ident = next(_ids_orden)
    _ultima_orden[trap_id] = (ident, tipo)
    return ident


@_serializar_orden
def pedir_reinicio(trap_id: int) -> None:
    _nueva_orden(trap_id, "reinicia")
    _reinicio_pedido[trap_id] = time.time()
    _apagado_pedido.pop(trap_id, None)       # la ultima orden manda: ver `pedir_apagado`
    _reposo_pedido.pop(trap_id, None)


@_serializar_orden
def hay_reinicio_pedido(trap_id: int, consumir: bool = False) -> bool:
    t = _reinicio_pedido.get(trap_id)
    if not t:
        return False
    if time.time() - t > REINICIO_VALIDO_S:
        _reinicio_pedido.pop(trap_id, None)
        return False
    if consumir:
        _reinicio_pedido.pop(trap_id, None)
    return True


@_serializar_orden
def cancelar_reinicio(trap_id: int) -> None:
    _reinicio_pedido.pop(trap_id, None)


# ------------------------------------------------------------- apagado de la placa a distancia
# Mismo camino que el reinicio (se anota, viaja en la telemetria, SE CONSUME al leerlo y caduca
# a los 5 min), con una diferencia que lo cambia todo: **no programa el RTC**. Una Zero
# apagada no escucha a nadie; para encenderla manualmente hay que pulsar A-GND en el Pololu.
# Existe para eso mismo: apagarla limpia antes de tocar el hardware (la faja de la camara, el
# INA), en vez de arrancarle la bateria con la tarjeta escribiendo.
#
# LA ULTIMA ORDEN MANDA. Apagar anula un reinicio pendiente y reiniciar anula un apagado: si
# conviviesen, la placa recibiria las dos en la misma respuesta y el resultado dependeria del
# orden en que el cliente las mire.
_apagado_pedido: dict[int, float] = {}
APAGADO_VALIDO_S = 300.0


@_serializar_orden
def pedir_apagado(trap_id: int) -> None:
    _nueva_orden(trap_id, "apaga")
    _apagado_pedido[trap_id] = time.time()
    _reinicio_pedido.pop(trap_id, None)
    _reposo_pedido.pop(trap_id, None)


@_serializar_orden
def hay_apagado_pedido(trap_id: int, consumir: bool = False) -> bool:
    t = _apagado_pedido.get(trap_id)
    if not t:
        return False
    if time.time() - t > APAGADO_VALIDO_S:
        _apagado_pedido.pop(trap_id, None)
        return False
    if consumir:
        _apagado_pedido.pop(trap_id, None)
        # Entregada: desde ahora la trampa se está apagando. Sin esto el panel la seguía dando
        # «En línea» los ~25 min de margen, aunque el propio servidor le había ordenado apagarse.
        _apagada_desde[trap_id] = time.time()
        _anunciar_estado()
    return True


@_serializar_orden
def cancelar_apagado(trap_id: int) -> None:
    _apagado_pedido.pop(trap_id, None)


# Trampas a las que se les ENTREGÓ la orden de apagar y no han vuelto a hablar. En memoria, como
# las órdenes. LIMITACIÓN ACEPTADA: si el servidor se reinicia se pierde, y la trampa vuelve a
# salir «En línea» hasta que venza su margen normal (~25 min); guardarlo pediría una columna.
# Una telemetría posterior la borra (la encendieron, o el apagado no llegó a ejecutarse), así que
# nunca se queda «apagada» una trampa que está hablando; salvo las que lleguen en los primeros
# `REHABLA_S`, que pueden ser un reintento solapado con la respuesta que llevaba la orden (Codex).
_apagada_desde: dict[int, float] = {}
REHABLA_S = 10.0


def _anunciar_estado() -> None:
    # Las pantallas abiertas se repintan con «bateria» (ver `services/en_linea.py`); sin esto,
    # «Apagada al momento» solo lo vería quien recargase.
    from . import cambios as cambios_svc
    cambios_svc.marcar("bateria")


def apagada_desde(trap_id: int) -> float | None:
    """Epoch en que se le entregó la orden de apagar, o None si no está apagada por orden."""
    return _apagada_desde.get(trap_id)


def limpiar_apagada(trap_id: int) -> None:
    """Ha vuelto a hablar: ya no está apagada (salvo un reintento de los primeros segundos)."""
    desde = _apagada_desde.get(trap_id)
    if desde is None or time.time() - desde < REHABLA_S:
        return
    _apagada_desde.pop(trap_id, None)
    _anunciar_estado()


# ------------------------------------------------------------- reposo a distancia
# La orden se REPITE hasta recibir acuse. Entregar una respuesta no demuestra que la Zero la
# haya recibido: la conexion puede romperse despues del endpoint y antes de llegarle un byte.
REPOSO_MIN_S = 120
REPOSO_MAX_S = 86400
REPOSO_ABS_MAX_S = 27 * 86400
REPOSO_ORDEN_VALIDA_S = 300.0
REPOSO_MARGEN_S = 120.0
_reposo_pedido: dict[int, dict] = {}
# Deliberadamente EN MEMORIA. Decision aceptada: si el servidor reinicia en mitad del reposo,
# puede aparecer un falso aviso de desconexion/sin fotos hasta que la trampa vuelva a hablar.
_reposo_activo: dict[int, dict] = {}
_reposo_cerrado: dict[int, int] = {}
_reposo_error: dict[int, str] = {}


@_serializar_orden
def pedir_reposo(trap_id: int, seg: int, *, exigir_apagado: bool = False,
                 target: int | None = None) -> int:
    if isinstance(seg, bool) or not REPOSO_MIN_S <= int(seg) <= (
            REPOSO_ABS_MAX_S if target is not None else REPOSO_MAX_S):
        raise ValueError("duración de reposo fuera de rango")
    if target is not None and (type(target) is not int or not exigir_apagado
                               or not 120 <= target - time.time() < REPOSO_ABS_MAX_S):
        raise ValueError("fecha de encendido fuera de rango")
    ident = _nueva_orden(trap_id, "reposo")
    _reposo_pedido[trap_id] = {"id": ident, "seg": int(seg), "pedido_en": time.time(),
                              "exigir_apagado": exigir_apagado, "target": target}
    _reposo_error.pop(trap_id, None)
    _reinicio_pedido.pop(trap_id, None)
    _apagado_pedido.pop(trap_id, None)
    return ident


@_serializar_orden
def orden_reposo(trap_id: int) -> dict | None:
    """Orden pendiente. No se consume al entregarla: solo al acusarla."""
    orden = _reposo_pedido.get(trap_id)
    if not orden:
        return None
    if time.time() - orden["pedido_en"] > REPOSO_ORDEN_VALIDA_S:
        _reposo_pedido.pop(trap_id, None)
        return None
    if _ultima_orden.get(trap_id) != (orden["id"], "reposo"):
        _reposo_pedido.pop(trap_id, None)
        return None
    respuesta = {"id": orden["id"], "seg": orden["seg"]}
    if orden["target"] is not None:
        respuesta["target"] = orden["target"]
    if orden["exigir_apagado"]:
        respuesta["exigir_apagado"] = True
    return respuesta


def _epoch_iso(valor) -> float | None:
    if not isinstance(valor, str):
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).timestamp()


@_serializar_orden
def anotar_reposo(trap_id: int, ack) -> bool:
    """Procesa el acuse (o una telemetria normal) y devuelve si sigue en reposo."""
    ahora = time.time()
    activo = _reposo_activo.get(trap_id)
    ack_id = ack.get("id") if isinstance(ack, dict) else None
    pendiente = _reposo_pedido.get(trap_id)
    if (isinstance(ack, dict) and ack.get('error') and pendiente
            and ack_id == pendiente['id']
            and _ultima_orden.get(trap_id) == (ack_id, 'reposo')):
        _reposo_error[trap_id] = str(ack['error'])[:160]
        _reposo_pedido.pop(trap_id, None)
        _reposo_cerrado[trap_id] = ack_id
        _anunciar_estado()
        return False
    if isinstance(ack, dict) and ack.get('error') and activo and ack_id == activo['id']:
        _reposo_error[trap_id] = str(ack['error'])[:160]
        _reposo_activo.pop(trap_id, None)
        _reposo_cerrado[trap_id] = ack_id
        _anunciar_estado()
        return False
    hasta = _epoch_iso(ack.get("hasta")) if isinstance(ack, dict) else None
    orden = _reposo_pedido.get(trap_id)

    # Solo la orden MAS RECIENTE puede abrir el estado. Un acuse viejo que llega tarde no
    # resucita un reposo cerrado ni pisa una segunda orden.
    if (orden and isinstance(ack_id, int) and not isinstance(ack_id, bool)
            and ack_id == orden["id"]
            and _ultima_orden.get(trap_id) == (ack_id, "reposo") and hasta is not None
            and (abs(hasta - orden["target"]) <= REPOSO_MARGEN_S
                 if orden["target"] is not None else
                 ahora - REPOSO_MARGEN_S <= hasta <= ahora + orden["seg"] + REPOSO_MARGEN_S)):
        _reposo_pedido.pop(trap_id, None)
        _reposo_activo[trap_id] = {"id": ack_id, "hasta": hasta, "ack_en": ahora,
                                  "modo": ack.get('modo', 'wifi')}
        _anunciar_estado()
        return True

    if activo is None:
        return False
    if ahora > activo["hasta"] + REPOSO_MARGEN_S:
        _reposo_activo.pop(trap_id, None)
        _reposo_cerrado[trap_id] = activo["id"]
        _anunciar_estado()
        return False
    if ack_id == activo["id"]:
        return True
    # Una peticion que ya estaba en vuelo al mandar el acuse puede terminar despues.
    if ahora - activo["ack_en"] < REHABLA_S:
        return True
    _reposo_activo.pop(trap_id, None)
    _reposo_cerrado[trap_id] = activo["id"]
    _anunciar_estado()
    return False


@_serializar_orden
def reposo_hasta(trap_id: int) -> datetime | None:
    activo = _reposo_activo.get(trap_id)
    if not activo:
        return None
    if time.time() > activo["hasta"] + REPOSO_MARGEN_S:
        _reposo_activo.pop(trap_id, None)
        _reposo_cerrado[trap_id] = activo["id"]
        _anunciar_estado()
        return None
    return datetime.fromtimestamp(activo["hasta"], timezone.utc)


@_serializar_orden
def reposo_modo(trap_id: int) -> str | None:
    return _reposo_activo.get(trap_id, {}).get('modo') if reposo_hasta(trap_id) else None


@_serializar_orden
def reposo_error(trap_id: int) -> str | None:
    return _reposo_error.get(trap_id)


@_serializar_orden
def reposo_confirmado_id(trap_id: int) -> int | None:
    """Id acusado solo si sigue siendo la ultima orden; evita dormir tras una orden nueva."""
    activo = _reposo_activo.get(trap_id)
    if not activo or _ultima_orden.get(trap_id) != (activo["id"], "reposo"):
        return None
    return activo["id"]


# SMS de prueba. Mismo patron que el reinicio: se pide, viaja en la telemetria y se consume al
# entregarlo. Existe porque un canal de avisos que no se puede probar a voluntad no es un canal
# fiable: el dia que haga falta seria el dia en que se descubre que no funcionaba.
# Una orden NO se da por hecha al entregarla: se da por hecha cuando la trampa demuestra que la
# ejecuto. La diferencia la aprendimos el 2026-09-15: el primer SMS de prueba se entrego, la
# placa se reinicio un segundo despues, y la orden **desaparecio sin que nadie se enterara**.
# Solo se supo porque yo estaba mirando el log; en campo habria sido un canal de avisos roto y
# aparentemente sano.
#
# La prueba es un contador: la placa manda en cada telemetria cuantos SMS lleva enviados
# (`sms_n`). Si sube despues de entregar la orden, se hizo. Un contador es mejor que un «ya
# esta» porque no se pierde si una telemetria no llega: el numero sigue ahi la siguiente vez.
_sms_pedido: dict[int, dict] = {}
_sms_visto: dict[int, int] = {}          # ultimo contador que reporto cada trampa

SMS_VALIDO_S = 300.0                     # tiempo total que se persigue la orden
SMS_REINTENTO_S = 90.0                   # si no hay confirmacion, se vuelve a entregar
SMS_MAX_ENTREGAS = 3


def anotar_sms_visto(trap_id: int, n: int | None) -> None:
    """Guarda el contador que reporta la placa. Se llama en cada telemetria."""
    if n is not None:
        _sms_visto[trap_id] = int(n)


def pedir_sms(trap_id: int) -> None:
    _sms_pedido[trap_id] = {
        "pedido_en": time.time(),
        "entregado_en": None,
        "entregas": 0,
        # Contra que se compara para saber si se ejecuto.
        "base": _sms_visto.get(trap_id, 0),
    }


def estado_sms_pedido(trap_id: int) -> dict | None:
    """Para poder contar al usuario en que va la orden, en vez de que desaparezca."""
    o = _sms_pedido.get(trap_id)
    if not o:
        return None
    return {"entregas": o["entregas"],
            "esperando_confirmacion": o["entregado_en"] is not None,
            "segundos": round(time.time() - o["pedido_en"], 1)}


def hay_sms_pedido(trap_id: int, consumir: bool = False) -> bool:
    """¿Hay que pedirle un SMS a esta trampa en ESTA respuesta?

    `consumir` se mantiene por compatibilidad con quien ya llamaba asi, pero ya no significa
    «bórrala»: la orden se borra cuando se confirma o cuando caduca, no al entregarla.
    """
    o = _sms_pedido.get(trap_id)
    if not o:
        return False
    ahora = time.time()

    # 1) ¿Ya lo hizo? El contador de la placa ha subido desde que se pidio.
    if _sms_visto.get(trap_id, 0) > o["base"]:
        _sms_pedido.pop(trap_id, None)
        log.info("trampa %s: SMS confirmado tras %d entrega(s)", trap_id, o["entregas"])
        return False

    # 2) ¿Se acabo el plazo? Se deja dicho que NO se confirmo: perder la orden es aceptable,
    #    perderla en silencio no.
    if ahora - o["pedido_en"] > SMS_VALIDO_S:
        _sms_pedido.pop(trap_id, None)
        log.warning("trampa %s: la orden de SMS caduco SIN confirmar tras %d entrega(s). "
                    "La placa no llego a ejecutarla (o se reinicio antes).",
                    trap_id, o["entregas"])
        return False

    # 3) Entregarla: la primera vez, o de nuevo si no hubo confirmacion en un rato.
    sin_entregar = o["entregado_en"] is None
    toca_reintento = (o["entregado_en"] is not None
                      and ahora - o["entregado_en"] > SMS_REINTENTO_S)
    if (sin_entregar or toca_reintento) and o["entregas"] < SMS_MAX_ENTREGAS:
        o["entregado_en"] = ahora
        o["entregas"] += 1
        if o["entregas"] > 1:
            log.info("trampa %s: reintento %d de la orden de SMS (no hubo confirmacion)",
                     trap_id, o["entregas"])
        return True

    # 4) Entregada y esperando a que la placa lo demuestre.
    return False


# LOG PEDIDO DESDE EL PANEL. No se consume al entregarlo, sino al recibir el log: si la
# respuesta de telemetría se pierde, la orden sigue en pie (hallazgo de Codex, 15/10). Tampoco
# caduca: en campo la Zero puede pasar horas dormida y la orden debe esperarla.
_log_pedido: dict[int, float] = {}


def pedir_log(trap_id: int) -> None:
    _log_pedido[trap_id] = time.time()


def hay_log_pedido(trap_id: int) -> bool:
    return trap_id in _log_pedido


def log_recibido(trap_id: int) -> None:
    _log_pedido.pop(trap_id, None)


def hay_foto_pedida(trap_id: int, consumir: bool = False) -> bool:
    t = _foto_pedida.get(trap_id)
    if not t:
        return False
    if time.time() - t > FOTO_VALIDA_S:      # caduca: no disparar fotos de peticiones viejas
        _foto_pedida.pop(trap_id, None)
        return False
    if consumir:
        _foto_pedida.pop(trap_id, None)
    return True
