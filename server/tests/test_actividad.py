"""
Pruebas de D1: los indicadores cuentan insectos, no fotos.

El defecto original sumaba `num_insectos` de cada foto de la ventana. Como una trampa adhesiva
acumula y los insectos siguen pegados, eso contaba **el mismo bicho una vez por captura**: con
las 108 fotos diarias medidas en la Pi, 30 moscas blancas se mostraban como **3.240**, y el
número subía o bajaba al tocar el intervalo de captura, que no tiene nada que ver con la plaga.

Estas pruebas fijan las dos magnitudes y su diferencia, porque es fácil que alguien «simplifique»
volviendo a la suma sin que nada falle: solo cambian los números.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import ADMIN, DEVICE_KEY


def _foto(cliente, jpeg, device, insectos, hace_horas=0.0, clase="WF"):
    """Sube una foto y la marca como procesada con ese conteo, fechada hacia atrás."""
    cap = datetime.now(timezone.utc) - timedelta(hours=hace_horas)
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": device, "captured_at": cap.isoformat()})
    img_id = r.json()["image_id"]
    # Se escribe el resultado directamente: el worker de IA no corre en las pruebas.
    from sqlmodel import Session
    from app.core.db import engine
    from app.models.models import Deteccion, Imagen
    with Session(engine) as s:
        im = s.get(Imagen, img_id)
        im.estado = "procesada"
        im.num_insectos = insectos
        im.modelo_version = "test"
        s.add(im)
        for i in range(insectos):
            s.add(Deteccion(imagen_id=img_id, clase=clase, confianza=0.9,
                            x=0.5, y=0.5, w=0.1, h=0.1))
        s.commit()
    return img_id


def _trap_id(cliente, device):
    return [t for t in cliente.get("/api/v1/traps?incluir_demo=true", auth=ADMIN).json()
            if t["device_id"] == device][0]["id"]


def _nuevas(session, trap_id, horas):
    from app.services import actividad as act
    return act.nuevas(session, trap_id, datetime.now(timezone.utc) - timedelta(hours=horas))


def _sesion():
    from sqlmodel import Session
    from app.core.db import engine
    return Session(engine)


# ------------------------------------------------------------------ la magnitud
def test_las_fotos_de_la_misma_tarjeta_no_se_suman(cliente, jpeg):
    """EL fallo de D1. Tres fotos de 30 insectos son 30 insectos, no 90."""
    dev = "esp32-ACT1"
    _foto(cliente, jpeg, dev, 30, hace_horas=3)
    _foto(cliente, jpeg, dev, 30, hace_horas=2)
    _foto(cliente, jpeg, dev, 30, hace_horas=1)
    tid = _trap_id(cliente, dev)
    with _sesion() as s:
        from app.services import actividad as act
        assert act.conteo_actual(s, tid) == 30
        assert _nuevas(s, tid, 24) == 30        # y no 90


def test_la_actividad_es_la_diferencia_no_el_ultimo_conteo(cliente, jpeg):
    """Con 40 pegados ayer y 46 hoy, lo nuevo son 6. Es lo que decide un tratamiento."""
    dev = "esp32-ACT2"
    _foto(cliente, jpeg, dev, 40, hace_horas=40)     # fuera de la ventana de 24 h
    _foto(cliente, jpeg, dev, 46, hace_horas=1)
    tid = _trap_id(cliente, dev)
    with _sesion() as s:
        assert _nuevas(s, tid, 24) == 6
        from app.services import actividad as act
        assert act.conteo_actual(s, tid) == 46      # el acumulado sigue siendo 46


def test_cambiar_la_tarjeta_no_cuenta_como_plaga_que_desaparece(cliente, jpeg):
    """El peor error posible: informar de una mejora cuando solo se cambió el cartón.

    Sin trocear por tarjeta, pasar de 50 pegados a 4 daría −46 capturas «nuevas».
    """
    dev = "esp32-ACT3"
    _foto(cliente, jpeg, dev, 50, hace_horas=20)
    tid = _trap_id(cliente, dev)
    cliente.post(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN, json={"nota": "cartón nuevo"})
    _foto(cliente, jpeg, dev, 4, hace_horas=0.2)     # ya en la tarjeta nueva
    with _sesion() as s:
        # 50 de la tarjeta vieja (que llevaba puesta desde antes de la ventana... pero su
        # primera foto está DENTRO, así que aporta 50) + 4 de la nueva, que partió de cero.
        n = _nuevas(s, tid, 24)
        assert n == 54, f"esperado 54 (50 + 4), obtenido {n}"
        assert n > 0, "un cambio de tarjeta nunca puede dar actividad negativa"


def test_una_tarjeta_puesta_dentro_de_la_ventana_parte_de_cero(cliente, jpeg):
    dev = "esp32-ACT4"
    _foto(cliente, jpeg, dev, 200, hace_horas=50)    # historia vieja, fuera de la ventana
    tid = _trap_id(cliente, dev)
    cliente.post(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN)
    _foto(cliente, jpeg, dev, 7, hace_horas=0.5)
    with _sesion() as s:
        assert _nuevas(s, tid, 24) == 7   # los 200 viejos no reaparecen


def test_sin_fotos_no_se_inventa_actividad(cliente, jpeg):
    dev = "esp32-ACT5"
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("c.jpg", jpeg, "image/jpeg")}, data={"device_id": dev})
    tid = _trap_id(cliente, dev)                     # la foto queda 'pendiente', sin analizar
    with _sesion() as s:
        assert _nuevas(s, tid, 24) is None


# ------------------------------------------------------------------ el panel
def test_los_kpis_distinguen_acumulado_de_ritmo(cliente, jpeg):
    dev = "esp32-ACT6"
    _foto(cliente, jpeg, dev, 11, hace_horas=30)
    _foto(cliente, jpeg, dev, 20, hace_horas=1)
    s = cliente.get("/api/v1/stats", auth=ADMIN).json()
    assert s["insectos"] >= 20            # acumulado: lo pegado ahora
    assert s["actividad_1d"] >= 9         # ritmo: lo nuevo (20 - 11)


def test_la_composicion_por_especie_no_multiplica_por_foto(cliente, jpeg):
    """Antes contaba las detecciones de TODAS las fotos: 5 moscas en 4 fotos daban 20."""
    dev = "esp32-ACT7"
    for h in (4, 3, 2, 1):
        _foto(cliente, jpeg, dev, 5, hace_horas=h, clase="WF")
    tid = _trap_id(cliente, dev)
    with _sesion() as s:
        from app.services import actividad as act
        assert act.especies_en_tarjetas(s, [tid]) == {"WF": 5}


def test_los_trips_cuentan_como_plaga(cliente, jpeg):
    """Modelo de 4 clases (2026-10-16): TR llega como clase y suma en plagas, no en auxiliares."""
    dev = "esp32-ACT-TR"
    _foto(cliente, jpeg, dev, 3, hace_horas=1, clase="TR")
    s = cliente.get("/api/v1/stats", auth=ADMIN).json()
    assert s["por_especie"]["TR"] >= 3 and s["trips"] >= 3
    assert s["plagas"] == s["mosca_blanca"] + s["trips"]
    assert "auxiliares" not in s, "la fauna auxiliar ya no se gestiona (2026-10-23)"
    r = cliente.get("/api/v1/images", params={"clase": "TR"}, auth=ADMIN).json()
    assert r and all(any(d["clase"] == "TR" for d in im["detecciones"]) for im in r)


def test_la_actividad_no_depende_del_intervalo_de_captura(cliente, jpeg):
    """Dos trampas con la misma plaga y distinto ritmo de fotos dan el MISMO número.

    Era el síntoma más claro del fallo: el indicador cambiaba al tocar `intervalo_seg`.
    """
    _foto(cliente, jpeg, "esp32-ACT8", 12, hace_horas=2)      # 1 foto
    for h in (5, 4, 3, 2, 1):                                 # 5 fotos, misma tarjeta
        _foto(cliente, jpeg, "esp32-ACT9", 12, hace_horas=h)
    with _sesion() as s:
        a = _nuevas(s, _trap_id(cliente, "esp32-ACT8"), 24)
        b = _nuevas(s, _trap_id(cliente, "esp32-ACT9"), 24)
    assert a == b == 12


# ------------------------------------------------------------------ mapa de calor
def test_el_mapa_de_calor_da_presion_y_acumulado_por_separado(cliente, jpeg):
    dev = "esp32-ACT10"
    _foto(cliente, jpeg, dev, 60, hace_horas=200)    # fuera de la ventana de presión (7 d)
    _foto(cliente, jpeg, dev, 63, hace_horas=2)
    tid = _trap_id(cliente, dev)
    gh = cliente.post("/api/v1/greenhouses", auth=ADMIN,
                      json={"nombre": "GH actividad", "num_lineos": 2}).json()
    cliente.patch(f"/api/v1/traps/{tid}", auth=ADMIN,
                  json={"invernadero_id": gh["id"], "sector": "A", "linea": 1})

    hm = cliente.get(f"/api/v1/greenhouses/{gh['id']}/heatmap", auth=ADMIN).json()
    assert hm["magnitud"] == "capturas_nuevas"
    assert hm["sectors"]["A"] == 3        # 63 - 60, no 123 ni 63
    assert hm["total"] == 3


def test_el_mapa_dice_cuantas_fotos_quedan_sin_analizar(cliente, jpeg):
    """Un cero sin analizar no es un cero medido, y la web tiene que poder distinguirlos.

    El worker de IA se arranca y se cierra a mano (es el PC del usuario), así que hay ratos
    **normales** con fotos en cola. Durante esos ratos el mapa decía «0 pegados · 0 capturas
    nuevas», que se lee como **«no hay plaga»** — justo la decisión contraria a la correcta.

    No se trata de exigir que el worker esté corriendo: se trata de no afirmar lo que todavía
    no se ha contado.
    """
    dev = "esp32-PEND1"
    _foto(cliente, jpeg, dev, 5, hace_horas=1)            # esta sí está procesada
    tid = _trap_id(cliente, dev)
    gh = cliente.post("/api/v1/greenhouses", auth=ADMIN,
                      json={"nombre": "GH pendientes", "num_lineos": 3}).json()
    cliente.patch(f"/api/v1/traps/{tid}", auth=ADMIN,
                  json={"invernadero_id": gh["id"], "sector": "B", "linea": 2})

    m = cliente.get(f"/api/v1/greenhouses/{gh['id']}/map", auth=ADMIN).json()
    assert m["pendientes"] == 0, "sin cola, el cero del mapa SÍ es una medida"

    # Llega una foto nueva y nadie la ha analizado todavía.
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": dev})
    assert r.status_code == 201

    m = cliente.get(f"/api/v1/greenhouses/{gh['id']}/map", auth=ADMIN).json()
    assert m["pendientes"] >= 1, "la web no puede saber que el conteo está incompleto"

    # Y no cuenta las de OTROS invernaderos: el aviso es de este, o no sirve.
    _foto(cliente, jpeg, "esp32-PEND2", 1, hace_horas=1)
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("c.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-PEND2"})
    m2 = cliente.get(f"/api/v1/greenhouses/{gh['id']}/map", auth=ADMIN).json()
    assert m2["pendientes"] == m["pendientes"], "se está contando la cola de otro invernadero"

def test_una_trampa_sin_medidas_es_sin_dato_no_cero(cliente, jpeg):
    """Una trampa cuya foto nadie ha contado no puede sumar «0 capturas» a su celda.

    El mapa pintaba igual «he contado y no hay plaga» que «no he contado nada», y lo primero
    lleva a no tratar. La celda dice cuántas de sus trampas tienen medida y cuántas no.
    """
    medida, sin_medir = "esp32-SD1", "esp32-SD2"
    _foto(cliente, jpeg, medida, 0, hace_horas=1)        # contada: cero de verdad
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},   # sin analizar
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": sin_medir})
    assert r.status_code == 201
    gh = cliente.post("/api/v1/greenhouses", auth=ADMIN,
                      json={"nombre": "GH sin dato", "num_lineos": 3}).json()
    for dev, linea in ((medida, 1), (sin_medir, 2)):
        cliente.patch(f"/api/v1/traps/{_trap_id(cliente, dev)}", auth=ADMIN,
                      json={"invernadero_id": gh["id"], "sector": "A", "linea": linea})

    m = cliente.get(f"/api/v1/greenhouses/{gh['id']}/map", auth=ADMIN).json()
    c1, c2 = m["cells"]["A|1"], m["cells"]["A|2"]
    assert (c1["con_dato"], c1["sin_dato"], c1["nuevas"]) == (1, 0, 0), "el cero medido se pierde"
    assert (c2["con_dato"], c2["sin_dato"]) == (0, 1), "una trampa sin contar pasa por un cero"
    assert m["trampas_sin_dato"] == 1
