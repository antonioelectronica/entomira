"""Sensores en directo: el instante del INA226 y de la celda, y la estimación de autonomía."""
from __future__ import annotations

import asyncio
import time

from conftest import ADMIN, DEVICE_KEY

from app.services import live as live_svc
from app.services import sensores as sensores_svc


def _tele(cliente, dev, red="wifi", **extra):
    cuerpo = {"device_id": dev, "red": red, **extra}
    return cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=cuerpo)


def _id(cliente, dev):
    return next(t["id"] for t in cliente.get("/api/v1/traps", auth=ADMIN).json()
                if t["device_id"] == dev)


def _sensores(cliente, tid):
    return cliente.post(f"/api/v1/traps/{tid}/sensores", auth=ADMIN)


def test_el_instante_llega_al_panel(cliente):
    assert _tele(cliente, "zero-SEN1", ina_ya={"v": 5.1, "ma": 200.0},
                 voltaje=3.7, bateria=50).status_code == 200
    tid = _id(cliente, "zero-SEN1")
    d = _sensores(cliente, tid).json()
    assert d["rail_v"] == 5.1 and d["ma"] == 200.0 and d["mw"] == 1020.0
    assert d["celda_v"] == 3.7 and d["soc"] == 50
    assert d["edad_ina_s"] is not None and d["edad_ina_s"] < 5


def test_ina219_en_directo_cambia_antes_de_guardar_otro_minuto(cliente):
    dev = "zero-SEN-INA219"
    assert _tele(cliente, dev, ina219={"v": 3.601, "ma": -215.0}).status_code == 200
    tid = _id(cliente, dev)
    primera = _sensores(cliente, tid).json()
    assert primera["ina219_ma"] == -215.0
    assert primera["edad_hw_s"] < 5

    assert _tele(cliente, dev, ina219={"v": 3.577, "ma": -339.0}).status_code == 200
    segunda = _sensores(cliente, tid).json()
    assert segunda["ina219_ma"] == -339.0
    assert segunda["ina219_v"] == 3.577
    historico = cliente.get(f"/api/v1/traps/{tid}/hardware", auth=ADMIN).json()["lecturas"]
    assert len(historico) == 1
    assert historico[0]["corriente_ma"] == -215.0


def test_sin_lectura_es_hueco_no_cero(cliente):
    """Una trampa sin INA ni MAX: todo hueco, y la estimación dice por qué no la hay."""
    assert _tele(cliente, "zero-SEN2").status_code == 200
    d = _sensores(cliente, _id(cliente, "zero-SEN2")).json()
    assert d["rail_v"] is None and d["ma"] is None and d["mw"] is None
    assert d["celda_v"] is None and d["soc"] is None
    assert d["estimacion"]["i_celda_ma"] is None and d["estimacion"]["motivo"]


def test_un_valor_raro_no_tumba_la_telemetria(cliente):
    """Un NaN no llega por JSON, pero un texto o un fuera de rango sí: hueco y 200."""
    r = _tele(cliente, "zero-SEN3", ina_ya={"v": "cinco", "ma": 99999}, voltaje=9.9)
    assert r.status_code == 200
    d = _sensores(cliente, _id(cliente, "zero-SEN3")).json()
    assert d["rail_v"] is None and d["ma"] is None and d["celda_v"] is None


def test_el_panel_acelera_por_wifi_sin_pedir_video(cliente):
    """El latido de sensores corta la retención de la telemetría, pero NO enciende el vídeo."""
    _tele(cliente, "zero-SEN4")
    tid = _id(cliente, "zero-SEN4")
    assert _sensores(cliente, tid).json()["rapido"] is True
    assert sensores_svc.quiere(tid)
    en_vivo = asyncio.run(live_svc.esperar_peticion(tid, 5.0))
    assert en_vivo is False                  # volvió enseguida, y sin vídeo


def test_por_4g_tambien_se_acelera(cliente):
    """Desde el 15/10/2026 (orden de Antonio): el plan da de sobra y la latencia importaba más."""
    _tele(cliente, "zero-SEN5", red="lte")
    tid = _id(cliente, "zero-SEN5")
    d = _sensores(cliente, tid).json()
    assert d["rapido"] is True and sensores_svc.quiere(tid)


def test_pedir_log_suelta_la_espera_de_la_telemetria(cliente):
    _tele(cliente, "zero-SEN6")
    tid = _id(cliente, "zero-SEN6")
    live_svc.pedir_log(tid)
    t0 = time.time()
    asyncio.run(live_svc.esperar_peticion(tid, 5.0))
    assert time.time() - t0 < 1.0
    live_svc.log_recibido(tid)


def test_estimacion_de_la_celda():
    # 5 V x 200 mA = 1 W a la salida; con η 0,8 salen 1,25 W de la celda: a 3,75 V, 333 mA.
    e = sensores_svc.estimar(5.0, 200.0, 3.75, 50.0, "descargando", eta=0.8, mah=3000.0)
    assert e["p_salida_w"] == 1.0
    assert abs(e["i_celda_ma"] - 333.3) < 0.1
    # La mitad de 3000 mAh a 333 mA: 4,5 h.
    assert abs(e["horas"] - 4.5) < 0.01


def test_sin_capacidad_no_se_inventa_el_tiempo():
    e = sensores_svc.estimar(5.0, 200.0, 3.75, 50.0, "descargando", eta=0.8, mah=None)
    assert e["i_celda_ma"] is not None and e["horas"] is None
    assert "capacidad" in e["motivo"]


def test_cargando_no_se_estima_la_descarga():
    e = sensores_svc.estimar(5.0, 200.0, 3.75, 50.0, "cargando", eta=0.8, mah=3000.0)
    assert e["i_celda_ma"] is None and e["horas"] is None and "cargando" in e["motivo"]


def test_sin_consumo_a_la_salida_no_es_autonomia_infinita():
    """0 mA a la salida: la trampa va por otro camino. No se divide por cero ni se promete nada."""
    e = sensores_svc.estimar(5.39, 0.0, 3.08, 0.0, "descargando", eta=0.85, mah=3000.0)
    assert e["i_celda_ma"] is None and e["horas"] is None and e["motivo"]


def test_una_lectura_vieja_no_pasa_por_instantanea(monkeypatch):
    sensores_svc.anotar(9001, {"v": 5.0, "ma": 100.0}, 3.7, 40)
    reloj = sensores_svc.time.time() + sensores_svc.FRESCO_S + 5
    monkeypatch.setattr(sensores_svc.time, "time", lambda: reloj)
    d = sensores_svc.ahora(9001, "descargando")
    assert d["rail_v"] is None and d["celda_v"] is None
    assert d["edad_ina_s"] > sensores_svc.FRESCO_S          # pero se dice cuánto tiene


def test_sin_descarga_comprobada_no_hay_autonomia():
    """«Reposo» o «midiendo» puede ser un cargador recién enchufado: corriente sí, tiempo no."""
    for estado in ("reposo", "midiendo", None):
        e = sensores_svc.estimar(5.0, 200.0, 3.75, 50.0, estado, eta=0.8, mah=3000.0)
        assert e["i_celda_ma"] is not None and e["horas"] is None, estado
        assert "batería" in e["motivo"]


def test_lecturas_de_momentos_distintos_no_se_cruzan(monkeypatch):
    """El INA dejó de contestar hace 20 s y la celda sigue: no se divide lo viejo por lo nuevo."""
    reloj = [1_000_000.0]
    monkeypatch.setattr(sensores_svc.time, "time", lambda: reloj[0])
    sensores_svc.anotar(9002, {"v": 5.0, "ma": 200.0}, 3.7, 50)
    reloj[0] += 20
    sensores_svc.anotar(9002, None, 3.69, 49)
    d = sensores_svc.ahora(9002, "descargando")
    assert d["ma"] == 200.0 and d["celda_v"] == 3.69          # las dos se enseñan
    assert d["estimacion"]["i_celda_ma"] is None               # pero no se cruzan
    assert "momentos distintos" in d["estimacion"]["motivo"]
