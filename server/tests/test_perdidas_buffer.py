"""Las fotos que la trampa pierde en su buffer se ven en el panel, no solo en su log."""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev, **extra):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": dev, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _titulos(cliente):
    return [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]


def test_perdidas_del_buffer_dan_aviso(cliente):
    dev = "zero-BUF1"
    _tele(cliente, dev, buf_perdidas={"descartadas": 0, "sin_guardar": 0})
    assert not any("ha perdido fotos" in t and dev in t for t in _titulos(cliente))

    _tele(cliente, dev, buf_perdidas={"descartadas": 0, "sin_guardar": 3})
    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
    a = next(a for a in avisos if "ha perdido fotos" in a["titulo"] and dev in a["titulo"])
    assert a["nivel"] == "critico" and "3 sin poder guardar" in a["detalle"]


def test_un_dato_malformado_no_rompe_la_telemetria(cliente):
    dev = "zero-BUF2"
    _tele(cliente, dev, buf_perdidas={"descartadas": -1, "sin_guardar": "x"})
    _tele(cliente, dev, buf_perdidas="basura")
    assert not any("ha perdido fotos" in t and dev in t for t in _titulos(cliente))
