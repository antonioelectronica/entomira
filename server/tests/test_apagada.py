"""Una trampa apagada desde la web sale «Apagada» al momento, no «En línea» 25 minutos.

El servidor le ORDENA apagarse; seguir pintándola viva durante el margen de desconexión era
mentir con un dato que él mismo tenía. Y si vuelve a hablar, deja de estar apagada.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": dev})
    assert r.status_code == 200, r.text
    return r.json()


def _trampa(cliente, dev):
    return next(t for t in cliente.get("/api/v1/traps", auth=ADMIN).json()
                if t["device_id"] == dev)


def test_apagada_desde_la_web_y_vuelta_a_la_vida(cliente):
    dev = "zero-OFF1"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    assert _trampa(cliente, dev)["en_linea"] is True

    assert cliente.post(f"/api/v1/traps/{tid}/apagar", auth=ADMIN).status_code == 200
    # Pedida pero aún no entregada: sigue viva (la orden puede no llegar nunca).
    assert _trampa(cliente, dev)["en_linea"] is True

    assert _tele(cliente, dev).get("apaga") is True          # entregada
    t = _trampa(cliente, dev)
    assert t["estado"] == "apagada"
    assert t["en_linea"] is False
    titulos = [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]
    assert not any("sin subir fotos" in x and dev in x for x in titulos)

    # Un reintento solapado en los primeros segundos no la «resucita»...
    _tele(cliente, dev)
    assert _trampa(cliente, dev)["estado"] == "apagada"
    # ...pero si vuelve a hablar más tarde (la encendieron, o no se apagó), ya no está apagada.
    from app.services import live as live_svc
    live_svc._apagada_desde[tid] -= live_svc.REHABLA_S + 1
    _tele(cliente, dev)
    t = _trampa(cliente, dev)
    assert t["en_linea"] is True and t["estado"] != "apagada"
