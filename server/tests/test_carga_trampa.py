"""El veredicto de carga que calcula la trampa manda sobre la tendencia del servidor."""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev, **extra):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": dev, "voltaje": 3.9, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _trampa(cliente, dev):
    return next(t for t in cliente.get("/api/v1/traps", auth=ADMIN).json() if t["device_id"] == dev)


def test_manda_el_veredicto_de_la_trampa(cliente):
    dev = "zero-CARGA1"
    _tele(cliente, dev, carga={"estado": "cargando", "origen": "salto", "confianza": "alta",
                               "mv_min": None, "n": 40})
    t = _trampa(cliente, dev)
    assert t["carga_estado"] == "cargando"
    assert t["carga_fuente"] == "trampa:salto" and t["carga_confianza"] == "alta"


def test_un_informe_malformado_no_rompe_nada(cliente):
    dev = "zero-CARGA2"
    _tele(cliente, dev, carga={"estado": "inventado"})
    _tele(cliente, dev, carga="basura")
    t = _trampa(cliente, dev)
    assert t["carga_fuente"] is None or not t["carga_fuente"].startswith("trampa")


def test_un_veredicto_viejo_caduca(cliente):
    """La vigencia cuenta desde la última muestra de la trampa, no desde que llega."""
    dev = "zero-CARGA3"
    _tele(cliente, dev, carga={"estado": "cargando", "origen": "rapida", "confianza": "media",
                               "mv_min": None, "edad_s": 600})
    t = _trampa(cliente, dev)
    assert not (t["carga_fuente"] or "").startswith("trampa")


def test_un_numero_desmesurado_no_rompe_la_telemetria(cliente):
    dev = "zero-CARGA4"
    _tele(cliente, dev, carga={"estado": "cargando", "origen": "salto", "confianza": "alta",
                               "mv_min": 10 ** 400, "edad_s": 10 ** 400})
    t = _trampa(cliente, dev)
    assert t["carga_estado"] == "cargando" and t["tendencia_mv_h"] is None


def test_ina219_limpia_soc_antiguo_y_no_recurre_a_tension(cliente):
    dev = "zero-INA219"
    _tele(cliente, dev, bateria=78, crate=-4)
    _tele(cliente, dev, carga={"estado": "indeterminado", "origen": "ina219",
                               "confianza": "baja", "corriente_ma": None,
                               "edad_s": 600})
    t = _trampa(cliente, dev)
    assert t["bateria"] is None and t["crate"] is None
    assert t["bateria_estimada"] is not None
    assert t["carga_estado"] == "midiendo"
    assert t["carga_fuente"] == "trampa:ina219 sin dato"


def test_porcentaje_estimado_es_separado_y_no_activa_soc_medido(cliente):
    dev = "zero-ESTIMACION"
    _tele(cliente, dev, voltaje=3.79325,
          carga={"estado": "descargando", "origen": "ina219"})
    t = _trampa(cliente, dev)
    assert t["bateria"] is None and t["bateria_estimada"] == 40
    _tele(cliente, dev, voltaje=4.177454)
    assert _trampa(cliente, dev)["bateria_estimada"] == 100
