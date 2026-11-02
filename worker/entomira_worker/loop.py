"""Bucle de servicio del Worker: procesa imágenes pendientes con YOLO en GPU."""
from __future__ import annotations

import errno
import logging
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

from . import config, infer, telemetry
from .client import ServerClient

log = logging.getLogger("entomira.worker.loop")

# Techo de la espera cuando nada avanza. Cinco minutos: lo justo para no machacar la red ni
# la GPU con una averia persistente, y lo bastante poco para que al arreglarla se note solo.
ESPERA_MAX_S = 300

# UN SOLO WORKER A LA VEZ, Y EL CANDADO VA AQUI POR UNA RAZON.
#
# El 2026-09-19 habia DOS corriendo y el sistema no analizaba nada: los dos clavados a
# 11 MB y 0 s de CPU, bloqueados al inicializar CUDA sobre la misma GPU. Ni un error, ni
# una traza: las fotos se acumulaban en `pendiente` y la purga las borra a los 7 dias sin
# haberlas mirado.
#
# Lo puse en `run_worker_consola.py`, y estaba MAL: las dos interfaces graficas
# construyen `WorkerLoop` a pelo y no pasan por alli. Los dos workers que se pelearon
# aquel dia los lanzo el Centro. O sea: el cerrojo estaba en la unica puerta por la que
# no entra nadie. Hallazgo de Codex, 2026-09-19. Aqui pasan los tres lanzadores.
#
# Se usa un socket y no un fichero con el PID: el sistema lo suelta solo cuando el
# proceso muere, pase lo que pase. Un fichero hay que borrarlo, y el dia que el worker
# se cierre a lo bruto deja un candado que impide arrancar el que si funcionaba.
PUERTO_CANDADO = 47653


class YaHayOtroWorker(Exception):
    """Ya hay un worker con el turno cogido. No es un fallo: es el candado haciendo su trabajo."""


NOMBRE_MUTEX = "Local\\entomira_worker_candado"
NOMBRE_EVENTO = "Local\\entomira_worker_mostrar"


class _Candado:
    """Mutex con nombre de Windows. Lo suelta el sistema en cuanto el proceso muere, pase lo que
    pase, y sin el TIME_WAIT de un puerto TCP: el 18/10/2026 el candado por puerto se quedó
    «ocupado» minutos después de cerrar el worker, sin ningún proceso ni socket visible, y
    cada reapertura fallaba con «puerto ocupado»."""

    def __init__(self, handle) -> None:
        self.handle = handle

    def close(self) -> None:
        if self.handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def tomar_el_turno():
    """Devuelve el candado. Levanta `YaHayOtroWorker` si ya hay otro worker con él."""
    if os.name != "nt":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", PUERTO_CANDADO))
            s.listen(1)
            return s
        except OSError as e:
            s.close()
            if e.errno in (errno.EADDRINUSE, getattr(errno, 'WSAEADDRINUSE', errno.EADDRINUSE)):
                raise YaHayOtroWorker("el puerto %d ya está ocupado" % PUERTO_CANDADO)
            raise
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, True, NOMBRE_MUTEX)
    if not h:
        raise OSError("no se pudo crear el candado (CreateMutexW)")
    if k32.GetLastError() == 183:            # ERROR_ALREADY_EXISTS: ya hay otro worker
        k32.CloseHandle(ctypes.c_void_p(h))
        raise YaHayOtroWorker("ya hay otro worker abierto (está en la bandeja de iconos ocultos)")
    return _Candado(ctypes.c_void_p(h))


def hay_otro_worker() -> bool:
    """¿Existe ya el mutex del candado? (sin cogerlo)."""
    if os.name != "nt":
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenMutexW.restype = ctypes.c_void_p
    h = k32.OpenMutexW(0x00100000, False, NOMBRE_MUTEX)      # SYNCHRONIZE
    if not h:
        return False
    k32.CloseHandle(ctypes.c_void_p(h))
    return True


def pedir_que_se_muestre() -> bool:
    """Desde otro lanzamiento: pide al worker que ya está abierto que enseñe su ventana."""
    if os.name != "nt":
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenEventW.restype = ctypes.c_void_p
    h = k32.OpenEventW(0x0002, False, NOMBRE_EVENTO)      # EVENT_MODIFY_STATE
    if not h:
        return False
    k32.SetEvent(ctypes.c_void_p(h))
    k32.CloseHandle(ctypes.c_void_p(h))
    return True


class ImagenIlegible(Exception):
    """El fichero descargado NO se puede abrir como imagen. Comprobado, no supuesto."""


def _por_que_no_se_puede_leer(ruta) -> str:
    """Devuelve el motivo si el fichero no es una imagen legible, o "" si si lo es.

    Es lo que separa «esta rota» de «ha fallado»: se abre el fichero y se verifica. Contar
    excepciones no distingue un JPEG truncado --lo que deja un corte de corriente a mitad de
    escribir el buffer, que en una trampa a bateria no es hipotetico-- de una GPU sin memoria
    o un servidor que no contesta. Confundirlos significa descartar fotos BUENAS en silencio.
    Aviso de Codex al revisar el arreglo de la cola.
    """
    # PRIMERO SE LEEN LOS BYTES, Y AQUI NO SE JUZGA NADA. Un fichero que no esta, un disco que
    # no responde o un permiso son problemas de ESTA MAQUINA, no de la foto: la excepcion sube
    # y el bucle la trata como infraestructura. Antes se devolvia «no se puede leer del disco»
    # como si fuera un motivo de imagen rota, y eso marcaba `error` en el servidor una foto que
    # podia estar perfecta -- y la dejaba a merced de la purga sin haberse analizado nunca.
    datos = ruta.read_bytes()
    if not datos:
        return "el fichero esta vacio"

    # Y AHORA SE DECODIFICA EN MEMORIA. Ya no hay disco de por medio, asi que lo que falle aqui
    # es del CONTENIDO. Se usa `load()` y no `verify()`: `verify()` mira la cabecera y no
    # decodifica los pixeles, asi que un JPEG cortado DESPUES de la cabecera se le cuela --y
    # ese es justo el que deja un corte de corriente a mitad de escribir el buffer--. Lo
    # senalo Codex, y la prueba lo confirma: con `verify()` ese caso pasaba por bueno.
    try:
        import io as _io
        from PIL import Image
    except ImportError:
        # Sin PIL no se puede afirmar nada, y afirmar sin poder es justo lo que no se hace
        # aqui: se trata como fallo de infraestructura y la imagen se queda en la cola.
        return ""
    try:
        with Image.open(_io.BytesIO(datos)) as im:
            im.load()                        # decodifica de verdad
    except MemoryError:
        raise                                # no es la imagen: es la memoria de esta maquina
    except Exception as e:                   # noqa: BLE001
        return "no se decodifica como imagen (%s)" % type(e).__name__
    return ""


MADRID = "Europe/Madrid"


def franja_pendiente(ahora, horas: list[str], inicio, hechas: set) -> str | None:
    """La franja de análisis que toca atender ahora, o None.

    `ahora` e `inicio` con zona (Europe/Madrid, aunque el PC esté en otro huso). `horas`, las
    HH:MM que da el servidor (cada despertar de campo + 10 min). Solo cuentan las franjas de
    hoy posteriores al arranque (lo anterior lo cubre el análisis inicial) y que no se hayan
    atendido ya. La clave es «fecha HH:MM»: el día del cambio de hora la hora repetida no se
    dispara dos veces. Acordado con Codex, 17/10/2026.
    """
    for h in sorted(horas):
        try:
            hh, mm = map(int, h.split(":"))
        except (ValueError, AttributeError):
            continue
        cuando = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        clave = f"{ahora.date().isoformat()} {h}"
        if inicio < cuando <= ahora and clave not in hechas:
            return clave
    return None


def proxima_franja(ahora, horas: list[str]) -> str | None:
    futuras = [h for h in sorted(horas) if h > ahora.strftime("%H:%M")]
    return futuras[0] if futuras else (sorted(horas)[0] if horas else None)


class WorkerLoop:
    """Poll al servidor → inferencia en GPU → subir resultados. Reconecta solo."""

    def __init__(self, on_status=None, on_llamada=None) -> None:
        self.client = ServerClient()
        self.on_status = on_status or (lambda s: None)
        # Lo llama el candado cuando OTRO lanzamiento llama a la puerta: la app muestra su
        # ventana en vez de que el segundo diga «puerto ocupado» (18/10/2026).
        self.on_llamada = on_llamada or (lambda: None)
        self._running = False
        self._tmp = Path(tempfile.gettempdir()) / "entomira_worker"
        self._tmp.mkdir(exist_ok=True)

    def _status(self, msg: str) -> None:
        log.info(msg)
        self.on_status(msg)

    def stop(self) -> None:
        self._running = False

    TELE_INTERVAL = 15   # s entre envíos de telemetría (independiente del análisis)

    def run_forever(self) -> None:
        # EL CANDADO, LO PRIMERO. Aqui pasan los tres lanzadores --consola, Worker y
        # Centro--, asi que es el unico sitio donde cerrar la puerta las cierra todas.
        try:
            candado = tomar_el_turno()
        except YaHayOtroWorker as e:
            # SE AVISA **Y** SE PROPAGA. Tragarse la excepcion aqui hacia que el lanzador de
            # consola terminara con codigo 0: "NO ARRANCO" por pantalla y exito para quien lo
            # llamara. Un supervisor o un script de arranque leen el codigo, no la pantalla, y
            # se quedan creyendo que hay un worker sano donde no hay ninguno. Hallazgo de Codex
            # revisando este mismo cambio, 2026-09-19.
            self._status("NO ARRANCO: %s. Dos workers a la vez se bloquean entre ellos "
                         "en la GPU y el sistema deja de analizar EN SILENCIO "
                         "(pasó el 2026-09-19)." % e)
            raise
        threading.Thread(target=self._atender_llamadas, daemon=True).start()
        try:
            self._run_forever()
        finally:
            candado.close()

    def _atender_llamadas(self) -> None:
        """Espera el evento con nombre que dispara otro lanzamiento y pide mostrar la ventana."""
        if os.name != "nt":
            return
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = ctypes.c_void_p
        h = k32.CreateEventW(None, False, False, NOMBRE_EVENTO)   # auto-reset
        if not h:
            return
        h = ctypes.c_void_p(h)
        try:
            while self._running or not hasattr(self, "_fallidas"):
                if k32.WaitForSingleObject(h, 1000) == 0:          # WAIT_OBJECT_0
                    try:
                        self.on_llamada()
                    except Exception:                   # noqa: BLE001
                        log.exception("on_llamada")
        finally:
            k32.CloseHandle(h)

    def _run_forever(self) -> None:
        self._running = True
        # Imagenes que han reventado en esta sesion, y CUANTAS VECES.
        #
        # Saltarlas en memoria era una tirita, y ademas dejaba la cola atascada por otro
        # camino: seguian `pendiente` en el servidor, y como `/inference/pending` sirve las 20
        # primeras POR FECHA, con 20 ilegibles a la cabeza el lote entero se saltaba y la 21 no
        # llegaba nunca. Reproducido en `scripts/_repro_cola_worker.py`: 21 pendientes, 20
        # fallidas, **0 procesables**. Ahora, cuando se COMPRUEBA que el fichero no se abre
        # como imagen, se le dice al servidor y sale de la cola. Los fallos que no son de la
        # imagen (red, disco, GPU) se reintentan: bloquear la cola es malo, pero descartar una
        # foto buena es peor, porque esa no vuelve.
        #
        # No hacia falta tocar el esquema: el estado 'error' ya estaba documentado en el
        # modelo (`pendiente|procesada|error`), solo que nadie lo escribia.
        self._fallidas: dict = {}
        # Las que el SERVIDOR ya ha sacado de la cola. Sin esto, un aviso que no llega deja
        # la imagen `pendiente` alli y saltada aqui: con 20 asi, la cola se atasca otra vez.
        self._avisadas: set = set()
        self._sin_avance = 0              # vueltas seguidas sin procesar ni una imagen
        self._params = {"conf": 0.25, "iou": 0.45, "intervalo": config.POLL_SECONDS,
                        "pausado": False}
        self._last_tele = 0.0
        # FRANJAS (17/10/2026): con trampas en campo, el worker solo pide pendientes 10 min
        # después de cada despertar, más una vez nada más arrancar. «arranque» y cada franja se
        # dan por atendidas solo cuando la cola queda VACÍA; si falla la red o la GPU, se sigue
        # reintentando con la espera de siempre.
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        self._zona = ZoneInfo(MADRID)
        self._inicio = _dt.now(self._zona)
        self._franjas_hechas: set = set()
        self._franja_abierta: str | None = "arranque"
        self._status("Worker iniciado. Buscando servidor…")
        while self._running:
            try:
                if not self.client.ping():
                    self._status(f"Servidor no disponible; reintento…")
                    time.sleep(self._params["intervalo"])
                    continue

                # telemetría + parámetros: solo cada TELE_INTERVAL s (no en cada ciclo)
                if time.time() - self._last_tele >= self.TELE_INTERVAL:
                    self._last_tele = time.time()
                    try:
                        self.client.push_stats(telemetry.collect())
                        p = self.client.get_params()
                        if p:
                            self._params.update(p)
                    except Exception:
                        pass

                if self._params.get("pausado"):
                    self._status("Análisis pausado desde el servidor.")
                    time.sleep(self._params["intervalo"])
                    continue

                horas = self._params.get("horas_analisis") or []
                if horas and self._franja_abierta is None:
                    from datetime import datetime as _dt
                    ahora = _dt.now(self._zona)
                    self._franja_abierta = franja_pendiente(ahora, horas, self._inicio,
                                                            self._franjas_hechas)
                    if self._franja_abierta is None:
                        self._status(f"En línea. Próximo análisis a las "
                                     f"{proxima_franja(ahora, horas)} (10 min tras el despertar "
                                     f"de la trampa); lo que llegue antes espera hasta entonces.")
                        time.sleep(self._params["intervalo"])
                        continue

                pending = self.client.pending_images()
                if not pending:
                    if self._franja_abierta is not None:
                        self._franjas_hechas.add(self._franja_abierta)
                        if self._franja_abierta != "arranque":
                            self._status(f"Franja {self._franja_abierta[-5:]} atendida: cola vacía.")
                        self._franja_abierta = None
                        if horas:
                            continue              # vuelve a mirar la franja siguiente sin esperar
                    self._status(f"En línea ({self.client.active_base}). Sin pendientes.")
                    time.sleep(self._params["intervalo"])
                    continue

                self._status(f"{len(pending)} imagen(es) pendientes; procesando…")
                progreso = False          # ¿ha avanzado algo en esta vuelta?
                infra = 0                 # fallos que NO son de la imagen
                for item in pending:
                    # EL TRY VA AQUI, POR IMAGEN, Y NO SOLO EN EL CICLO.
                    #
                    # Con el `except` unicamente en el bucle exterior, una sola imagen que
                    # reventara cortaba el lote ENTERO y, como `pending_images()` las sirve
                    # ordenadas por fecha, esa misma volvia a ser la primera en el ciclo
                    # siguiente: bucle infinito, **nada** se procesaba nunca mas y el worker
                    # segui­a pareciendo sano. Medido el 2026-09-19: un JPEG truncado hace que
                    # `infer_image` lance `IndexError: list index out of range`, y un JPEG
                    # truncado es lo que deja un corte de corriente a mitad de escribir el
                    # buffer -- en una trampa a bateria, eso no es hipotetico.
                    ident = item.get("image_id") or item.get("id")
                    if ident in self._avisadas:
                        continue                      # el servidor ya la saco de la cola
                    if self._fallidas.get(ident) == "ilegible":
                        # Se sabe que esta rota, pero el aviso no llego. Lo unico pendiente es
                        # avisar; no se vuelve a intentar procesarla.
                        if self.client.marcar_ilegible(ident):
                            self._avisadas.add(ident)
                            self._status(f"Imagen {ident}: aviso de ilegible entregado")
                        continue
                    try:
                        self._process(item)
                        progreso = True
                        self._fallidas.pop(ident, None)     # salio bien: se olvida el fallo
                    except ImagenIlegible as e:
                        # ILEGIBLE DE VERDAD: el fichero descargado no se puede abrir como
                        # imagen. No es «ha fallado tres veces», es que se ha MIRADO y esta
                        # roto. La distincion la pidio Codex y tiene razon: contar excepciones
                        # no separa un JPEG truncado de un corte de red que dura un rato, y
                        # con esa confusion se tiraban imagenes BUENAS en silencio, que es el
                        # fallo mas caro que tiene este proyecto.
                        if self.client.marcar_ilegible(ident):
                            self._avisadas.add(ident)
                            self._status(f"Imagen {ident} ILEGIBLE ({e}): marcada como error "
                                         f"en el servidor y fuera de la cola")
                        else:
                            # El aviso se reintenta en la siguiente vuelta. Darla por saltada
                            # aqui la dejaria `pendiente` en el servidor para siempre, y con
                            # 20 asi la cola se vuelve a atascar. Aviso de Codex.
                            self._fallidas[ident] = "ilegible"
                            self._status(f"Imagen {ident} ILEGIBLE ({e}): NO se pudo avisar al "
                                         f"servidor; se reintentara el aviso")
                    except Exception as e:            # noqa: BLE001
                        # Fallo de INFRAESTRUCTURA (red, disco, GPU, modelo). La imagen sigue
                        # `pendiente` y se reintenta: puede estar perfecta. Bloquear la cola es
                        # malo, pero descartar una foto buena es peor -- esa no vuelve.
                        n = (self._fallidas.get(ident) or 0)
                        n = n + 1 if isinstance(n, int) else 1
                        self._fallidas[ident] = n
                        infra += 1
                        self._status(f"Imagen {ident}: fallo {type(e).__name__} (intento {n}). "
                                     f"No se descarta: se reintentara.")
                # PAUSA CUANDO NO SE AVANZA. El `except` de cada imagen se traga la excepcion,
                # asi que no se llegaba al descanso del manejador de abajo: con una GPU caida y
                # la cola llena, el worker se quedaba descargando y reintentando en bucle
                # cerrado, gastando red y disco sin adelantar nada. La espera crece con las
                # vueltas seguidas sin progreso, hasta un techo. Hallazgo de Codex.
                if progreso:
                    self._sin_avance = 0
                elif infra:
                    self._sin_avance = min(self._sin_avance + 1, 6)
                    espera = min(self._params.get("intervalo", config.POLL_SECONDS)
                                 * (2 ** (self._sin_avance - 1)), ESPERA_MAX_S)
                    self._status(f"{infra} fallo(s) que no son de la imagen y ninguna ha "
                                 f"avanzado: esperando {espera:.0f} s antes de reintentar.")
                    time.sleep(espera)
            except Exception as e:  # nunca morir por un fallo puntual
                self._status(f"Error en el ciclo: {e}. Reintentando…")
                time.sleep(self._params.get("intervalo", config.POLL_SECONDS))

    def _process(self, item: dict) -> None:
        image_id = item.get("image_id") or item.get("id")
        dest = self._tmp / f"{image_id}.jpg"
        if not self.client.download_image(image_id, str(dest)):
            self._status(f"No se pudo descargar la imagen {image_id}")
            return
        p = getattr(self, "_params", {})
        try:
            result = infer.infer_image(str(dest), conf=p.get("conf", 0.25),
                                       iou=p.get("iou", 0.45))
        except Exception as e:                        # noqa: BLE001
            # AQUI SE MIRA EL FICHERO, no se cuenta cuantas veces ha fallado. Si no se puede
            # decodificar, esta rota y se puede afirmar; si se decodifica bien, el problema es
            # de otra cosa (GPU, modelo, red) y la imagen no tiene la culpa.
            try:
                motivo = _por_que_no_se_puede_leer(dest)
            except Exception:                         # noqa: BLE001
                # NO SE HA PODIDO COMPROBAR (el temporal ya no esta, el disco falla, no hay
                # memoria). No saber no es saber que esta rota: se trata como infraestructura y
                # la foto se queda en la cola. Aviso de Codex.
                motivo = ""
            if motivo:
                raise ImagenIlegible(motivo) from e
            raise
        if self.client.submit_result(image_id, result):
            self._status(f"Imagen {image_id}: {result['num_insectos']} insectos ✓")
        else:
            self._status(f"No se pudo subir el resultado de {image_id}")
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
