"""La trampa dice qué config tiene aplicada y el servidor avisa si no es la suya.

Fallo F7 del experimento del artículo B: una trampa con los valores de fábrica sigue viva y
mandando telemetría, así que el centinela la da por sana.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev, cfg):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": dev, "cfg_aplicada": cfg})
    assert r.status_code == 200, r.text
    return r.json()["cfg"]


def _titulos(cliente):
    return [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]


def test_una_trampa_con_la_config_de_fabrica_da_aviso(cliente):
    from app.services import config_aplicada as svc
    svc.olvidar()
    _tele(cliente, "zero-CFG1", {"v": 0, "origen": "fabrica"})
    # Un informe suelto no: tras arrancar sin red la config llega justo después.
    assert not any("zero-CFG1" in t for t in _titulos(cliente))
    tid = next(k for k, v in svc._estado.items() if v["origen"] == "fabrica")
    svc._estado[tid]["desde"] -= svc.GRACIA_S + 1
    _tele(cliente, "zero-CFG1", {"v": 0, "origen": "fabrica"})
    assert any("zero-CFG1" in t and "fábrica" in t for t in _titulos(cliente))


def test_un_desfase_de_un_informe_es_normal_y_uno_que_dura_no(cliente):
    from app.services import config_aplicada as svc
    svc.olvidar()
    quiere = _tele(cliente, "zero-CFG2", {"v": 0, "origen": "servidor"})
    mala = {"v": quiere + 5, "origen": "disco"}
    _tele(cliente, "zero-CFG2", mala)
    assert not any("zero-CFG2" in t for t in _titulos(cliente)), "un solo informe no es desfase"
    # Mismo desfase dos veces y pasado el margen: ahora sí.
    tid = next(k for k, v in svc._estado.items() if v["v"] == mala["v"])
    svc._estado[tid]["desde"] -= svc.GRACIA_S + 1
    _tele(cliente, "zero-CFG2", mala)
    assert any("zero-CFG2" in t and "no ha aplicado" in t for t in _titulos(cliente))
    # Y al aplicarla, el aviso se va.
    _tele(cliente, "zero-CFG2", {"v": quiere, "origen": "servidor"})
    assert not any("zero-CFG2" in t for t in _titulos(cliente))


def test_un_dato_malformado_no_rompe_la_telemetria(cliente):
    for raro in ("v3", [1, 2], {"v": "7", "origen": "servidor"}, {"v": 1, "origen": "marte"},
                 {"v": True, "origen": "disco"}):
        r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                         json={"device_id": "zero-CFG3", "cfg_aplicada": raro})
        assert r.status_code == 200, (raro, r.text)
