"""
Pruebas de la API de ENTOMIRA.

No pretenden cubrirlo todo: cubren **lo que ya ha fallado de verdad**. Cada bloque nace de un
fallo real de desarrollo, y por eso vale la pena mantenerlas:

- Borrado con `ids` vacío → se iba por la rama de VACIAR LA GALERÍA ENTERA.
- Cotas de `intervalo_seg` → poner 10 s llenó el disco de la Pi y tumbó la ingesta 3 días.
- Filtro de imágenes de demostración → los conteos mezclaban dataset con capturas reales.
- Contrato de `/telemetry` y `/firmware/latest` → si cambian, la trampa deja de actualizarse
  o entra en bucle de reinstalación.
- Autenticación de dispositivo → una clave mal validada abre la ingesta a cualquiera.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


# --------------------------------------------------------------------- autenticación
def test_api_sin_credenciales_responde_401(cliente):
    assert cliente.get("/api/v1/traps").status_code == 401


def test_clave_de_dispositivo_incorrecta_no_ingesta(cliente, jpeg):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": "clave-falsa"},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-INTRUSO"})
    assert r.status_code == 401


def test_admin_entra_con_basic(cliente):
    assert cliente.get("/api/v1/traps", auth=ADMIN).status_code == 200


# --------------------------------------------------------------------- ingesta
def test_ingesta_registra_la_trampa_y_devuelve_config(cliente, jpeg):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-NUEVA"})
    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["registered"] is True          # primera vez: se auto-registra
    assert "config" in cuerpo and "intervalo_seg" in cuerpo["config"]


def test_ingesta_guarda_bateria_y_firmware(cliente, jpeg):
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("c.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-BAT", "bateria": "77", "voltaje": "3.912",
                       "crate": "-4.5", "rssi": "-61", "fw": "1.9-pruebas"})
    t = [x for x in cliente.get("/api/v1/traps", auth=ADMIN).json()
         if x["device_id"] == "esp32-BAT"][0]
    assert t["bateria"] == 77
    assert abs(t["voltaje"] - 3.912) < 1e-6
    assert t["fw_version"] == "1.9-pruebas"      # sin esto no se sabe si una OTA ha entrado


# --------------------------------------------------------------------- configuración
def test_intervalo_por_debajo_del_minimo_se_rechaza(cliente, trampa):
    # 10 s de intervalo con QSXGA llenaron el disco de la Pi y tumbaron la ingesta 3 días.
    r = cliente.put(f"/api/v1/traps/{trampa}/config", auth=ADMIN, json={"intervalo_seg": 5})
    assert r.status_code == 422


def test_intervalo_valido_sube_la_version_de_config(cliente, trampa):
    antes = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["config"]["version"]
    r = cliente.put(f"/api/v1/traps/{trampa}/config", auth=ADMIN, json={"intervalo_seg": 600})
    assert r.status_code == 200
    assert r.json()["version"] > antes           # la placa detecta el cambio por la version


def test_ventana_despierto_fuera_de_rango_se_rechaza(cliente, trampa):
    assert cliente.put(f"/api/v1/traps/{trampa}/config", auth=ADMIN,
                       json={"ventana_despierto_seg": 999}).status_code == 422


# --------------------------------------------------------------------- galería y borrado
def test_borrado_con_ids_vacio_no_vacia_la_galeria(cliente, trampa, jpeg):
    """El fallo más peligroso encontrado: `?ids=` vacío caía en «vaciar todo»."""
    for _ in range(3):
        cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01"})
    antes = cliente.get("/api/v1/images/count", auth=ADMIN).json()["total"]
    assert antes >= 3
    r = cliente.request("DELETE", "/api/v1/images?ids=", auth=ADMIN)
    assert r.status_code == 422
    assert cliente.get("/api/v1/images/count", auth=ADMIN).json()["total"] == antes


def test_borrado_por_ids_borra_solo_esas(cliente, trampa, jpeg):
    for _ in range(3):
        cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01"})
    imgs = cliente.get("/api/v1/images?limit=3", auth=ADMIN).json()
    antes = cliente.get("/api/v1/images/count", auth=ADMIN).json()["total"]
    objetivo = imgs[0]["id"]
    r = cliente.request("DELETE", f"/api/v1/images?ids={objetivo}", auth=ADMIN)
    assert r.status_code == 200 and r.json()["borradas"] == 1
    assert cliente.get("/api/v1/images/count", auth=ADMIN).json()["total"] == antes - 1
    assert cliente.get(f"/api/v1/images/{objetivo}", auth=ADMIN).status_code == 404


def test_la_galeria_no_mezcla_las_de_demostracion(cliente):
    """Las de la vista Demo salen de un dataset: mezclarlas falsea los conteos."""
    todas = cliente.get("/api/v1/images?incluir_demo=true&limit=200", auth=ADMIN).json()
    normales = cliente.get("/api/v1/images?limit=200", auth=ADMIN).json()
    assert all(not i["es_demo"] for i in normales)
    assert len(normales) <= len(todas)


# --------------------------------------------------------------------- trampas
def test_el_listado_de_trampas_oculta_la_de_demostracion(cliente):
    ids = [t["device_id"] for t in cliente.get("/api/v1/traps", auth=ADMIN).json()]
    assert "demo-fallback" not in ids
    con_demo = [t["device_id"] for t in
                cliente.get("/api/v1/traps?incluir_demo=true", auth=ADMIN).json()]
    assert len(con_demo) >= len(ids)


def test_pc_de_subidas_manuales_no_es_trampa_ni_genera_avisos(cliente, jpeg):
    from datetime import datetime, timedelta, timezone

    from app.core.db import sesion_corta
    from app.models.models import Imagen

    antes = cliente.get("/api/v1/stats", auth=ADMIN).json()["trampas"]
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("manual.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "pc-entomira"})
    assert r.status_code == 201
    tid = r.json()["trap_id"]
    with sesion_corta() as s:
        im = s.get(Imagen, r.json()["image_id"])
        im.capturada = datetime.now(timezone.utc) - timedelta(hours=2)
        s.add(im)
        s.commit()

    ids = [t["device_id"] for t in cliente.get(
        "/api/v1/traps?incluir_demo=true", auth=ADMIN).json()]
    assert "pc-entomira" not in ids
    assert cliente.get("/api/v1/stats", auth=ADMIN).json()["trampas"] == antes
    assert any(im["trampa_id"] == tid for im in cliente.get(
        f"/api/v1/images?trampa={tid}", auth=ADMIN).json())
    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
    assert not any("pc-entomira" in a["titulo"] for a in avisos)


def test_trap_out_expone_los_campos_que_usa_la_web(cliente, trampa):
    t = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()
    for campo in ("estado", "bateria", "voltaje", "crate", "carga_estado", "fw_version",
                  "config", "num_imagenes"):
        assert campo in t, f"falta {campo}: la web lo pinta"


def test_borrar_trampa_con_lecturas_hardware(cliente):
    alta = cliente.post("/api/v1/traps", auth=ADMIN,
                        json={"device_id": "esp32-BORRAR-HARDWARE"})
    assert alta.status_code == 201
    tid = alta.json()["id"]
    telemetria = cliente.post("/api/v1/telemetry",
                             headers={"X-API-Key": DEVICE_KEY},
                             json={"device_id": "esp32-BORRAR-HARDWARE",
                                   "ina219": {"v": 3.91, "ma": -120}})
    assert telemetria.status_code == 200
    assert cliente.get(f"/api/v1/traps/{tid}/hardware", auth=ADMIN).json()["lecturas"]

    borrado = cliente.delete(f"/api/v1/traps/{tid}", auth=ADMIN)
    assert borrado.status_code == 204
    assert cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).status_code == 404


# --------------------------------------------------------------------- telemetría
def test_una_telemetria_que_no_cambia_nada_no_repinta_la_web(cliente, trampa):
    """Dos telemetrías idénticas NO pueden anunciar un cambio. Si lo hacen, la web parpadea.

    Medido el 2026-09-19 en el sistema real: `/telemetry` marcaba un cambio SIEMPRE, aunque no
    llegara ni un dato nuevo. No se notó hasta que hubo vídeo en vivo, porque con alguien
    mirando el servidor contesta al instante y la trampa manda telemetría **cada 2 s** en vez
    de cada 20. La web sondea `/cambios` cada 4 s y, al ver la versión subida, llama a
    `router()`, que repinta la vista entera con `app.innerHTML = ''`: la página se quedaba en
    blanco un instante, una y otra vez, justo mientras se miraba el vídeo.
    """
    igual = {"device_id": "esp32-PRUEBA01", "voltaje": 3.7, "bateria": 55, "fw": "1.0-test"}
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=igual)
    v0 = cliente.get("/api/v1/cambios", auth=ADMIN).json()["v"]

    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=igual)
    v1 = cliente.get("/api/v1/cambios", auth=ADMIN).json()["v"]
    assert v1 == v0, "una telemetría sin novedades ha anunciado un cambio: la web se repintará"

    # Y lo contrario también tiene que seguir funcionando: un dato NUEVO sí se anuncia, o la
    # web dejaría de enterarse de que baja la batería sin recargar a mano.
    distinto = dict(igual, bateria=41)
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=distinto)
    v2 = cliente.get("/api/v1/cambios", auth=ADMIN).json()["v"]
    assert v2 > v1, "un cambio real de batería no se ha anunciado: la web se queda vieja"


def test_telemetria_actualiza_y_anuncia_version(cliente, trampa):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-PRUEBA01", "voltaje": 3.7, "bateria": 55,
                           "fw": "1.0-test"})
    assert r.status_code == 200
    d = r.json()
    # La placa lee estas dos claves: si desaparecen, deja de enterarse de las OTA y de las
    # peticiones de foto.
    assert "fw" in d and "foto" in d


def test_la_telemetria_anuncia_la_version_de_config(cliente, trampa):
    """La placa se entera por aqui de que su config se ha quedado vieja.

    Nace del fallo del 2026-09-09: la config solo viajaba en la respuesta del `/ingest`, asi
    que una trampa recien arrancada (o que perdio esa respuesta) seguia disparando en SVGA
    con el servidor puesto en QSXGA, y nada lo decia. Con la version en la telemetria, que
    va cada 10 s, la trampa lo detecta y pide la config.
    """
    d = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-PRUEBA01"}).json()
    assert "cfg" in d, "la placa compara esta clave con su version: sin ella no resincroniza"
    v0 = d["cfg"]

    # Se toca el BRILLO y no la resolucion: desde 2026-09-12 la resolucion por defecto ya es
    # la maxima, asi que ponerla otra vez no cambiaria nada y el test no probaria lo que dice.
    cliente.put(f"/api/v1/traps/{trampa}/config", auth=ADMIN, json={"brillo": 1})
    d2 = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                      json={"device_id": "esp32-PRUEBA01"}).json()
    assert d2["cfg"] > v0, "al cambiar la config, la version anunciada tiene que subir"


def test_la_config_viaja_en_la_respuesta_del_ingest(cliente, trampa, jpeg):
    """Contrato nativo que lee el cliente Zero al descargar la configuración."""
    d = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("cap.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01", "config_version": 0}).json()
    assert "config" in d and "version" in d["config"]
    assert d["config"]["ancho"] == 4608 and d["config"]["alto"] == 2592
    assert {"ev", "balance", "enfoque", "lente_dioptrias"} <= d["config"].keys()
    assert not {"resolucion", "ae_level", "wb_mode", "gain_ceiling", "lenc", "awb"} & d["config"].keys()


def test_la_config_de_la_pcb_se_consulta_por_device_id(cliente, trampa):
    """`GET /traps/<device_id>/config`: es lo que la placa pide ANTES de su primera foto."""
    d = cliente.get("/api/v1/traps/esp32-PRUEBA01/config",
                    headers={"X-API-Key": DEVICE_KEY}).json()
    assert d["version"] >= 1 and d["ancho"] == 4608 and d["alto"] == 2592


def test_las_fotos_retenidas_en_la_tarjeta_se_ven_en_la_web(cliente, trampa):
    """El buffer local no puede ser un estado invisible.

    La trampa guarda en su tarjeta lo que no puede subir —para eso esta el buffer—, pero
    mientras el contador no baje hay capturas que existen y todavia no se pueden analizar. Si
    eso no se ve, la serie de esa trampa tiene un hueco que nadie distingue de «no habia
    insectos», que es el peor error posible en una herramienta de decision.
    """
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-PRUEBA01", "buf": 7})

    t = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()
    assert t["buffer_pend"] == 7

    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
    assert any("retenidas" in a["titulo"] for a in avisos), "el aviso tiene que salir"

    # Y cuando la trampa las sube, el aviso desaparece: un aviso que no se apaga se ignora.
    cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                 json={"device_id": "esp32-PRUEBA01", "buf": 0})
    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
    assert not any("retenidas" in a["titulo"] for a in avisos)


def test_la_foto_puede_traer_la_hora_en_que_se_tomo(cliente, jpeg):
    """`captured_at`: es lo que hace honesta a una foto que esperó en el buffer.

    La fecha va **relativa a hoy** a propósito. Antes estaba escrita a mano —«2026-09-11»— y el
    2026-09-17 esa foto cumplió seis días, cruzó el umbral del aviso de purga (5 días) y **tumbó
    otra prueba** que cuenta esos avisos. Nadie había tocado nada: la rompió el calendario.

    Lo que esta prueba quiere decir es «una foto de hace un rato», no una fecha concreta. Se
    escribe lo que se quiere decir, y así no caduca.
    """
    from datetime import datetime, timedelta, timezone

    cuando = (datetime.now(timezone.utc) - timedelta(hours=6)).replace(microsecond=0)
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01",
                           "captured_at": cuando.isoformat()})
    assert r.status_code == 201
    img = cliente.get(f"/api/v1/images/{r.json()['image_id']}", auth=ADMIN).json()
    assert img["capturada"].startswith(cuando.strftime("%Y-%m-%dT%H:%M:%S")), img["capturada"]


def test_la_misma_captura_dos_veces_no_crea_dos_filas(cliente, trampa, jpeg):
    """Ingesta idempotente por (trampa, captured_at).

    El acuse de recibo por USB es indirecto: si el puente sube la foto pero su respuesta se
    pierde, la trampa la da por NO subida, la guarda en su buffer y acaba mandandola otra vez.
    Dos filas para una sola captura inflarian el conteo acumulado de la tarjeta, que es el
    numero con el que se decide si tratar.
    """
    cuando = "2026-09-11T18:49:54+00:00"
    datos = {"device_id": "esp32-PRUEBA01", "captured_at": cuando}

    antes = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["num_imagenes"]
    r1 = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                      files={"image": ("c.jpg", jpeg, "image/jpeg")}, data=datos)
    assert r1.status_code == 201 and r1.json()["duplicada"] is False

    r2 = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                      files={"image": ("c.jpg", jpeg, "image/jpeg")}, data=datos)
    # OK a proposito: la foto ESTA en el servidor, asi que la trampa debe borrarla de su
    # tarjeta. Un error la dejaria reintentando la misma captura para siempre.
    assert r2.status_code == 200, r2.text
    assert r2.json()["duplicada"] is True
    assert r2.json()["image_id"] == r1.json()["image_id"], "tiene que devolver LA que ya habia"

    despues = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["num_imagenes"]
    assert despues == antes + 1, "una captura, una fila"


def test_sin_hora_de_captura_no_se_descarta_nada(cliente, trampa, jpeg):
    """Sin `captured_at`, cada foto se fecha al llegar: ahi no hay duplicado que valga."""
    antes = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["num_imagenes"]
    for _ in range(2):
        r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                         files={"image": ("c.jpg", jpeg, "image/jpeg")},
                         data={"device_id": "esp32-PRUEBA01"})
        assert r.json()["duplicada"] is False
    despues = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["num_imagenes"]
    assert despues == antes + 2


def test_dos_capturas_distintas_entran_las_dos(cliente, trampa, jpeg):
    """El filtro es por INSTANTE: un segundo de diferencia ya son dos capturas."""
    antes = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["num_imagenes"]
    for cuando in ("2026-09-11T19:00:00+00:00", "2026-09-11T19:00:01+00:00"):
        r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                         files={"image": ("c.jpg", jpeg, "image/jpeg")},
                         data={"device_id": "esp32-PRUEBA01", "captured_at": cuando})
        assert r.json()["duplicada"] is False
    despues = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["num_imagenes"]
    assert despues == antes + 2


def test_la_hora_de_captura_se_guarda_en_UTC(cliente, trampa, jpeg):
    """SQLite no guarda el huso: si entra un `+02:00` tal cual, queda 2 h desplazado."""
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01",
                           "captured_at": "2026-09-11T21:30:00+02:00"})
    img = cliente.get(f"/api/v1/images/{r.json()['image_id']}", auth=ADMIN).json()
    assert img["capturada"].startswith("2026-09-11T19:30:00"), img["capturada"]

    # Y la misma captura expresada en otro huso es LA MISMA: no debe duplicarse.
    r2 = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                      files={"image": ("c.jpg", jpeg, "image/jpeg")},
                      data={"device_id": "esp32-PRUEBA01",
                            "captured_at": "2026-09-11T19:30:00+00:00"})
    assert r2.json()["duplicada"] is True


def test_el_snapshot_dice_si_es_la_camara_o_una_foto_vieja(cliente, trampa, jpeg):
    """Los tres caminos del snapshot salian con un 200 identico.

    Si no hay fotograma en vivo, el servidor devuelve la ultima imagen guardada — que es util,
    pero puede tener horas. Sin marcarla, quien esta encuadrando la trampa cree que ve la camara.
    """
    r = cliente.get(f"/api/v1/traps/{trampa}/snapshot", auth=ADMIN)
    assert r.status_code == 200
    assert r.headers["X-Entomira-Fuente"] == "archivo"
    assert float(r.headers["X-Entomira-Edad-Seg"]) >= 0


def test_el_servidor_marca_el_ritmo_de_la_telemetria(cliente, trampa):
    """Cada informe abre una conexion TLS nueva: ~4 KB de saludo para 150 bytes de JSON.

    A 10 s fijos eran 8.640 envios al dia, ~1 GB/mes por 4G — mas que las propias fotos. El
    ritmo lo decide el servidor porque es el unico que sabe si hay alguien mirando el panel:
    con alguien delante el dato se esta usando y 10 s es lo que hace util la pantalla; sin
    nadie, son datos quemados.
    """
    # Estado limpio: mirar una foto TAMBIEN cuenta como estar mirando (el snapshot renueva la
    # peticion de video), asi que una prueba anterior puede dejar la trampa «vista».
    from app.services import live as live_svc
    from app.services import traps as traps_svc
    live_svc.parar_vivo(trampa)
    traps_svc._keep_awake.pop(trampa, None)

    def informa(red):
        return cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                            json={"device_id": "esp32-PRUEBA01", "red": red}).json()

    assert informa("lte")["cada"] == 600, "por movil y sin nadie mirando: lo mas espaciado"
    assert informa("wifi")["cada"] == 60, "por WiFi no cuesta nada, pero tampoco hace falta 10 s"

    # Alguien abre el panel de control: el navegador manda su latido.
    cliente.post(f"/api/v1/traps/{trampa}/live-ping", auth=ADMIN)
    assert informa("lte")["cada"] == 10, "con alguien mirando, el dato vale: rapido"


def test_una_trampa_lenta_por_4G_no_sale_offline(cliente, trampa):
    """El margen de «offline» sale de la cadencia que el propio servidor ha mandado.

    Si le pedimos informar cada 600 s por 4G y la damos por muerta a los 180, la pintamos
    offline estando perfectamente. Eso es peor que no pintar nada: enseña a ignorar el panel.
    """
    from datetime import datetime, timedelta, timezone
    from app.models.models import Trampa
    from app.services import live as live_svc, traps as traps_svc
    from app.core.db import engine
    from sqlmodel import Session as S

    live_svc.parar_vivo(trampa)
    traps_svc._keep_awake.pop(trampa, None)

    # Sin invernadero una trampa sale «sin_asignar» pase lo que pase, así que hay que darle uno.
    gh = cliente.post("/api/v1/greenhouses", auth=ADMIN,
                      json={"nombre": "Invernadero del ritmo"}).json()
    with S(engine) as ses:
        t = ses.get(Trampa, trampa)
        t.red = "lte"
        t.invernadero_id = gh["id"]
        t.ultimo_visto = datetime.now(timezone.utc) - timedelta(seconds=400)
        ses.add(t); ses.commit()

    # 400 s sin hablar, pero es que se le ha pedido hablar cada 600: está perfecta.
    assert cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["estado"] == "online"

    # Con la misma ausencia pero por WiFi (cadencia de 60 s), 400 s sí son demasiados.
    with S(engine) as ses:
        t = ses.get(Trampa, trampa)
        t.red = "wifi"
        ses.add(t); ses.commit()
    assert cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["estado"] == "offline"

    with S(engine) as ses:                      # se deja como estaba para el resto de pruebas
        t = ses.get(Trampa, trampa)
        t.ultimo_visto = datetime.now(timezone.utc)
        ses.add(t); ses.commit()


def test_el_long_polling_admite_esperas_de_hasta_60_s(cliente, trampa):
    """Alargar la espera baja las conexiones sin tocar la latencia: el servidor retiene la
    respuesta y contesta EN CUANTO alguien mira, se haya pedido 20 s o 60."""
    r = cliente.get("/api/v1/traps/esp32-PRUEBA01/live?espera=0",
                    headers={"X-API-Key": DEVICE_KEY})
    assert r.status_code == 200 and "en_vivo" in r.json()


def test_una_captura_sin_fila_no_deja_fichero_en_disco(cliente, jpeg):
    """El fichero no es definitivo hasta que la fila existe.

    Antes se escribia primero y la fila despues: si el commit fallaba, el JPEG se quedaba en
    disco sin nadie que lo mirara ni lo borrara. Un huerfano invisible que come disco es
    exactamente como se lleno la Pi en julio y tumbo la ingesta tres dias en silencio.
    """
    from pathlib import Path
    from app.services import images as images_svc

    saved = images_svc.save_capture("esp32-PRUEBA01", jpeg)
    definitivo = Path(saved["ruta_fichero"])
    provisional = Path(saved["ruta_fichero"] + images_svc.PARCIAL)

    # Recien guardado NO existe con su nombre bueno: esta a la espera de que haya fila.
    assert provisional.exists() and not definitivo.exists()

    images_svc.descartar(saved)          # el commit falla -> no debe quedar nada
    assert not provisional.exists() and not definitivo.exists()

    # Y por el camino bueno, la confirmacion lo deja en su sitio.
    saved = images_svc.save_capture("esp32-PRUEBA01", jpeg)
    images_svc.confirmar(saved)
    assert Path(saved["ruta_fichero"]).exists()
    assert not Path(saved["ruta_fichero"] + images_svc.PARCIAL).exists()


def test_la_ingesta_normal_deja_el_fichero_en_su_sitio(cliente, jpeg):
    """La via real: subir una foto deja el JPEG definitivo, sin `.parcial` sueltos."""
    from pathlib import Path
    from app.services import images as images_svc

    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01"})
    assert r.status_code == 201
    ficha = cliente.get(f"/api/v1/images/{r.json()['image_id']}", auth=ADMIN).json()
    assert ficha["id"] == r.json()["image_id"]

    from app.core import config as cfg
    sobrantes = list(Path(cfg.IMAGES_DIR).rglob("*" + images_svc.PARCIAL))
    assert not sobrantes, f"quedaron provisionales sin confirmar: {sobrantes}"


def test_telemetria_exige_clave_de_dispositivo(cliente):
    assert cliente.post("/api/v1/telemetry",
                        json={"device_id": "esp32-PRUEBA01"}).status_code == 401


def test_la_carga_se_deduce_de_la_tension(cliente, trampa):
    """Sube la tensión en varias muestras: tiene que verse como 'cargando'."""
    import time
    from app.services import bateria as bat
    bat._hist.clear()
    ahora = time.time()
    for i, v in enumerate([3.70, 3.75, 3.80, 3.85]):
        bat._hist[trampa].append((ahora - (3 - i) * 40, v))
    estado = bat.estado(trampa, crate=None)
    assert estado["estado"] == "cargando"
    assert estado["fuente"] == "tension"
    assert estado["tendencia_mv_h"] > 0


# --------------------------------------------------------------------- firmware / OTA
def test_firmware_sin_publicar_devuelve_vacio(cliente):
    r = cliente.get("/api/v1/firmware/latest", headers={"X-API-Key": DEVICE_KEY})
    assert r.status_code == 200 and r.json() == {}


FIRMA_DE_PRUEBA = "3045022100" + "ab" * 30      # no se valida aqui: la valida la placa


def test_publicar_firmware_y_descargarlo(cliente):
    datos = b"\x00" * 200_000                    # tamaño plausible de un binario
    r = cliente.post("/api/v1/firmware", auth=ADMIN,
                     files={"fichero": ("firmware.bin", datos, "application/octet-stream")},
                     data={"version": "9.9-test", "notas": "prueba",
                           "firma": FIRMA_DE_PRUEBA})
    assert r.status_code == 200
    m = r.json()
    assert m["version"] == "9.9-test" and m["bytes"] == len(datos) and len(m["md5"]) == 32

    l = cliente.get("/api/v1/firmware/latest", headers={"X-API-Key": DEVICE_KEY}).json()
    assert l["version"] == "9.9-test" and l["md5"] == m["md5"]
    # La firma tiene que LLEGAR a la trampa: es lo unico que la placa puede comprobar.
    assert l["firma"] == FIRMA_DE_PRUEBA
    b = cliente.get("/api/v1/firmware/bin", headers={"X-API-Key": DEVICE_KEY})
    assert b.status_code == 200 and len(b.content) == len(datos)


def test_no_se_publica_firmware_sin_firmar(cliente):
    """Sin firma no se publica.

    El MD5 protege de una descarga corrupta, no de un binario malicioso: lo anuncia el mismo
    que sirve el fichero. Desde que el OTA funciona de verdad, un firmware sin firmar es
    ejecucion de codigo en la trampa para cualquiera que pueda suplantar al servidor. La
    placa ya los rechaza; el servidor tampoco los acepta, para que el fallo se vea AL
    PUBLICAR y no tres dias despues, al notar que ninguna trampa se actualiza.
    """
    r = cliente.post("/api/v1/firmware", auth=ADMIN,
                     files={"fichero": ("firmware.bin", b"\x00" * 200_000,
                                        "application/octet-stream")},
                     data={"version": "9.9-sin-firma"})
    assert r.status_code == 422
    assert "firma" in r.json()["detail"].lower()


def test_no_se_publica_un_binario_diminuto(cliente):
    """Publicar basura dejaría a las trampas intentando instalarla en bucle."""
    r = cliente.post("/api/v1/firmware", auth=ADMIN,
                     files={"fichero": ("firmware.bin", b"corto", "application/octet-stream")},
                     data={"version": "rota"})
    assert r.status_code == 422


def test_el_firmware_no_se_sirve_sin_clave(cliente):
    assert cliente.get("/api/v1/firmware/latest").status_code == 401


# --------------------------------------------------------------------- vista en vivo
def test_pedir_foto_a_maxima_resolucion_llega_a_la_trampa(cliente, trampa):
    assert cliente.post(f"/api/v1/traps/{trampa}/foto", auth=ADMIN).status_code == 200
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "esp32-PRUEBA01"})
    assert r.json()["foto"] is True              # la trampa la recoge…
    r2 = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                      json={"device_id": "esp32-PRUEBA01"})
    assert r2.json()["foto"] is False            # …y solo se dispara UNA vez


def test_el_contador_de_cambios_sube_al_entrar_una_foto(cliente, jpeg):
    v0 = cliente.get("/api/v1/cambios", auth=ADMIN).json()["v"]
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("c.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-PRUEBA01"})
    assert cliente.get("/api/v1/cambios", auth=ADMIN).json()["v"] > v0


# --------------------------------------------------------------------- salud
def test_health_responde_con_los_conteos(cliente):
    d = cliente.get("/api/v1/health", auth=ADMIN).json()
    assert d["status"] == "ok" and "pendientes" in d and "trampas" in d


def test_el_latido_dice_por_que_via_va_el_video(cliente, trampa):
    """El navegador no puede distinguir las dos vias del video, y se equivocaba en las dos.

    Por la via de EMPUJE el servidor guarda cada fotograma y `edad_frame` mide la frescura de
    verdad. Por el relay DIRECTO por IP los bytes van de la trampa al navegador sin pasar por
    el servidor, asi que `edad_frame` se queda viejo aunque el video vaya perfecto: el visor
    llego a decir «Sin senal reciente · hace 2 min» con la imagen cambiando delante (medido el
    2026-09-19). Un <img> no lee cabeceras, asi que la via la tiene que decir quien la elige.
    """
    from app.services import live as live_svc

    def latido():
        return cliente.post(f"/api/v1/traps/{trampa}/live-ping", auth=ADMIN).json()

    # Sin ningun stream abierto no se inventa una via.
    live_svc._via.pop(trampa, None)
    assert latido()["via"] is None, "sin stream reciente, la via es desconocida"

    live_svc.marcar_via(trampa, "directo")
    assert latido()["via"] == "directo"
    live_svc.marcar_via(trampa, "empuje")
    assert latido()["via"] == "empuje"

    # Y CADUCA. Una via de hace media hora no dice nada del video de ahora, y contestar con
    # ella seria peor que contestar `None`: el visor se fiaria de un camino que ya no existe.
    import time as _t
    live_svc._via[trampa] = ("directo", _t.time() - live_svc.VIA_VALIDA_S - 1)
    assert latido()["via"] is None, "una via vieja tiene que caducar, no arrastrarse"


def test_la_via_del_video_se_renueva_mientras_pasan_bytes(cliente, trampa):
    """Marcar la via solo al abrir el stream la hacia caducar con el video yendo bien.

    A los 120 s de una conexion sana, `via_actual` devolvia None y el visor volvia a juzgar
    por `edad_frame`, que en la via DIRECTA no mide nada: «Sin senal reciente» sobre una imagen
    que cambia delante. Por eso el relay la renueva por cada trozo que manda, y por eso aqui se
    comprueba que renovar REJUVENECE la marca. Hallazgo de Codex.
    """
    import time as _t
    from app.services import live as live_svc

    # A un segundo de caducar, llega otro trozo del relay.
    live_svc._via[trampa] = ("directo", _t.time() - live_svc.VIA_VALIDA_S + 1)
    live_svc.marcar_via(trampa, "directo")
    assert _t.time() - live_svc._via[trampa][1] < 1, "la marca tiene que rejuvenecer"
    assert live_svc.via_actual(trampa) == "directo"

    # Pero no se toca el reloj en CADA trozo: se refresca como mucho cada REFRESCO_VIA_S, y
    # un fotograma son muchos trozos.
    sello = live_svc._via[trampa][1]
    live_svc.marcar_via(trampa, "directo")
    assert live_svc._via[trampa][1] == sello, "no hace falta tocar el reloj en cada trozo"


def test_el_relay_marca_la_via_al_mandar_video(cliente, trampa, monkeypatch):
    """Lo que arregla el fallo no es la funcion, es QUIEN la llama.

    La prueba de arriba comprueba que renovar rejuvenece la marca, pero eso ya lo hacia antes:
    el fallo era que el relay solo marcaba AL ABRIR, asi que a los 120 s de video sano la via
    caducaba. Comprobarlo mirando `via_actual` al final no valdria --la marca de apertura ya
    la deja puesta y la prueba pasaria sin el arreglo--, asi que se CUENTAN las llamadas: una
    es la apertura; mas de una significa que tambien se marca al mandar bytes.
    """
    from app.services import live as live_svc

    llamadas = []
    original = live_svc.marcar_via
    monkeypatch.setattr(live_svc, "marcar_via",
                        lambda tid, via: (llamadas.append(via), original(tid, via))[1])

    live_svc.guardar_frame(trampa, bytes([255, 216, 255, 217]))   # un JPEG minimo, recien llegado
    live_svc.pedir_vivo(trampa)

    with cliente.stream("GET", f"/api/v1/traps/{trampa}/stream", auth=ADMIN) as r:
        assert r.status_code == 200
        next(r.iter_raw())                                  # el primer trozo del MJPEG

    assert llamadas[0] == "empuje", "al abrir se marca la via elegida"
    assert len(llamadas) > 1, "mandar video tiene que marcar la via, no solo abrirlo"


def test_una_imagen_ilegible_sale_de_la_cola(cliente, trampa):
    """Sin esto, 20 imagenes ilegibles vuelven a atascar la cola ENTERA.

    El arreglo anterior puso el `try` por imagen, asi que una ilegible ya no corta el lote.
    Pero seguia `pendiente` en el servidor y el worker solo la saltaba en su memoria: como
    `/inference/pending` sirve las 20 primeras POR FECHA, con 20 ilegibles a la cabeza el lote
    entero se salta y la 21 no llega nunca. Reproducido en `scripts/_repro_cola_worker.py`:
    21 pendientes, 20 fallidas, **0 procesables**, sin un solo error.

    No hizo falta tocar el esquema: el estado 'error' ya estaba en el modelo, solo que nadie
    lo escribia. Hallazgo de Codex en la mentoria del 2026-09-19.
    """
    import os
    from app.core.db import sesion_corta
    from app.models.models import Imagen

    worker = {"X-API-Key": os.environ["WORKER_API_KEY"]}

    with sesion_corta() as s:
        im = Imagen(trampa_id=trampa, ruta_fichero="rota.jpg", estado="pendiente")
        s.add(im)
        s.commit()
        ident = im.id

    def en_la_cola() -> bool:
        # `limit` alto a proposito: la vista sirve 20 por defecto y la base de pruebas
        # arrastra pendientes de otros casos. Con el limite por defecto, esta prueba estaria
        # midiendo el orden por fecha en vez del estado, que es lo que se quiere comprobar.
        lote = cliente.get("/api/v1/inference/pending?limit=500", headers=worker).json()
        return any(i["image_id"] == ident for i in lote)

    assert en_la_cola(), "de partida tiene que estar pendiente"

    r = cliente.put(f"/api/v1/inference/{ident}/ilegible", headers=worker)
    assert r.status_code == 200 and r.json()["estado"] == "error"
    assert not en_la_cola(), "una ilegible tiene que SALIR de la cola, no quedarse dando vueltas"

    # Y un aviso que llega tarde no puede tirar un resultado bueno.
    with sesion_corta() as s:
        im = s.get(Imagen, ident)
        im.estado = "procesada"
        im.num_insectos = 4
        s.add(im)
        s.commit()
    r = cliente.put(f"/api/v1/inference/{ident}/ilegible", headers=worker)
    assert r.json()["cambiado"] is False
    with sesion_corta() as s:
        assert s.get(Imagen, ident).estado == "procesada", "no se degrada lo ya procesado"


def test_avisa_de_una_trampa_callada_aunque_no_tenga_invernadero(cliente):
    """El aviso existia y no saltaba: se saltaba las trampas sin invernadero asignado.

    Es el estado en el que esta la unica trampa real del proyecto. El 2026-09-19 estuvo 15 min
    muerta --se reinicio a mitad de una tanda de video-- y `/avisos` contestaba `total: 0`: el
    servidor tenia el dato y sabia que su intervalo eran 30 s, y no dijo nada. El invernadero
    es una etiqueta de organizacion, no una condicion para estar viva.

    La trampa se crea aqui, con su unica foto vieja: usar la de la fixture medía otra cosa
    --tiene una foto recien subida, asi que el maximo era de hace 0 s y no habia nada que
    avisar--. Una prueba que pasa por el motivo equivocado es peor que no tenerla.
    """
    from datetime import datetime, timedelta, timezone
    from app.core.db import sesion_corta
    from app.models.models import ConfigCamara, Imagen, Trampa

    with sesion_corta() as s:
        t = Trampa(device_id="zero-callada-prueba", alias="Callada", owner_id=None,
                   invernadero_id=None)          # SIN invernadero: el caso que se comia
        s.add(t)
        s.commit()
        tid = t.id
        s.add(ConfigCamara(trampa_id=tid, intervalo_seg=30))
        # Su unica foto es de hace media hora: con intervalo de 30 s deberia haber subido 60.
        s.add(Imagen(trampa_id=tid, ruta_fichero="vieja.jpg", estado="procesada",
                     capturada=datetime.now(timezone.utc) - timedelta(minutes=30)))
        s.commit()

    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()
    titulos = " | ".join(a["titulo"] for a in avisos["avisos"])
    assert "Callada" in titulos and "sin subir fotos" in titulos,         "una trampa que subia fotos y ha dejado de hacerlo tiene que avisar: " + titulos


def test_la_trampa_trae_su_ultima_imagen(cliente, trampa):
    """Sin esto, la vista Trampas pedia una peticion POR TRAMPA para la ultima foto.

    Con 20 trampas son 20 peticiones simultaneas contra un pool de 15 conexiones: el fallo que
    ya dejo la API sin servicio en el ensayo de 20 (G15). Y de paso viaja `procesada`, que no
    se exponia en ningun sitio y obligaba a la tarjeta a poner «Fecha de analisis no
    disponible»: un recuento sin saber de cuando es el analisis no se puede interpretar.
    """
    from datetime import datetime, timezone
    from app.core.db import sesion_corta
    from app.models.models import Imagen

    with sesion_corta() as s:
        s.add(Imagen(trampa_id=trampa, ruta_fichero="pendiente.jpg", estado="pendiente",
                     num_insectos=0, capturada=datetime.now(timezone.utc)))
        s.commit()

    t = [x for x in cliente.get("/api/v1/traps", auth=ADMIN).json() if x["id"] == trampa][0]
    ult = t["ultima_imagen"]
    assert ult is not None, "la trampa tiene fotos y no trae ninguna"
    assert ult["estado"] == "pendiente"
    # LA REGLA DE LA CASA: un 0 en una foto que nadie ha mirado no es «no hay insectos».
    assert ult["num_insectos"] is None, \
        "una foto sin analizar no puede dar un recuento: seria inventarse un cero"
    assert "procesada" in ult, "falta la fecha de analisis, que es el dato que no existia"


def test_el_video_no_escribe_en_la_base_por_cada_fotograma(cliente, trampa, jpeg):
    """Una escritura por fotograma agota el pool y deja la API SIN SERVICIO.

    El 2026-09-19 a las 19:19, con Antonio mirando el directo, la API dejo de responder:

        sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached

    `/frame` llamaba a `touch_seen` --que hace `commit`-- en cada fotograma. A los 6,6 fps que
    da la Zero son ~7 transacciones de escritura por segundo contra SQLite sobre una tarjeta
    SD: se encolan, cada una retiene su conexion, y el pool (5 + 10) se agota. Entonces no
    responde NADA: ni la web, ni la telemetria, ni el propio video. Y el servicio no se cae,
    asi que desde fuera parece una caida sin proceso muerto.

    Se comprueba lo que importa: que veinte fotogramas seguidos NO son veinte escrituras.
    """
    import os
    from app.core.db import sesion_corta
    from app.models.models import Trampa
    from app.api import live as live_api

    device = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["device_id"]
    cabeceras = {"X-API-Key": os.environ["DEVICE_API_KEY"], "Content-Type": "image/jpeg"}

    escrituras = {"n": 0}
    original = live_api.traps_svc.touch_seen

    def contando(session, trap):
        escrituras["n"] += 1
        return original(session, trap)

    live_api.traps_svc.touch_seen = contando
    live_api._ID_DE_DEVICE.clear()
    live_api._VISTO_EN_BD.clear()
    live_api._PENDIENTE_CONSUMO.clear()
    try:
        for _ in range(20):
            r = cliente.post(f"/api/v1/traps/{device}/frame", headers=cabeceras, content=jpeg)
            assert r.status_code == 200, r.text
    finally:
        live_api.traps_svc.touch_seen = original

    assert escrituras["n"] <= 2, \
        "20 fotogramas han provocado %d escrituras: eso es lo que agota el pool" % escrituras["n"]
    # Y el primero SI tiene que escribir: si no, `ultimo_visto` se queda viejo y la trampa
    # aparece OFFLINE en su propio panel mientras emite video en directo.
    assert escrituras["n"] >= 1, "el primer fotograma tiene que refrescar «visto por ultima vez»"
