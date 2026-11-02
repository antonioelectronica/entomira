"""
Estado de salud del sistema y avisos: que un fallo se VEA en vez de pasar en silencio.

La lección más cara del proyecto: el 31 de julio el disco se llenó, la ingesta dejó de
aceptar fotos y **nadie se enteró en tres días**. El servicio seguía `active`, la web
funcionaba y la única señal eran 422 respuestas 500 enterradas en el journal.

Aquí se recogen las condiciones que merecen un aviso visible, con una regla: **un aviso solo
sirve si dice qué ha pasado, desde cuándo y qué hacer**. Los avisos vagos se aprenden a
ignorar, y entonces no avisan de nada.
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from ..core import config
from ..models.models import ConfigCamara, Imagen, Trampa
from . import config_aplicada as cfg_aplicada_svc
from . import perdidas_buffer as perdidas_svc
from . import live as live_svc
from . import sensores_calidad as sensores_calidad_svc
from . import traps as traps_svc

# --- disco lleno: lo marca la propia ingesta cuando rechaza una imagen ---------------
_disco: dict[str, float | None] = {"desde": None, "libre_mb": None}

# --- el worker de IA: lo marca el propio worker al pedir trabajo ----------------------
#
# El estado vive AQUI y no en `api/health.py` para no invertir la dependencia: el servicio no
# puede importar la capa de API. `health.py` solo delega.
_worker: dict[str, float | None] = {"visto": None}

# Cuanto puede faltar el worker antes de que sea un problema. Se avisa SOLO si ademas hay
# fotos esperando: sin trabajo pendiente, que no este no le hace dano a nadie todavia.
WORKER_AUSENTE_AVISO_MIN = 30.0
WORKER_AUSENTE_CRITICO_H = 6.0

# Umbrales. Se avisa ANTES de que sea un problema, no cuando ya lo es.
DISCO_AVISO_PCT = 80.0
DISCO_CRITICO_PCT = 90.0
# Sin fotos durante más de esto, algo pasa. Se compara contra el intervalo de cada trampa
# para no dar falsos avisos con intervalos largos.
SIN_FOTOS_FACTOR = 4

# La purga borra a los 7 días lo que siga sin procesar (`scripts/purga.py`). Se avisa con 2 días
# de margen: suficiente para abrir el worker sin que el aviso se vuelva ruido permanente.
# Si se cambia el valor en la purga, hay que cambiarlo aquí: son la misma regla contada dos
# veces, y el día que se separen el aviso mentirá.
PURGA_SIN_PROCESAR_DIAS = 7
PURGA_AVISO_MARGEN_DIAS = 2
# Desde el 2026-10-23 la purga NO borra por edad (`PURGA_POR_EDAD` en `scripts/purga.py`, orden
# de Antonio). Apagada, avisar de un borrado que no va a ocurrir sería mentir; si se vuelve a
# encender allí, hay que encenderla aquí.
PURGA_POR_EDAD = False


def marcar_worker_visto() -> None:
    """Lo llama el worker cada vez que pide trabajo."""
    _worker["visto"] = time.time()


def worker_ausente_s() -> float | None:
    """Segundos desde que se vio al worker, o None si no se ha visto nunca en este arranque."""
    return None if _worker["visto"] is None else time.time() - _worker["visto"]


def marcar_disco_lleno(libre_mb: float) -> None:
    if _disco["desde"] is None:
        _disco["desde"] = time.time()
    _disco["libre_mb"] = libre_mb


def marcar_disco_ok() -> None:
    _disco["desde"] = None
    _disco["libre_mb"] = None


def _aviso(nivel: str, titulo: str, detalle: str, accion: str) -> dict:
    return {"nivel": nivel, "titulo": titulo, "detalle": detalle, "accion": accion}


def avisos(session: Session) -> list[dict]:
    """Lista de problemas activos, del más grave al menos. Vacía = todo en orden."""
    out: list[dict] = []
    ahora = datetime.now(timezone.utc)
    # Las subidas manuales tienen una fila de Trampa por la ingesta, pero no hay equipo
    # autónomo del que esperar fotos, batería, sensores o conexión.
    fijas = session.exec(select(Trampa).where(
        Trampa.owner_id.is_(None),
        Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)).all()

    # 1) disco
    du = shutil.disk_usage(str(config.DATA_DIR))
    usado_pct = du.used / du.total * 100
    libre_gb = du.free / 1e9
    if _disco["desde"]:
        minutos = (time.time() - _disco["desde"]) / 60
        out.append(_aviso(
            "critico", "La ingesta está RECHAZANDO fotos por falta de espacio",
            f"Desde hace {minutos:.0f} min. Quedan {_disco['libre_mb']:.0f} MB.",
            "Libera espacio o vacía la galería. La purga automática corre a las 04:00."))
    elif usado_pct >= DISCO_CRITICO_PCT:
        out.append(_aviso(
            "critico", "Disco casi lleno",
            f"{usado_pct:.0f} % usado, {libre_gb:.1f} GB libres.",
            "Cuando se llene, la ingesta dejará de aceptar fotos."))
    elif usado_pct >= DISCO_AVISO_PCT:
        out.append(_aviso(
            "aviso", "El disco se está llenando",
            f"{usado_pct:.0f} % usado, {libre_gb:.1f} GB libres.",
            "Revisa la política de purga o el intervalo de captura."))

    # 1b) EL WORKER DE IA NO APARECE, y hay fotos esperando.
    #
    # Esto costo caro el 2026-09-19: el worker llevaba horas parado, habia 317 imagenes en
    # `pendiente` y CERO procesadas, y nada lo decia. La purga borra a los 7 dias lo que sigue
    # sin procesar, asi que el sistema estaba tirando las fotos **sin haberlas mirado nunca**,
    # sin un error, sin un 5xx y sin que el disco se resintiera. El dato para detectarlo ya
    # existia (`/health/worker`); lo que faltaba era que alguien lo mirara.
    #
    # Se exige que HAYA pendientes: un worker apagado sin trabajo no es un problema todavia, y
    # un aviso permanente se aprende a ignorar, que es como no tenerlo.
    pendientes = session.exec(
        select(func.count()).select_from(Imagen).where(Imagen.estado == "pendiente")
    ).one()
    ausente = worker_ausente_s()
    if pendientes and (ausente is None or ausente > WORKER_AUSENTE_AVISO_MIN * 60):
        if ausente is None:
            desde = "no ha aparecido desde que arranco el servidor"
        elif ausente > WORKER_AUSENTE_CRITICO_H * 3600:
            desde = "lleva %.0f h sin aparecer" % (ausente / 3600)
        else:
            desde = "lleva %.0f min sin aparecer" % (ausente / 60)
        critico = ausente is None or ausente > WORKER_AUSENTE_CRITICO_H * 3600
        out.append(_aviso(
            "critico" if critico else "aviso",
            "El worker de IA no esta analizando las fotos",
            "%d imagen(es) esperando y el worker %s." % (pendientes, desde),
            "Arrancalo con: python worker/run_worker_consola.py (venv de C:/ento_ml)."
            + (" Si no se procesan, la purga las borra a los %d dias SIN haberlas mirado."
               % PURGA_SIN_PROCESAR_DIAS if PURGA_POR_EDAD else "")))

    # 2) trampas que llevan demasiado sin subir una foto
    for trap in fijas:
        # AQUI HABIA UN `trap.invernadero_id is None: continue`, Y SE COMIA EL AVISO ENTERO.
        #
        # Dejaba fuera a toda trampa sin invernadero asignado, que es justo el estado en el que
        # esta la unica trampa real del proyecto. El 2026-09-19 estuvo **15 minutos muerta**
        # --se reinicio a mitad de una tanda de video-- y `/avisos` seguia contestando
        # `total: 0`: el sistema tenia el dato, sabia que su intervalo eran 30 s, y no dijo
        # nada. Lo vi al mirar por que no habia saltado ningun aviso.
        #
        # El filtro que hace falta ya esta tres lineas mas abajo: `ultima is None` descarta a
        # las que no han subido nunca una foto, que son las recien registradas y las de
        # prueba. Una que SI subia y ha dejado de hacerlo merece aviso tenga invernadero o no:
        # el invernadero es una etiqueta de organizacion, no una condicion para estar viva.
        if trap.device_id == "demo-fallback":
            continue
        ultima = session.exec(
            select(func.max(Imagen.capturada)).where(Imagen.trampa_id == trap.id)).one()
        if ultima is None:
            continue
        # Apagada a propósito desde la web: no es una avería, pero tampoco se calla, porque una
        # trampa olvidada apagada no captura nada. Se dice que está apagada y desde cuándo.
        desde = live_svc.apagada_desde(trap.id)
        if desde is not None:
            hora = datetime.fromtimestamp(desde, timezone.utc).astimezone().strftime("%d/%m %H:%M")
            out.append(_aviso(
                "aviso", f"«{trap.alias or trap.device_id}» está apagada",
                f"Se apagó desde la web el {hora}. No captura hasta que se encienda.",
                "Vuelve a darle alimentación cuando quieras que siga capturando."))
            continue
        # Dormida por orden y con acuse: no subir fotos ni hablar es el comportamiento pedido.
        if live_svc.reposo_hasta(trap.id) is not None:
            continue
        if ultima.tzinfo is None:
            ultima = ultima.replace(tzinfo=timezone.utc)
        callada_s = (ahora - ultima).total_seconds()

        cfg = session.get(ConfigCamara, trap.id)
        intervalo = (86400 if cfg and cfg.deep_sleep else
                     ((cfg.intervalo_seg if cfg else 600) or 600))
        if callada_s > intervalo * SIN_FOTOS_FACTOR:
            out.append(_aviso(
                "aviso" if callada_s < intervalo * 12 else "critico",
                f"«{trap.alias or trap.device_id}» lleva {callada_s/60:.0f} min sin subir fotos",
                f"Su intervalo es de {intervalo} s, así que debería subir una cada "
                f"{intervalo/60:.0f} min.",
                "Comprueba alimentación, WiFi y que el puente USB esté activo."))

    # 2b) trampas que NO usan la config que el servidor les da. Viva, mandando telemetría y
    # con valores de fábrica: parece sana y dispara con el intervalo y la cámara equivocados
    # (fallo F7 del experimento del artículo B, que el centinela no ve).
    for trap in fijas:
        motivo = cfg_aplicada_svc.problema(trap.id)
        if motivo is None:
            continue
        dato = cfg_aplicada_svc.ver(trap.id)
        nombre = trap.alias or trap.device_id
        if motivo == "fabrica":
            out.append(_aviso(
                "aviso", f"«{nombre}» funciona con la configuración de fábrica",
                "No ha podido descargar su config ni tenía una guardada.",
                "Comprueba que alcanza el servidor; la reintenta en cada telemetría."))
        else:
            out.append(_aviso(
                "aviso", f"«{nombre}» no ha aplicado su configuración",
                f"Usa la v{dato['v']} y el servidor tiene la v{dato['quiere']}.",
                "Mira su registro: la config puede venir rechazada o no llegar."))

    # 2b') fotos perdidas en el buffer de la trampa (buffer o disco llenos). Sin esto se perdían
    # en silencio: el artículo F midió 30/30 con el disco de la trampa lleno.
    for trap in fijas:
        p = perdidas_svc.problema(trap.id)
        if p is None:
            continue
        nombre = trap.alias or trap.device_id
        partes = []
        if p["sin_guardar"]:
            partes.append(f"{p['sin_guardar']} sin poder guardar (tarjeta llena o dañada)")
        if p["descartadas"]:
            partes.append(f"{p['descartadas']} descartadas por buffer lleno")
        solo_consumo = not (p["sin_guardar"] or p["descartadas"])
        if p.get("ina"):
            partes.append(f"{p['ina']} ventana(s) de consumo descartadas por cola llena (siguen en su registro local)")
        out.append(_aviso(
            "critico" if p["sin_guardar"] else "aviso",
            f"«{nombre}» ha perdido " + ("datos de consumo que no pudo enviar" if solo_consumo else "fotos que no pudo subir"),
            "En la última semana: " + " y ".join(partes) + ".",
            "Libera espacio en su tarjeta o dale conexión para que vacíe la cola."))

    # 2c) sensores que dan un número que no es una medida (a cero o clavados): fallo F6.
    recientes = sensores_calidad_svc.ventanas_recientes(session, [t.id for t in fijas])
    for trap in fijas:
        malos = sensores_calidad_svc.problemas(trap, recientes.get(trap.id, []))
        if malos:
            out.append(_aviso(
                "aviso", f"Sensor sospechoso en «{trap.alias or trap.device_id}»",
                (lambda s: s[:1].upper() + s[1:])("; ".join(malos)) + ".",
                "Revisa su cableado: un sensor suelto se lee como cero, no como hueco."))

    # 3) batería baja en alguna trampa
    for trap in fijas:
        if trap.bateria is None or trap.owner_id is not None:
            continue
        # Un 0 % con la celda a 0 V es un sensor suelto, no una batería vacía: ya lo dice el
        # aviso de sensor (2c), y un «crítico» falso manda a cargar una batería que está bien.
        # SOLO ese caso exacto: cualquier otro porcentaje bajo conserva su crítico (Codex).
        if (trap.bateria == 0 and trap.voltaje is not None
                and trap.voltaje < sensores_calidad_svc.CELDA_CERO_V):
            continue
        if trap.bateria < 15:
            out.append(_aviso(
                "critico", f"Batería crítica en «{trap.alias or trap.device_id}»",
                # El voltaje puede faltar aunque haya porcentaje: con el f-string a pelo, el
                # aviso decia «12 % (None V)». Un aviso critico con un `None` dentro invita a
                # pensar que el roto es la web, justo cuando hay que hacer caso al aviso.
                (f"{trap.bateria} % ({trap.voltaje} V)." if trap.voltaje is not None
                 else f"{trap.bateria} %."),
                "Se apagará pronto. Conéctala o revisa la carga solar."))
        elif trap.bateria < 30:
            out.append(_aviso(
                "aviso", f"Batería baja en «{trap.alias or trap.device_id}»",
                f"{trap.bateria} %.",
                "Por debajo del 30 % tampoco se instalan actualizaciones OTA."))

    # 3b) fotos retenidas en la tarjeta de una trampa
    # Que la trampa no las pierda es justo lo que hace el buffer local; lo que NO puede pasar es
    # que se queden ahí calladas. Mientras el contador no baje, hay capturas que existen pero no
    # se pueden analizar, y la serie de esa trampa tiene un hueco que no es «no había insectos».
    for trap in fijas:
        pend = trap.buffer_pend or 0
        if pend <= 0 or trap.owner_id is not None:
            continue
        out.append(_aviso(
            "aviso" if pend < 50 else "critico",
            f"{pend} fotos retenidas en «{trap.alias or trap.device_id}»",
            "La trampa las guardó en su tarjeta porque no pudo subirlas: están a salvo, "
            "pero todavía no están aquí.",
            "Se suben solas cuando vuelve la cobertura. Si el contador no baja, revisa el 4G "
            "o acerca la trampa a la WiFi."))

    # 3c/3d) Avisos de plan de datos móviles RETIRADOS el 15/10/2026 por orden de Antonio:
    # comprobó que la trampa no llega al límite mensual y el aviso solo era ruido. El gasto
    # se sigue midiendo (services/consumo.py); lo que se quita es el aviso.

    # 4) imágenes atascadas sin procesar: el worker no está haciendo su trabajo
    pendientes = session.exec(select(func.count()).select_from(Imagen)
                              .where(Imagen.estado == "pendiente")).one()
    if pendientes > 20:
        out.append(_aviso(
            "aviso", f"{pendientes} imágenes esperando análisis",
            "Se acumulan porque el worker de IA no las está procesando.",
            "Arranca el Centro de Control en el PC (pestaña Worker IA)."))

    # 4b) LA PURGA VA A BORRAR FOTOS QUE NUNCA SE HAN CONTADO.
    #
    # La purga borra a los 7 días lo que siga sin procesar. Esa regla existe por una razón buena
    # —el disco se llenó en julio y la ingesta se cayó tres días en silencio ([[G4]])— pero
    # tiene un filo: el worker de IA se abre y se cierra a mano, así que una semana sin abrirlo
    # significa **fotos destruidas sin haberse contado jamás**. Y la purga no avisa: borra.
    #
    # Una foto sin analizar que se borra no deja hueco en la serie: deja un hueco que **nadie
    # sabe que existe**, porque no llegó a ser un dato. Por eso se avisa ANTES, con margen para
    # abrir el worker, en vez de informar después de la pérdida.
    riesgo = 0 if not PURGA_POR_EDAD else session.exec(
        select(func.count()).select_from(Imagen)
        .where(Imagen.estado != "procesada",
               Imagen.capturada < datetime.now(timezone.utc)
               - timedelta(days=PURGA_SIN_PROCESAR_DIAS - PURGA_AVISO_MARGEN_DIAS))).one()
    if riesgo:
        out.append(_aviso(
            "critico",
            f"{riesgo} fotos se van a BORRAR sin haberse analizado",
            f"La purga elimina a los {PURGA_SIN_PROCESAR_DIAS} días lo que siga sin procesar, y "
            f"a estas les quedan menos de {PURGA_AVISO_MARGEN_DIAS} días. Se perderán sin llegar "
            "a ser un dato: no dejan hueco en la serie, dejan un hueco que nadie sabe que existe.",
            "Abre Worker_IA.exe en el PC: en cuanto procese la cola, dejan de estar en riesgo."))

    orden = {"critico": 0, "aviso": 1}
    return sorted(out, key=lambda a: orden.get(a["nivel"], 9))
