"""Telemetría ligera de la trampa (sin imagen).

La placa solo reportaba batería al subir una foto, es decir **cada `intervalo_seg`** (600 s
en campo). Para ver si el panel solar está cargando eso es demasiado espaciado, así que la
PCB manda aquí un JSON de ~150 bytes cada `TELEMETRIA_MS` mientras está despierta. No crea
imágenes ni ficheros: solo refresca la fila de la trampa (y su `ultimo_visto`, así que
también sirve de latido).

Autenticación por `X-API-Key` de dispositivo (ver `core/auth.py`, `_KEYED_PREFIXES`).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core.db import sesion_corta
from ..core.red import ip_valida
from ..core.security import require_device
from ..services import bateria as bat_svc
from ..services import cambios as cambios_svc
from ..services import consumo as consumo_svc
from ..services import ina as ina_svc
from ..services import live as live_svc
from ..services import config_aplicada as cfg_aplicada_svc
from ..services import perdidas_buffer as perdidas_svc
from ..services import sensores as sensores_svc
from ..services import traps as traps_svc
from ..models.models import LecturaHardware, Trampa
from .firmware import _meta

# Hueco a partir del cual se considera que la trampa **volvio** en vez de seguir hablando.
# Una telemetria normal llega cada 20 s, y cada 2 s mientras alguien mira el video; 90 s son
# holgados para las dos sin confundir un retraso con una reconexion.
RECONEXION_S = 90.0

router = APIRouter(prefix="/api/v1", tags=["telemetría"])

# La cadencia vive en `services/traps.py`, junto a la regla de «offline» que la usa: si el
# servidor pide informar cada 600 s y da por muerta a la trampa a los 180, se contradice solo.


class TelemetryIn(BaseModel):
    device_id: str
    bateria: int | None = None      # % (SOC del MAX17048)
    voltaje: float | None = None    # V de la celda
    crate: float | None = None      # %/h con signo: >0 cargando, <0 descargando
    rssi: int | None = None         # dBm de la red en uso (WiFi o movil)
    fw: str | None = None           # version de firmware de la placa
    ip: str | None = None
    # Transporte con el que la placa ha mandado ESTO. Sin el dato, la web no puede saber si
    # la trampa es alcanzable en la red local (streaming directo) o esta detras del NAT del
    # operador, que son dos situaciones muy distintas para el que la esta manejando.
    red: str | None = None          # wifi | lte
    # La trampa MIDE por donde sale su trafico al servidor en vez de declararlo a mano, asi
    # que ademas puede decir si el 4G esta listo aunque ahora mismo no se este usando --por
    # ejemplo con el servidor en la misma LAN, que se alcanza por WiFi con el modem perfecto--.
    lte_lista: bool | None = None   # hay modem con direccion, se use o no
    ifaz: str | None = None         # interfaz real: usb0, wlan0...
    operador: str | None = None     # nombre de la red movil
    # Fotos que la placa tiene guardadas en su tarjeta esperando a que vuelva la red.
    buf: int | None = None
    # Cuantos SMS lleva enviados desde que tiene corriente. Es el acuse de recibo de las
    # ordenes de SMS: si sube despues de entregar una, es que se ejecuto. Ver `services/live.py`.
    sms_n: int | None = None
    # Ventanas de 5 min del medidor de consumo (INA226), las que la trampa tenga pendientes.
    # Tipo LAXO a proposito: se validan una a una en `services/ina.py`, y una ventana rara se
    # descarta sola en vez de rechazar el informe entero --un 422 aqui la trampa lo repetiria
    # en cada telemetria y parecería caída por culpa de un numero--.
    #
    # `Any` y no `list`: con `list`, un cuerpo que trajera aqui un objeto lo rechazaba PYDANTIC
    # con un 422 antes de llegar a esa validacion tolerante, y la trampa repetiria el informe
    # sin fin. Hallazgo de Codex al revisar el 205ea83, 2026-09-26.
    ina: Any = None
    # La ULTIMA lectura del INA226 ({"v": V, "ma": mA}), para el panel de sensores en directo.
    # Las ventanas de arriba son el historico de 5 min; esto es el instante. Tambien laxo: lo
    # valida `services/sensores.py` campo a campo.
    ina_ya: Any = None
    # La config que la trampa tiene APLICADA ({"v": version, "origen": servidor|disco|fabrica}).
    # Laxo por lo mismo que `ina`: lo valida `services/config_aplicada.py`.
    cfg_aplicada: Any = None
    buf_perdidas: Any = None
    carga: Any = None
    ina219: Any = None
    rtc: Any = None
    # Acuse de reposo. Solo este dato, no haber construido la respuesta anterior, permite
    # al servidor afirmar que la trampa esta voluntariamente dormida.
    reposo_ack: Any = None


class _Contesta(NamedTuple):
    """Lo que hace falta para responder, en numeros sueltos.

    **Aqui dentro no hay ni un objeto de la base, y eso es el arreglo.** El 500 de las 111
    veces al dia no vino de escribir `trap.id` en vez de `trap_id`: vino de que `trap` seguia
    a mano cuatro lineas despues de cerrar la sesion. Mientras la variable este en el ambito,
    tarde o temprano alguien la usa -- lo hice yo, el mismo dia que escribi el comentario que
    lo prohibia.

    Con la fase de base de datos metida en su propia funcion, `trap` y `cfgm` **dejan de
    existir** al salir de ella. Ya no es una regla que haya que recordar: es que no se puede.
    """

    trap_id: int
    cfg_version: int
    tope: float          # cuanto se puede retener esta trampa (0 en modo campo)
    cada: float          # segundos hasta el proximo informe, si no se retiene
    # Cada cuanto debe CAPTURAR, con la politica de datos ya aplicada. Se calcula aqui dentro,
    # que es donde `trap` y `cfgm` todavia existen, y sale como un numero suelto: es justo la
    # regla de esta tupla.
    intervalo: int
    campo: bool


def _fase_bd(body: TelemetryIn) -> _Contesta:
    """TODO lo que toca la base de datos, y nada mas. Devuelve numeros, no filas.

    **La sesion nace y muere aqui dentro.** Antes la recibia el endpoint por
    `Depends(get_session)`, que la mantiene viva --y con ella una conexion del pool--
    durante TODA la peticion; como esta se retiene hasta 120 s, habia que acordarse de
    cerrarla a mano antes de esperar. Acordarse no basta: la regla estaba escrita en
    mayusculas y cuatro lineas mas abajo ya estaba incumplida. 111 respuestas 500 en un dia.

    Con el `with` no hay nada que recordar, y lo que sale de aqui son numeros: una fila
    fuera de este bloque estaria desligada y con los atributos caducados.
    """
    with sesion_corta() as session:
        return _leer(session, body)


def _config_tras_espera(trap_id: int, anterior: _Contesta) -> _Contesta:
    """Lee la config vigente antes de entregar una orden de corte retenida.

    La primera lectura ocurre ANTES del long polling. Si se cambia de banco a
    invernadero mientras esa respuesta espera, entregar el corte con la versión
    vieja apaga la Zero sin que haya guardado la configuración nueva.
    """
    with sesion_corta() as session:
        trap = session.get(Trampa, trap_id)
        if trap is None:
            return anterior
        cfgm = traps_svc.ensure_config(session, trap)
        intervalo, _ = traps_svc.intervalo_efectivo(trap, cfgm)
        return _Contesta(trap_id, cfgm.version,
                         traps_svc.espera_seg(trap, cfgm),
                         traps_svc.cadencia_seg(trap, cfgm),
                         intervalo, bool(cfgm.deep_sleep))


def _leer(session: Session, body: TelemetryIn) -> _Contesta:
    """El cuerpo de `_fase_bd`, ya con la sesion abierta."""
    trap, _ = traps_svc.get_or_register(session, body.device_id)

    # Lo que la placa dice haber hecho. Va antes de decidir nada: el contador de SMS enviados
    # es el acuse de recibo de las ordenes, y hay que apuntarlo aunque no haya ninguna en curso.
    # Si habla, no está apagada: la encendieron o el apagado no llegó a ejecutarse.
    live_svc.limpiar_apagada(trap.id)
    live_svc.anotar_reposo(trap.id, body.reposo_ack)
    live_svc.anotar_sms_visto(trap.id, body.sms_n)
    live_svc.marcar_lte(trap.id, body.lte_lista, body.ifaz)
    if body.fw:
        trap.fw_version = body.fw
    # Foto de lo que la web ENSEÑA, antes de tocar nada. Sirve para no anunciar un cambio
    # que no ha existido: ver mas abajo.
    MOSTRADOS = ("bateria", "voltaje", "crate", "rssi", "ip", "red", "operador",
                 "buffer_pend", "fw_version")
    antes = {c: getattr(trap, c, None) for c in MOSTRADOS}
    visto_antes = trap.ultimo_visto

    for campo in ("bateria", "voltaje", "crate", "rssi", "ip", "red", "operador"):
        val = getattr(body, campo)
        if campo == "ip":
            val = ip_valida(val)                # texto libre de la trampa: solo IPs de verdad
        if val is not None:
            setattr(trap, campo, val)
    # El nuevo INA219 de la Zero mide tensión/corriente, no SOC ni crate. Limpiar
    # los valores persistidos del MAX17043 anterior para no mostrar un % obsoleto.
    if isinstance(body.carga, dict) and body.carga.get("origen") == "ina219":
        trap.bateria = None
        trap.crate = None
    if body.buf is not None:
        trap.buffer_pend = body.buf
    bat_svc.anotar(trap.id, body.voltaje)   # historial corto -> deteccion rapida de carga
    bat_svc.anotar_trampa(trap.id, body.carga)   # veredicto de la trampa (1 min), si lo manda
    sensores_svc.anotar(trap.id, body.ina_ya, body.voltaje, body.bateria,
                        body.ina219)   # cada informe, sin esperar al histórico de un minuto
    ina_nuevas = ina_svc.guardar(session, trap.id, body.ina)
    ahora = datetime.now(timezone.utc)
    # Una muestra por minuto como máximo. Los huecos siguen siendo NULL, nunca cero.
    def numero(d, clave, minimo, maximo):
        x = d.get(clave) if isinstance(d, dict) else None
        return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and minimo <= x <= maximo else None
    v = numero(body.ina219, 'v', 2.5, 5.5)
    ma = numero(body.ina219, 'ma', -35000, 35000)
    rtc_hora = None
    if isinstance(body.rtc, dict) and body.rtc.get('modelo') == 'PCF8523':
        try:
            rtc_hora = datetime.fromisoformat(body.rtc['hora'].replace('Z', '+00:00'))
            if abs((ahora - rtc_hora).total_seconds()) > 300:
                rtc_hora = None
        except (KeyError, ValueError, TypeError):
            pass
    previa = session.exec(select(LecturaHardware).where(LecturaHardware.trampa_id == trap.id)
                          .order_by(LecturaHardware.id.desc()).limit(1)).first()
    if (v is not None or ma is not None or rtc_hora is not None) and (previa is None or
            (ahora - previa.instante.replace(tzinfo=timezone.utc)).total_seconds() >= 60):
        session.add(LecturaHardware(trampa_id=trap.id, instante=ahora, tension_v=v,
                                    corriente_ma=ma, rtc_hora=rtc_hora))
    trap.ultimo_visto = ahora
    session.add(trap)
    session.commit()

    # SOLO se anuncia un cambio si de verdad ha cambiado algo que se ve.
    #
    # Esto se marcaba en CADA telemetria, y el efecto no se veia hasta que hubo video: con
    # alguien mirando en vivo el servidor contesta al instante, asi que la trampa manda
    # telemetria **cada 2 s** en vez de cada 20. La web sondea `/cambios` cada 4 s y, cuando
    # la version sube, llama a `router()`, que repinta la vista ENTERA (`app.innerHTML=''`).
    # Resultado medido el 2026-09-19: la pagina se quedaba en blanco un instante cada pocos
    # segundos justo mientras se miraba el video. Y no habia cambiado ni un dato: lo unico
    # distinto era `ultimo_visto`.
    #
    # `ultimo_visto` a secas no cuenta como cambio, porque cambia siempre. Lo que si cuenta
    # es volver de estar caida: ahi el panel pasa de OFFLINE a online y hay que repintar.
    # SQLite devuelve el `datetime` SIN zona horaria y `ahora` la lleva: restarlos a pelo
    # revienta con "can't subtract offset-naive and offset-aware". Lo guardado siempre es UTC.
    if visto_antes is not None and visto_antes.tzinfo is None:
        visto_antes = visto_antes.replace(tzinfo=timezone.utc)
    volvio = visto_antes is None or (ahora - visto_antes).total_seconds() > RECONEXION_S
    # Una ventana nueva del INA tambien se ve: es la cifra de consumo de la ficha.
    if volvio or ina_nuevas or any(getattr(trap, c, None) != antes[c] for c in MOSTRADOS):
        cambios_svc.marcar("bateria")

    # `cfg`: version de configuracion vigente. La placa la compara con la suya y, si no
    # coincide, pide la config. Antes la config solo viajaba en la respuesta del /ingest, asi
    # que si esa respuesta se perdia (o la placa arrancaba en frio) la trampa se quedaba
    # disparando con los valores por defecto sin que nada lo dijera.
    cfgm = traps_svc.ensure_config(session, trap)
    # ...y lo que la trampa dice tener aplicado, para avisar si no casa (ver `estado.avisos`).
    cfg_aplicada_svc.anotar(trap.id, body.cfg_aplicada, cfgm.version)
    perdidas_svc.anotar(trap.id, body.buf_perdidas)

    # `cada`: los segundos que la trampa debe esperar hasta el proximo informe. Lo decide el
    # servidor porque es el unico que sabe si hay alguien mirando; la placa solo obedece.
    # "Mirando" es `live_svc.quiere_vivo`: lo pone el latido del navegador (`/live-ping`)
    # mientras el panel esta abierto, que es exactamente la senal de que hay una persona
    # delante. `keep_awake_activo` es otra cosa -- "no te duermas" -- y se cuenta tambien
    # porque implica lo mismo: alguien esta pendiente de esta trampa ahora.
    tope = traps_svc.espera_seg(trap, cfgm)      # 0 en modo campo: la placa se va a dormir
    cada = traps_svc.cadencia_seg(trap, cfgm)

    # Datos moviles: esta peticion ha costado una conexion y su carga util. Solo cuenta si la
    # trampa viene por 4G; por WiFi o cable no se gasta plan.
    #
    # OJO con esta linea: no escribe casi nunca -- acumula en memoria y vuelca cada 30 s --,
    # pero cuando vuelca hace `commit()`, y un commit **caduca los atributos** de `trap`. Esa
    # es la mitad del fallo del 2026-09-14; la otra mitad era tener `trap` a mano despues.
    # Aqui ya no puede pasar: `trap` muere con esta funcion.
    # Las ventanas del INA viajan en este mismo cuerpo y tambien son plan: sin sumarlas, el
    # contador de datos se quedaria corto justo en lo que se añadio para medir mejor.
    extra = len(json.dumps(body.ina, separators=(",", ":"))) if body.ina else 0
    consumo_svc.anotar(session, trap, "telemetria", bytes_=350 + extra)

    intervalo, _motivo = traps_svc.intervalo_efectivo(trap, cfgm)
    return _Contesta(trap_id=trap.id, cfg_version=cfgm.version, tope=tope, cada=cada,
                     intervalo=intervalo, campo=bool(cfgm.deep_sleep))


@router.post("/telemetry", dependencies=[Depends(require_device)])
async def telemetry(body: TelemetryIn, espera: float = 0.0):
    """Telemetria **y** long polling de video en la misma peticion.

    Antes eran dos conexiones distintas y frecuentes --telemetria cada 10 s y
    `GET /traps/<id>/live` cada 20 s--, y por 4G lo caro no son los 150 bytes del JSON sino
    **abrir la conexion**: cada saludo TLS son ~4 KB. Fusionarlas deja **una sola** conexion
    haciendo los dos trabajos.

    Con `?espera=N` el servidor **retiene la respuesta** hasta N segundos y contesta en cuanto
    alguien abre la vista en vivo. Asi el video arranca igual de rapido que antes --la respuesta
    la retiene el servidor, no la trae el siguiente sondeo-- y, mientras nadie mire, la trampa
    gasta una conexion por ventana en vez de una cada pocos segundos.

    La funcion tiene dos mitades y **entre ellas se cierra la sesion**: arriba `_fase_bd`, la
    unica que habla con la base; abajo la espera, que solo maneja numeros.
    """

    # Toda la base de datos, en una linea, y ya cerrada al volver de ella.
    d = _fase_bd(body)

    # De paso se le dice que version de firmware hay publicada. La telemetria ya viaja cada
    # 10 s, asi que la trampa se entera de una actualizacion casi al momento en vez de
    # esperar a su sondeo de 15 min. No cuesta ni una peticion mas.
    m = _meta() or {}

    # LA ESPERA. Aqui es donde esta peticion hace tambien de long polling de video: se retiene
    # hasta `espera` segundos y se contesta en cuanto alguien abre la vista. El tope lo pone el
    # servidor (`traps_svc.espera_seg`) para poder afinarlo sin reflashear ninguna trampa.
    espera = max(0.0, min(espera, d.tope))

    # AQUI YA NO HAY NI SESION NI CONEXION DEL POOL. No hace falta cerrar nada: la
    # sesion nunca llego a salir de `_fase_bd`.
    #
    # Importa porque el pool son 15 conexiones (ver `core/db.py`) y esta peticion se
    # retiene hasta 120 s. Medido el 2026-09-12 con 20 trampas ficticias: las 15
    # primeras se quedaron con todas las conexiones durante la espera y a partir de ahi
    # **no respondio nada**, ni `/health` ni la web, porque cualquier peticion sale del
    # mismo pool. El servicio seguia `active` y el log simplemente dejaba de escribir.

    # Si ESTA peticion se retiene, el ritmo lo marca la propia retencion: la trampa debe volver
    # a llamar en cuanto reciba la respuesta (`cada = 0`).
    #
    # Medido antes de esto: con retencion de 120 s Y cadencia de 600 s, las dos se SUMABAN y la
    # trampa hablaba cada 12 minutos, dejando una **ventana ciega de 10 minutos** en la que
    # nadie podia alcanzarla para pedirle video -- justo lo contrario de para lo que se
    # fusionaron las dos peticiones.
    cada = 0 if espera > 0 else d.cada

    if d.campo:
        en_vivo = False
    elif espera > 0:
        en_vivo = await live_svc.esperar_peticion(d.trap_id, espera)
    else:
        en_vivo = live_svc.quiere_vivo(d.trap_id)

    reposo = live_svc.orden_reposo(d.trap_id)
    if reposo is not None and espera > 0:
        # La orden puede haber despertado una respuesta que ya llevaba hasta 120 s
        # esperando con `cfg` antiguo. La sesion nueva vive solo esta consulta.
        d = _config_tras_espera(d.trap_id, d)
        if d.campo:
            en_vivo = False

    return {"ok": True, "trap_id": d.trap_id, "fw": m.get("version", ""),
            "cfg": d.cfg_version,
            "cada": cada,
            # EL INTERVALO VIAJA EN CADA TELEMETRIA, no solo al cambiar la version de camara.
            # `obedecer()` en la trampa solo descarga la config si cambia `cfg`, y **cambiar de
            # red no cambia esa version**: sin esto, una trampa que pasa a 4G seguiria
            # disparando cada 30 s hasta que alguien tocara la configuracion. Lo vio Codex al
            # acordar el diseño, 2026-09-20.
            "intervalo": d.intervalo,
            # Cuanto debe pedir que se le retenga la PROXIMA: lo decide el servidor.
            "espera": d.tope,
            "en_vivo": en_vivo,
            "keep_awake": False if d.campo else traps_svc.keep_awake_activo(d.trap_id),
            "foto": live_svc.hay_foto_pedida(d.trap_id, consumir=True),
            # Se repite hasta que llega el log: perder esta respuesta no pierde la orden.
            "log": live_svc.hay_log_pedido(d.trap_id),
            # Reinicio a distancia. Se consume al entregarlo: si se quedara puesto, la placa
            # reiniciaria, lo volveria a leer al arrancar y no saldria del bucle.
            "reinicia": live_svc.hay_reinicio_pedido(d.trap_id, consumir=True),
            # Apagado a distancia: igual que el reinicio, pero sin alarma RTC; la Zero con
            # Pololu necesita un pulso manual A-GND. `pedir_apagado` y `pedir_reinicio` se anulan
            # entre si, asi que nunca viajan los dos a la vez.
            "apaga": live_svc.hay_apagado_pedido(d.trap_id, consumir=True),
            # Se repite hasta el acuse: perder una respuesta no crea un reposo ficticio.
            "reposo": reposo,
            # El cliente no apaga radios hasta ver su id confirmado. Si una orden mas nueva
            # gano durante el acuse, sale None y la Zero permanece despierta para recogerla.
            "reposo_ack": live_svc.reposo_confirmado_id(d.trap_id),
            # SMS de prueba del canal de avisos. Se consume al entregarlo, igual que
            # el reinicio: si se quedara puesto, la trampa mandaria uno cada vuelta.
            "sms": live_svc.hay_sms_pedido(d.trap_id, consumir=True)}
