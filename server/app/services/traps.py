"""Lógica de trampas: auto-registro y estado online/offline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..core import config
from ..models.models import ConfigCamara, Trampa

# Identificador usado para subir fotos a mano desde el PC de Antonio. El auto-registro de
# ingesta conserva sus imágenes, pero este registro no representa hardware supervisable.
MANUAL_UPLOAD_DEVICE = "pc-entomira"

# ---------------------------------------------------------------- «no te duermas»
# En modo campo (`deep_sleep=1`) la PCB se duerme entre capturas y deja de responder: ni
# streaming, ni /snapshot. Cuando alguien abre el panel de control de una trampa, la web
# marca aquí que la quiere despierta; la respuesta de /ingest se lo dice a la placa, que
# no dormirá ese ciclo. Es en memoria a propósito: si el servidor reinicia, la trampa
# vuelve a dormir (que es lo seguro para la batería).
_keep_awake: dict[int, datetime] = {}
KEEP_AWAKE_S = 180


def marcar_keep_awake(trap_id: int) -> datetime:
    """Pide que la trampa no se duerma durante los próximos KEEP_AWAKE_S segundos."""
    _keep_awake[trap_id] = datetime.now(timezone.utc)
    return _keep_awake[trap_id]


def keep_awake_activo(trap_id: int) -> bool:
    ts = _keep_awake.get(trap_id)
    if not ts:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() < KEEP_AWAKE_S


def parar_keep_awake(trap_id: int) -> None:
    """Descarta el permiso temporal de permanecer despierta al entrar en campo."""
    _keep_awake.pop(trap_id, None)


def get_or_register(session: Session, device_id: str) -> tuple[Trampa, bool]:
    """Devuelve la trampa; si el device_id es desconocido, la auto-registra.

    Robusto ante primeras subidas simultáneas del mismo dispositivo (device_id único):
    si otra petición lo creó a la vez, se recupera en vez de fallar.
    """
    trap = session.exec(select(Trampa).where(Trampa.device_id == device_id)).first()
    if trap:
        return trap, False

    trap = Trampa(device_id=device_id, alias=device_id, estado="sin_asignar")
    session.add(trap)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        trap = session.exec(select(Trampa).where(Trampa.device_id == device_id)).first()
        return trap, False
    session.refresh(trap)

    session.add(ConfigCamara(trampa_id=trap.id))
    session.commit()
    session.refresh(trap)
    return trap, True


def ensure_config(session: Session, trap: Trampa) -> ConfigCamara:
    cfg = session.get(ConfigCamara, trap.id)
    if not cfg:
        cfg = ConfigCamara(trampa_id=trap.id)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)
    return cfg


def touch_seen(session: Session, trap: Trampa) -> None:
    trap.ultimo_visto = datetime.now(timezone.utc)
    if trap.estado == "offline":
        trap.estado = "online" if trap.invernadero_id else "sin_asignar"
    elif trap.estado == "sin_asignar" and trap.invernadero_id:
        trap.estado = "online"
    elif trap.invernadero_id:
        trap.estado = "online"
    session.add(trap)
    session.commit()


# CADA CUANTO tiene que informar la trampa. No es fijo, y ese es el arreglo: el dato no vale
# lo mismo en todo momento. Mientras alguien tiene el panel abierto, la batería y el ritmo de
# carga se están MIRANDO y 10 s es lo que hace útil la pantalla (colocar la trampa al sol y ver
# si carga). Sin nadie delante son datos móviles quemados: cada informe abre una conexión TLS
# nueva, ~4 KB de saludo para llevar 150 bytes.
#
# Medido antes: 10 s fijos = 8.640 informes/día ≈ 1 GB/mes por 4G, más que las propias fotos.
CADENCIA_MIRANDO_S = 10     # alguien tiene el panel abierto: el dato se está usando
CADENCIA_WIFI_S = 60        # sin nadie delante, pero por WiFi no cuesta nada
CADENCIA_MOVIL_S = 600      # sin nadie delante y por 4G: 144 informes/día en vez de 8.640


def cadencia_seg(trap: Trampa, cfg: ConfigCamara | None = None) -> int:
    """Cada cuántos segundos debe informar ESTA trampa ahora mismo."""
    from . import live as live_svc          # import local: `live` ya importa de aquí
    if (cfg is None or not cfg.deep_sleep) and (
            live_svc.quiere_vivo(trap.id) or keep_awake_activo(trap.id)):
        return CADENCIA_MIRANDO_S
    return CADENCIA_MOVIL_S if (trap.red or "") == "lte" else CADENCIA_WIFI_S


# Cuánto retiene el servidor la petición de telemetría (que hace también de long polling de
# vídeo). Es el que fija cuántas conexiones abre la trampa: una por ventana.
#
# 120 s por 4G y no más, de momento, porque una conexión móvil retenida demasiado tiempo se la
# puede llevar por delante el NAT del operador —y entonces la trampa gasta la conexión para
# nada—. Vive aquí para poder subirlo sin reflashear ninguna trampa.
# CON DATOS MOVILES, UNA FOTO AL DIA. Regla de Antonio (2026-09-20): «solo 1 imagen por dia si
# estamos con 4G... en el banco de pruebas tendra wifi y podra hacer mas».
#
# La cuenta que la justifica: una captura son ~4 MB. Al intervalo de 30 s del banco serian
# 8 MB por minuto --casi medio giga a la hora-- y en unos minutos de prueba real el contador
# del plan paso de cero a 33 MB.
#
# ES UN MINIMO ENTRE CAPTURAS, NO UN VALOR: se aplica con `max()`. Quien configure dos dias
# quiere dos dias; el tope existe para frenar, no para acelerar. Lo apunto Codex al acordarlo.
TOPE_MOVIL_S = 86_400

# 120 y no 80: decidido por Antonio el 2026-09-26. Los 80 s eran para no rozar el 524 de
# Cloudflare Tunnel (~125 s), y Cloudflare quedó descartado.
ESPERA_MOVIL_S = 120
ESPERA_WIFI_S = 25


def intervalo_efectivo(trap: Trampa, cfg: ConfigCamara) -> tuple[int, str | None]:
    """Cada cuanto debe capturar ESTA trampa ahora mismo, y por que si no es lo configurado.

    Devuelve (segundos, motivo). `motivo` es None cuando manda la configuracion tal cual; si
    no, es una frase para enseñar en el panel: **una trampa que hace una foto al dia cuando la
    configuraste cada 30 s parece averiada si nadie lo explica**, y aqui los fallos no dan
    error.

    AHORRAR ES EL FALLO SEGURO. Se aplica el tope no solo con `lte`, sino tambien cuando la red
    **no se sabe**: sin dato, `desconocida`, o una VPN de la que no se conoce el transporte.
    Gastar por defecto puede costar el plan del mes; ahorrar por defecto cuesta fotos, que se
    recuperan pidiendo una. Criterio acordado con Codex el 2026-09-20.
    """
    if cfg.deep_sleep:
        return 86400, ("Modo invernadero: foto y corte a las %s (hora de Madrid)"
                       % ", ".join(cfg.horas_despertar or [cfg.hora_despertar]))
    base = int(cfg.intervalo_seg)
    red = (trap.red or "").strip().lower()
    if red in ("wifi", "eth"):
        return base, None
    if red == "lte":
        if base >= TOPE_MOVIL_S:
            return base, None
        return TOPE_MOVIL_S, "Con datos móviles: una foto al día (configurado: cada %s)" % (
            _en_palabras(base))
    return max(base, TOPE_MOVIL_S), (
        "Red sin confirmar: límite preventivo de una foto al día (configurado: cada %s)"
        % _en_palabras(base))


def _en_palabras(seg: int) -> str:
    """«30 s», «10 min», «2 h»... para que el panel no enseñe 86400."""
    if seg < 60:
        return "%d s" % seg
    if seg < 3600:
        return "%d min" % round(seg / 60)
    if seg < 86_400:
        return "%d h" % round(seg / 3600)
    d = seg / 86_400
    return "%d día%s" % (round(d), "" if round(d) == 1 else "s")


def espera_seg(trap: Trampa, cfg: ConfigCamara | None = None) -> int:
    """Segundos que el servidor retendrá la próxima petición de esta trampa.

    **Cero en modo campo.** Con `deep_sleep`, la trampa se despierta, captura, sube y se vuelve
    a dormir en `ventana_despierto_seg` (15 s por defecto). Retenerle la respuesta 120 s la
    tendría despierta ocho veces más de lo previsto — y la autonomía del TFM está calculada
    sobre esa ventana. La retención es para la trampa que ya está despierta de todos modos.
    """
    if cfg is not None and cfg.deep_sleep:
        return 0
    return ESPERA_MOVIL_S if (trap.red or "") == "lte" else ESPERA_WIFI_S


def grace_seconds(cfg: ConfigCamara | None, trap: Trampa | None = None) -> int:
    """Margen antes de dar una trampa por desconectada.

    DORMIDA (`deep_sleep=1`) solo aparece una vez por intervalo: con 600 s de intervalo y
    180 s de margen saldría SIEMPRE offline aunque funcionase perfectamente. En ese caso el
    margen es dos intervalos + la ventana despierta (tolera perder un ciclo).

    DESPIERTA, el margen sale de **la cadencia que el propio servidor le ha mandado**: si le
    pedimos informar cada 600 s por 4G y la damos por muerta a los 180, la pintamos offline
    estando perfectamente — que es peor que no pintar nada, porque enseña a ignorar el panel.
    """
    if cfg and cfg.deep_sleep:
        return int(86400 * 2 + cfg.ventana_despierto_seg)
    if trap is not None:
        # Dos ventanas perdidas + un minuto. Cuenta con la RETENCIÓN: mientras el servidor
        # aguanta la petición, la trampa no vuelve a hablar, y eso no es estar desconectada.
        ventana = cadencia_seg(trap) + espera_seg(trap, cfg)
        return max(config.ONLINE_GRACE, ventana * 2 + 60)
    return config.ONLINE_GRACE


def en_linea(trap: Trampa, cfg: ConfigCamara | None = None) -> bool:
    """¿Ha dado señales esta trampa dentro de su margen?

    **Una sola definición para todo el sistema.** La web la recalculaba por su cuenta con
    `180000` ms escritos a pelo —una herencia de cuando la telemetría iba cada 10 s— mientras
    el servidor usaba `grace_seconds()`, que sale de la cadencia y la retención que él mismo le
    ha pedido a esa trampa. Con la config actual son **180 s frente a 1500 s**: entre esos dos
    números la misma tarjeta decía «En línea» y «sin conexión» a la vez, y el panel de control
    —que usa esta condición para decidir si pide vídeo— **cortaba el directo solo**, porque la
    trampa habla cada ~2 min, justo en el filo de los 180 s.

    Va separado de `computed_estado` a propósito: aquella responde «qué pinto en la etiqueta»
    (y una trampa sin invernadero sale «sin asignar»), esta responde «¿está viva?», que es lo
    que hay que mirar antes de pedirle vídeo.
    """
    if trap.ultimo_visto is None:
        return False
    from . import live as live_svc          # aquí dentro: live no debe importar traps al cargar
    if (live_svc.apagada_desde(trap.id) is not None
            or live_svc.reposo_hasta(trap.id) is not None):
        return False
    lv = trap.ultimo_visto
    if lv.tzinfo is None:
        lv = lv.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - lv) <= timedelta(seconds=grace_seconds(cfg, trap))


def computed_estado(trap: Trampa, cfg: ConfigCamara | None = None) -> str:
    """Estado efectivo teniendo en cuenta el tiempo desde el ultimo visto."""
    if trap.invernadero_id is None:
        base = "sin_asignar"
    else:
        base = "online"
    if trap.ultimo_visto is None:
        return base if base == "sin_asignar" else "offline"
    from . import live as live_svc
    if live_svc.reposo_hasta(trap.id) is not None:
        return "reposo"
    if live_svc.apagada_desde(trap.id) is not None:
        return "apagada"
    lv = trap.ultimo_visto
    if lv.tzinfo is None:
        lv = lv.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - lv > timedelta(seconds=grace_seconds(cfg, trap)):
        return "offline" if trap.invernadero_id else "sin_asignar"
    return base
