"""
Relay de streaming en vivo: el servidor hace de proxy del MJPEG de la PCB.

La PCB expone un flujo MJPEG (esp32-camera) en http://<ip_trampa>:81/stream cuando el
servidor se lo pide (FASE 3). Aquí el servidor lo reenvía al navegador para que Antonio
vea lo que ve la cámara con la configuración vigente. Sin firmware de streaming todavía,
responde con un mensaje claro y el frontend muestra un aviso.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlmodel import Session, select

import io
import time

from PIL import Image as PILImage

from ..models.models import ConfigCamara, Imagen, Trampa
from ..core.db import sesion_corta
from ..core.red import ip_de_camara
from ..core.deps import current_user, is_farmer
from ..services import live as live_svc
from ..services.images import wb_fix


def _trap_or_404(session: Session, request: Request, trap_id: int) -> Trampa:
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    user = current_user(request)
    if is_farmer(user) and trap.owner_id != user["id"]:
        raise HTTPException(404, "Trampa no encontrada")
    return trap


def _ficha_para_el_relay(request: Request, trap_id: int) -> tuple[int, str | None]:
    """Comprueba permisos y devuelve SOLO los dos datos sueltos que el relay necesita.

    Existe para que `stream()` **no llegue a tener un objeto `Trampa` en su ambito**. El relay
    cierra la sesion y luego vive minutos; cualquier atributo ORM que sobreviva a ese cierre es
    un `DetachedInstanceError` esperando a que alguien lo toque. En `telemetry.py` eso costo
    111 respuestas 500 en un dia (2026-09-14), y el aviso estaba escrito en un comentario que
    yo mismo me salte cuatro lineas despues.

    Un comentario no impide nada. Un ambito, si.
    """
    with sesion_corta() as session:
        trap = _trap_or_404(session, request, trap_id)
        cfgm = session.get(ConfigCamara, trap.id)
        if cfgm and cfgm.deep_sleep:
            live_svc.parar_vivo(trap.id)
            raise HTTPException(409, "Modo invernadero: vídeo desactivado para permitir el corte")
        live_svc.pedir_vivo(trap.id)
        return trap.id, ip_de_camara(trap.ip)     # nunca una IP que no sea de LAN (SSRF)


def _correct(jpeg_bytes: bytes) -> bytes:
    """Aplica la corrección de color (magenta a alta resolución) a un JPEG."""
    try:
        im = wb_fix(PILImage.open(io.BytesIO(jpeg_bytes)).convert("RGB"))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()
    except Exception:
        return jpeg_bytes

router = APIRouter(prefix="/api/v1", tags=["streaming"])

DEVICE_STREAM_PORT = 81
DEVICE_STREAM_PATH = "/stream"


@router.get("/traps/{trap_id}/stream")
async def stream(trap_id: int, request: Request):
    # Permisos y datos, en una funcion aparte: al volver de ella no hay ningun objeto de la
    # base en este ambito, solo un entero y una cadena. Ver `_ficha_para_el_relay`.
    trap_pk, trap_ip = _ficha_para_el_relay(request, trap_id)

    # NO HAY SESION QUE SOLTAR, y eso es el arreglo.
    #
    # Un MJPEG es una respuesta que dura **minutos**. Con `Depends(get_session)` la sesion
    # --y con ella una conexion del pool-- vivia hasta que la respuesta terminaba: **cada
    # panel de video abierto se quedaba con una de las 15**. Con 15 flujos, o con conexiones
    # huerfanas que el servidor tarda en detectar (por el funnel pasa), el servidor deja de
    # responder TODO: la web, la telemetria que le dice a la trampa que emita, y el propio
    # video. Es el mismo fallo que mato la API en el ensayo de 20 trampas, pero aqui lo
    # dispara simplemente mirar.
    #
    # Ahora la sesion nace y muere dentro de `_ficha_para_el_relay`, asi que aqui abajo no
    # hay ni sesion que cerrar ni fila que tocar: solo `trap_pk` y `trap_ip`.

    # Relay directo: solo si la trampa está en ESTA red y contesta ya. Se comprueba con un
    # sondeo corto en vez de fiarse de `trap.ip`, porque una IP de otra red se queda guardada
    # en la ficha y el usuario acabaría esperando un timeout para nada.
    directo = False
    if trap_ip and live_svc.edad_frame(trap_pk) is None:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"http://{trap_ip}:{DEVICE_STREAM_PORT}/snapshot")
            directo = r.status_code == 200
        except httpx.HTTPError:
            directo = False

    # Se anota la via ELEGIDA, para que el navegador sepa si `edad_frame` mide algo. Por el
    # relay directo los bytes no pasan por `_frames`, asi que ese dato se queda viejo aunque
    # el video vaya perfecto, y el visor acababa diciendo «sin senal reciente» con la imagen
    # cambiando delante. Viaja en la respuesta de `/live-ping`.
    live_svc.marcar_via(trap_pk, "directo" if directo else "empuje")

    if not directo:
        # Vía de empuje: la única que funciona con la trampa en otra red. Si todavía no hay
        # fotogramas, NO se falla: se espera a que lleguen (la petición de vídeo ya está
        # hecha arriba y la trampa la recoge en su long polling). Así abrir el panel arranca
        # el vídeo solo, sin que el usuario tenga que reintentar.
        async def relay_push():
            ultimo = 0.0
            primera = True
            # ANTES: `fin = time.time() + 600`, y a los 10 minutos el vídeo se cortaba en seco
            # aunque hubiera alguien delante mirando. El tope existía para no dejar la cámara
            # emitiendo por una conexión huérfana, pero eso ya lo resuelve mejor `quiere_vivo`:
            # la ventana la renueva **el cliente** con `/live-ping`, así que si nadie mira se
            # apaga sola en 25 s. Cortar por reloj castigaba justo al que sí estaba mirando.
            while True:
                if await request.is_disconnected():
                    break
                if not primera and not live_svc.quiere_vivo(trap_pk):
                    break                     # nadie renueva: se acabó, sin esperar 10 min
                # OJO: aqui NO se renueva la peticion de video. Se hacia, y una conexion
                # huerfana (un navegador cerrado que el servidor no detecta) la renovaba
                # eternamente: la trampa transmitia a 2,3 fps con NADIE mirando, vaciando la
                # bateria. Ahora quien renueva es el cliente, con /live-ping. Ver live.py.
                # margen amplio en el primer fotograma: la trampa tarda hasta una ventana de
                # long polling (~20 s) en enterarse de que alguien está mirando.
                dato = await live_svc.esperar_frame(
                    trap_pk, ultimo, espera_s=30.0 if primera else 4.0)
                if not dato:
                    if primera:
                        break                 # ni con 30 s: que el cliente pase al fallback
                    # La trampa hace pausas legítimas (sube su foto de turno, reconecta…).
                    # Se reenvía el último fotograma para que el flujo no se quede mudo: un
                    # MJPEG sin datos lo da por caído el navegador y se pierde la vista.
                    jpeg = live_svc.ultimo_frame(trap_pk, max_edad=120)
                    if not jpeg:
                        break
                else:
                    primera = False
                    jpeg, ultimo = dato
                # La via se renueva MIENTRAS PASAN BYTES, no solo al abrir. Marcarla una vez
                # la hacia caducar a los 120 s con el video yendo perfectamente, y el visor
                # volvia a juzgar por `edad_frame`, que en la via directa no mide nada.
                live_svc.marcar_via(trap_pk, "empuje")
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                       + jpeg + b"\r\n")

        return StreamingResponse(
            relay_push(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Entomira-Fuente": "push"})

    upstream = f"http://{trap_ip}:{DEVICE_STREAM_PORT}{DEVICE_STREAM_PATH}"

    async def relay():
        # timeout de conexión corto; lectura sin límite (flujo continuo)
        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
        try:
            async with client.stream("GET", upstream) as resp:
                async for chunk in resp.aiter_raw():
                    # Igual que en la vía de empuje: la marca se renueva mientras corran los
                    # bytes. Aquí importa el doble, porque por esta vía `edad_frame` no se
                    # rellena nunca y la marca es lo ÚNICO que impide que el visor declare
                    # «sin señal reciente» sobre un vídeo que va bien.
                    live_svc.marcar_via(trap_pk, "directo")
                    yield chunk
        except (httpx.HTTPError, GeneratorExit):
            return
        finally:
            await client.aclose()

    return StreamingResponse(
        relay(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/traps/{trap_id}/snapshot")
async def snapshot(trap_id: int, request: Request):
    """Foto en vivo de la cámara (vista por fotogramas; funciona por el funnel).

    Si la cámara es alcanzable por WiFi → foto en directo. Si no (p. ej. tethered por USB
    con WiFi caída) → la última imagen real que subió la trampa. Así la vista en vivo
    funciona siempre.

    **Sin sesión de base de datos en el ámbito**, como el resto de los endpoints que esperan:
    aquí la espera es la llamada HTTP a la trampa, que puede costar los 6 s enteros del timeout
    cuando la IP guardada es de otra red (lo normal con la trampa por 4G). Retener una de las
    15 conexiones del pool durante 6 s por cada visita es la misma aritmética que dejó la API
    sin servicio con 16 trampas.
    """
    # Permisos y datos, en un bloque corto. De aquí salen dos valores, no una fila.
    trap_pk, trap_ip = _ficha_para_el_relay(request, trap_id)

    # 1) fotograma que la propia trampa acaba de empujar. Va primero porque es el único
    #    camino que funciona cuando la trampa está en OTRA red (detrás de un NAT): ahí la
    #    conexión directa de abajo no tiene a dónde llamar. Ver services/live.py.
    frame = live_svc.ultimo_frame(trap_pk)
    if frame:
        return Response(content=frame, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Entomira-Fuente": "push",
                                 "X-Entomira-Edad-Seg":
                                     f"{live_svc.edad_frame(trap_pk) or 0:.1f}"})

    # 2) foto en directo por WiFi, si la trampa está en la misma red (corregida de color).
    #    Hasta 6 s, y SIN conexión del pool agarrada.
    if trap_ip:
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get(f"http://{trap_ip}:{DEVICE_STREAM_PORT}/snapshot")
            if r.status_code == 200 and r.content:
                return Response(content=_correct(r.content), media_type="image/jpeg",
                                headers={"Cache-Control": "no-store",
                                         "X-Entomira-Fuente": "directo",
                                         "X-Entomira-Edad-Seg": "0.0"})
        except httpx.HTTPError:
            pass

    # 3) fallback: última imagen subida (por USB o WiFi)
    #
    # Esta imagen NO es vídeo: puede tener horas. Salía con un 200 idéntico al de los otros dos
    # caminos y sin ninguna marca, así que quien estaba encuadrando la trampa creía estar viendo
    # la cámara mientras miraba una foto vieja. Se mantiene el fallback —es útil— pero diciendo
    # lo que es y cuántos segundos tiene, y la web lo pinta encima de la imagen.
    # Otro bloque corto: de la base salen la ruta y la fecha, no la fila.
    with sesion_corta() as session:
        last = session.exec(
            select(Imagen).where(Imagen.trampa_id == trap_id)
            .order_by(Imagen.capturada.desc()).limit(1)
        ).first()
        ruta = last.ruta_fichero if last else None
        cap = last.capturada if last else None

    if ruta and Path(ruta).exists():
        if cap is not None and cap.tzinfo is None:
            cap = cap.replace(tzinfo=timezone.utc)
        edad = (datetime.now(timezone.utc) - cap).total_seconds() if cap else 0.0
        return FileResponse(ruta, media_type="image/jpeg",
                            headers={"Cache-Control": "no-store",
                                     "X-Entomira-Fuente": "archivo",
                                     "X-Entomira-Edad-Seg": f"{edad:.0f}"})
    raise HTTPException(503, "Sin imagen disponible de esta trampa todavía.")
