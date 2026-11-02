"""
Pruebas de las dos piezas que faltaban en el modelo de datos (mentoría 3):

- **Condiciones de captura (D4)**: cada foto perdía para siempre la resolución, la exposición
  y la escala con las que se tomó, que es justo el eje del análisis del proyecto.
- **Tarjeta (D2)**: el sistema no sabía que una trampa adhesiva ACUMULA, así que un conteo no
  se podía referir a un periodo ni distinguir un cambio de cartón de una plaga que se va.

Cada prueba defiende un comportamiento cuya pérdida sería silenciosa: nada falla, simplemente
los números dejan de significar lo que parecen significar.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _subir(cliente, jpeg, device="esp32-COND", **extra):
    return cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                        files={"image": ("c.jpg", jpeg, "image/jpeg")},
                        data={"device_id": device, **extra})


def _trap_id(cliente, device):
    return [t for t in cliente.get("/api/v1/traps?incluir_demo=true", auth=ADMIN).json()
            if t["device_id"] == device][0]["id"]


# ------------------------------------------------------- D4 · condiciones de captura
def test_la_imagen_guarda_con_que_se_tomo(cliente, jpeg):
    r = _subir(cliente, jpeg, device="esp32-COND1", config_version="1")
    img = cliente.get(f"/api/v1/images/{r.json()['image_id']}", auth=ADMIN).json()
    c = img["condiciones"]
    assert c is not None, "sin esto, la foto no se puede estratificar después por condiciones"
    assert c["config_version"] == 1
    assert c["resolucion"]                    # denormalizada, para filtrar sin JOIN
    assert c["completa"] is True              # la versión estaba en el histórico


def test_la_version_que_manda_es_la_de_la_placa_no_la_del_servidor(cliente, jpeg):
    """Entre editar la config y que la placa la aplique pasa hasta un intervalo entero.

    La foto se tomó con la ANTERIOR, y atribuirle la del servidor sería inventarse el dato.
    """
    _subir(cliente, jpeg, device="esp32-COND2", config_version="1")
    tid = _trap_id(cliente, "esp32-COND2")
    cambio = cliente.put(f"/api/v1/traps/{tid}/config", auth=ADMIN,
                         json={"ancho": 1600, "alto": 1200, "ev": -0.5})
    assert cambio.status_code == 200 and cambio.json()["version"] > 1
    # la placa sigue subiendo con la versión vieja
    r = _subir(cliente, jpeg, device="esp32-COND2", config_version="1")
    img = cliente.get(f"/api/v1/images/{r.json()['image_id']}", auth=ADMIN).json()
    assert img["condiciones"]["config_version"] == 1
    assert img["condiciones"]["ev"] == 0
    assert img["condiciones"]["ancho"] == 4608


def test_cada_cambio_de_config_queda_congelado_en_el_historial(cliente, jpeg):
    """Las fotos solo guardan el número de versión: sin el histórico no significa nada."""
    _subir(cliente, jpeg, device="esp32-COND3", config_version="1")
    tid = _trap_id(cliente, "esp32-COND3")
    for ajustes in ({"ev": -0.5, "enfoque": "manual", "lente_dioptrias": 3.3},
                    {"ev": 0.5, "balance": "cloudy"}):
        cambio = cliente.put(f"/api/v1/traps/{tid}/config", auth=ADMIN, json=ajustes)
        assert cambio.status_code == 200
    h = cliente.get(f"/api/v1/traps/{tid}/config/historial", auth=ADMIN).json()["historial"]
    assert len(h) >= 3                                  # la inicial y los dos cambios
    assert [x["version"] for x in h] == sorted((x["version"] for x in h), reverse=True)
    assert h[0]["ev"] == 0.5 and h[1]["ev"] == -0.5
    assert h[0]["balance"] == "cloudy" and h[1]["balance"] == "auto"
    assert h[0]["enfoque"] == "manual" and h[0]["lente_dioptrias"] == 3.3
    assert h[0]["num_imagenes"] == 0                    # aún no ha subido nada con esa
    assert any(x["num_imagenes"] >= 1 for x in h)


def test_se_puede_aislar_la_galeria_por_resolucion(cliente, jpeg):
    """El hallazgo del proyecto es que la resolución fija el techo: hay que poder separarla."""
    _subir(cliente, jpeg, device="esp32-COND4", config_version="1")
    # El JPEG de prueba mide 64x48: la resolución se guarda en píxeles, sin apodos.
    n = cliente.get("/api/v1/images/count?resolucion=64x48", auth=ADMIN).json()["total"]
    assert n >= 1
    assert cliente.get("/api/v1/images/count?resolucion=NOEXISTE",
                       auth=ADMIN).json()["total"] == 0


def test_la_resolucion_sale_de_los_pixeles_no_de_lo_que_cree_el_servidor(cliente, jpeg):
    """La placa pierde su config al arrancar en frío y puede disparar con otra distinta.

    Medido en la Pi: reportaba `version 0` mientras el servidor tenía la 59 y la foto llegaba
    en SVGA con la configuración puesta en QSXGA. Copiar la del servidor habría escrito una
    condición FALSA en la imagen, que es peor que no escribir ninguna.
    """
    _subir(cliente, jpeg, device="esp32-COND7", config_version="1")
    tid = _trap_id(cliente, "esp32-COND7")
    cambio = cliente.put(f"/api/v1/traps/{tid}/config", auth=ADMIN,
                         json={"ancho": 1920, "alto": 1080})
    assert cambio.status_code == 200 and cambio.json()["ancho"] == 1920
    r = _subir(cliente, jpeg, device="esp32-COND7", config_version="0")   # placa recién arrancada
    img = cliente.get(f"/api/v1/images/{r.json()['image_id']}", auth=ADMIN).json()
    assert img["condiciones"]["resolucion"] == "64x48"      # lo que de verdad midió la foto
    assert img["condiciones"]["completa"] is False          # y se admite que no se sabe más


def test_la_escala_calibrada_de_la_trampa_viaja_con_cada_foto(cliente, jpeg):
    """px/mm es lo que convierte un conteo en una medida; se mide una vez y se hereda."""
    _subir(cliente, jpeg, device="esp32-COND5")
    tid = _trap_id(cliente, "esp32-COND5")
    r = cliente.patch(f"/api/v1/traps/{tid}", auth=ADMIN,
                      json={"px_mm": 7.5, "distancia_cm": 18.0})
    assert r.status_code == 200 and r.json()["px_mm"] == 7.5
    img_id = _subir(cliente, jpeg, device="esp32-COND5", config_version="1").json()["image_id"]
    img = cliente.get(f"/api/v1/images/{img_id}", auth=ADMIN).json()
    assert img["condiciones"]["px_mm"] == 7.5


def test_una_escala_absurda_se_rechaza(cliente, jpeg):
    _subir(cliente, jpeg, device="esp32-COND6")
    tid = _trap_id(cliente, "esp32-COND6")
    assert cliente.patch(f"/api/v1/traps/{tid}", auth=ADMIN,
                         json={"px_mm": 0}).status_code == 422


# ------------------------------------------------------------------ D2 · tarjeta
def test_la_primera_tarjeta_adopta_las_fotos_que_ya_habia(cliente, jpeg):
    """El histórico existente no puede quedarse huérfano al empezar a llevar el registro."""
    for _ in range(3):
        _subir(cliente, jpeg, device="esp32-TARJ1")
    tid = _trap_id(cliente, "esp32-TARJ1")
    t = cliente.get(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN).json()
    assert t["puesta"] is True
    assert t["num_imagenes"] >= 3


def test_tarjeta_nueva_cierra_la_anterior_y_empieza_de_cero(cliente, jpeg):
    """El punto entero de D2: la caída del conteo a cero deja de ser una anomalía.

    Sin esto, cambiar el cartón se lee como que la plaga ha desaparecido — el peor error
    posible en una herramienta de decisión, porque informa de una mejora cuando no ha pasado
    nada.
    """
    _subir(cliente, jpeg, device="esp32-TARJ2")
    tid = _trap_id(cliente, "esp32-TARJ2")
    vieja = cliente.get(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN).json()

    r = cliente.post(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN, json={"nota": "cartón nuevo"})
    assert r.status_code == 200
    d = r.json()
    assert d["anterior"]["id"] == vieja["id"]
    assert d["anterior"]["retirada"] is not None
    assert d["actual"]["id"] != vieja["id"]
    assert d["actual"]["num_imagenes"] == 0      # periodo limpio
    assert d["actual"]["nota"] == "cartón nuevo"

    # y las fotos nuevas se cuentan en la tarjeta nueva, no en la vieja
    _subir(cliente, jpeg, device="esp32-TARJ2")
    assert cliente.get(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN).json()["num_imagenes"] == 1


def test_el_historial_de_tarjetas_es_la_serie_de_la_trampa(cliente, jpeg):
    _subir(cliente, jpeg, device="esp32-TARJ3")
    tid = _trap_id(cliente, "esp32-TARJ3")
    cliente.post(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN, json={"nota": "1"})
    cliente.post(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN, json={"nota": "2"})
    h = cliente.get(f"/api/v1/traps/{tid}/tarjetas", auth=ADMIN).json()["tarjetas"]
    assert len(h) == 3
    assert h[0]["puesta"] is True
    assert all(x["puesta"] is False for x in h[1:]), "solo puede haber UNA tarjeta puesta"


def test_no_se_da_un_ritmo_de_capturas_con_dos_horas_de_exposicion(cliente, jpeg):
    """30 insectos en 2 h serían «360/día»: una alarma falsa nada más cambiar el cartón."""
    _subir(cliente, jpeg, device="esp32-TARJ4")
    tid = _trap_id(cliente, "esp32-TARJ4")
    t = cliente.post(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN).json()["actual"]
    assert t["dias_expuesta"] < 0.5
    assert t["capturas_por_dia"] is None


def test_la_tarjeta_la_cambia_el_admin(cliente, jpeg):
    _subir(cliente, jpeg, device="esp32-TARJ5")
    tid = _trap_id(cliente, "esp32-TARJ5")
    assert cliente.post(f"/api/v1/traps/{tid}/tarjeta").status_code == 401


def test_una_trampa_que_no_existe_no_inventa_tarjeta(cliente):
    assert cliente.get("/api/v1/traps/999999/tarjeta", auth=ADMIN).status_code == 404
