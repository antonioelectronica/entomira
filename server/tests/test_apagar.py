"""Apagado a distancia: viaja una vez, caduca, se cancela y no convive con un reinicio."""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": dev, "red": "wifi"})
    assert r.status_code == 200
    return r.json()


def _id(cliente, dev):
    _tele(cliente, dev)
    return next(t["id"] for t in cliente.get("/api/v1/traps", auth=ADMIN).json()
                if t["device_id"] == dev)


def test_la_orden_se_entrega_una_sola_vez(cliente):
    """Si se quedara puesta, la placa la leeria otra vez al encenderla y se apagaria sola."""
    tid = _id(cliente, "zero-APAGA1")
    respuesta = cliente.post(f"/api/v1/traps/{tid}/apagar", auth=ADMIN)
    assert respuesta.status_code == 200
    assert "A-GND" in respuesta.json()["aviso"]
    assert _tele(cliente, "zero-APAGA1")["apaga"] is True
    assert _tele(cliente, "zero-APAGA1")["apaga"] is False


def test_se_puede_cancelar_antes_de_recogerla(cliente):
    tid = _id(cliente, "zero-APAGA2")
    cliente.post(f"/api/v1/traps/{tid}/apagar", auth=ADMIN)
    assert cliente.post(f"/api/v1/traps/{tid}/apagar/cancelar", auth=ADMIN).status_code == 200
    assert _tele(cliente, "zero-APAGA2")["apaga"] is False


def test_la_ultima_orden_manda(cliente):
    """Apagar y reiniciar nunca viajan juntos: el resultado dependeria del orden de lectura."""
    tid = _id(cliente, "zero-APAGA3")
    cliente.post(f"/api/v1/traps/{tid}/reiniciar", auth=ADMIN)
    cliente.post(f"/api/v1/traps/{tid}/apagar", auth=ADMIN)
    j = _tele(cliente, "zero-APAGA3")
    assert j["apaga"] is True and j["reinicia"] is False

    cliente.post(f"/api/v1/traps/{tid}/apagar", auth=ADMIN)
    cliente.post(f"/api/v1/traps/{tid}/reiniciar", auth=ADMIN)
    j = _tele(cliente, "zero-APAGA3")
    assert j["reinicia"] is True and j["apaga"] is False


def test_trampa_inexistente(cliente):
    assert cliente.post("/api/v1/traps/999999/apagar", auth=ADMIN).status_code == 404
