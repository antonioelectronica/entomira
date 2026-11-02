"""Log de la Zero en el servidor: subida con clave, lectura de admin y orden que no se pierde."""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev):
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": dev})
    assert r.status_code == 200
    return r.json()


def _tid(cliente, dev):
    return next(t for t in cliente.get("/api/v1/traps", auth=ADMIN).json()
                if t["device_id"] == dev)["id"]


def _subir(cliente, dev, texto="hola", motivo="corte", key=DEVICE_KEY):
    return cliente.post(f"/api/v1/traps/{dev}/log", headers={"X-API-Key": key},
                        json={"boot": "b1", "version": "zero-0.9", "motivo": motivo,
                              "texto": texto})


def test_subida_y_lectura_del_ultimo_log(cliente):
    dev = "zero-log-1"
    _tele(cliente, dev)
    tid = _tid(cliente, dev)
    assert cliente.get(f"/api/v1/traps/{tid}/logs/ultimo", auth=ADMIN).status_code == 404
    r = _subir(cliente, dev, "línea 1\nlínea 2\n")
    assert r.status_code == 200
    texto = cliente.get(f"/api/v1/traps/{tid}/logs/ultimo", auth=ADMIN).text
    assert "línea 2" in texto and "zero-0.9" in texto and "motivo corte" in texto
    lista = cliente.get(f"/api/v1/traps/{tid}/logs", auth=ADMIN).json()
    assert lista[0]["nombre"] == r.json()["nombre"]
    assert cliente.get(f"/api/v1/traps/{tid}/logs/{lista[0]['nombre']}", auth=ADMIN).status_code == 200


def test_sin_clave_o_con_datos_raros_se_rechaza(cliente):
    dev = "zero-log-2"
    _tele(cliente, dev)
    tid = _tid(cliente, dev)
    assert _subir(cliente, dev, key="mala").status_code == 401
    assert _subir(cliente, dev, motivo="../x").status_code == 422
    assert _subir(cliente, dev, texto="x" * 300_001).status_code == 422
    assert _subir(cliente, "zero-que-no-existe").status_code == 404
    assert cliente.get(f"/api/v1/traps/{tid}/logs/entomira.db", auth=ADMIN).status_code == 404
    # Con la barra codificada la ruta no casa y responde la SPA: nunca el fichero.
    r = cliente.get(f"/api/v1/traps/{tid}/logs/..%2F..%2Fentomira.db", auth=ADMIN)
    assert "text/plain" not in r.headers.get("content-type", "") and "SQLite" not in r.text
    assert cliente.get(f"/api/v1/traps/{tid}/logs").status_code == 401


def test_orden_de_log_se_repite_hasta_recibirlo(cliente):
    dev = "zero-log-3"
    _tele(cliente, dev)
    tid = _tid(cliente, dev)
    assert _tele(cliente, dev)["log"] is False
    assert cliente.post(f"/api/v1/traps/{tid}/log/pedir", auth=ADMIN).status_code == 200
    assert _tele(cliente, dev)["log"] is True
    assert _tele(cliente, dev)["log"] is True          # la respuesta anterior pudo perderse
    assert _subir(cliente, dev, motivo="pedido").status_code == 200
    assert _tele(cliente, dev)["log"] is False


def test_dos_subidas_en_el_mismo_segundo_no_se_pisan(cliente):
    dev = "zero-log-4"
    _tele(cliente, dev)
    tid = _tid(cliente, dev)
    a = _subir(cliente, dev, "uno", motivo="pedido").json()["nombre"]
    b = _subir(cliente, dev, "dos", motivo="pedido").json()["nombre"]
    assert a != b
    assert len(cliente.get(f"/api/v1/traps/{tid}/logs", auth=ADMIN).json()) == 2


def test_el_log_de_un_corte_publica_el_despertar_confirmado(cliente):
    import time
    dev = "zero-log-5"
    _tele(cliente, dev)
    tid = _tid(cliente, dev)

    def ficha():
        return next(t for t in cliente.get("/api/v1/traps", auth=ADMIN).json() if t["id"] == tid)

    def subir(motivo, despertar):
        return cliente.post(f"/api/v1/traps/{dev}/log", headers={"X-API-Key": DEVICE_KEY},
                            json={"motivo": motivo, "texto": "x", "despertar": despertar})

    objetivo = int(time.time()) + 1800
    assert subir("corte_panel", objetivo).status_code == 200
    t = ficha()
    assert t["despertar_origen"] == "corte_panel" and t["despertar_confirmado"]
    assert abs(_epoch(t["despertar_confirmado"]) - objetivo) < 2
    # Un log que no es de corte no lo toca; un apagado sin alarma lo borra.
    assert subir("pedido", None).status_code == 200
    assert ficha()["despertar_origen"] == "corte_panel"
    assert subir("apagado", None).status_code == 200
    t = ficha()
    assert t["despertar_confirmado"] is None and t["despertar_origen"] == "apagado"
    # Una hora absurda no se publica como confirmada.
    assert subir("corte", int(time.time()) + 60 * 86400).status_code == 200
    assert ficha()["despertar_confirmado"] is None


def _epoch(iso):
    from datetime import datetime, timezone
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
