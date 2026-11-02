"""Contador de datos móviles y telemetría fusionada con el long polling de vídeo.

Las dos cosas van juntas en este fichero porque son la misma historia: por 4G lo caro no son
los bytes del JSON sino **abrir la conexión**, y la fusión existe precisamente para abrir
menos. El contador es lo que permite comprobarlo con un número en vez de con una intuición.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _trap_id(cliente, dev="esp32-CONSUMO01"):
    return cliente.get("/api/v1/traps", auth=ADMIN).json()[0]["id"]


def _alta(cliente, jpeg, dev="esp32-CONSUMO01"):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": dev})
    assert r.status_code == 201
    return r.json()["trap_id"]


def test_por_wifi_no_se_cuenta_nada(cliente, jpeg):
    """Por WiFi o por cable no se gasta plan: contarlo sería mentir sobre la factura."""
    tid = _alta(cliente, jpeg, "esp32-WIFI01")
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-WIFI01", "red": "wifi"})
    _alta(cliente, jpeg, "esp32-WIFI01")

    c = cliente.get(f"/api/v1/traps/{tid}/consumo", auth=ADMIN).json()
    assert c["bytes"] == 0 and c["conexiones"] == 0


def test_por_4G_se_cuenta_la_foto_y_la_telemetria(cliente, jpeg):
    tid = _alta(cliente, jpeg, "esp32-LTE01")
    # La trampa declara que viene por red móvil: a partir de aquí, todo cuesta plan.
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-LTE01", "red": "lte"})
    _alta(cliente, jpeg, "esp32-LTE01")

    c = cliente.get(f"/api/v1/traps/{tid}/consumo", auth=ADMIN).json()
    assert "telemetria" in c["por_concepto"] and "ingesta" in c["por_concepto"]
    # La foto pesa más que la telemetría, pero la telemetría cuesta su conexión igual.
    assert c["por_concepto"]["ingesta"]["bytes"] > c["por_concepto"]["telemetria"]["bytes"]
    assert c["conexiones"] >= 2

    # El estimado SUMA el saludo TLS de cada conexión, que con peticiones pequeñas es la mayor
    # parte del gasto. Medido y estimado se devuelven por separado, a propósito.
    assert c["estimado_b"] == c["bytes"] + c["conexiones"] * c["coste_conexion_b"]
    assert c["estimado_b"] > c["bytes"]


def test_la_ficha_de_la_trampa_lleva_el_gasto(cliente, jpeg):
    tid = _alta(cliente, jpeg, "esp32-LTE02")
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-LTE02", "red": "lte"})
    t = cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()
    assert t["datos_mes_b"] is not None and t["datos_mes_b"] > 0


def test_la_telemetria_hace_tambien_de_long_polling(cliente, jpeg):
    """Una sola petición para las dos cosas.

    Antes eran dos conexiones frecuentes y distintas. Ahora la telemetría se retiene y contesta
    en cuanto alguien abre la vista en vivo, así que el vídeo arranca igual de rápido pero la
    trampa abre la mitad de conexiones.
    """
    tid = _alta(cliente, jpeg, "esp32-LTE03")
    from app.services import live as live_svc
    live_svc.parar_vivo(tid)

    d = cliente.post("/api/v1/telemetry?espera=0", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-LTE03", "red": "lte"}).json()
    assert d["en_vivo"] is False
    # El servidor dice cuánto retener la próxima: se afina sin reflashear ninguna trampa.
    assert d["espera"] > 0

    live_svc.pedir_vivo(tid)                 # alguien abre el panel
    d = cliente.post("/api/v1/telemetry?espera=5", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-LTE03", "red": "lte"}).json()
    assert d["en_vivo"] is True, "con alguien mirando tiene que contestar YA, no esperar"


def test_corte_entregado_tras_long_polling_lleva_config_actual(cliente, jpeg, monkeypatch):
    """Un cambio a campo mientras la respuesta espera no debe llegar tras el corte."""
    import time
    from sqlmodel import Session
    from app.core.db import engine
    from app.models.models import ConfigCamara
    from app.services import live as live_svc

    tid = _alta(cliente, jpeg, "zero-CFGCORTE01")
    anterior = cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()["config"]["version"]
    objetivo = int(time.time()) + 3600

    async def cambiar_y_pedir_corte(trap_id, espera):
        assert trap_id == tid and espera > 0
        with Session(engine) as session:
            cfg = session.get(ConfigCamara, tid)
            cfg.deep_sleep = 1
            cfg.hora_despertar = "20:30"
            cfg.version += 1
            session.add(cfg)
            session.commit()
        live_svc.pedir_reposo(tid, 3600, exigir_apagado=True, target=objetivo)
        return False

    monkeypatch.setattr(live_svc, "esperar_peticion", cambiar_y_pedir_corte)
    r = cliente.post("/api/v1/telemetry?espera=5", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "zero-CFGCORTE01", "red": "lte"})
    assert r.status_code == 200
    d = r.json()
    assert d["reposo"]["target"] == objetivo
    assert d["cfg"] == anterior + 1
    assert d["espera"] == 0
    assert d["en_vivo"] is False


def test_la_retencion_marca_el_ritmo(cliente, jpeg):
    """Si se retiene, `cada` va a 0: la trampa vuelve a llamar en cuanto le contesten.

    Si no, retencion y cadencia se SUMAN: con 120 s de retencion y 600 s de cadencia la trampa
    hablaba cada 12 minutos y quedaba una **ventana ciega de 10 minutos** en la que nadie podia
    pedirle video — justo lo contrario de para lo que se fusionaron las dos peticiones.
    """
    tid = _alta(cliente, jpeg, "esp32-RITMO01")
    from app.services import live as live_svc
    live_svc.parar_vivo(tid)

    d = cliente.post("/api/v1/telemetry?espera=1", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-RITMO01", "red": "lte"}).json()
    assert d["espera"] > 0
    assert d["cada"] == 0, "con retencion, el ritmo lo marca la retencion"


def test_en_modo_campo_no_se_retiene_la_peticion(cliente, jpeg):
    """Con `deep_sleep`, retencion CERO.

    La trampa de campo se despierta, captura, sube y se duerme en `ventana_despierto_seg`
    (15 s). Retenerle la respuesta 120 s la tendria despierta ocho veces mas de lo previsto, y
    la autonomia del TFM esta calculada sobre esa ventana. La retencion es para la trampa que
    ya esta despierta de todas formas.
    """
    from app.models.models import Trampa
    from app.core.db import engine
    from app.services import traps as traps_svc
    from sqlmodel import Session as S

    tid = _alta(cliente, jpeg, "esp32-CAMPO01")
    with S(engine) as ses:
        t = ses.get(Trampa, tid)
        t.red = "lte"
        ses.add(t); ses.commit()
        cfg = traps_svc.ensure_config(ses, t)
        assert traps_svc.espera_seg(t, cfg) > 0, "despierta: si se retiene"
        cfg.deep_sleep = 1
        ses.add(cfg); ses.commit()
        assert traps_svc.espera_seg(t, cfg) == 0, "modo campo: NO se retiene"

    d = cliente.post("/api/v1/telemetry?espera=120", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-CAMPO01", "red": "lte"}).json()
    assert d["espera"] == 0


def test_una_trampa_retenida_no_sale_offline(cliente, jpeg):
    """Mientras el servidor aguanta su petición, la trampa no habla — y eso no es estar caída."""
    from app.services import traps as traps_svc
    from app.models.models import Trampa
    from app.core.db import engine
    from sqlmodel import Session as S

    tid = _alta(cliente, jpeg, "esp32-LTE04")
    with S(engine) as ses:
        t = ses.get(Trampa, tid)
        t.red = "lte"
        ses.add(t); ses.commit()
        cfg = traps_svc.ensure_config(ses, t)
        # El margen tiene que cubrir la ventana entera: cadencia + lo que dura la retención.
        assert traps_svc.grace_seconds(cfg, t) > traps_svc.espera_seg(t)


def test_la_ota_se_cuenta_y_es_lo_mas_caro_que_baja_una_trampa(cliente, jpeg):
    """Una actualización son ~1 MB por 4G. Si no se cuenta, el contador miente en lo que importa.

    Encontrado el 2026-09-12 **mirando la tabla después de una OTA real**: el servidor ya sabía
    contarla —lee `?device_id=` y el `Range`—, pero **el firmware no mandaba el parámetro**, así
    que `device_id` llegaba vacío y el concepto `ota` no aparecía nunca. Un megabyte invisible al
    lado de una telemetría de 150 bytes que sí se contaba.

    Es el mismo fallo, por quinta vez, que las conexiones de vídeo: **el sitio donde se cuenta
    estaba bien y el sitio que tenía que avisar no avisaba**. Ninguna de las dos mitades daba
    error por su cuenta.
    """
    tid = _alta(cliente, jpeg, "esp32-OTA01")
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-OTA01", "red": "lte"})

    datos = b"\x00" * 200_000
    firma = "3045022100" + "ab" * 30
    assert cliente.post("/api/v1/firmware", auth=ADMIN,
                        files={"fichero": ("firmware.bin", datos, "application/octet-stream")},
                        data={"version": "9.9-ota", "notas": "", "firma": firma}
                        ).status_code == 200

    # La trampa la baja por trozos con `Range`, como hace de verdad (24 KB cada uno).
    for desde in range(0, 3 * 24 * 1024, 24 * 1024):
        hasta = desde + 24 * 1024 - 1
        r = cliente.get("/api/v1/firmware/bin?device_id=esp32-OTA01",
                        headers={"X-API-Key": DEVICE_KEY,
                                 "Range": f"bytes={desde}-{hasta}"})
        assert r.status_code in (200, 206)

    c = cliente.get(f"/api/v1/traps/{tid}/consumo", auth=ADMIN).json()
    assert "ota" in c["por_concepto"], "la descarga de firmware NO se está contando"
    # Tres trozos de 24 KB: lo que dice el `Range`, no el fichero entero ni cero.
    assert c["por_concepto"]["ota"]["bytes"] == 3 * 24 * 1024
    # Y pesa más que todo lo demás junto, que es justo por lo que hay que contarla.
    otros = sum(v["bytes"] for k, v in c["por_concepto"].items() if k != "ota")
    assert c["por_concepto"]["ota"]["bytes"] > otros


def test_la_retencion_no_se_queda_con_la_conexion_de_la_base_de_datos(cliente, jpeg):
    """Una petición retenida NO puede seguir agarrando una conexión del pool mientras espera.

    **Esto tumbó el servidor entero el 2026-09-12.** En el ensayo con 20 trampas ficticias, 20
    retenciones simultáneas dejaron la API sin responder —ni `/health`, ni la web— y hubo que
    reiniciar el servicio a mano. El servicio seguía `active`; el log simplemente dejaba de
    escribir.

    La causa: `Depends(get_session)` mantiene la sesión viva durante toda la petición, y una
    retención dura hasta 120 s. El pool son 5 conexiones + 10 de desbordamiento = **15**. O sea
    que **la trampa nº 16 dejaba sin conexión a todo lo demás**, porque cualquier petición de
    cualquier endpoint sale del mismo pool.

    Un fallo que con una trampa no existe y con veinte lo tira todo: exactamente la razón de
    ser del ensayo (hallazgo D3 de la mentoría).
    """
    from app.core.db import engine

    tid = _alta(cliente, jpeg, "esp32-POOL01")
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-POOL01", "red": "lte"})

    antes = engine.pool.checkedout()
    r = cliente.post("/api/v1/telemetry?espera=1", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-POOL01", "red": "lte"})
    assert r.status_code == 200
    despues = engine.pool.checkedout()
    assert despues <= antes, "la petición retenida no devolvió su conexión al pool"

    # Y el dato tiene que seguir llegando entero: soltar la sesión no puede costar campos.
    d = r.json()
    for clave in ("ok", "trap_id", "fw", "cfg", "cada", "espera", "en_vivo", "foto"):
        assert clave in d, f"falta «{clave}» tras cerrar la sesión: la placa lo lee"
    assert d["trap_id"] == tid


def test_en_linea_y_estado_no_pueden_contradecirse(cliente, jpeg):
    """«En línea» se calcula UNA vez, en el servidor. La web no puede tener su propia regla.

    **Esto rompía el vídeo en directo.** La web decidía «en línea» con `180000` ms escritos a
    pelo —herencia de cuando la telemetría iba cada 10 s— mientras el servidor usaba
    `grace_seconds()` = (cadencia + retención) × 2 + 60, que con la config de campo son 1500 s.

    Entre esos dos números la misma tarjeta pintaba **«En línea» y «sin conexión» a la vez**, y
    el panel de control —que usa esa condición para decidir si pide vídeo— **cortaba el directo
    solo**: la trampa habla cada ~2 min, justo en el filo de los 180 s.
    """
    from app.models.models import Trampa
    from app.core.db import engine
    from app.services import traps as traps_svc
    from app.models.models import Invernadero
    from sqlmodel import Session as S, select

    tid = _alta(cliente, jpeg, "esp32-LINEA01")
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-LINEA01", "red": "lte"})

    t = cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()
    assert "en_linea" in t, "la web necesita este campo; sin él vuelve a inventarse la regla"
    assert t["en_linea"] is True
    # Y aquí está justo la razón de que sean DOS campos: esta trampa no tiene invernadero, así
    # que `estado` vale «sin_asignar» —correcto para la etiqueta— mientras `en_linea` dice que
    # está viva. Si la web mirase solo `estado` no le pediría vídeo a una trampa recién puesta,
    # que es exactamente el momento en que más falta hace el directo.
    assert t["estado"] == "sin_asignar"

    with S(engine) as ses:
        trap = ses.get(Trampa, tid)
        trap.red = "lte"
        ses.add(trap); ses.commit()
        cfg = traps_svc.ensure_config(ses, trap)
        # El margen tiene que ser MUY superior a los 3 minutos que usaba la web: con cadencia
        # de 4G son (600 + 120) x 2 + 60 = 1500 s. Si alguien lo baja a 180, el directo vuelve
        # a cortarse y nadie relacionará una cosa con la otra.
        assert traps_svc.grace_seconds(cfg, trap) > 600, (
            "con un margen corto, una trampa de 4G sale «sin conexión» entre informe e informe")
        # Con invernadero asignado, las dos tienen que decir lo mismo: si no, vuelve la
        # contradicción de «En línea» y «sin conexión» en la misma tarjeta.
        gh = ses.exec(select(Invernadero)).first()
        if gh is None:                       # el fixture no siempre trae invernaderos
            gh = Invernadero(nombre="GH en_linea")
            ses.add(gh); ses.commit(); ses.refresh(gh)
        trap.invernadero_id = gh.id
        ses.add(trap); ses.commit()
        assert traps_svc.en_linea(trap, cfg) is True
        assert traps_svc.computed_estado(trap, cfg) == "online"


def test_ver_video_no_puede_secar_el_pool_de_conexiones(cliente, jpeg):
    """Un MJPEG dura minutos; no puede quedarse con una conexión de la BD todo ese rato.

    **Esto es por lo que «el directo falla bastante».** `Depends(get_session)` no suelta la
    sesión hasta que la respuesta termina, y una respuesta de vídeo dura hasta que el que mira
    cierra. O sea que **cada panel de vídeo abierto se llevaba una conexión del pool**, que son
    5 + 10 = **15**.

    Con unos cuantos flujos abiertos —o con conexiones huérfanas, que por el funnel de Tailscale
    tardan en detectarse— el servidor se queda sin conexiones y **deja de responder todo**: la
    web, la telemetría que le dice a la trampa que emita, y el propio vídeo. El mismo fallo que
    mató la API en el ensayo de 20 trampas, pero aquí lo dispara simplemente mirar.
    """
    from app.core.db import engine

    tid = _alta(cliente, jpeg, "esp32-STREAM01")

    antes = engine.pool.checkedout()
    # Se pide el flujo y se cierra enseguida: lo que importa es que la conexión vuelva.
    with cliente.stream("GET", f"/api/v1/traps/{tid}/stream", auth=ADMIN) as r:
        assert r.status_code == 200
        assert "multipart/x-mixed-replace" in r.headers.get("content-type", "")
    despues = engine.pool.checkedout()
    assert despues <= antes, "el flujo de vídeo no devolvió su conexión a la base de datos"


def test_avisa_antes_de_borrar_fotos_sin_analizar(cliente, jpeg, monkeypatch):
    """La purga borra a los 7 días lo que siga sin procesar. Eso tiene que avisarse ANTES.

    La regla existe por una razón buena —el disco se llenó en julio y la ingesta se cayó tres
    días en silencio— pero tiene un filo: el worker de IA **se abre y se cierra a mano**, así
    que una semana sin abrirlo significa fotos **destruidas sin haberse contado jamás**.

    Y una foto sin analizar que se borra no deja un hueco en la serie: deja un hueco que **nadie
    sabe que existe**, porque nunca llegó a ser un dato. Por eso el aviso va antes de la pérdida
    y no después.
    """
    from datetime import datetime, timedelta, timezone
    from app.models.models import Imagen, Trampa
    from app.core.db import engine
    from app.services import estado as estado_svc
    # La purga por edad está apagada desde el 2026-10-23; la regla del aviso se conserva por si
    # se vuelve a encender, y se prueba encendida.
    monkeypatch.setattr(estado_svc, "PURGA_POR_EDAD", True)
    from sqlalchemy import func
    from sqlmodel import Session as S, select

    _alta(cliente, jpeg, "esp32-PURGA01")

    # El aviso cuenta las fotos en riesgo de TODAS las trampas, y otras pruebas dejan fotos
    # viejas por ahí. Un aviso provocado por la foto de otro no demuestra nada sobre la nuestra,
    # así que se cuenta solo lo que aporta ESTA trampa. Mismo arreglo que hizo falta en el aviso
    # del plan de datos.
    def fotos_en_riesgo_propias():
        with S(engine) as ses:
            tid = ses.exec(select(Trampa).where(
                Trampa.device_id == "esp32-PURGA01")).first().id
            return ses.exec(select(func.count()).select_from(Imagen).where(
                Imagen.trampa_id == tid,
                Imagen.estado != "procesada",
                Imagen.capturada < datetime.now(timezone.utc) - timedelta(
                    days=estado_svc.PURGA_SIN_PROCESAR_DIAS
                    - estado_svc.PURGA_AVISO_MARGEN_DIAS))).one()

    def avisos_de_purga():
        # Hay aviso «nuestro» si esta trampa aporta al menos una foto en riesgo y el servidor
        # lo está contando.
        with S(engine) as ses:
            hay = [a for a in estado_svc.avisos(ses)
                   if "sin haberse analizado" in a["titulo"]]
        return hay if fotos_en_riesgo_propias() else []

    # Una foto recién subida y sin analizar NO debe disparar el aviso: es lo normal.
    assert not avisos_de_purga(), "no puede avisar por una foto recién llegada"

    # Se envejece hasta el borde del corte.
    with S(engine) as ses:
        img = ses.exec(select(Imagen).order_by(Imagen.id.desc())).first()
        img.estado = "pendiente"
        img.capturada = (datetime.now(timezone.utc)
                         - timedelta(days=estado_svc.PURGA_SIN_PROCESAR_DIAS
                                     - estado_svc.PURGA_AVISO_MARGEN_DIAS + 0.5))
        ses.add(img); ses.commit()

    avisos = avisos_de_purga()
    assert avisos, "una foto a punto de borrarse sin analizar tiene que avisar"
    assert avisos[0]["nivel"] == "critico", "perder datos no contados no es un «aviso» menor"
    # Y el aviso tiene que decir qué hacer, no solo que pasa algo malo.
    assert "Worker_IA" in avisos[0]["accion"]


def test_un_agricultor_no_alcanza_nada_de_admin(cliente, jpeg):
    """El aislamiento por rol, fijado. Una regresión aquí sería **silenciosa**.

    Nunca se había comprobado con una prueba: se verificó a mano el 2026-09-13 contra el
    servidor real y funcionaba, pero nada impedía que un endpoint nuevo naciera abierto. De
    hecho tres de los que se comprueban aquí —`/reiniciar`, `/consumo` y `/backup/db`— se
    añadieron esta misma semana.

    Lo que más importa: un agricultor **no puede reiniciar la trampa de otro** ni descargarse la
    base de datos entera.
    """
    tid = _alta(cliente, jpeg, "esp32-ROL01")

    r = cliente.post("/api/v1/users", auth=ADMIN, json={
        "username": "agri_prueba", "password": "agri12345", "rol": "agricultor",
        "nombre": "Agricultor de prueba"})
    assert r.status_code == 201
    uid = r.json()["id"]
    AGRI = ("agri_prueba", "agri12345")

    # Sin invernadero asignado no ve NADA, pero tampoco recibe un error: ve su lista vacía.
    for ruta in ("/api/v1/traps", "/api/v1/images?limit=5", "/api/v1/greenhouses"):
        r = cliente.get(ruta, auth=AGRI)
        assert r.status_code == 200, f"{ruta} debería responder, vacío"
        assert r.json() == [], f"{ruta} le está enseñando datos que no son suyos"

    # Y todo lo de administración, cerrado.
    prohibidos = [
        ("get", "/api/v1/users"),
        ("get", f"/api/v1/traps/{tid}/consumo"),
        ("get", "/api/v1/backup/db"),
        ("delete", f"/api/v1/traps/{tid}"),
        ("post", f"/api/v1/traps/{tid}/reiniciar"),
        # Apagar no tiene vuelta a distancia: si naciera abierto, un agricultor podria dejar
        # a oscuras la trampa de otro hasta que alguien fuera a quitarle la bateria.
        ("post", f"/api/v1/traps/{tid}/apagar"),
        ("post", f"/api/v1/traps/{tid}/apagar/cancelar"),
    ]
    for metodo, ruta in prohibidos:
        r = getattr(cliente, metodo)(ruta, auth=AGRI)
        assert r.status_code == 403, f"{metodo.upper()} {ruta} NO está protegido ({r.status_code})"

    cliente.delete(f"/api/v1/users/{uid}", auth=ADMIN)


def test_una_foto_cortada_no_tumba_la_ingesta(cliente):
    """Una subida truncada por 4G tiene que rechazarse limpia, no reventar con un 500.

    El log de la placa lo dice tal cual: «se corto el envio de la imagen». Lo que llega
    entonces no es un JPEG, y Pillow levantaba `UnidentifiedImageError` sin que nadie la
    capturara: **500**.

    La diferencia entre 500 y 422 no es de estilo. Ante un 500 la trampa entiende «el servidor
    está mal» y **reintenta**, y cada reintento por móvil son otros ~300 KB del plan gastados en
    reenviar exactamente los mismos bytes rotos. Un 422 dice «esto que me mandas no vale», que
    es la verdad, y la placa no insiste.
    """
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("cortada.jpg", b"\xff\xd8\xff\xe0 esto se corto", "image/jpeg")},
                     data={"device_id": "esp32-CORTADA01"})
    assert r.status_code == 422, f"una foto ilegible debe dar 422, no {r.status_code}"
    assert "cortada" in r.json()["detail"] or "no se puede leer" in r.json()["detail"]

    # Y no puede quedar basura en disco por el intento fallido.
    from app.core import config as cfg
    from pathlib import Path
    sueltos = list(Path(cfg.IMAGES_DIR).rglob("*CORTADA01*"))
    assert not sueltos, f"la ingesta fallida dejó ficheros: {sueltos}"


def test_sin_avisos_de_plan_de_datos(cliente, jpeg):
    """Retirados el 15/10/2026 por orden de Antonio: la trampa no llega al límite mensual.

    Ni siquiera un ritmo de banco (20 s) por 4G debe generar avisos de plan de datos.
    """
    from app.models.models import Trampa
    from app.core.db import engine
    from app.services import estado as estado_svc, traps as traps_svc
    from sqlmodel import Session as S

    tid = _alta(cliente, jpeg, "esp32-RITMO99")
    with S(engine) as ses:
        t = ses.get(Trampa, tid)
        t.red = "lte"
        ses.add(t); ses.commit()
        cfg = traps_svc.ensure_config(ses, t)
        cfg.intervalo_seg = 20
        ses.add(cfg); ses.commit()
        assert not [a for a in estado_svc.avisos(ses)
                    if "plan" in a["titulo"] or "Datos móviles" in a["titulo"]]


def test_no_se_acepta_una_imagen_absurdamente_grande(cliente):
    """Sin tope, una placa averiada llena el disco — y el disco lleno tumba la ingesta en silencio."""
    from app.api.ingest import MAX_SUBIDA_B

    enorme = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_SUBIDA_B + 1024)
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("enorme.jpg", enorme, "image/jpeg")},
                     data={"device_id": "esp32-ENORME01"})
    assert r.status_code == 413, f"debería rechazarse por tamaño, no {r.status_code}"
    assert "demasiado grande" in r.json()["detail"]


def test_una_subida_rechazada_no_deja_una_trampa_fantasma(cliente):
    """Rechazar la foto no puede dejar la trampa dada de alta.

    Se vio el 2026-09-14: `esp32-TOPE01` aparecía en el panel como una trampa más, sin haber
    subido jamás una foto —todas sus subidas se rechazaron por tamaño—, porque el alta ocurría
    **antes** de la comprobación.

    **Una trampa en la lista es algo que alguien va a ir a buscar al invernadero.** No puede
    aparecer una que no existe.
    """
    from app.api.ingest import MAX_SUBIDA_B

    antes = {t["device_id"] for t in cliente.get("/api/v1/traps", auth=ADMIN).json()}
    enorme = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_SUBIDA_B + 2048)
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("enorme.jpg", enorme, "image/jpeg")},
                     data={"device_id": "esp32-FANTASMA01"})
    assert r.status_code == 413
    despues = {t["device_id"] for t in cliente.get("/api/v1/traps", auth=ADMIN).json()}
    assert "esp32-FANTASMA01" not in despues, "la subida rechazada dejó una trampa fantasma"
    assert despues == antes, "la lista de trampas ha cambiado con una subida rechazada"


def test_la_retencion_sobrevive_al_volcado_del_contador(cliente, jpeg):
    """El caso que rompía en producción: que el contador vuelque JUSTO en esta petición.

    **111 respuestas 500 en 24 h**, todas en `POST /telemetry?espera=120` —la petición retenida,
    la que sostiene el directo—, y ninguna reproducible a mano. La causa es una coincidencia:

    1. `consumo_svc.anotar()` NO escribe en cada llamada: acumula en memoria y vuelca cada
       `VOLCADO_S = 30 s`. Así que **una de cada tantas peticiones hace `commit()`**.
    2. Un `commit()` **caduca todos los atributos** de los objetos de la sesión (`expire_on_
       commit`, que viene puesto por defecto).
    3. Justo después, la retención **cierra la sesión** para no quedarse con una conexión del
       pool durante 120 s (lo que arregló el fallo de las 20 trampas).
    4. Y entonces `trap.id` —un atributo caducado, en un objeto ya desligado— intenta releer la
       fila, no encuentra sesión, y revienta: `DetachedInstanceError` → 500.

    Sin el paso 1 no falla, y el paso 1 depende del reloj. De ahí que la prueba del pool pasara
    siempre y el servidor fallara cada pocos minutos: **la prueba no fallaba por suerte**, no
    por estar bien. Aquí se le quita la suerte forzando el volcado.
    """
    from app.services import consumo as consumo_svc

    _alta(cliente, jpeg, "esp32-VUELCA1")
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-VUELCA1", "red": "lte"})

    # Que el próximo `anotar` vuelque sí o sí: es lo que en producción pasa por azar.
    consumo_svc._ultimo_volcado = 0.0

    r = cliente.post("/api/v1/telemetry?espera=1", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-VUELCA1", "red": "lte"})
    assert r.status_code == 200, (
        "la petición retenida murió cuando el contador volcó: el 500 que dejaba muda la trampa")
    assert r.json()["ok"] is True


def test_una_orden_no_se_da_por_hecha_hasta_que_la_placa_lo_demuestra():
    """La orden de SMS se persigue hasta que hay prueba de que se ejecutó.

    **Lo que pasó el 2026-09-15.** La orden viajaba en la respuesta de la telemetría y el
    servidor la borraba al entregarla. La placa se reinició un segundo después y la orden
    **desapareció sin que nadie se enterara**: el SMS de prueba nunca salió, y solo se supo
    porque había alguien mirando el log. En campo eso es un canal de avisos roto con aspecto de
    sano, que es la peor clase de roto.

    Ahora la prueba es un contador: la placa manda cuántos SMS lleva enviados (`sms_n`). Si sube
    después de entregar la orden, se hizo. Si no sube, se reintenta; y si se acaba el plazo, se
    deja dicho en el log en vez de desaparecer.
    """
    from app.services import live as live_svc

    tid = 4242
    live_svc._sms_pedido.pop(tid, None)
    live_svc._sms_visto.pop(tid, None)

    live_svc.anotar_sms_visto(tid, 3)          # la placa ya llevaba 3 enviados
    live_svc.pedir_sms(tid)

    # Primera telemetría: se entrega.
    assert live_svc.hay_sms_pedido(tid) is True

    # Segunda telemetría SIN que el contador suba: la placa no lo hizo (o se reinició).
    # La orden NO se ha perdido: sigue viva esperando confirmación.
    assert live_svc.hay_sms_pedido(tid) is False, "no se reentrega de inmediato: se espera"
    assert live_svc.estado_sms_pedido(tid) is not None, \
        "la orden se perdió en silencio, que es justo lo que pasó el 2026-09-15"

    # La placa lo hace y lo demuestra subiendo el contador.
    live_svc.anotar_sms_visto(tid, 4)
    assert live_svc.hay_sms_pedido(tid) is False
    assert live_svc.estado_sms_pedido(tid) is None, "confirmada: ya no hay nada pendiente"


def test_la_orden_de_sms_se_reintenta_si_no_hay_confirmacion():
    """Entregar una vez no basta si la placa se reinicia justo después."""
    import time as _t

    from app.services import live as live_svc

    tid = 4243
    live_svc._sms_pedido.pop(tid, None)
    live_svc._sms_visto.pop(tid, None)
    live_svc.pedir_sms(tid)

    assert live_svc.hay_sms_pedido(tid) is True          # entrega 1
    # Se finge que pasó el tiempo de espera sin que el contador subiera.
    live_svc._sms_pedido[tid]["entregado_en"] = _t.time() - live_svc.SMS_REINTENTO_S - 1
    assert live_svc.hay_sms_pedido(tid) is True, \
        "sin confirmación y pasado el plazo, hay que volver a intentarlo"
    assert live_svc.estado_sms_pedido(tid)["entregas"] == 2


def test_un_agricultor_no_puede_gastarle_la_bateria_a_una_trampa_ajena(cliente, jpeg):
    """`live-ping` y `foto` comprobaban que la trampa EXISTE, no de quien es.

    Con eso, un agricultor podia mandar a CUALQUIER trampa del sistema que se pusiera a
    transmitir video o que disparara una foto a maxima resolucion: bateria, datos moviles y
    almacenamiento de otro, gastados por quien no tiene por que ver esa trampa. No es el mismo
    caso que los endpoints de administracion --esos ya estaban cerrados con `require_admin`--:
    estos dos estaban abiertos a cualquier usuario autenticado. Hallazgo de Codex en la
    mentoria del 2026-09-19.

    Se responde 404 y no 403 a proposito: a quien no puede verla no se le confirma que existe.
    """
    tid = _alta(cliente, jpeg, "esp32-ROL02")

    r = cliente.post("/api/v1/users", auth=ADMIN, json={
        "username": "agri_bateria", "password": "agri12345", "rol": "agricultor",
        "nombre": "Agricultor de prueba"})
    assert r.status_code == 201
    uid = r.json()["id"]
    AGRI = ("agri_bateria", "agri12345")

    for ruta in (f"/api/v1/traps/{tid}/live-ping", f"/api/v1/traps/{tid}/foto"):
        r = cliente.post(ruta, auth=AGRI)
        assert r.status_code == 404,             "%s deja que un agricultor mande sobre una trampa ajena (%s)" % (ruta, r.status_code)

    # Y el admin sigue pudiendo, que si no la proteccion seria un candado en la puerta propia.
    for ruta in (f"/api/v1/traps/{tid}/live-ping", f"/api/v1/traps/{tid}/foto"):
        assert cliente.post(ruta, auth=ADMIN).status_code == 200, "el admin tiene que poder"

    cliente.delete(f"/api/v1/users/{uid}", auth=ADMIN)
